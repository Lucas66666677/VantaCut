from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.progress import publish_project_status
from app.db.session import SessionLocal
from app.models.entities import MediaAsset, MediaStatus
from app.services.audio_delivery import AudioDeliveryError
from app.services.storage import download_object, upload_object
from app.worker import celery_app


STEM_TIMEOUT_SECONDS = 2 * 60 * 60


def _run(command: list[str], *, timeout_seconds: int = STEM_TIMEOUT_SECONDS) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise AudioDeliveryError("Stem extraction timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise AudioDeliveryError(f"Stem extraction failed: {(exc.stderr or '')[-3000:]}") from exc


def _combine_music(bass_path: Path, other_path: Path, output_path: Path) -> None:
    _run([
        "ffmpeg", "-y", "-i", str(bass_path), "-i", str(other_path),
        "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0:dropout_transition=0[music]",
        "-map", "[music]", "-c:a", "pcm_s16le", str(output_path),
    ])


@celery_app.task(bind=True, name="audio.extract_stems")
def extract_stems(self, media_asset_id: str, model_name: str = "htdemucs") -> dict[str, Any]:
    """Run local Demucs and store editable Dialogue/Music/SFX stems in MinIO.

    Demucs produces vocals/drums/bass/other. We retain those raw stems and expose a practical
    editorial mapping: dialogue=vocals, sfx=drums/percussive, music=bass+other.
    """
    db = SessionLocal()
    asset: MediaAsset | None = None
    try:
        asset = db.get(MediaAsset, UUID(media_asset_id))
        if asset is None:
            raise AudioDeliveryError("Media asset not found")
        if asset.status != MediaStatus.READY:
            raise AudioDeliveryError("Media asset must finish preprocessing before stem extraction")
        metadata = dict(asset.metadata_json or {})
        metadata["stems"] = {"status": "processing", "model": model_name}
        asset.metadata_json = metadata
        db.commit()
        project_id = str(asset.project_id)
        publish_project_status(project_id, progress=5, stage="stem_downloading", message="正在下載原始素材音訊", job_id=self.request.id)

        with tempfile.TemporaryDirectory(prefix=f"stems-{media_asset_id}-") as temporary:
            workdir = Path(temporary)
            source_video = workdir / "source.mp4"
            demucs_input = workdir / "input.wav"
            download_object(asset.storage_key, str(source_video))
            _run([
                "ffmpeg", "-y", "-i", str(source_video), "-vn", "-ar", "44100", "-ac", "2",
                "-c:a", "pcm_s16le", str(demucs_input),
            ], timeout_seconds=30 * 60)
            publish_project_status(project_id, progress=20, stage="stem_separating", message="Demucs 正在分離人聲與樂器", job_id=self.request.id)
            _run([
                sys.executable, "-m", "demucs.separate", "-n", model_name, "--out", str(workdir / "demucs"), str(demucs_input),
            ])
            stem_directory = workdir / "demucs" / model_name / demucs_input.stem
            raw = {name: stem_directory / f"{name}.wav" for name in ("vocals", "drums", "bass", "other")}
            missing = [name for name, path in raw.items() if not path.exists()]
            if missing:
                raise AudioDeliveryError(f"Demucs did not produce required stems: {', '.join(missing)}")
            music = workdir / "music.wav"
            _combine_music(raw["bass"], raw["other"], music)
            editor_stems = {"dialogue": raw["vocals"], "sfx": raw["drums"], "music": music}
            base_key = f"projects/{asset.project_id}/media/{asset.id}/stems"
            keys: dict[str, str] = {}
            publish_project_status(project_id, progress=80, stage="stem_uploading", message="正在儲存可編輯音軌", job_id=self.request.id)
            for name, path in editor_stems.items():
                key = f"{base_key}/{name}.wav"
                upload_object(key, str(path), "audio/wav")
                keys[f"{name}_key"] = key
            raw_keys: dict[str, str] = {}
            for name, path in raw.items():
                key = f"{base_key}/raw/{name}.wav"
                upload_object(key, str(path), "audio/wav")
                raw_keys[f"{name}_key"] = key

        metadata = dict(asset.metadata_json or {})
        metadata["stems"] = {
            "status": "completed", "provider": "demucs", "model": model_name,
            "sample_rate": 44100, "channels": 2, "mapping": {
                "dialogue": "vocals", "sfx": "drums", "music": "bass + other",
            }, **keys, "raw": raw_keys,
        }
        asset.metadata_json = metadata
        db.commit()
        publish_project_status(project_id, progress=100, stage="stem_completed", status="completed", message="音訊分軌完成", job_id=self.request.id)
        return {"media_asset_id": media_asset_id, "status": "completed", "stems": keys}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                metadata = dict(current.metadata_json or {})
                metadata["stems"] = {"status": "failed", "error": str(exc)}
                current.metadata_json = metadata
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="stem_failed", status="failed", message=str(exc), job_id=self.request.id)
        raise
    finally:
        db.close()
