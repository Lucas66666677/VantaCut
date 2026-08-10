"""Low-latency, server-side live switching for WebRTC and MediaMTX sources.

FastAPI owns signalling and control only.  MediaMTX terminates RTMP / WHIP / WHEP
at the edge, while this process subscribes to selected sources and publishes one
program feed to a platform RTMP ingest endpoint through aiortc's FFmpeg recorder.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import tempfile
import time
import wave
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRecorder, MediaRelay
from av import AudioFrame, AudioResampler, VideoFrame

from app.core.config import settings

Layout = Literal["single", "split", "wide"]


class LiveDirectorError(RuntimeError):
    pass


class SileroVAD:
    """Stateful 16 kHz Silero adapter with an explicit energy fallback.

    Mount a TorchScript Silero model at ``SILERO_VAD_TORCHSCRIPT_PATH`` in
    production.  The fallback is deliberately visible in source status so it is
    never mistaken for a neural VAD in observability or benchmarks.
    """

    def __init__(self, model_path: str | None, threshold: float) -> None:
        self.threshold = threshold
        self._model: Any | None = None
        self._buffer = np.empty(0, dtype=np.float32)
        self._last_probability = 0.0
        self.kind = "energy_fallback"
        self._resampler = AudioResampler(format="s16", layout="mono", rate=16_000)
        if model_path:
            try:
                import torch

                self._torch = torch
                self._model = torch.jit.load(model_path, map_location="cpu")
                self._model.eval()
                self.kind = "silero_torchscript"
            except Exception as exc:  # Keep a live show running if the model mount is bad.
                self.load_error = str(exc)
            else:
                self.load_error = None
        else:
            self.load_error = "SILERO_VAD_TORCHSCRIPT_PATH is not configured"

    def score(self, frame: AudioFrame) -> float:
        converted = self._resampler.resample(frame)
        converted_frames = converted if isinstance(converted, list) else [converted]
        samples = [
            item.to_ndarray().astype(np.float32, copy=False).reshape(-1) / 32768.0
            for item in converted_frames
            if item is not None
        ]
        if not samples:
            return self._last_probability
        audio = np.concatenate(samples)
        if self._model is None:
            # Calibrated enough as a degraded-mode guard only; not a substitute for Silero.
            rms = float(np.sqrt(np.mean(np.square(audio)) + 1e-12))
            self._last_probability = min(1.0, rms / 0.055)
            return self._last_probability

        self._buffer = np.concatenate((self._buffer, audio))[-4096:]
        # Silero streaming models accept 512 samples at 16 kHz (32 ms).
        if len(self._buffer) < 512:
            return self._last_probability
        chunk, self._buffer = self._buffer[:512], self._buffer[512:]
        try:
            with self._torch.no_grad():
                result = self._model(self._torch.from_numpy(chunk), 16_000)
            self._last_probability = float(result.item())
        except Exception:
            # A model ABI mismatch must not terminate the publisher mid-show.
            self._model = None
            self.kind = "energy_fallback"
            self._last_probability = min(1.0, float(np.sqrt(np.mean(chunk * chunk))) / 0.055)
        return self._last_probability


@dataclass
class CaptionOverlay:
    text: str
    emotion: str
    animation_preset: str
    expires_at: float


@dataclass
class SourceState:
    camera_id: str
    is_wide_camera: bool = False
    video_track: MediaStreamTrack | None = None
    audio_track: MediaStreamTrack | None = None
    caption_audio_track: MediaStreamTrack | None = None
    program_audio_track: MediaStreamTrack | None = None
    player: MediaPlayer | None = None
    latest_frame: VideoFrame | None = None
    activity_score: float = 0.0
    vad_kind: str = "pending"
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


class RollingCaptionTranscriber:
    """Non-blocking interim caption worker over short, VAD-gated PCM windows.

    It deliberately runs outside the VAD/video loops: remote ASR latency only
    delays captions, never camera switching or program-frame delivery.
    """

    def __init__(self, director: "LiveDirector", camera_id: str) -> None:
        self.director, self.camera_id = director, camera_id
        self._resampler = AudioResampler(format="s16", layout="mono", rate=16_000)
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._inflight = False
        self._last_text = ""

    async def ingest(self, frame: AudioFrame, speech_score: float) -> None:
        converted = self._resampler.resample(frame)
        frames = converted if isinstance(converted, list) else [converted]
        for item in frames:
            if item is None:
                continue
            pcm = item.to_ndarray().astype(np.int16, copy=False).reshape(-1)
            self._chunks.append(pcm)
            self._samples += len(pcm)
        window_samples = int(16_000 * settings.live_caption_window_seconds)
        if self._samples < window_samples or self._inflight or speech_score < settings.live_vad_threshold:
            # Cap memory during long pauses or an unavailable ASR service.
            while self._samples > window_samples * 2 and self._chunks:
                self._samples -= len(self._chunks.pop(0))
            return
        payload = np.concatenate(self._chunks)
        self._chunks.clear()
        self._samples = 0
        self._inflight = True
        try:
            result = await asyncio.to_thread(self._transcribe, payload)
            text = result[0]
            if text and text != self._last_text and self.director.active_camera_id == self.camera_id:
                self._last_text = text
                self.director.set_caption(*result)
        finally:
            self._inflight = False

    @staticmethod
    def _transcribe(payload: np.ndarray) -> tuple[str, str, str, float]:
        from app.ai.providers.factory import get_asr_provider
        from app.services.kinetic_subtitles import annotate_transcript_kinetics

        with tempfile.TemporaryDirectory(prefix="live-caption-") as temp_dir:
            audio_path = Path(temp_dir) / "window.wav"
            with wave.open(str(audio_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(payload.astype(np.int16, copy=False).tobytes())
            transcript = get_asr_provider().transcribe(str(audio_path), word_timestamps=True)
        annotate_transcript_kinetics(transcript)
        text = transcript.text.strip()
        words = [word for segment in transcript.segments for word in segment.words]
        emphasis = max(words, key=lambda word: word.emotion_intensity, default=None)
        return (
            text,
            emphasis.emotion if emphasis else "neutral",
            emphasis.animation_preset if emphasis else "pop",
            settings.live_caption_ttl_seconds,
        )


class ProgramVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, director: "LiveDirector") -> None:
        super().__init__()
        self.director = director
        self._pts = 0
        self._time_base = Fraction(1, director.fps)
        self._next_frame_at = time.monotonic()

    async def recv(self) -> VideoFrame:
        delay = self._next_frame_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._next_frame_at = max(self._next_frame_at + 1 / self.director.fps, time.monotonic())
        image = self.director.compose_program_frame()
        output = VideoFrame.from_ndarray(image, format="bgr24")
        output.pts = self._pts
        output.time_base = self._time_base
        self._pts += 1
        return output


class ProgramAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, director: "LiveDirector") -> None:
        super().__init__()
        self.director = director
        self._pts = 0
        self._time_base = Fraction(1, 48_000)

    def _silence(self) -> AudioFrame:
        frame = AudioFrame(format="s16", layout="mono", samples=960)
        frame.sample_rate = 48_000
        frame.planes[0].update(np.zeros(960, dtype=np.int16).tobytes())
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += 960
        return frame

    async def recv(self) -> AudioFrame:
        track = self.director.current_program_audio_track()
        if track is None:
            await asyncio.sleep(0.02)
            return self._silence()
        try:
            return await asyncio.wait_for(track.recv(), timeout=0.12)
        except Exception:
            return self._silence()
class LiveDirector:
    """Owns a single program bus; run one instance per live session per process."""

    def __init__(
        self,
        *,
        session_id: str,
        project_id: str,
        width: int,
        height: int,
        fps: int,
        output_rtmp_url: str | None,
        wide_camera_id: str | None,
    ) -> None:
        self.session_id = session_id
        self.project_id = project_id
        self.width, self.height, self.fps = width, height, fps
        self.output_rtmp_url = output_rtmp_url
        self.wide_camera_id = wide_camera_id
        self.relay = MediaRelay()
        self.sources: dict[str, SourceState] = {}
        self.peers: set[RTCPeerConnection] = set()
        self.layout_override: Literal["auto", "single", "split", "wide"] = "auto"
        self.camera_override: str | None = None
        self.layout: Layout = "wide" if wide_camera_id else "single"
        self.active_camera_id: str | None = wide_camera_id
        self.caption: CaptionOverlay | None = None
        self.status: Literal["created", "live", "stopped", "failed"] = "created"
        self._last_switch_at = 0.0
        self._lock = asyncio.Lock()
        self._recorder: MediaRecorder | None = None
        self._video_program = ProgramVideoTrack(self)
        self._audio_program = ProgramAudioTrack(self)

    async def start(self) -> None:
        if self.status == "live":
            return
        if self.output_rtmp_url:
            self._recorder = MediaRecorder(
                self.output_rtmp_url,
                format="flv",
                options={
                    "vcodec": settings.live_video_codec,
                    "acodec": "aac",
                    "preset": "veryfast",
                    "tune": "zerolatency",
                    "g": str(self.fps),
                    "flvflags": "no_duration_filesize",
                },
            )
            self._recorder.addTrack(self._video_program)
            self._recorder.addTrack(self._audio_program)
            await self._recorder.start()
        self.status = "live"

    async def stop(self) -> None:
        self.status = "stopped"
        for source in self.sources.values():
            for task in source.tasks:
                task.cancel()
            for task in source.tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if source.player:
                source.player.stop()
        for peer in list(self.peers):
            with contextlib.suppress(Exception):
                await peer.close()
        self.peers.clear()
        if self._recorder:
            with contextlib.suppress(Exception):
                await self._recorder.stop()
        self._video_program.stop()
        self._audio_program.stop()

    async def add_websocket_offer(self, camera_id: str, sdp: str, is_wide_camera: bool) -> RTCSessionDescription:
        peer = RTCPeerConnection()
        self.peers.add(peer)
        tracks: dict[str, MediaStreamTrack] = {}

        @peer.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            tracks[track.kind] = track
            if track.kind == "video":
                asyncio.create_task(self.add_source(camera_id, video=track, audio=tracks.get("audio"), is_wide_camera=is_wide_camera))
            elif track.kind == "audio" and "video" in tracks:
                asyncio.create_task(self.add_source(camera_id, video=tracks["video"], audio=track, is_wide_camera=is_wide_camera))

        @peer.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if peer.connectionState in {"failed", "closed", "disconnected"}:
                self.peers.discard(peer)

        await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        answer = await peer.createAnswer()
        await peer.setLocalDescription(answer)
        if peer.localDescription is None:
            raise LiveDirectorError("WebRTC answer creation failed")
        return peer.localDescription

    async def attach_gateway_source(self, camera_id: str, rtsp_url: str, is_wide_camera: bool) -> None:
        """Read a MediaMTX path internally; external callers cannot inject a URL."""
        player = MediaPlayer(
            rtsp_url,
            format="rtsp",
            options={"rtsp_transport": "tcp", "fflags": "nobuffer", "flags": "low_delay"},
        )
        await self.add_source(camera_id, video=player.video, audio=player.audio, is_wide_camera=is_wide_camera, player=player)

    async def add_source(
        self,
        camera_id: str,
        *,
        video: MediaStreamTrack | None,
        audio: MediaStreamTrack | None,
        is_wide_camera: bool,
        player: MediaPlayer | None = None,
    ) -> None:
        if video is None:
            raise LiveDirectorError("A live camera needs a video track")
        async with self._lock:
            old = self.sources.pop(camera_id, None)
            if old:
                for task in old.tasks:
                    task.cancel()
            state = SourceState(
                camera_id=camera_id,
                is_wide_camera=is_wide_camera,
                video_track=self.relay.subscribe(video, buffered=False),
                audio_track=self.relay.subscribe(audio, buffered=False) if audio else None,
                caption_audio_track=self.relay.subscribe(audio, buffered=False) if audio and settings.live_captions_enabled else None,
                program_audio_track=self.relay.subscribe(audio, buffered=True) if audio else None,
                player=player,
            )
            if is_wide_camera:
                self.wide_camera_id = camera_id
            self.sources[camera_id] = state
            state.tasks.append(asyncio.create_task(self._consume_video(state), name=f"live-video-{camera_id}"))
            if state.audio_track:
                state.tasks.append(asyncio.create_task(self._consume_audio(state), name=f"live-vad-{camera_id}"))
            if state.caption_audio_track:
                state.tasks.append(asyncio.create_task(self._consume_captions(state), name=f"live-caption-{camera_id}"))

    async def _consume_video(self, state: SourceState) -> None:
        assert state.video_track is not None
        try:
            while self.status != "stopped":
                state.latest_frame = await state.video_track.recv()
        except Exception:
            # The source state remains visible as disconnected; a later publisher can replace it.
            state.latest_frame = None

    async def _consume_audio(self, state: SourceState) -> None:
        assert state.audio_track is not None
        vad = SileroVAD(settings.silero_vad_torchscript_path, settings.live_vad_threshold)
        state.vad_kind = vad.kind
        try:
            while self.status != "stopped":
                probability = vad.score(await state.audio_track.recv())
                # Short EMA provides 60-150ms response without flickering on individual frames.
                state.activity_score = (state.activity_score * 0.58) + (probability * 0.42)
                state.vad_kind = vad.kind
        except Exception:
            state.activity_score = 0.0

    async def _consume_captions(self, state: SourceState) -> None:
        assert state.caption_audio_track is not None
        transcriber = RollingCaptionTranscriber(self, state.camera_id)
        try:
            while self.status != "stopped":
                frame = await state.caption_audio_track.recv()
                await transcriber.ingest(frame, state.activity_score)
        except Exception:
            # Caption failures are intentionally non-fatal to the live program.
            return

    def choose_program(self) -> tuple[Layout, str | None]:
        available = [source for source in self.sources.values() if source.latest_frame is not None]
        active = [source for source in available if source.activity_score >= settings.live_vad_threshold]
        now = time.monotonic()
        if self.layout_override == "single" and self.camera_override in self.sources:
            return "single", self.camera_override
        if self.layout_override == "split":
            return "split", None
        if self.layout_override == "wide" and self.wide_camera_id in self.sources:
            return "wide", self.wide_camera_id
        if len(active) >= 2:
            return "split", None
        if len(active) == 1:
            desired = active[0].camera_id
            if desired != self.active_camera_id and now - self._last_switch_at >= settings.live_min_switch_seconds:
                self._last_switch_at = now
                return "single", desired
            return self.layout, self.active_camera_id
        if self.wide_camera_id and self.wide_camera_id in self.sources:
            return "wide", self.wide_camera_id
        return "single", available[0].camera_id if available else None

    def current_program_audio_track(self) -> MediaStreamTrack | None:
        layout, camera_id = self.choose_program()
        # A real N-way audio mixer can replace this selector. Until then, split
        # view keeps the clearest active speaker instead of accidentally emitting
        # silence whenever two people talk at once.
        if layout == "split":
            candidates = [source for source in self.sources.values() if source.program_audio_track]
            camera_id = max(candidates, key=lambda source: source.activity_score).camera_id if candidates else None
        return self.sources.get(camera_id).program_audio_track if camera_id and camera_id in self.sources else None

    @staticmethod
    def _resize(frame: VideoFrame, width: int, height: int) -> np.ndarray:
        image = frame.to_ndarray(format="bgr24")
        return cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)

    def _draw_caption(self, image: np.ndarray) -> None:
        if not self.caption or self.caption.expires_at <= time.monotonic():
            self.caption = None
            return
        elapsed = max(0.0, 1.0 - (self.caption.expires_at - time.monotonic()) / 0.22)
        scale = 1.0 + (0.14 * math.sin(min(elapsed, 1.0) * math.pi)) if self.caption.animation_preset in {"pop", "spring"} else 1.0
        font_scale = max(0.65, min(2.1, self.width / 900 * scale))
        colour = {"surprise": (40, 220, 255), "anger": (40, 60, 255), "joy": (70, 240, 100), "emphasis": (255, 220, 40)}.get(self.caption.emotion, (255, 255, 255))
        text = self.caption.text[:80]
        (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, font_scale, 2)
        x, y = max(24, (self.width - text_width) // 2), self.height - max(52, text_height + 44)
        cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_DUPLEX, font_scale, (0, 0, 0), 7, cv2.LINE_AA)
        cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_DUPLEX, font_scale, colour, 2, cv2.LINE_AA)

    def compose_program_frame(self) -> np.ndarray:
        self.layout, self.active_camera_id = self.choose_program()
        canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        available = [item for item in self.sources.values() if item.latest_frame is not None]
        if self.layout == "split" and len(available) >= 2:
            left, right = available[:2]
            canvas[:, : self.width // 2] = self._resize(left.latest_frame, self.width // 2, self.height)
            canvas[:, self.width // 2 :] = self._resize(right.latest_frame, self.width - self.width // 2, self.height)
            cv2.line(canvas, (self.width // 2, 0), (self.width // 2, self.height), (255, 255, 255), 3)
        elif self.active_camera_id in self.sources and self.sources[self.active_camera_id].latest_frame is not None:
            canvas = self._resize(self.sources[self.active_camera_id].latest_frame, self.width, self.height)
        self._draw_caption(canvas)
        return canvas

    def set_caption(self, text: str, emotion: str, animation_preset: str, ttl_seconds: float) -> None:
        self.caption = CaptionOverlay(text, emotion, animation_preset, time.monotonic() + ttl_seconds)

    def set_override(self, layout: Literal["auto", "single", "split", "wide"], camera_id: str | None) -> None:
        self.layout_override, self.camera_override = layout, camera_id

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "layout": self.layout,
            "active_camera_id": self.active_camera_id,
            "caption": self.caption.__dict__ if self.caption else None,
            "sources": [
                {
                    "camera_id": source.camera_id,
                    "is_wide_camera": source.is_wide_camera,
                    "has_video": source.latest_frame is not None,
                    "activity_score": round(source.activity_score, 3),
                    "vad": source.vad_kind,
                }
                for source in self.sources.values()
            ],
        }


class LiveDirectorRegistry:
    """Process-local media registry; Redis is reserved for control/state fan-out.

    WebRTC tracks are live sockets and cannot be shared between Uvicorn workers.
    Run the director deployment with one worker per pod and use session affinity.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LiveDirector] = {}

    def create(self, **kwargs: Any) -> LiveDirector:
        director = LiveDirector(**kwargs)
        self._sessions[director.session_id] = director
        return director

    def get(self, session_id: str) -> LiveDirector:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise LiveDirectorError("Live session not found on this director node") from exc

    async def stop(self, session_id: str) -> None:
        director = self.get(session_id)
        await director.stop()
        self._sessions.pop(session_id, None)


live_directors = LiveDirectorRegistry()
