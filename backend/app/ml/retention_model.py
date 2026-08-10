"""Sequence model used once platform-retention labels have calibrated a checkpoint.

The network predicts a per-window hazard rather than independent percentages.  A
survival curve (`cumprod(1 - hazard)`) guarantees a bounded, non-increasing
expected retention curve and makes sharp drops directly explainable.
"""
from __future__ import annotations

import torch
from torch import Tensor, nn


FEATURE_NAMES = (
    "pacing", "brightness", "motion", "semantic_quality", "silence_ratio",
    "beat_alignment", "b_roll_coverage", "long_shot_ratio", "speech_rate",
)


class RetentionTransformer(nn.Module):
    """Causal Transformer with a hazard head for viewer-survival estimation."""

    def __init__(self, feature_dim: int = len(FEATURE_NAMES), hidden_dim: int = 96, heads: int = 4, layers: int = 3, dropout: float = 0.12) -> None:
        super().__init__()
        self.input_projection = nn.Sequential(nn.LayerNorm(feature_dim), nn.Linear(feature_dim, hidden_dim), nn.GELU())
        self.position = nn.Parameter(torch.zeros(1, 4096, hidden_dim))
        block = nn.TransformerEncoderLayer(d_model=hidden_dim, nhead=heads, dim_feedforward=hidden_dim * 3, dropout=dropout, batch_first=True, activation="gelu", norm_first=True)
        self.encoder = nn.TransformerEncoder(block, num_layers=layers)
        self.hazard_head = nn.Sequential(nn.LayerNorm(hidden_dim), nn.Linear(hidden_dim, 1))

    def forward(self, features: Tensor, padding_mask: Tensor | None = None) -> tuple[Tensor, Tensor]:
        """Return per-window dropout hazard and expected 0–100 survival curve."""
        length = features.shape[1]
        if length > self.position.shape[1]:
            raise ValueError("Timeline exceeds the model positional embedding capacity")
        causal_mask = torch.full((length, length), float("-inf"), device=features.device)
        causal_mask = torch.triu(causal_mask, diagonal=1)
        encoded = self.encoder(self.input_projection(features) + self.position[:, :length], mask=causal_mask, src_key_padding_mask=padding_mask)
        # Calibrated training constrains this to plausible per-second churn (0–25%).
        hazard = torch.sigmoid(self.hazard_head(encoded).squeeze(-1)) * 0.25
        retention = torch.cumprod(1.0 - hazard, dim=1) * 100.0
        return hazard, retention
