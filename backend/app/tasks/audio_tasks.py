import re
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.ai.providers.factory import get_asr_provider
from app.ai.providers.schemas import Transcript, WordTimestamp
from app.db.session import SessionLocal
from app.models.entities import AIAnalysis, AnalysisType, MediaAsset, Timeline
from app.schemas.academic import AcademicGlossaryEntry
from app.services.academic import canonicalise_glossary_text
from app.core.progress import publish_project_status
from app.core.ai_retry import is_retryable_ai_error, retry_ai_task
from app.services.education_subtitles import analyze_delivery
from app.services.kinetic_subtitles import annotate_transcript_kinetics
from app.services.storage import download_object
from app.worker import celery_app


SILENCE_NOISE_THRESHOLD = "-35dB"
SILENCE_MIN_DURATION_SECONDS = 0.8
FFMPEG_TIMEOUT_SECONDS = 10 * 60

# Keep this deliberately conservative: every marker remains user-reviewable in the timeline.
FILLER_WORDS = frozenset({"呃", "嗯", "啊", "那個", "就是", "然後", "然後呢", "額", "uh", "um", "erm"})
TRIM_PUNCTUATION = "，。！？、；：,.!?;:()（）[]【】\"'“”"


class AudioAnalysisError(RuntimeError):
    pass


def _run(command: list[str], timeout: int = FFMPEG_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioAnalysisError(f"Command timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        raise AudioAnalysisError((exc.stderr or "FFmpeg command failed")[-2000:]) from exc
    except OSError as exc:
        raise AudioAnalysisError("ffmpeg/ffprobe is not installed or executable") from exc


def detect_silences(audio_path: Path) -> list[dict[str, float]]:
    """Return silences detected by FFmpeg's silencedetect filter."""
    result = _run([
        "ffmpeg", "-hide_banner", "-i", str(audio_path),
        "-af", f"silencedetect=noise={SILENCE_NOISE_THRESHOLD}:d={SILENCE_MIN_DURATION_SECONDS}",
        "-f", "null", "-",
    ])
    starts: list[float] = []
    silences: list[dict[str, float]] = []
    for line in result.stderr.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if end_match and starts:
            start = starts.pop(0)
            end = float(end_match.group(1))
            if end > start:
                silences.append({"start": start, "end": end})
    return silences


def _normalized_word(word: str) -> str:
    return word.strip().lower().strip(TRIM_PUNCTUATION)


def _marker(
    marker_type: str,
    start: float,
    end: float,
    reason: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "type": marker_type,
        "start": round(start, 3),
        "end": round(max(end, start), 3),
        "reason": reason,
        "confidence": confidence,
        "suggested_action": "remove",
    }


def detect_filler_markers(words: Iterable[WordTimestamp]) -> list[dict[str, Any]]:
    """Detect fillers and conservative consecutive repetitions from word timestamps."""
    ordered_words = sorted(words, key=lambda item: (item.start, item.end))
    markers: list[dict[str, Any]] = []

    for word in ordered_words:
        normalized = _normalized_word(word.word)
        if normalized in FILLER_WORDS:
            markers.append(_marker(
                "filler_word", word.start, word.end,
                f"Detected filler word: {word.word}", 0.95,
            ))

    for index in range(len(ordered_words) - 1):
        first = _normalized_word(ordered_words[index].word)
        second = _normalized_word(ordered_words[index + 1].word)
        if (
            first == "那"
            and second == "個"
            and ordered_words[index + 1].start - ordered_words[index].end <= 0.5
        ):
            markers.append(_marker(
                "filler_word",
                ordered_words[index].start,
                ordered_words[index + 1].end,
                "Detected filler phrase: 那個",
                0.95,
            ))

    index = 0
    while index < len(ordered_words):
        normalized = _normalized_word(ordered_words[index].word)
        if not normalized or normalized in FILLER_WORDS:
            index += 1
            continue
        end_index = index + 1
        while (
            end_index < len(ordered_words)
            and _normalized_word(ordered_words[end_index].word) == normalized
            and ordered_words[end_index].start - ordered_words[end_index - 1].end <= 0.8
        ):
            end_index += 1
        if end_index - index >= 3:
            markers.append(_marker(
                "repetition",
                ordered_words[index + 1].start,
                ordered_words[end_index - 1].end,
                f"Repeated word: {ordered_words[index].word}",
                0.8,
            ))
        index = end_index
    return markers


def _all_words(transcript: Transcript) -> list[WordTimestamp]:
    return [word for segment in transcript.segments for word in segment.words]


def build_clip_analysis_list(
    silences: list[dict[str, float]], filler_markers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = [
        _marker(
            "silence", silence["start"], silence["end"],
            f"Silence longer than {SILENCE_MIN_DURATION_SECONDS}s", 1.0,
        )
        for silence in silences
    ]
    clips.extend(filler_markers)
    return sorted(clips, key=lambda item: (item["start"], item["end"]))


def build_rough_cut_timeline_suggestions(
    *, asset_id: str, duration_seconds: float, markers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Partition source time into reviewable keep/remove clips without losing source timing."""
    if duration_seconds <= 0:
        return []
    merged: list[dict[str, Any]] = []
    for marker in sorted(markers, key=lambda item: (float(item["start"]), float(item["end"]))):
        start = max(0.0, float(marker["start"])); end = min(duration_seconds, float(marker["end"]))
        if end <= start:
            continue
        if merged and start <= float(merged[-1]["source_end"]) + .04:
            current = merged[-1]; current["source_end"] = max(float(current["source_end"]), end)
            current["issue_types"] = sorted(set(current["issue_types"]) | {str(marker["type"])})
            current["reasons"].append(str(marker["reason"])); current["confidence_score"] = max(float(current["confidence_score"]), float(marker["confidence"]) * 100)
        else:
            merged.append({"source_start": start, "source_end": end, "issue_types": [str(marker["type"])], "reasons": [str(marker["reason"])], "confidence_score": float(marker["confidence"]) * 100})
    suggestions: list[dict[str, Any]] = []; cursor = 0.0
    for index, removed in enumerate(merged):
        start, end = float(removed["source_start"]), float(removed["source_end"])
        if start - cursor >= .04:
            suggestions.append({"id": f"rough-keep-{index}", "source_asset_id": asset_id, "source_start": round(cursor, 3), "source_end": round(start, 3), "action": "keep", "confidence_score": 100, "reason": "保留的語音與畫面內容。"})
        issue_types = list(removed["issue_types"])
        suggestions.append({"id": f"rough-remove-{index}", "source_asset_id": asset_id, "source_start": round(start, 3), "source_end": round(end, 3), "action": "remove", "confidence_score": round(float(removed["confidence_score"]), 1), "reason": "；".join(dict.fromkeys(removed["reasons"])), "issue_types": issue_types})
        cursor = end
    if duration_seconds - cursor >= .04:
        suggestions.append({"id": "rough-keep-tail", "source_asset_id": asset_id, "source_start": round(cursor, 3), "source_end": round(duration_seconds, 3), "action": "keep", "confidence_score": 100, "reason": "保留的語音與畫面內容。"})
    return suggestions


@celery_app.task(bind=True, name="audio.analyze_audio_rough_cut")
def analyze_audio_rough_cut(self, asset_id: str) -> dict[str, Any]:
    """Persist silence/filler/repetition candidates for user-reviewed rough cutting."""
    db = SessionLocal()
    analysis: AIAnalysis | None = None
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None:
            raise AudioAnalysisError(f"Media asset {asset_id} not found")
        if not asset.audio_key:
            raise AudioAnalysisError("Media asset has no extracted audio; run media preprocessing first")

        analysis = AIAnalysis(
            media_asset_id=asset.id,
            analysis_type=AnalysisType.ROUGH_CUT,
            model_name=get_asr_provider().name,
            status="processing",
            result_json={},
        )
        db.add(analysis)
        db.commit()
        publish_project_status(str(asset.project_id), progress=10, stage="audio_downloading", message="正在下載音訊素材", job_id=self.request.id)

        with tempfile.TemporaryDirectory(prefix=f"rough-cut-{asset_id}-") as temp_dir:
            audio_path = Path(temp_dir) / "audio-16khz.wav"
            download_object(asset.audio_key, str(audio_path))
            publish_project_status(str(asset.project_id), progress=25, stage="silence_detection", message="正在偵測靜音區段", job_id=self.request.id)
            silences = detect_silences(audio_path)
            publish_project_status(str(asset.project_id), progress=50, stage="transcribing", message="正在語音轉錄", job_id=self.request.id)
            transcript = get_asr_provider().transcribe(str(audio_path), word_timestamps=True)
            academic_timeline = db.scalar(select(Timeline).where(Timeline.project_id == asset.project_id, Timeline.is_current.is_(True)))
            glossary = [AcademicGlossaryEntry.model_validate(item) for item in dict(academic_timeline.settings_json or {}).get("academic_glossary", [])] if academic_timeline else []
            glossary_review: list[dict[str, Any]] = []
            for segment in transcript.segments:
                segment.text, corrections = canonicalise_glossary_text(segment.text, glossary)
                glossary_review.extend({"source_start": segment.start, **item} for item in corrections)
                for word in segment.words:
                    word.word, corrections = canonicalise_glossary_text(word.word, glossary)
                    glossary_review.extend({"source_start": word.start, **item} for item in corrections)
            annotate_transcript_kinetics(transcript)
            publish_project_status(str(asset.project_id), progress=80, stage="nlp_analysis", message="正在分析贅詞與語速", job_id=self.request.id)
            transcript.delivery_hints = analyze_delivery(transcript, silences)
            filler_markers = detect_filler_markers(_all_words(transcript))
            clip_analysis = build_clip_analysis_list(silences, filler_markers)
            timeline_suggestions = build_rough_cut_timeline_suggestions(
                asset_id=str(asset.id), duration_seconds=float(asset.duration_seconds or 0), markers=clip_analysis,
            )

        analysis.status = "completed"
        analysis.confidence = 1.0
        analysis.result_json = {
            "version": 1,
            "silences": silences,
            "filler_markers": filler_markers,
            "clip_analysis": clip_analysis,
            "timeline_suggestions": timeline_suggestions,
            "transcript": transcript.model_dump(mode="json"),
            "academic_glossary_review": glossary_review,
        }
        db.commit()
        # Rebuild once transcripts become available, adding timestamped text vectors.
        celery_app.send_task("media.generate_media_embeddings", args=[str(asset.id)])
        # This produces advisory delivery hints; it intentionally does not cut any footage.
        celery_app.send_task("analysis.analyze_speaker_state", args=[str(asset.id)])
        publish_project_status(str(asset.project_id), progress=100, stage="audio_analysis_completed", status="completed", message="音訊粗剪分析完成", job_id=self.request.id)
        return {
            "analysis_id": str(analysis.id),
            "asset_id": asset_id,
            "clip_count": len(clip_analysis),
            "status": analysis.status,
        }
    except Exception as exc:
        db.rollback()
        if asset is not None and analysis is not None and is_retryable_ai_error(exc):
            current = db.get(AIAnalysis, analysis.id)
            if current is not None:
                current.status = "retrying"
                current.error_message = str(exc)
                db.commit()
            retry_ai_task(
                self, exc, project_id=str(asset.project_id), stage="transcribing",
                message="AI 轉錄服務暫時不可用", job_id=self.request.id,
            )
        if analysis is not None:
            current = db.get(AIAnalysis, analysis.id)
            if current is not None:
                current.status = "failed"
                current.error_message = str(exc)
                db.commit()
        if asset is not None:
            publish_project_status(str(asset.project_id), progress=0, stage="audio_analysis_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
