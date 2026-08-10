# Browser ONNX model assets

Do not commit production model weights here. Generate them on a trusted build
machine and deploy them as static/CDN assets:

```powershell
./scripts/export_browser_models.ps1
cd frontend; npm install; npm run prepare:ort
```

The application expects the following paths by default:

- `/models/whisper-tiny/encoder.int8.onnx`
- `/models/whisper-tiny/decoder.int8.onnx`
- `/models/yolo/yolo11n.int8.onnx`

`onnxruntime-web` WebAssembly binaries are copied to `/public/ort` by
`npm run prepare:ort` so the WASM fallback is served with the app.
