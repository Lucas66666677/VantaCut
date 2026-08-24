#!/usr/bin/env python3
"""Fail CI when a rendered MP4 regresses visually, changes color metadata, or loses A/V sync."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class QualityGateError(RuntimeError):
    pass


def probe(path: Path) -> dict[str, Any]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def video_stream(metadata: dict[str, Any]) -> dict[str, Any]:
    stream = next((item for item in metadata["streams"] if item.get("codec_type") == "video"), None)
    if not stream: raise QualityGateError("Video stream is missing")
    return stream


def audio_stream(metadata: dict[str, Any]) -> dict[str, Any]:
    stream = next((item for item in metadata["streams"] if item.get("codec_type") == "audio"), None)
    if not stream: raise QualityGateError("Audio stream is missing")
    return stream


def duration(metadata: dict[str, Any]) -> float:
    return float(metadata.get("format", {}).get("duration") or 0)


def frame_at(path: Path, seconds: float) -> np.ndarray:
    stream = video_stream(probe(path))
    rate = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    numerator, denominator = (float(value) for value in rate.split("/", 1))
    fps = numerator / denominator if denominator else 0
    if fps <= 0:
        raise QualityGateError(f"Cannot determine frame rate for {path}")
    target_frame = max(0, round(seconds * fps))
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{target_frame})",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ], check=True, capture_output=True)
    width, height = int(stream["width"]), int(stream["height"])
    if len(result.stdout) != width * height:
        raise QualityGateError(f"Cannot decode a frame at {seconds:.3f}s from {path}")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape((height, width))


def ssim(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape: right = cv2.resize(right, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_AREA)
    left, right = left.astype(np.float64), right.astype(np.float64)
    mu_left, mu_right = cv2.GaussianBlur(left, (11, 11), 1.5), cv2.GaussianBlur(right, (11, 11), 1.5)
    variance_left = cv2.GaussianBlur(left * left, (11, 11), 1.5) - mu_left * mu_left
    variance_right = cv2.GaussianBlur(right * right, (11, 11), 1.5) - mu_right * mu_right
    covariance = cv2.GaussianBlur(left * right, (11, 11), 1.5) - mu_left * mu_right
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    return float(np.mean(((2 * mu_left * mu_right + c1) * (2 * covariance + c2)) / ((mu_left**2 + mu_right**2 + c1) * (variance_left + variance_right + c2))))


def psnr(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape: right = cv2.resize(right, (left.shape[1], left.shape[0]), interpolation=cv2.INTER_AREA)
    value = cv2.PSNR(left, right)
    return float(99.0 if np.isinf(value) else value)


def visual_metrics(expected: Path, actual: Path, critical_times: list[float], samples: int) -> dict[str, float]:
    available = min(duration(probe(expected)), duration(probe(actual)))
    if available <= 0.2: raise QualityGateError("Rendered video duration is invalid")
    times = sorted({round(value, 3) for value in [*(available * (index + 1) / (samples + 1) for index in range(samples)), *critical_times] if 0.02 <= value < available - .02})
    overall_ssim: list[float] = []; overall_psnr: list[float] = []; subtitle_ssim: list[float] = []
    for timestamp in times:
        reference, candidate = frame_at(expected, timestamp), frame_at(actual, timestamp)
        overall_ssim.append(ssim(reference, candidate)); overall_psnr.append(psnr(reference, candidate))
        lower = int(reference.shape[0] * .64)
        subtitle_ssim.append(ssim(reference[lower:], candidate[lower:]))
    return {"sample_count": len(times), "ssim_mean": float(np.mean(overall_ssim)), "ssim_min": float(np.min(overall_ssim)), "psnr_mean": float(np.mean(overall_psnr)), "psnr_min": float(np.min(overall_psnr)), "subtitle_ssim_min": float(np.min(subtitle_ssim))}


def extract_audio(path: Path, target: Path) -> tuple[np.ndarray, int]:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(target)], check=True)
    with wave.open(str(target), "rb") as source:
        rate = source.getframerate(); samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return samples, rate


def marker_offset_seconds(path: Path) -> float:
    metadata = probe(path); video = video_stream(metadata)
    capture = cv2.VideoCapture(str(path)); fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0; luminance: list[float] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok: break
            luminance.append(float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))))
    finally: capture.release()
    if not luminance: raise QualityGateError("No frames available for A/V marker check")
    video_marker = int(np.argmax(luminance)) / fps
    with tempfile.TemporaryDirectory() as temporary:
        samples, rate = extract_audio(path, Path(temporary) / "audio.wav")
    window = max(1, round(rate * .02)); rms = np.array([np.sqrt(np.mean(samples[index:index + window] ** 2)) for index in range(0, max(1, len(samples) - window), window)])
    if not len(rms): raise QualityGateError("No audio samples available for A/V marker check")
    audio_marker = int(np.argmax(rms)) * window / rate
    stream_delta = abs(float(video.get("start_time") or 0) - float(audio_stream(metadata).get("start_time") or 0))
    return float(abs(audio_marker - video_marker) + stream_delta)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True, type=Path); parser.add_argument("--actual", required=True, type=Path)
    parser.add_argument("--critical-times", default="", help="Comma-separated transition/subtitle timestamps")
    parser.add_argument("--report", required=True, type=Path); parser.add_argument("--min-ssim", type=float, default=.985); parser.add_argument("--min-psnr", type=float, default=34); parser.add_argument("--min-subtitle-ssim", type=float, default=.97); parser.add_argument("--max-av-delay-ms", type=float, default=50); parser.add_argument("--samples", type=int, default=12)
    args = parser.parse_args(); critical_times = [float(value) for value in args.critical_times.split(",") if value.strip()]
    expected, actual = probe(args.expected), probe(args.actual)
    expected_video, actual_video = video_stream(expected), video_stream(actual)
    color_keys = ("color_space", "color_transfer", "color_primaries")
    color_match = {key: {"expected": expected_video.get(key), "actual": actual_video.get(key)} for key in color_keys}
    metrics = visual_metrics(args.expected, args.actual, critical_times, args.samples); av_delay_ms = marker_offset_seconds(args.actual) * 1000
    report = {"expected": str(args.expected), "actual": str(args.actual), "video": metrics, "color_metadata": color_match, "av_delay_ms": av_delay_ms}
    args.report.parent.mkdir(parents=True, exist_ok=True); args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    mismatches = [key for key, value in color_match.items() if value["expected"] and value["actual"] and value["expected"] != value["actual"]]
    failures = []
    if metrics["ssim_min"] < args.min_ssim: failures.append(f"SSIM {metrics['ssim_min']:.4f} < {args.min_ssim}")
    if metrics["psnr_min"] < args.min_psnr: failures.append(f"PSNR {metrics['psnr_min']:.2f} < {args.min_psnr}")
    if metrics["subtitle_ssim_min"] < args.min_subtitle_ssim: failures.append(f"subtitle SSIM {metrics['subtitle_ssim_min']:.4f} < {args.min_subtitle_ssim}")
    if av_delay_ms > args.max_av_delay_ms: failures.append(f"A/V delay {av_delay_ms:.1f}ms > {args.max_av_delay_ms:.1f}ms")
    if mismatches: failures.append(f"color metadata changed: {', '.join(mismatches)}")
    if failures: raise SystemExit("; ".join(failures))


if __name__ == "__main__": main()
