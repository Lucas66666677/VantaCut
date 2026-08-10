"""Quantize browser ONNX models: dynamic INT8 for Whisper transformers, static QDQ INT8 for YOLO CNNs."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterator

import numpy as np
import onnx
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_dynamic, quantize_static


class ImageCalibrationReader(CalibrationDataReader):
    def __init__(self, model_path: Path, calibration_dir: Path, size: int) -> None:
        import cv2

        self.cv2 = cv2
        model = onnx.load_model(str(model_path))
        self.input_name = model.graph.input[0].name
        self.items = [path for path in calibration_dir.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
        if not self.items:
            raise ValueError("Calibration directory has no supported images")
        self.size = size
        self._iterator: Iterator[Path] = iter(self.items)

    def get_next(self) -> dict[str, np.ndarray] | None:
        try:
            path = next(self._iterator)
        except StopIteration:
            return None
        image = self.cv2.imread(str(path))
        if image is None:
            return self.get_next()
        image = self.cv2.resize(image, (self.size, self.size), interpolation=self.cv2.INTER_LINEAR)
        rgb = self.cv2.cvtColor(image, self.cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return {self.input_name: np.transpose(rgb, (2, 0, 1))[None]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("dynamic", "static"), required=True)
    parser.add_argument("--calibration-dir", type=Path)
    parser.add_argument("--image-size", type=int, default=640)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "dynamic":
        # Best fit for Whisper encoder/decoder MatMul-heavy graphs; benchmark WebGPU separately.
        quantize_dynamic(str(args.input), str(args.output), weight_type=QuantType.QInt8, per_channel=True)
        return
    if args.calibration_dir is None:
        parser.error("--calibration-dir is required for static YOLO QDQ quantization")
    quantize_static(
        str(args.input), str(args.output), ImageCalibrationReader(args.input, args.calibration_dir, args.image_size),
        quant_format=QuantFormat.QDQ, activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
        per_channel=True,
    )


if __name__ == "__main__":
    main()
