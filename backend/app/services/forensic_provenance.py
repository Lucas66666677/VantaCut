"""Forensic watermarking and C2PA provenance for final delivery assets.

The DCT layer is a robust ownership signal, not DRM: transcoding, cropping and
screen capture can reduce confidence, and a determined attacker can attempt
removal. C2PA is an independently verifiable, signed provenance record whose
binding becomes invalid whenever the final container is materially modified.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import struct
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import cv2
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

ProgressCallback = Callable[[int], None]
_MAGIC = b"AVDG"
_AAD = b"ai-video-editor/forensic-watermark/v1"
_PLAINTEXT = struct.Struct(">B16s16s16sI")  # version, user UUID, project hash, render UUID, issued epoch


class ForensicError(RuntimeError):
    pass


@dataclass(frozen=True)
class WatermarkClaims:
    user_id: str
    project_hash: str
    render_job_id: str
    issued_at: int
    version: int = 1


@dataclass(frozen=True)
class WatermarkEmbeddingReport:
    frames_seen: int
    frames_marked: int
    frame_stride: int
    bits_per_packet: int
    copies_per_frame: int
    strength: float


def _key() -> bytes:
    raw = settings.watermark_encryption_key
    if not raw:
        raise ForensicError("WATERMARK_ENCRYPTION_KEY is required when forensic watermarking is enabled")
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except Exception as exc:
        raise ForensicError("WATERMARK_ENCRYPTION_KEY must be URL-safe base64") from exc
    if len(key) != 32:
        raise ForensicError("WATERMARK_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def make_watermark_claims(*, user_id: UUID, project_id: UUID, render_job_id: UUID) -> WatermarkClaims:
    return WatermarkClaims(
        user_id=str(user_id),
        project_hash=hashlib.sha256(str(project_id).encode("ascii")).hexdigest(),
        render_job_id=str(render_job_id),
        issued_at=int(time.time()),
    )


def _encrypt_claims(claims: WatermarkClaims) -> bytes:
    project_digest = bytes.fromhex(claims.project_hash)[:16]
    plaintext = _PLAINTEXT.pack(
        claims.version,
        UUID(claims.user_id).bytes,
        project_digest,
        UUID(claims.render_job_id).bytes,
        claims.issued_at,
    )
    nonce = os.urandom(12)
    return _MAGIC + nonce + AESGCM(_key()).encrypt(nonce, plaintext, _AAD)


def _decrypt_claims(packet: bytes) -> WatermarkClaims:
    if not packet.startswith(_MAGIC) or len(packet) < len(_MAGIC) + 12 + 16:
        raise ForensicError("Watermark packet header is invalid")
    nonce, ciphertext = packet[4:16], packet[16:]
    try:
        raw = AESGCM(_key()).decrypt(nonce, ciphertext, _AAD)
        version, user_bytes, project_digest, render_bytes, issued_at = _PLAINTEXT.unpack(raw)
    except Exception as exc:
        raise ForensicError("Watermark authentication failed") from exc
    return WatermarkClaims(
        version=version,
        user_id=str(UUID(bytes=user_bytes)),
        project_hash=project_digest.hex(),
        render_job_id=str(UUID(bytes=render_bytes)),
        issued_at=issued_at,
    )


def _to_bits(packet: bytes) -> list[int]:
    return [int(bit) for byte in packet for bit in f"{byte:08b}"]


def _from_bits(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise ForensicError("Watermark bit stream is not byte-aligned")
    return bytes(int("".join(str(bit) for bit in bits[index:index + 8]), 2) for index in range(0, len(bits), 8))


def _block_coordinates(width: int, height: int, bit_count: int, copies: int) -> list[tuple[int, int]]:
    blocks = [(x, y) for y in range(0, height - 7, 8) for x in range(0, width - 7, 8)]
    required = bit_count * copies
    if len(blocks) < required:
        raise ForensicError(f"Video frame is too small for robust watermark payload ({len(blocks)} blocks < {required})")
    # The positions are key-derived and stable across frames: sampled screen
    # recordings can still be decoded even when original frame numbers are lost.
    seed = int.from_bytes(hmac.new(_key(), b"aivideo-dct-chroma-positions-v1", hashlib.sha256).digest()[:8], "big")
    return random.Random(seed).sample(blocks, required)


def _force_difference(first: float, second: float, bit: int, strength: float) -> tuple[float, float]:
    first_sign, second_sign = (-1.0 if first < 0 else 1.0), (-1.0 if second < 0 else 1.0)
    first_abs, second_abs = abs(first), abs(second)
    difference = first_abs - second_abs
    wanted = difference >= strength if bit else difference <= -strength
    if wanted:
        return first, second
    midpoint = (first_abs + second_abs) / 2.0
    high, low = midpoint + strength / 2.0, max(0.0, midpoint - strength / 2.0)
    return (first_sign * high, second_sign * low) if bit else (first_sign * low, second_sign * high)


def _embed_frame(frame: np.ndarray, bits: list[int], *, copies: int, strength: float) -> np.ndarray:
    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    chroma = ycrcb[:, :, 2].astype(np.float32)
    for index, (x, y) in enumerate(_block_coordinates(chroma.shape[1], chroma.shape[0], len(bits), copies)):
        bit = bits[index % len(bits)]
        coefficients = cv2.dct(chroma[y:y + 8, x:x + 8])
        coefficients[2, 3], coefficients[3, 2] = _force_difference(coefficients[2, 3], coefficients[3, 2], bit, strength)
        chroma[y:y + 8, x:x + 8] = cv2.idct(coefficients)
    ycrcb[:, :, 2] = np.clip(chroma, 0, 255).astype(np.uint8)
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)


def _decode_frame(frame: np.ndarray, packet_size: int, *, copies: int) -> tuple[bytes, float]:
    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    chroma = ycrcb[:, :, 2].astype(np.float32)
    bits: list[int] = []
    margins: list[float] = []
    packet_bits = packet_size * 8
    coordinates = _block_coordinates(chroma.shape[1], chroma.shape[0], packet_bits, copies)
    for bit_index in range(packet_bits):
        votes: list[int] = []
        for copy_index in range(copies):
            x, y = coordinates[copy_index * packet_bits + bit_index]
            coefficients = cv2.dct(chroma[y:y + 8, x:x + 8])
            margin = float(abs(coefficients[2, 3]) - abs(coefficients[3, 2]))
            votes.append(1 if margin >= 0 else 0)
            margins.append(abs(margin))
        bits.append(1 if sum(votes) >= (copies + 1) / 2 else 0)
    return _from_bits(bits), float(np.mean(margins)) if margins else 0.0


def embed_forensic_watermark(
    input_path: str | Path,
    output_path: str | Path,
    claims: WatermarkClaims,
    *,
    progress_callback: ProgressCallback | None = None,
) -> WatermarkEmbeddingReport:
    """Write redundant DCT bits into Cb chroma and retain original audio losslessly.

    FFV1 is deliberately used for the interim video so the following final codec
    receives watermark pixels only once. The delivery encoder is still expected
    to attenuate the signal, which is why repeated copies are embedded.
    """
    packet = _encrypt_claims(claims)
    bits = _to_bits(packet)
    source, destination = Path(input_path), Path(output_path)
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ForensicError("Cannot decode input video for forensic watermarking")
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 1)
    blocks = (width // 8) * (height // 8)
    copies = min(settings.watermark_max_copies, blocks // len(bits))
    if copies < settings.watermark_min_copies:
        capture.release()
        raise ForensicError("Output resolution cannot hold the required watermark redundancy")
    command = [
        "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-video_size", f"{width}x{height}",
        "-framerate", f"{fps:.6f}", "-i", "pipe:0", "-i", str(source),
        "-map", "0:v:0", "-map", "1:a?", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv444p",
        "-c:a", "copy", "-shortest", str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    frames_seen = frames_marked = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frames_seen % settings.watermark_frame_stride == 0:
                frame = _embed_frame(frame, bits, copies=copies, strength=settings.watermark_dct_strength)
                frames_marked += 1
            assert process.stdin is not None
            process.stdin.write(frame.tobytes())
            frames_seen += 1
            if progress_callback and frames_seen % max(1, int(fps)) == 0:
                progress_callback(min(100, int(frames_seen / frame_count * 100)))
    except BrokenPipeError as exc:
        raise ForensicError("FFmpeg watermark encoder stopped unexpectedly") from exc
    finally:
        capture.release()
        if process.stdin:
            process.stdin.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.wait() != 0 or not destination.exists():
        raise ForensicError(f"Forensic watermark mux failed: {stderr[-1500:]}")
    return WatermarkEmbeddingReport(frames_seen, frames_marked, settings.watermark_frame_stride, len(bits), copies, settings.watermark_dct_strength)


def extract_forensic_watermark(video_path: str | Path, *, sample_limit: int = 36) -> dict[str, Any]:
    """Attempt recovery from the final asset or a screen-recorded derivative."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ForensicError("Cannot decode video for forensic extraction")
    packet_size = 4 + 12 + _PLAINTEXT.size + 16  # magic + nonce + AES-GCM ciphertext/tag
    frame_count = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 1)
    stride = max(1, frame_count // sample_limit)
    attempts = 0
    best_margin = 0.0
    try:
        for frame_number in range(0, frame_count, stride):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = capture.read()
            if not ok:
                continue
            attempts += 1
            blocks = (frame.shape[1] // 8) * (frame.shape[0] // 8)
            copies = min(settings.watermark_max_copies, blocks // (packet_size * 8))
            if copies < settings.watermark_min_copies:
                continue
            packet, margin = _decode_frame(frame, packet_size, copies=copies)
            best_margin = max(best_margin, margin)
            if not packet.startswith(_MAGIC):
                continue
            try:
                claims = _decrypt_claims(packet)
            except ForensicError:
                continue
            return {"detected": True, "confidence": round(min(1.0, margin / max(1.0, settings.watermark_dct_strength)), 3), "sampled_frames": attempts, "claims": asdict(claims)}
    finally:
        capture.release()
    return {"detected": False, "confidence": round(min(1.0, best_margin / max(1.0, settings.watermark_dct_strength)), 3), "sampled_frames": attempts}


def build_c2pa_manifest(*, title: str, provenance: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    actions = [
        {"action": "c2pa.created", "when": now, "softwareAgent": settings.c2pa_claim_generator},
        {"action": "c2pa.edited", "when": now, "softwareAgent": settings.c2pa_claim_generator},
    ]
    if provenance.get("color_lut") or provenance.get("film_optics"):
        actions.append({"action": "c2pa.color_adjustments", "when": now, "softwareAgent": settings.c2pa_claim_generator})
    return {
        "claim_generator": settings.c2pa_claim_generator,
        "title": title,
        "assertions": [
            {"label": "c2pa.actions", "data": {"actions": actions}},
            # Custom assertions are signed by C2PA together with standard actions.
            # Avoid prompts, user email, or source URLs: provenance must be useful
            # to verifiers without leaking private project content.
            {"label": "com.aivideo.provenance.v1", "data": provenance},
        ],
    }


def sign_c2pa_asset(input_path: str | Path, output_path: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Embed and verify a C2PA manifest through a separately provisioned signer.

    Private keys never enter Python or a manifest; ``C2PA_SIGNER_PATH`` must point
    to an HSM/KMS-aware signer implementing c2patool's signer subprocess protocol.
    """
    if not settings.c2patool_command or not settings.c2pa_signer_path:
        raise ForensicError("C2PATOOL_COMMAND and C2PA_SIGNER_PATH are required when C2PA is enabled")
    source, destination = Path(input_path), Path(output_path)
    with tempfile.TemporaryDirectory(prefix="c2pa-") as temp_dir:
        definition = Path(temp_dir) / "manifest.json"
        definition.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        command = [
            settings.c2patool_command, str(source), "--manifest", str(definition), "--output", str(destination),
            "--signer-path", settings.c2pa_signer_path, "--force",
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=settings.c2pa_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise ForensicError("C2PA signing timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise ForensicError((exc.stderr or exc.stdout or "C2PA signing failed")[-2000:]) from exc
    if not destination.exists():
        raise ForensicError("C2PA tool completed without creating a signed asset")
    verification = subprocess.run([settings.c2patool_command, str(destination), "--info"], capture_output=True, text=True, timeout=120)
    if verification.returncode != 0:
        raise ForensicError((verification.stderr or verification.stdout or "C2PA post-sign verification failed")[-2000:])
    return {"manifest": manifest, "verification_report": (verification.stdout or verification.stderr)[-4000:]}


def verify_c2pa_asset(video_path: str | Path) -> dict[str, Any]:
    if not settings.c2patool_command:
        return {"available": False, "reason": "C2PATOOL_COMMAND is not configured"}
    result = subprocess.run([settings.c2patool_command, str(video_path), "--info"], capture_output=True, text=True, timeout=120)
    return {"available": True, "valid": result.returncode == 0, "report": (result.stdout or result.stderr)[-4000:]}
