"""Optional, consent-bound FACS action-unit inference adapter.

The provisioned ONNX model must output AU01, AU04, AU12, AU15, AU20 and AU25 activations in that order.
No emotion, personality, truthfulness, or clinical state is inferred from these measurements.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings


ACTION_UNITS = ("au01_inner_brow", "au04_brow_lowerer", "au12_lip_corner", "au15_lip_depressor", "au20_lip_stretcher", "au25_lips_part")


class FACSActionUnitEstimator:
    def __init__(self) -> None:
        self._net = None
        if settings.facs_onnx_path and Path(settings.facs_onnx_path).is_file():
            try:
                import cv2
                self._net = cv2.dnn.readNetFromONNX(settings.facs_onnx_path)
            except Exception:
                self._net = None

    @property
    def available(self) -> bool:
        return self._net is not None

    def predict(self, face_bgr):
        if self._net is None or face_bgr is None or face_bgr.size == 0:
            return None
        import cv2
        import numpy as np

        size = settings.facs_input_size
        blob = cv2.dnn.blobFromImage(face_bgr, scalefactor=1 / 255.0, size=(size, size), swapRB=True)
        self._net.setInput(blob)
        values = np.asarray(self._net.forward()).reshape(-1)
        if values.size < len(ACTION_UNITS):
            return None
        # Models may emit logits or probabilities; normalise conservatively to [0, 1].
        values = 1 / (1 + np.exp(-values[:len(ACTION_UNITS)])) if (values.min() < 0 or values.max() > 1) else values[:len(ACTION_UNITS)]
        return {name: float(max(0.0, min(1.0, value))) for name, value in zip(ACTION_UNITS, values, strict=True)}
