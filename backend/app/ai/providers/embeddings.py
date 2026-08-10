"""OpenCLIP-compatible image/text embeddings with a deterministic dev fallback."""
from __future__ import annotations

import hashlib
import math
import os
from typing import Any

from app.ai.providers.base import EmbeddingProvider


def _normalise(values: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / magnitude for value in values]


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic vectors for UI/API development; not semantically meaningful."""

    @property
    def name(self) -> str:
        return "mock_clip"

    @property
    def dimensions(self) -> int:
        return 512

    def _embed_bytes(self, payload: bytes) -> list[float]:
        values: list[float] = []
        seed = payload
        while len(values) < self.dimensions:
            seed = hashlib.sha256(seed).digest()
            values.extend((byte / 127.5) - 1.0 for byte in seed)
        return _normalise(values[: self.dimensions])

    def embed_text(self, text: str) -> list[float]:
        return self._embed_bytes(text.strip().lower().encode("utf-8"))

    def embed_image(self, image_path: str) -> list[float]:
        with open(image_path, "rb") as image_file:
            return self._embed_bytes(image_file.read())


class OpenCLIPEmbeddingProvider(EmbeddingProvider):
    """Open-source OpenAI CLIP-compatible ViT-B/32 provider, lazily loaded per worker."""

    def __init__(self, model_name: str | None = None, pretrained: str | None = None) -> None:
        self.model_name = model_name or os.getenv("OPENCLIP_MODEL", "ViT-B-32")
        self.pretrained = pretrained or os.getenv("OPENCLIP_PRETRAINED", "laion2b_s34b_b79k")
        self._runtime: tuple[Any, Any, Any, Any] | None = None

    @property
    def name(self) -> str:
        return f"open_clip:{self.model_name}:{self.pretrained}"

    @property
    def dimensions(self) -> int:
        return 512

    def _load(self) -> tuple[Any, Any, Any, Any]:
        if self._runtime is not None:
            return self._runtime
        try:
            import open_clip
            import torch
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("OpenCLIP requires open_clip_torch, torch and Pillow") from exc
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained, device=device
        )
        model.eval()
        self._runtime = (model, preprocess, open_clip.get_tokenizer(self.model_name), (torch, Image, device))
        return self._runtime

    @staticmethod
    def _to_vector(tensor: Any) -> list[float]:
        tensor = tensor / tensor.norm(dim=-1, keepdim=True)
        return [float(value) for value in tensor[0].detach().cpu().tolist()]

    def embed_text(self, text: str) -> list[float]:
        model, _, tokenizer, runtime = self._load()
        torch, _, device = runtime
        with torch.no_grad():
            return self._to_vector(model.encode_text(tokenizer([text]).to(device)))

    def embed_image(self, image_path: str) -> list[float]:
        model, preprocess, _, runtime = self._load()
        torch, Image, device = runtime
        with Image.open(image_path).convert("RGB") as image:
            image_tensor = preprocess(image).unsqueeze(0).to(device)
        with torch.no_grad():
            return self._to_vector(model.encode_image(image_tensor))
