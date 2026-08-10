"""Turn delivery metrics into reversible talking-head review markers, never autonomous edits."""
from __future__ import annotations

import copy
from typing import Any


def _filler_count(rough_result: dict[str, Any], start: float, end: float) -> int:
    return sum(
        1 for item in rough_result.get("clip_analysis", [])
        if isinstance(item, dict) and item.get("type") in {"filler_word", "repetition"}
        and float(item.get("start", 0)) < end and float(item.get("end", 0)) > start
    )


def derive_talking_head_markers(speaker_segments: list[dict[str, Any]], rough_result: dict[str, Any], *, confidence_threshold: int = 58) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for segment in speaker_segments:
        if segment.get("assessment_status") != "assessed": continue
        start, end = float(segment["source_start"]), float(segment["source_end"]); metrics = dict(segment.get("metrics", {}))
        confidence, fluency = int(segment.get("confidence_score", 100)), int(segment.get("fluency_score", 100))
        filler_count = _filler_count(rough_result, start, end)
        triggers: list[str] = []
        if confidence < confidence_threshold: triggers.append(f"自信呈現 {confidence}/100")
        if float(metrics.get("gaze_away_rate", 0)) >= 35: triggers.append("眼神多次離開鏡頭")
        if float(metrics.get("blink_rate_per_min", 0)) >= 28: triggers.append("眼睛閉合／眨眼訊號偏密集")
        if float(metrics.get("body_rigidity_proxy", 0)) >= 68: triggers.append("肢體動態偏低")
        if fluency < 55 or filler_count >= 2: triggers.append("語句節奏不夠流暢" if fluency < 55 else f"包含 {filler_count} 個贅詞／重複語")
        if not triggers: continue
        severe = confidence < confidence_threshold - 9 or (float(metrics.get("gaze_away_rate", 0)) >= 45 and (fluency < 55 or filler_count >= 2))
        recommendation = "review_cut" if severe else "b_roll"
        action_text = "建議裁切或重錄" if recommendation == "review_cut" else "建議以 B-Roll 覆蓋"
        markers.append({"segment_id": str(segment.get("segment_id", "")), "source_start": start, "source_end": end, "recommendation": recommendation, "reason": f"{action_text}：{'、'.join(triggers)}", "metrics": metrics, "confidence_score": confidence, "fluency_score": fluency})
    return markers


def apply_talking_head_markers(timeline: Any, markers: list[dict[str, Any]]) -> None:
    settings = copy.deepcopy(timeline.settings_json or {}); confirmed = dict(settings.get("confirmed_timeline", {}))
    if "tracks" not in confirmed:
        confirmed["tracks"] = [{"id": "main-video", "type": "main_video", "z_index": 0, "clips": list(confirmed.get("segments", []))}]
    for track in confirmed.get("tracks", []):
        if not isinstance(track, dict) or track.get("type") != "main_video": continue
        for clip in track.get("clips", []):
            overlaps = [marker for marker in markers if float(clip.get("source_start", 0)) < marker["source_end"] and float(clip.get("source_end", 0)) > marker["source_start"]]
            if not overlaps: continue
            marker = overlaps[0]; hints = list(clip.get("creator_hints", [])); flags = list(clip.get("review_flags", []))
            hint = str(marker["reason"])
            if hint not in hints: hints.append(hint)
            if "Talking-Head：建議審閱呈現狀態" not in flags: flags.append("Talking-Head：建議審閱呈現狀態")
            clip["creator_hints"], clip["review_flags"], clip["talking_head_recommendation"] = hints, flags, marker["recommendation"]
            clip["speaker_state"] = {"confidence_score": marker["confidence_score"], "fluency_score": marker["fluency_score"], "assessment_status": "assessed", "metrics": marker["metrics"]}
    settings["confirmed_timeline"] = confirmed; settings["multitrack_timeline"] = confirmed
    settings["talking_head_confidence"] = {"status": "completed", "markers": markers, "advisory_only": True, "limitations": ["標記僅描述畫面與逐字稿的可觀察訊號，並非人格、心理或情緒診斷。", "建議不會自動刪除內容；請由創作者決定裁切、重錄或 B-Roll 覆蓋。"]}
    timeline.settings_json = settings
