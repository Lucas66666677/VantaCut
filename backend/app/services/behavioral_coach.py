"""Observable delivery coaching: visual presentation, vocal stability and response structure.

Metrics are advisory presentation signals, not psychological, medical, personality, or deception scores.
"""
from __future__ import annotations

import re
from typing import Any


STAR_MARKERS = {
    "situation": ("situation", "context", "when", "當時", "背景", "情境", "面對"),
    "task": ("task", "goal", "responsible", "目標", "任務", "負責", "需要"),
    "action": ("action", "i decided", "i led", "i implemented", "我採取", "我先", "我負責", "執行"),
    "result": ("result", "outcome", "impact", "learned", "結果", "成果", "因此", "學到"),
}


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def score_star_structure(text: str) -> dict[str, Any]:
    lowered = text.lower()
    coverage = {name: any(marker in lowered for marker in markers) for name, markers in STAR_MARKERS.items()}
    score = round(sum(coverage.values()) / len(coverage) * 100)
    missing = [name.upper() for name, present in coverage.items() if not present]
    suggestion = "回應已呈現完整 STAR 骨架。" if not missing else f"可補上 {'、'.join(missing)}，讓回應更接近 STAR 架構。"
    return {"star_score": score, "coverage": coverage, "suggestion": suggestion}


def analyze_vocal_stability(audio_path: str, transcript_segments: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Pitch contour / frame-to-frame jitter proxy. Silence/noisy input yields no negative inference."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        return {}
    samples, sample_rate = librosa.load(audio_path, sr=None, mono=True)
    output: dict[str, dict[str, float]] = {}
    for index, segment in enumerate(transcript_segments, start=1):
        start, end = float(segment["start"]), float(segment["end"])
        clip = samples[int(start * sample_rate):int(end * sample_rate)]
        if len(clip) < sample_rate // 2:
            continue
        try:
            f0 = librosa.yin(clip, fmin=65, fmax=400, sr=sample_rate)
            voiced = f0[np.isfinite(f0)]
        except Exception:
            continue
        if len(voiced) < 4:
            continue
        jitter = float(np.mean(np.abs(np.diff(voiced))) / max(float(np.mean(voiced)), 1e-6) * 100)
        pitch_range = float(np.std(voiced) / max(float(np.mean(voiced)), 1e-6) * 100)
        words = len(re.findall(r"\w+|[\u4e00-\u9fff]", str(segment.get("text", ""))))
        wpm = words / max(end - start, 1e-6) * 60
        pace_score = _clamp(100 - max(0, wpm - 175) * .55 - max(0, 95 - wpm) * .25)
        stability = _clamp(100 - jitter * 7 - max(0, pitch_range - 24) * .8)
        output[f"segment-{index:04d}"] = {
            "pitch_jitter_percent": round(jitter, 2), "pitch_variation_percent": round(pitch_range, 2),
            "words_per_minute": round(wpm, 1), "vocal_stability_score": round(stability), "pace_score": round(pace_score),
        }
    return output


def build_behavioral_coach_report(
    *, visual_segments: list[dict[str, Any]], transcript_segments: list[dict[str, Any]], vocal: dict[str, dict[str, float]]
) -> dict[str, Any]:
    text = " ".join(str(item.get("text", "")) for item in transcript_segments)
    star = score_star_structure(text)
    transcript_by_id = {f"segment-{index:04d}": item for index, item in enumerate(transcript_segments, start=1)}
    coached: list[dict[str, Any]] = []
    for visual in visual_segments:
        segment_id = str(visual["segment_id"]); metrics = dict(visual.get("metrics", {})); acoustic = vocal.get(segment_id, {})
        semantic = score_star_structure(str(transcript_by_id.get(segment_id, {}).get("text", "")))["star_score"]
        visual_score = float(visual.get("confidence_score", 0)); voice_score = float(acoustic.get("vocal_stability_score", 60)); pace_score = float(acoustic.get("pace_score", 65))
        coaching_score = round(visual_score * .45 + voice_score * .25 + pace_score * .15 + semantic * .15)
        suggestions = list(visual.get("suggestions", []))
        if acoustic.get("words_per_minute", 0) > 175:
            suggestions.append("語速偏快；可先在此段保留自然停頓，或嘗試放慢約 15% 後再自行確認。")
        if acoustic.get("pitch_jitter_percent", 0) > 4:
            suggestions.append("音高在相鄰語音幀間變化較大；建議放慢呼吸、縮短句子後重錄。")
        if semantic < 50:
            suggestions.append("此段缺少明確的情境、行動或結果；可用 STAR 的一句結果收束重點。")
        coached.append({
            "segment_id": segment_id, "source_start": visual["source_start"], "source_end": visual["source_end"],
            "coaching_score": coaching_score, "priority": 100 - coaching_score,
            "visual_metrics": metrics, "acoustic_metrics": acoustic, "semantic_score": semantic, "suggestions": suggestions,
        })
    lowest = sorted(coached, key=lambda item: (-item["priority"], item["source_start"]))[:3]
    radar = {
        "eye_contact": round(sum(item["visual_metrics"].get("eye_contact", 0) for item in coached) / max(1, len(coached))),
        "posture_openness": round(sum(item["visual_metrics"].get("posture_openness", 0) for item in coached) / max(1, len(coached))),
        "gesture_openness": round(sum(item["visual_metrics"].get("gesture_openness", 0) for item in coached) / max(1, len(coached))),
        "vocal_stability": round(sum(item["acoustic_metrics"].get("vocal_stability_score", 60) for item in coached) / max(1, len(coached))),
        "response_structure": star["star_score"],
    }
    return {
        "version": 1, "advisory_only": True, "radar": radar, "star": star,
        "segments": coached, "lowest_confidence_segments": lowest,
        "limitations": [
            "此報告只描述畫面、音訊與文字的可觀察訊號，不診斷緊張、人格、心理健康或真實情緒。",
            "鏡頭角度、光線、麥克風與語言差異會影響結果；請人工確認所有建議。",
            "FACS 模型未設定或臉部證據不足時，不會產生微表情判定。",
        ],
    }


def apply_coach_markers_to_timeline(timeline, report: dict[str, Any]) -> None:
    markers = [{
        "segment_id": item["segment_id"], "source_start": item["source_start"], "source_end": item["source_end"],
        "priority": item["priority"], "reason": "；".join(item["suggestions"][:2]), "suggestions": item["suggestions"],
    } for item in report.get("lowest_confidence_segments", [])]
    settings = dict(timeline.settings_json or {})
    settings["behavioral_coach"] = {"status": "completed", "radar": report.get("radar", {}), "markers": markers, "advisory_only": True}
    # Keep existing confirmed Timeline immutable in shape; attach UI-only review flags to matching clips.
    confirmed = dict(settings.get("confirmed_timeline", {}))
    for track in confirmed.get("tracks", []):
        for clip in track.get("clips", []):
            overlaps = [marker for marker in markers if float(clip.get("source_start", 0)) < marker["source_end"] and float(clip.get("source_end", 0)) > marker["source_start"]]
            if overlaps:
                clip["review_flags"] = [*list(clip.get("review_flags", [])), "行為教練：建議審閱此段"]
                clip["creator_hints"] = [*list(clip.get("creator_hints", [])), overlaps[0]["reason"]]
    settings["confirmed_timeline"] = confirmed
    timeline.settings_json = settings
