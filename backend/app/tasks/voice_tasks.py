"""Long-running GPU tasks for consented XTTS voice-profile extraction and replacement TTS."""
from __future__ import annotations

import tempfile
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.ai.providers.factory import get_voice_clone_provider
from app.core.config import settings
from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, Timeline, VoiceProfile, VoiceProfileStatus
from app.services.storage import download_object, upload_bytes, upload_object
from app.services.voice_cloning import VoiceCloningError, VoiceProfileCipher, choose_clean_reference, extract_reference_audio, fit_replacement_to_slot
from app.services.voice_morphing import VoiceMorphError, build_rvc_command, characters, extract_audio_range, extract_prosody, fit_morph_duration
from app.schemas.subtitle import SubtitleCue
from app.services.subtitles import cues_to_ass, cues_to_srt
from app.worker import celery_app


@celery_app.task(bind=True, name="voice.extract_profile")
def extract_voice_profile(self, voice_profile_id: str) -> dict[str, Any]:
    db = SessionLocal(); profile: VoiceProfile | None = None
    try:
        profile = db.get(VoiceProfile, UUID(voice_profile_id))
        if profile is None:
            raise VoiceCloningError("Voice profile not found")
        asset = db.get(MediaAsset, profile.source_media_asset_id)
        if asset is None or not asset.audio_key:
            raise VoiceCloningError("The selected media asset has no prepared audio track")
        profile.status = VoiceProfileStatus.EXTRACTING; db.commit()
        publish_project_status(str(profile.project_id), progress=8, stage="voice_reference_scanning", message="正在尋找最清晰的 3–5 秒講者語音", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"voice-profile-{profile.id}-") as temporary:
            workdir = Path(temporary); source, reference, artifact = workdir / "source.wav", workdir / "reference.wav", workdir / "conditioning.pt"
            download_object(asset.audio_key, str(source))
            selection = choose_clean_reference(source)
            extract_reference_audio(source, reference, selection)
            publish_project_status(str(profile.project_id), progress=48, stage="voice_embedding", message="正在建立加密聲音特徵向量", job_id=self.request.id)
            provider = get_voice_clone_provider()
            metadata = provider.extract_profile(str(reference), str(artifact))
            base = f"projects/{profile.project_id}/voice-profiles/{profile.id}"
            reference_key, artifact_key = f"{base}/reference.wav", f"{base}/conditioning.bin"
            upload_object(reference_key, str(reference), "audio/wav")
            upload_bytes(artifact_key, VoiceProfileCipher().encrypt(artifact.read_bytes()), "application/octet-stream")
        profile.status = VoiceProfileStatus.READY; profile.provider_name = provider.name; profile.reference_audio_key = reference_key; profile.conditioning_artifact_key = artifact_key
        profile.reference_start_seconds, profile.reference_duration_seconds, profile.quality_score = selection.start_seconds, selection.duration_seconds, selection.quality_score
        profile.metadata_json = {"selection_metrics": selection.metrics, "embedding_metadata": metadata, "consent_bound": True}; profile.error_message = None
        db.commit()
        publish_project_status(str(profile.project_id), progress=100, stage="voice_profile_ready", status="completed", message="聲音 Profile 已建立，可用於 AI 補錄", job_id=self.request.id)
        return {"voice_profile_id": str(profile.id), "status": "ready", "quality_score": selection.quality_score}
    except Exception as exc:
        db.rollback()
        if profile is not None:
            current = db.get(VoiceProfile, profile.id)
            if current is not None:
                current.status = VoiceProfileStatus.FAILED; current.error_message = str(exc); db.commit()
            publish_project_status(str(profile.project_id), progress=0, stage="voice_profile_failed", status="failed", message="聲音 Profile 建立失敗", job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="voice.generate_replacement")
def generate_voice_replacement(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id)); profile = db.get(VoiceProfile, UUID(str(request["voice_profile_id"])))
        if timeline is None or profile is None or profile.project_id != timeline.project_id:
            raise VoiceCloningError("Timeline or voice profile is invalid")
        if profile.status != VoiceProfileStatus.READY or not profile.conditioning_artifact_key:
            raise VoiceCloningError("Voice profile is not ready")
        subtitles = dict(timeline.settings_json.get("subtitles", {})); cue = next((item for item in subtitles.get("items", []) if item.get("id") == request["cue_id"]), None)
        if cue is None:
            raise VoiceCloningError("Subtitle cue not found")
        start, end = float(cue["start_time"]), float(cue["end_time"])
        publish_project_status(str(timeline.project_id), progress=15, stage="voice_synthesizing", message="正在以授權聲音生成補錄", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"voice-replacement-{timeline.id}-") as temporary:
            workdir = Path(temporary); encrypted, artifact, raw_output, output = workdir / "conditioning.bin", workdir / "conditioning.pt", workdir / "raw-replacement.wav", workdir / "replacement.wav"
            download_object(profile.conditioning_artifact_key, str(encrypted)); artifact.write_bytes(VoiceProfileCipher().decrypt(encrypted.read_bytes()))
            provider = get_voice_clone_provider()
            synthesis = provider.synthesize(text=str(request["replacement_text"]), language=str(request.get("language") or profile.language or "zh-cn"), artifact_path=str(artifact), output_wav=str(raw_output), emotion=str(request.get("emotion", "neutral")), tempo=float(request.get("tempo", 1.0)))
            synthesis["slot_alignment"] = fit_replacement_to_slot(raw_output, output, slot_duration=end - start)
            replacement_id = str(uuid4()); audio_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/voice-replacements/{replacement_id}.wav"
            publish_project_status(str(timeline.project_id), progress=85, stage="voice_uploading", message="正在儲存補錄音檔與時間軸設定", job_id=self.request.id)
            upload_object(audio_key, str(output), "audio/wav")
        replacements = list(timeline.settings_json.get("voice_replacements", []))
        replacements.append({"id": replacement_id, "cue_id": request["cue_id"], "voice_profile_id": str(profile.id), "audio_key": audio_key, "start_time": start, "end_time": end, "original_text": cue.get("text", ""), "replacement_text": request["replacement_text"], "emotion": request.get("emotion", "neutral"), "tempo": request.get("tempo", 1.0), "mix_policy": "replace_dialogue", "status": "completed", "synthesis": synthesis})
        updated_subtitles = {**subtitles, "items": [{**item, "text": request["replacement_text"], "words": []} if item.get("id") == request["cue_id"] else item for item in subtitles.get("items", [])]}
        subtitle_cues = [SubtitleCue.model_validate(item) for item in updated_subtitles["items"]]
        if updated_subtitles.get("srt_key"):
            upload_bytes(str(updated_subtitles["srt_key"]), cues_to_srt(subtitle_cues).encode("utf-8"), "application/x-subrip")
        if updated_subtitles.get("ass_key"):
            upload_bytes(str(updated_subtitles["ass_key"]), cues_to_ass(subtitle_cues).encode("utf-8"), "text/x-ssa")
        timeline.settings_json = {**dict(timeline.settings_json or {}), "subtitles": updated_subtitles, "voice_replacements": replacements[-100:]}; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="voice_replacement_ready", status="completed", message="AI 補錄完成，可在時間軸預覽", job_id=self.request.id)
        return {"timeline_id": timeline_id, "replacement_id": replacement_id, "audio_key": audio_key}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            publish_project_status(str(timeline.project_id), progress=0, stage="voice_replacement_failed", status="failed", message="AI 補錄失敗，請重試", job_id=self.request.id)
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="voice.generate_morph")
def generate_voice_morph(self, timeline_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Convert one owned source range while passing its F0/RMS contour to an RVC worker."""
    db = SessionLocal(); timeline: Timeline | None = None
    try:
        timeline = db.get(Timeline, UUID(timeline_id)); asset = db.get(MediaAsset, UUID(str(request["source_media_asset_id"])))
        if timeline is None or asset is None or asset.project_id != timeline.project_id or not asset.audio_key:
            raise VoiceMorphError("Timeline or source audio is unavailable")
        character = characters().get(str(request["character_id"]))
        if character is None:
            raise VoiceMorphError("Unsupported fictional voice character")
        start, end, timeline_start = float(request["source_start"]), float(request["source_end"]), float(request["timeline_start"])
        if asset.duration_seconds is not None and end > float(asset.duration_seconds) + .05:
            raise VoiceMorphError("Voice-morph range exceeds source audio")
        publish_project_status(str(timeline.project_id), progress=10, stage="voice_morph_preparing", message="正在準備原始演出音軌", job_id=self.request.id)
        with tempfile.TemporaryDirectory(prefix=f"voice-morph-{timeline.id}-") as temporary:
            workdir = Path(temporary); source, clip = workdir / "source.wav", workdir / "clip.wav"
            f0_json, envelope_json, raw, output = workdir / "f0.json", workdir / "envelope.json", workdir / "morph-raw.wav", workdir / "morph.wav"
            download_object(asset.audio_key, str(source)); extract_audio_range(input_path=str(source), output_wav=str(clip), start=start, end=end)
            publish_project_status(str(timeline.project_id), progress=32, stage="voice_morph_prosody", message="正在提取原始語調、節奏與音量起伏", job_id=self.request.id)
            prosody = extract_prosody(clip, f0_destination=f0_json, envelope_destination=envelope_json)
            publish_project_status(str(timeline.project_id), progress=58, stage="voice_morph_rvc", message=f"正在轉換為{character.emoji} {character.label}音色並保留情緒", job_id=self.request.id)
            try:
                subprocess.run(build_rvc_command(input_wav=str(clip), output_wav=str(raw), character=character, f0_json=str(f0_json), envelope_json=str(envelope_json)), check=True, capture_output=True, text=True, timeout=settings.rvc_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise VoiceMorphError("RVC voice conversion timed out") from exc
            except (subprocess.CalledProcessError, OSError) as exc:
                detail = getattr(exc, "stderr", "") or str(exc)
                raise VoiceMorphError(f"RVC voice conversion failed: {detail[-1500:]}") from exc
            fit_morph_duration(input_wav=str(raw), output_wav=str(output), duration=end - start)
            morph_id = str(uuid4()); audio_key = f"projects/{timeline.project_id}/timelines/{timeline.id}/voice-morphs/{morph_id}.wav"
            upload_object(audio_key, str(output), "audio/wav")
        item = {"id": morph_id, "status": "completed", "source_media_asset_id": str(asset.id), "source_start": start, "source_end": end, "start_time": timeline_start, "end_time": timeline_start + (end - start), "character_id": character.id, "character_label": character.label, "audio_key": audio_key, "mix_policy": "replace_dialogue", "prosody": prosody, "consent_confirmed": True}
        settings = dict(timeline.settings_json or {}); settings["voice_morphs"] = [*list(settings.get("voice_morphs", [])), item][-100:]; settings["voice_morph_status"] = {"status": "completed", "last_morph_id": morph_id, "character_id": character.id}; timeline.settings_json = settings; db.commit()
        publish_project_status(str(timeline.project_id), progress=100, stage="voice_morph_ready", status="completed", message="角色音色預覽已完成，可在時間軸聆聽", job_id=self.request.id)
        return {"timeline_id": str(timeline.id), "morph_id": morph_id, "audio_key": audio_key}
    except Exception as exc:
        db.rollback()
        if timeline is not None:
            settings = dict(timeline.settings_json or {}); settings["voice_morph_status"] = {"status": "failed", "error": str(exc)}; timeline.settings_json = settings; db.commit()
            publish_project_status(str(timeline.project_id), progress=0, stage="voice_morph_failed", status="failed", message="角色音色轉換失敗", job_id=self.request.id)
        raise
    finally:
        db.close()
