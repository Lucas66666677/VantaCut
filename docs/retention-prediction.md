# Pre-export retention prediction

This feature estimates *risk*, not the private ranking logic of YouTube or TikTok. It must be labelled as a prediction until a calibrated checkpoint exists; it is not observed platform analytics and it must not be represented as such.

## Feature sequence

One feature vector is emitted every `RETENTION_WINDOW_SECONDS` (default: one second):

| Feature | Source |
| --- | --- |
| `pacing` | Timeline cut density |
| `brightness`, `motion` | Precomputed visual features / beat-sync visual momentum |
| `semantic_quality` | Multimodal final-cut evidence |
| `silence_ratio`, `speech_rate` | Rough-cut silence detection and word timestamps |
| `beat_alignment` | BGM beat timestamps |
| `b_roll_coverage`, `long_shot_ratio` | Timeline composition |

The current DB schema does not persist B-Roll output offsets; its feature is deliberately `0` until the multi-track document stores them. This prevents fabricated precision.

## Model and target

`RetentionTransformer` is causal: it may use footage up to a time window but never a future window. It predicts a bounded dropout hazard `h[t]`; the curve is `R[t] = 100 × ∏(1 - h[t])`. Train against consented published-video audience-retention curves using:

```text
target_hazard[t] = clamp(1 - observed_retention[t] / observed_retention[t-1], 0, 0.25)
loss = Huber(predicted_hazard, target_hazard) + monotonicity_regularizer
```

Split train/validation by creator and publish date, not by random windows, so a creator's editing style does not leak into both sets. Calibrate the retained percentages on a held-out set, persist the checkpoint as `RETENTION_MODEL_PATH`, and track MAE at 5/30/60 seconds plus hotspot precision for >=15% drops.

## Product behavior

`POST /api/v1/analysis/timelines/{timeline_id}/retention-prediction` stores an advisory report at `timeline.settings_json.retention_prediction`. If no valid checkpoint is deployed it produces `prediction_mode: "heuristic_baseline"`; the UI presents this as `未校正預測`. A checkpoint-loaded prediction returns `prediction_mode: "checkpoint"` and `已校正模型`.
