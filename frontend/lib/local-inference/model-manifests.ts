import type { LocalInferenceTask, LocalModelSpec } from "./use-local-inference";

/**
 * Browser models are intentionally served from /public/models instead of bundled
 * into the JavaScript chunk.  Deploy the output of scripts/export_browser_models.ps1
 * to the matching paths (or replace these URLs with signed CDN URLs).
 */
const model = (
  id: string,
  task: LocalInferenceTask,
  url: string,
  inputName: string,
  outputNames?: string[],
): LocalModelSpec => ({ id, task, url, inputName, outputNames });

export const localBrowserModels = {
  whisperTinyEncoder: model(
    "whisper-tiny-encoder-int8",
    "asr",
    "/models/whisper-tiny/encoder.int8.onnx",
    "input_features",
    ["last_hidden_state"],
  ),
  // Whisper decoding is autoregressive: run this decoder repeatedly with the
  // tokenizer-generated input_ids and the encoder's last_hidden_state output.
  whisperTinyDecoder: model(
    "whisper-tiny-decoder-int8",
    "asr",
    "/models/whisper-tiny/decoder.int8.onnx",
    "input_ids",
    ["logits"],
  ),
  yolo11n: model(
    "yolo11n-int8",
    "yolo",
    "/models/yolo/yolo11n.int8.onnx",
    "images",
    ["output0"],
  ),
} satisfies Record<string, LocalModelSpec>;
