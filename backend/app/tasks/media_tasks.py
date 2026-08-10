import json
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from app.db.session import SessionLocal
from app.core.progress import publish_project_status
from app.models.entities import MediaAsset, MediaStatus
from app.services.storage import download_object, upload_object
from app.worker import celery_app


FFMPEG_TIMEOUT_SECONDS = 15 * 60


class MediaProcessingError(RuntimeError):
    pass


def _run(command: list[str], timeout: int = FFMPEG_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result
    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError(f"Command timed out after {timeout}s: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[-2000:]
        raise MediaProcessingError(f"FFmpeg command failed: {detail}") from exc
    except OSError as exc:
        raise MediaProcessingError("ffmpeg/ffprobe is not installed or not executable") from exc


def _probe(input_path: Path) -> dict[str, object]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,width,height,avg_frame_rate",
            "-select_streams",
            "v:0",
            "-of",
            "json",
            str(input_path),
        ],
        timeout=120,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    format_data = payload.get("format") or {}
    duration = float(format_data.get("duration") or 0)
    raw_fps = str(stream.get("avg_frame_rate") or "0/0")
    try:
        fps = float(Fraction(raw_fps))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {
        "duration": duration,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": fps,
        "video_codec": stream.get("codec_name"),
    }


@celery_app.task(name="media.process_new_media")
def process_new_media(asset_id: str) -> dict[str, object]:
    db = SessionLocal()
    asset = None
    try:
        asset = db.get(MediaAsset, UUID(asset_id))
        if asset is None:
            raise MediaProcessingError(f"Media asset {asset_id} not found")

        asset.status = MediaStatus.PROCESSING
        db.commit()
        publish_project_status(str(asset.project_id), progress=5, stage="media_downloading", message="正在下載原始影片")

        with tempfile.TemporaryDirectory(prefix=f"media-{asset_id}-") as temp_dir:
            workdir = Path(temp_dir)
            original = workdir / "original"
            thumbnail = workdir / "thumbnail.jpg"
            audio = workdir / "audio.wav"
            proxy = workdir / "proxy.mp4"

            download_object(asset.storage_key, str(original))
            publish_project_status(str(asset.project_id), progress=20, stage="media_probing", message="正在讀取影片格式")
            metadata = _probe(original)
            timestamp = max(0.0, metadata["duration"] * 0.10)

            publish_project_status(str(asset.project_id), progress=35, stage="media_thumbnail", message="正在生成預覽縮圖")
            _run([
                "ffmpeg", "-y", "-ss", str(timestamp), "-i", str(original),
                "-frames:v", "1", "-q:v", "2", str(thumbnail),
            ])
            publish_project_status(str(asset.project_id), progress=50, stage="media_audio", message="正在抽取音訊")
            _run([
                "ffmpeg", "-y", "-i", str(original), "-vn", "-ac", "1",
                "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
            ])
            publish_project_status(str(asset.project_id), progress=70, stage="media_proxy", message="正在生成預覽代理檔")
            _run([
                "ffmpeg", "-y", "-i", str(original),
                "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", "1500k", "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart", str(proxy),
            ])

            base = f"projects/{asset.project_id}/derived/{asset.id}"
            thumbnail_key = f"{base}/thumbnail.jpg"
            audio_key = f"{base}/audio-16khz.wav"
            proxy_key = f"{base}/proxy-720p.mp4"
            publish_project_status(str(asset.project_id), progress=90, stage="media_uploading", message="正在上傳處理結果")
            upload_object(thumbnail_key, str(thumbnail), "image/jpeg")
            upload_object(audio_key, str(audio), "audio/wav")
            upload_object(proxy_key, str(proxy), "video/mp4")

            asset.duration_seconds = metadata["duration"]
            asset.width = metadata["width"]
            asset.height = metadata["height"]
            asset.fps = metadata["fps"]
            asset.video_codec = metadata["video_codec"]
            asset.metadata_json = metadata
            asset.thumbnail_key = thumbnail_key
            asset.audio_key = audio_key
            asset.proxy_key = proxy_key
            asset.status = MediaStatus.READY
            db.commit()
            celery_app.send_task("media.generate_media_embeddings", args=[str(asset.id)])
            celery_app.send_task("media.analyze_optics", args=[str(asset.id)])
            publish_project_status(str(asset.project_id), progress=100, stage="media_ready", status="completed", message="媒體預處理完成")
            return {"asset_id": asset_id, "status": asset.status.value, **metadata}
    except Exception as exc:
        db.rollback()
        if asset is not None:
            current = db.get(MediaAsset, asset.id)
            if current is not None:
                current.status = MediaStatus.FAILED
                current.metadata_json = {**(current.metadata_json or {}), "processing_error": str(exc)}
                db.commit()
            publish_project_status(str(asset.project_id), progress=0, stage="media_failed", status="failed", message=str(exc))
        raise
    finally:
        db.close()
