import subprocess
from pathlib import Path
from typing import Any

from app.ai.providers.factory import get_vision_provider
from app.ai.providers.schemas import Transcript, TranscriptSegment, WordTimestamp
from app.ai.rough_cut_prompts import (
    FINAL_CUT_SYSTEM_PROMPT,
    FINAL_CUT_USER_PROMPT,
    final_cut_response_schema,
)
from app.models.entities import Template
from app.schemas.final_cut import CandidateSegment, MultimodalScoreResult, SegmentScore


FRAME_EXTRACTION_TIMEOUT_SECONDS = 120


class FinalCutError(RuntimeError):
    pass


def build_candidate_segments(transcript: Transcript) -> list[CandidateSegment]:
    source_segments = [segment for segment in transcript.segments if segment.end > segment.start]
    if not source_segments:
        words = [word for segment in transcript.segments for word in segment.words]
        if words:
            source_segments = [TranscriptSegment(
                text=" ".join(word.word for word in words),
                start=min(word.start for word in words),
                end=max(word.end for word in words),
                words=words,
            )]

    candidates = [
        CandidateSegment(
            id=f"segment-{index:04d}",
            source_start=segment.start,
            source_end=segment.end,
            transcript=segment.text.strip() or " ".join(word.word for word in segment.words),
        )
        for index, segment in enumerate(source_segments, start=1)
        if (segment.text.strip() or segment.words)
    ]
    if not candidates:
        raise FinalCutError("Transcript has no timestamped segments to score")
    return candidates


def extract_segment_frames(video_path: Path, segments: list[CandidateSegment], output_dir: Path) -> list[dict[str, Any]]:
    """Extract one midpoint JPG per transcript segment for multimodal visual review."""
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for segment in segments:
        midpoint = (segment.source_start + segment.source_end) / 2
        output_path = output_dir / f"{segment.id}.jpg"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{midpoint:.3f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "3", str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=FRAME_EXTRACTION_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise FinalCutError(f"Frame extraction timed out for {segment.id}") from exc
        except subprocess.CalledProcessError as exc:
            raise FinalCutError(f"Frame extraction failed for {segment.id}: {(exc.stderr or '')[-500:]}") from exc
        if output_path.exists():
            frames.append({"segment_id": segment.id, "timestamp": midpoint, "path": str(output_path)})
    return frames


def score_segments_with_provider(
    video_uri: str,
    template: Template,
    segments: list[CandidateSegment],
    frames: list[dict[str, Any]],
    speaker_segments: list[dict[str, Any]] | None = None,
) -> list[SegmentScore]:
    provider = get_vision_provider()
    context = {
        "task": "rough_cut_scoring",
        "template": template.structure_json,
        "segments": [segment.model_dump(mode="json") for segment in segments],
        # The concrete provider uploads/encodes these worker-local frame paths when it calls its API.
        "sampled_frames": frames,
        # Landmark/pose scores are advisory evidence. The model must not fabricate biometric claims.
        "speaker_state_features": speaker_segments or [],
    }
    raw_result = provider.analyze_video(
        video_uri,
        f"{FINAL_CUT_SYSTEM_PROMPT}\n\n{FINAL_CUT_USER_PROMPT}",
        response_schema=final_cut_response_schema(),
        context=context,
    )
    try:
        parsed = MultimodalScoreResult.model_validate(raw_result)
    except Exception as exc:
        raise FinalCutError("Multimodal provider returned invalid final-cut JSON") from exc

    expected_ids = {segment.id for segment in segments}
    scores_by_id = {score.segment_id: score for score in parsed.segment_scores}
    if set(scores_by_id) != expected_ids:
        raise FinalCutError("Multimodal provider must score every transcript segment exactly once")
    return [scores_by_id[segment.id] for segment in segments]


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _slice_segment(segment: CandidateSegment, markers: list[dict[str, Any]]) -> list[tuple[float, float]]:
    bounds = {segment.source_start, segment.source_end}
    for marker in markers:
        start = float(marker["start"])
        end = float(marker["end"])
        if _overlap(segment.source_start, segment.source_end, start, end) > 0:
            bounds.add(max(segment.source_start, start))
            bounds.add(min(segment.source_end, end))
    ordered = sorted(bounds)
    return [
        (ordered[index], ordered[index + 1])
        for index in range(len(ordered) - 1)
        if ordered[index + 1] - ordered[index] >= 0.05
    ]


def merge_cut_evidence(
    segments: list[CandidateSegment],
    scores: list[SegmentScore],
    clip_analysis: list[dict[str, Any]],
    speaker_segments: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge semantic/video evidence with exact silence and filler intervals."""
    score_map = {score.segment_id: score for score in scores}
    decisions: list[dict[str, Any]] = []
    speaker_segments = speaker_segments or []

    for segment in segments:
        score = score_map[segment.id]
        related_speaker_states = [
            state for state in speaker_segments
            if _overlap(
                segment.source_start, segment.source_end,
                float(state.get("source_start", 0)), float(state.get("source_end", 0)),
            ) > 0
        ]
        quality = (
            score.semantic_completeness * 0.45
            + score.presentation_naturalness * 0.30
            + score.template_alignment * 0.25
        )
        for start, end in _slice_segment(segment, clip_analysis):
            duration = end - start
            overlaps = [
                marker for marker in clip_analysis
                if _overlap(start, end, float(marker["start"]), float(marker["end"])) > 0
            ]
            silence_seconds = sum(
                _overlap(start, end, float(marker["start"]), float(marker["end"]))
                for marker in overlaps if marker.get("type") == "silence"
            )
            filler_count = sum(
                1 for marker in overlaps if marker.get("type") in {"filler_word", "repetition"}
            )
            hard_remove = silence_seconds >= duration * 0.8 or filler_count > 0
            model_remove = score.recommended_action == "remove" and quality < 65

            if hard_remove or model_remove:
                reasons: list[str] = []
                if silence_seconds > 0:
                    reasons.append(f"包含 {silence_seconds:.1f} 秒靜音")
                if filler_count:
                    reasons.append(f"包含 {filler_count} 個贅詞或重複語")
                if model_remove:
                    reasons.append(f"多模態評分建議移除：{score.reason}")
                confidence = min(100, round(72 + silence_seconds / max(duration, 0.1) * 20 + filler_count * 4))
                action = "remove"
                reason = "；".join(reasons)
            else:
                action = "keep"
                confidence = round(max(0, min(100, quality)))
                reason = (
                    f"保留：語意完整度 {score.semantic_completeness:.0f}、"
                    f"畫面自然度 {score.presentation_naturalness:.0f}、"
                    f"模板符合度 {score.template_alignment:.0f}。{score.reason}"
                )
            creator_hints = [
                suggestion
                for state in related_speaker_states
                for suggestion in state.get("suggestions", [])
            ]
            speaker_summary: dict[str, Any] | None = None
            if related_speaker_states:
                speaker_summary = {
                    "confidence_score": round(sum(float(state.get("confidence_score", 0)) for state in related_speaker_states) / len(related_speaker_states)),
                    "fluency_score": round(sum(float(state.get("fluency_score", 0)) for state in related_speaker_states) / len(related_speaker_states)),
                    "assessment_status": "assessed" if any(state.get("assessment_status", "assessed") == "assessed" for state in related_speaker_states) else "insufficient_visual_evidence",
                    "metrics": related_speaker_states[0].get("metrics", {}),
                }
            review_flags = []
            has_assessed_speaker_state = any(state.get("assessment_status", "assessed") == "assessed" for state in related_speaker_states)
            if has_assessed_speaker_state and speaker_summary and speaker_summary["confidence_score"] < 55:
                review_flags.append("講者自信呈現偏低：請人工確認是否重錄或改以 B-Roll 覆蓋。")
            if has_assessed_speaker_state and speaker_summary and speaker_summary["fluency_score"] < 55:
                review_flags.append("講者節奏流暢度偏低：請確認是否縮短、重錄或補充字卡。")
            decisions.append({
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "action": action,
                "confidence_score": confidence,
                "reason": reason,
                "evidence": {
                    "semantic_completeness": score.semantic_completeness,
                    "presentation_naturalness": score.presentation_naturalness,
                    "template_alignment": score.template_alignment,
                    "silence_seconds": round(silence_seconds, 3),
                    "filler_count": filler_count,
                },
                # Delivery feedback must never silently convert a keep segment into a cut.
                "speaker_state": speaker_summary,
                "creator_hints": list(dict.fromkeys(creator_hints)),
                "review_flags": review_flags,
            })
    # Silences can occur between ASR segments; retain them as explicit remove intervals.
    for marker in clip_analysis:
        start = float(marker["start"])
        end = float(marker["end"])
        if any(_overlap(segment.source_start, segment.source_end, start, end) > 0 for segment in segments):
            continue
        marker_type = marker.get("type", "issue")
        decisions.append({
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "action": "remove",
            "confidence_score": round(float(marker.get("confidence", 0.8)) * 100),
            "reason": f"此段位於逐字稿間隙，包含 {marker_type}。",
            "evidence": {"marker_type": marker_type},
        })
    return sorted(decisions, key=lambda item: (item["source_start"], item["source_end"]))
