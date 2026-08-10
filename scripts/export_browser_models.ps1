param(
  [string]$OutputRoot = "frontend/public/models",
  [string]$CalibrationDir = "models/calibration-images"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path "$OutputRoot/whisper-tiny" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutputRoot/yolo" | Out-Null

# Setup once: python -m pip install "optimum[onnxruntime]" transformers ultralytics onnx onnxruntime opencv-python
optimum-cli export onnx --model openai/whisper-tiny.en --task automatic-speech-recognition "$OutputRoot/whisper-tiny/fp32"
python scripts/quantize_onnx_int8.py --input "$OutputRoot/whisper-tiny/fp32/encoder_model.onnx" --output "$OutputRoot/whisper-tiny/encoder.int8.onnx" --mode dynamic
python scripts/quantize_onnx_int8.py --input "$OutputRoot/whisper-tiny/fp32/decoder_model.onnx" --output "$OutputRoot/whisper-tiny/decoder.int8.onnx" --mode dynamic

# YOLO 11 nano keeps the browser payload small; use representative UI/speaker frames for calibration.
yolo export model=yolo11n.pt format=onnx imgsz=640 opset=17 simplify=True dynamic=False
Move-Item -Force yolo11n.onnx "$OutputRoot/yolo/yolo11n.fp32.onnx"
python scripts/quantize_onnx_int8.py --input "$OutputRoot/yolo/yolo11n.fp32.onnx" --output "$OutputRoot/yolo/yolo11n.int8.onnx" --mode static --calibration-dir $CalibrationDir --image-size 640

Write-Host "Export complete. Validate output accuracy against held-out ASR audio and detection frames before publishing models."
