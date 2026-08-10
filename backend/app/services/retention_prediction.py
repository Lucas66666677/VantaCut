"""Feature extraction, calibrated inference, and explainable pre-export retention advice."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.retention_model import FEATURE_NAMES, RetentionTransformer
from app.models.entities import AIAnalysis, AnalysisType, Clip, MediaAsset, Timeline, TrackType


@dataclass(frozen=True)
class OutputSegment:
    start: float
    end: float
    source_start: float
    asset_id: object
    duration: float
    is_b_roll: bool = False


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _latest_analyses(db: Session, asset_ids: list[object]) -> dict[object, AIAnalysis]:
    if not asset_ids:
        return {}
    records = db.scalars(
        select(AIAnalysis)
        .where(AIAnalysis.media_asset_id.in_(asset_ids), AIAnalysis.status == "completed")
        .order_by(AIAnalysis.created_at.desc())
    ).all()
    results: dict[object, AIAnalysis] = {}
    for record in records:
        # Rough cut includes silence/transcript and final_cut adds multimodal evidence.
        if record.media_asset_id not in results and record.analysis_type == AnalysisType.ROUGH_CUT:
            results[record.media_asset_id] = record
    return results


def _output_segments(timeline: Timeline) -> list[OutputSegment]:
    main = sorted((clip for clip in timeline.clips if clip.track == TrackType.MAIN_VIDEO and clip.enabled), key=lambda clip: clip.order_index)
    if not main:
        main = sorted((clip for clip in timeline.clips if clip.enabled), key=lambda clip: clip.order_index)
    cursor = 0.0
    segments: list[OutputSegment] = []
    for clip in main:
        duration = max(0.0, float(clip.source_end) - float(clip.source_start))
        if duration <= 0:
            continue
        segments.append(OutputSegment(cursor, cursor + duration, float(clip.source_start), clip.source_asset_id, duration))
        cursor += duration
    return segments


def _normalised_metadata(asset: MediaAsset) -> tuple[float, float]:
    metadata = dict(asset.metadata_json or {})
    visual = dict(metadata.get("visual_features") or {})
    brightness = visual.get("brightness", metadata.get("average_brightness", 0.5))
    motion = visual.get("motion", metadata.get("visual_motion", 0.25))
    try:
        return _clamp(float(brightness)), _clamp(float(motion) * (1.0 if float(motion) <= 1 else 0.1))
    except (TypeError, ValueError):
        return 0.5, 0.25


def _analysis_signals(analysis: AIAnalysis | None, source_time: float, window_seconds: float) -> tuple[float, float, float]:
    """Return semantic quality, silence overlap ratio and normalised speaking rate."""
    if analysis is None:
        return 0.62, 0.0, 0.5
    result = dict(analysis.result_json or {})
    semantic_values: list[float] = []
    final_timeline = dict(result.get("final_timeline") or {})
    for segment in final_timeline.get("segments", []):
        if _overlap(source_time, source_time + window_seconds, float(segment.get("source_start", 0)), float(segment.get("source_end", 0))) <= 0:
            continue
        evidence = dict(segment.get("evidence") or {})
        values = [evidence.get(name) for name in ("semantic_completeness", "presentation_naturalness", "template_alignment")]
        usable = [float(value) for value in values if isinstance(value, (float, int))]
        if usable:
            semantic_values.append(sum(usable) / len(usable) / 100.0)
    semantic = _clamp(sum(semantic_values) / len(semantic_values)) if semantic_values else _clamp(float(analysis.confidence or 0.62))
    silence = sum(
        _overlap(source_time, source_time + window_seconds, float(item.get("start", 0)), float(item.get("end", 0)))
        for item in result.get("silences", [])
    ) / max(window_seconds, 0.001)
    words = [
        word for segment in dict(result.get("transcript") or {}).get("segments", [])
        for word in segment.get("words", [])
        if _overlap(source_time, source_time + window_seconds, float(word.get("start", 0)), float(word.get("end", 0))) > 0
    ]
    # 2.8 words/sec is a comfortable explanatory-video reference pace.
    speech_rate = _clamp((len(words) / max(window_seconds, 0.001)) / 2.8)
    return semantic, _clamp(silence), speech_rate


def _nearest_alignment(time_seconds: float, beats: list[float]) -> float:
    if not beats:
        return 0.45
    nearest = min(abs(time_seconds - beat) for beat in beats)
    return _clamp(1.0 - nearest / 0.25)


def _visual_momentum(time_seconds: float, events: list[dict[str, Any]], fallback: tuple[float, float]) -> tuple[float, float]:
    if not events:
        return fallback
    nearest = min(events, key=lambda item: abs(float(item.get("time", 0)) - time_seconds))
    luminance = nearest.get("luminance", fallback[0])
    motion = nearest.get("motion", fallback[1])
    try:
        return _clamp(float(luminance)), _clamp(float(motion) * (1.0 if float(motion) <= 1 else 0.1))
    except (TypeError, ValueError):
        return fallback


def _features_for_timeline(db: Session, timeline: Timeline) -> tuple[list[list[float]], list[dict[str, float]], float]:
    segments = _output_segments(timeline)
    if not segments:
        raise ValueError("Timeline has no enabled main-video clips to predict")
    duration = segments[-1].end
    window = max(0.5, settings.retention_window_seconds)
    assets = {asset.id: asset for asset in db.scalars(select(MediaAsset).where(MediaAsset.id.in_([segment.asset_id for segment in segments]))).all()}
    analyses = _latest_analyses(db, list(assets))
    beat_sync = dict((timeline.settings_json or {}).get("beat_sync") or {})
    beats = [float(value) for value in dict(beat_sync.get("music") or {}).get("beats", []) if isinstance(value, (float, int))]
    visual_events = [dict(item) for item in beat_sync.get("visual_momentum", []) if isinstance(item, dict)]
    features: list[list[float]] = []
    evidence: list[dict[str, float]] = []
    steps = max(1, math.ceil(duration / window))
    for index in range(steps):
        start = index * window
        end = min(duration, start + window)
        segment = next((item for item in segments if item.start <= start < item.end), segments[-1])
        asset = assets.get(segment.asset_id)
        local_source = segment.source_start + (start - segment.start)
        brightness, motion = _visual_momentum(local_source, visual_events, _normalised_metadata(asset) if asset else (0.5, 0.25))
        semantic, silence_ratio, speech_rate = _analysis_signals(analyses.get(segment.asset_id), local_source, end - start)
        cut_count = sum(1 for item in segments if abs(item.start - start) <= window * 0.55)
        pacing = _clamp(cut_count / 2.0)
        b_roll_coverage = 0.0  # DB clips currently do not persist output offsets for overlay tracks.
        long_shot_ratio = _clamp(segment.duration / 8.0)
        vector = [pacing, brightness, motion, semantic, silence_ratio, _nearest_alignment(start, beats), b_roll_coverage, long_shot_ratio, speech_rate]
        features.append(vector)
        evidence.append(dict(zip(FEATURE_NAMES, vector, strict=True)))
    return features, evidence, duration


@lru_cache(maxsize=1)
def _load_checkpoint() -> RetentionTransformer | None:
    if not settings.retention_model_path:
        return None
    checkpoint_path = Path(settings.retention_model_path)
    if not checkpoint_path.is_file():
        return None
    try:
        import torch
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        config = dict(payload.get("config") or {}) if isinstance(payload, dict) else {}
        model = RetentionTransformer(**{key: config[key] for key in ("feature_dim", "hidden_dim", "heads", "layers", "dropout") if key in config})
        model.load_state_dict(payload.get("model_state_dict", payload), strict=True)
        model.eval()
        return model
    except Exception:
        # A bad/old checkpoint must not prevent creators from receiving the transparent baseline.
        return None


def _heuristic_hazards(features: list[list[float]]) -> list[float]:
    hazards: list[float] = []
    for pacing, brightness, motion, semantic, silence, beat, b_roll, long_shot, speech_rate in features:
        stimulation = pacing * 0.18 + motion * 0.18 + beat * 0.10 + b_roll * 0.14 + semantic * 0.25 + min(speech_rate, 1.0) * 0.08 + brightness * 0.07
        penalties = silence * 0.30 + long_shot * max(0.0, 0.45 - motion) * 0.13 + max(0.0, 0.35 - semantic) * 0.16 + max(0.0, 0.28 - speech_rate) * 0.08
        hazards.append(_clamp(0.018 + penalties - stimulation * 0.016, 0.004, 0.14))
    return hazards


def _predict_hazards(features: list[list[float]]) -> tuple[list[float], str, bool]:
    model = _load_checkpoint()
    if model is None:
        return _heuristic_hazards(features), "retention-heuristic-v1", False
    try:
        import torch
        # The model has a finite positional table; preserve continuity by stitching hazards.
        hazards: list[float] = []
        for offset in range(0, len(features), 4096):
            tensor = torch.tensor([features[offset:offset + 4096]], dtype=torch.float32)
            with torch.no_grad():
                predicted, _retention = model(tensor)
            hazards.extend(float(value) for value in predicted[0].tolist())
        return hazards, "retention-transformer-v1", True
    except Exception:
        return _heuristic_hazards(features), "retention-heuristic-v1", False


def _hotspot_suggestion(feature: dict[str, float]) -> tuple[str, str]:
    if feature["silence_ratio"] >= 0.18:
        return "靜音比例偏高，會中斷觀眾的理解節奏。", "強烈建議跳剪靜音；若保留停頓，加入重點字卡或反應鏡頭。"
    if feature["long_shot_ratio"] >= 0.95 and feature["motion"] < 0.25 and feature["pacing"] < 0.35:
        return "單一鏡頭超過 8 秒且畫面動態不足。", "強烈建議加入 B-Roll、局部放大，或在語意轉折處插入跳剪。"
    if feature["semantic_quality"] < 0.42:
        return "AI 語意／講者呈現評分偏低，資訊價值可能不足。", "縮短重複說明，前移結論，或以畫面示例與字卡補足觀眾理解。"
    if feature["speech_rate"] < 0.28:
        return "語速偏慢且沒有足夠的畫面刺激。", "刪除冗長停頓、加快口白 3–8%，並用動態字幕維持注意力。"
    if feature["beat_alignment"] < 0.2:
        return "畫面轉折未對齊可用的 BGM 節拍。", "把轉場、B-Roll 進場或關鍵字動畫吸附到下一個重拍。"
    return "此處多項留存特徵同時偏弱。", "縮短此段並加入明確的視覺變化或下一段內容預告。"


def _build_hotspots(curve: list[dict[str, float]], evidence: list[dict[str, float]], window: float) -> list[dict[str, Any]]:
    hotspots: list[dict[str, Any]] = []
    index = 0
    while index < len(evidence):
        end_index = min(len(curve) - 1, index + max(1, round(4 / window)))
        predicted_drop = curve[index]["expected_retention"] - curve[end_index]["expected_retention"]
        if predicted_drop > 15:
            local = max(range(index, end_index), key=lambda item: curve[item + 1]["risk_score"])
            reason, suggestion = _hotspot_suggestion(evidence[local])
            hotspots.append({
                "id": f"retention-{index}-{end_index}", "start_time": round(index * window, 3),
                "end_time": round((end_index + 1) * window, 3), "predicted_drop": round(predicted_drop, 2),
                "risk_score": round(max(curve[item + 1]["risk_score"] for item in range(index, end_index + 1)), 1),
                "reason": reason, "suggestion": suggestion, "feature_evidence": {key: round(value, 3) for key, value in evidence[local].items()},
            })
            index = end_index
        else:
            index += 1
    return hotspots


def predict_timeline_retention(db: Session, timeline: Timeline) -> dict[str, Any]:
    features, evidence, duration = _features_for_timeline(db, timeline)
    hazards, model_name, calibrated = _predict_hazards(features)
    window = max(0.5, settings.retention_window_seconds)
    current = 100.0
    curve: list[dict[str, float]] = [{"time_seconds": 0.0, "expected_retention": 100.0, "risk_score": 0.0}]
    for index, hazard in enumerate(hazards):
        current *= 1.0 - _clamp(hazard, 0.0, 0.25)
        curve.append({"time_seconds": round(min(duration, (index + 1) * window), 3), "expected_retention": round(current, 2), "risk_score": round(_clamp(hazard / 0.10) * 100, 1)})
    hotspots = _build_hotspots(curve, evidence, window)
    if calibrated:
        summary = f"已使用校正 Transformer 預測 {duration:.0f} 秒影片；偵測到 {len(hotspots)} 個高流失風險區段。"
    else:
        summary = f"目前為未校正的製作前 heuristic baseline（非實際平台數據）；偵測到 {len(hotspots)} 個可優先審閱區段。"
    return {"model_name": model_name, "prediction_mode": "checkpoint" if calibrated else "heuristic_baseline", "is_calibrated": calibrated, "window_seconds": window, "curve": curve, "hotspots": hotspots, "summary": summary}
