"""Exercise ingestion with real FFmpeg files and isolated storage/DB boundaries."""

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.media_preprocessing import MediaProcessingError, extract_audio, probe, run  # noqa: E402


class Status(Enum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


def load_worker(asset, download, upload):
    db = Mock()
    db.get.return_value = asset
    celery = SimpleNamespace(task=lambda **kwargs: lambda function: function, send_task=Mock())
    definitions = {
        "app.db.session": {"SessionLocal": lambda: db},
        "app.core.progress": {"publish_project_status": Mock()},
        "app.models.entities": {"MediaAsset": object, "MediaStatus": Status},
        "app.services.storage": {"download_object": download, "upload_object": upload},
        "app.worker": {"celery_app": celery},
    }
    modules = {}
    for name, values in definitions.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        modules[name] = module
    spec = importlib.util.spec_from_file_location("ingestion_worker_test", ROOT / "backend/app/tasks/media_tasks.py")
    worker = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(worker)
    return worker, db


class MediaIngestionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shutil.which("ffmpeg"), "Install FFmpeg to run the media gate")
        self.assertIsNotNone(shutil.which("ffprobe"))
        self.directory = tempfile.TemporaryDirectory(prefix="vantacut-media-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def source(self, with_audio):
        source = self.root / "source.mp4"
        command = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=0.5"]
        if with_audio:
            # Put audio first: metadata must select by codec_type, not array position.
            command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-map", "1:a:0", "-map", "0:v:0", "-c:a", "aac"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)]
        run(command)
        return source

    def ingest(self, source):
        asset = SimpleNamespace(id=uuid4(), project_id=uuid4(), storage_key="original", metadata_json={})
        uploaded = {}

        def upload(key, path, mime):
            target = self.root / Path(key).name
            shutil.copyfile(path, target)
            uploaded[mime] = target

        worker, db = load_worker(asset, lambda key, path: shutil.copyfile(source, path), upload)
        result = worker.process_new_media(str(asset.id))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(asset.status, Status.READY)
        self.assertGreater(uploaded["image/jpeg"].stat().st_size, 0)
        proxy = probe(uploaded["video/mp4"])
        self.assertEqual(proxy["height"], 720)
        self.assertGreater(proxy["duration"], 0)
        db.close.assert_called_once()
        return asset, uploaded, proxy

    def test_silent_video_reaches_ready_without_inventing_an_audio_artifact(self):
        source = self.source(False)
        # This is the exact old extraction step that rejected valid silent video.
        with self.assertRaises(MediaProcessingError):
            run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(self.root / "old.wav")])
        asset, uploaded, proxy = self.ingest(source)
        self.assertIsNone(asset.audio_key)
        self.assertFalse(asset.metadata_json["has_audio"])
        self.assertNotIn("audio/wav", uploaded)
        self.assertFalse(proxy["has_audio"])

    def test_video_with_audio_keeps_mono_16khz_audio_and_correct_video_metadata(self):
        asset, uploaded, proxy = self.ingest(self.source(True))
        self.assertEqual((asset.width, asset.height, asset.fps), (160, 90, 24))
        self.assertTrue(asset.metadata_json["has_audio"])
        self.assertTrue(proxy["has_audio"])
        self.assertTrue(asset.audio_key.endswith("audio-16khz.wav"))
        audio = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(uploaded["audio/wav"])]).stdout)["streams"][0]
        self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("pcm_s16le", "16000", 1))

    def test_decode_failure_is_not_silently_accepted_as_no_audio(self):
        with self.assertRaises(MediaProcessingError):
            extract_audio(self.root / "missing.mp4", self.root / "audio.wav", has_audio=True)

    def test_invalid_media_remains_failed(self):
        invalid = self.root / "invalid.mp4"
        invalid.write_bytes(b"not a video")
        asset = SimpleNamespace(id=uuid4(), project_id=uuid4(), storage_key="original", metadata_json={})
        upload = Mock()
        worker, db = load_worker(asset, lambda key, path: shutil.copyfile(invalid, path), upload)
        with self.assertRaises(MediaProcessingError):
            worker.process_new_media(str(asset.id))
        self.assertEqual(asset.status, Status.FAILED)
        upload.assert_not_called()
        db.rollback.assert_called_once()
        db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
