# Behavioral analysis and coaching

`POST /api/v1/media/{media_asset_id}/analyze-behavioral-coach` requires the existing timed ASR/rough-cut result. It samples the proxy with MediaPipe Face Mesh/Pose, optionally runs an explicitly provisioned FACS ONNX model, analyses pitch contour and pacing from audio, and evaluates visible STAR response markers.

The task stores its report as an `AIAnalysis` with `analysis_type=speaker_state` and `model_name=behavioral_coach_v1`. If a Timeline ID is supplied, or `POST /api/v1/timelines/{timeline_id}/apply-behavioral-coach` is called later, the three highest-priority segments are saved under `settings_json.behavioral_coach` and attached to matching clips as review flags/hints.

This is a creator-facing delivery aid only. It must not be used to diagnose anxiety, emotion, personality, deception, health, employment suitability, or any protected trait. Require explicit creator consent before processing face/voice data; set `FACS_ONNX_PATH` only to a licensed, evaluated Action Unit model whose output layout matches `app.services.facs.ACTION_UNITS`.
