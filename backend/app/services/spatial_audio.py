"""First-order Ambisonics encoding and deterministic channel-bed rendering."""
from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SpatialLayout = Literal["5.1", "7.1.4"]
SAMPLE_RATE = 48_000


@dataclass(frozen=True)
class SpatialSource:
    path: Path
    start_time: float
    gain_db: float
    x: float
    y: float
    z: float


def _speaker_vectors(layout: SpatialLayout):
    import numpy as np

    # x=left/right, y=rear/front, z=floor/ceiling. LFE has no positional projection.
    base = [
        (-.707, .707, 0), (.707, .707, 0), (0, 1, 0), (0, 0, 0),
        (-.707, -.707, 0), (.707, -.707, 0),
    ]
    if layout == "7.1.4":
        base = [
            (-.707, .707, 0), (.707, .707, 0), (0, 1, 0), (0, 0, 0),
            (-.707, -.707, 0), (.707, -.707, 0), (-1, 0, 0), (1, 0, 0),
            (-.707, .707, .707), (.707, .707, .707), (-.707, -.707, .707), (.707, -.707, .707),
        ]
    return np.asarray(base, dtype=np.float32)


def encode_first_order_ambisonics(samples, x: float, y: float, z: float):
    """Encode mono samples into SN3D-like W/X/Y/Z components for a virtual point source."""
    import numpy as np

    direction = np.asarray([x, y, z], dtype=np.float32)
    norm = float(np.linalg.norm(direction))
    direction = direction / norm if norm > 1e-5 else np.asarray([0, 1, 0], dtype=np.float32)
    return np.stack((samples * .70710678, samples * direction[0], samples * direction[1], samples * direction[2]), axis=1)


def decode_ambisonics_to_layout(b_format, layout: SpatialLayout):
    import numpy as np

    vectors = _speaker_vectors(layout)
    decoded = np.zeros((b_format.shape[0], len(vectors)), dtype=np.float32)
    for channel, vector in enumerate(vectors):
        if channel == 3:  # LFE receives only a restrained low-frequency send below.
            continue
        decoded[:, channel] = b_format[:, 0] + b_format[:, 1] * vector[0] + b_format[:, 2] * vector[1] + b_format[:, 3] * vector[2]
    decoded[:, 3] = b_format[:, 0] * .08
    peak = float(np.max(np.abs(decoded))) if decoded.size else 1.0
    return decoded / max(peak, 1.0)


def _read_mono_pcm16(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as reader:
        if reader.getframerate() != SAMPLE_RATE or reader.getsampwidth() != 2:
            raise ValueError("Spatial sources must be 48kHz PCM16 WAV files")
        channels = reader.getnchannels()
        values = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return values.reshape(-1, channels).mean(axis=1) if channels > 1 else values


def render_spatial_mix(sources: list[SpatialSource], output_path: Path, *, duration_seconds: float, layout: SpatialLayout) -> None:
    import numpy as np

    channels = 6 if layout == "5.1" else 12
    mixed = np.zeros((max(1, math.ceil(duration_seconds * SAMPLE_RATE)), channels), dtype=np.float32)
    for source in sources:
        samples = _read_mono_pcm16(source.path) * (10 ** (source.gain_db / 20))
        encoded = encode_first_order_ambisonics(samples, source.x, source.y, source.z)
        decoded = decode_ambisonics_to_layout(encoded, layout)
        start = max(0, round(source.start_time * SAMPLE_RATE))
        end = min(len(mixed), start + len(decoded))
        if end > start:
            mixed[start:end] += decoded[:end - start]
    peak = float(np.max(np.abs(mixed))) if mixed.size else 1.0
    if peak > .95:
        mixed *= .95 / peak
    with wave.open(str(output_path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes((mixed * 32767).clip(-32768, 32767).astype("<i2").tobytes())
