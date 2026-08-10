"""Explainable first-three-seconds diagnostics and reversible Hook rescue edits."""
from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AIAnalysis, Clip, MediaAsset, Timeline, TrackType
from app.services.agent_timeline_versions import clone_timeline_version, materialise_confirmed_timeline


HOOK_WINDOW_SECONDS = 3.0


def _main_clips(timeline: Timeline) -> list[tuple[Clip, float, float]]:
    cursor = 0.0; result: list[tuple[Clip, float, float]] = []
    for clip in sorted((item for item in timeline.clips if item.track == TrackType.MAIN_VIDEO and item.enabled), key=lambda item: item.order_index):
        duration = max(0.0, float(clip.source_end) - float(clip.source_start))
        if duration:
            result.append((clip, cursor, cursor + duration)); cursor += duration
    return result


def _asset_motion(asset: MediaAsset | None) -> float:
    if asset is None:
        return .2
    metadata = dict(asset.metadata_json or {}); visual = dict(metadata.get("visual_features") or {})
    value = visual.get("motion", metadata.get("visual_motion", .2))
    try:
        value = float(value)
        return max(0.0, min(1.0, value if value <= 1 else value / 10))
    except (TypeError, ValueError):
        return .2


def _opening_subtitles(settings: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in dict(settings.get("subtitles") or {}).get("items", []) if isinstance(item, dict) and float(item.get("start_time", 99)) < HOOK_WINDOW_SECONDS]


def _audio_impact(settings: dict[str, Any], *, has_voice: bool) -> bool:
    auto_sfx = dict(settings.get("auto_sfx") or {})
    has_sfx = any(float(event.get("timeline_start", 99)) < HOOK_WINDOW_SECONDS for event in auto_sfx.get("events", []) if isinstance(event, dict))
    return has_voice or has_sfx or bool(auto_sfx.get("bgm_asset_id")) or bool(dict(settings.get("one_click") or {}).get("bgm_asset_id")) or bool(dict(settings.get("beat_sync_montage") or {}).get("bgm_asset_id"))


def _analysis_score(analysis: AIAnalysis, source_start: float, source_end: float) -> float:
    best = float(analysis.confidence or .45)
    result = dict(analysis.result_json or {})
    for item in dict(result.get("final_timeline") or {}).get("segments", []):
        if not isinstance(item, dict):
            continue
        start, end = float(item.get("source_start", 0)), float(item.get("source_end", 0))
        if end <= source_start or start >= source_end:
            continue
        evidence = dict(item.get("evidence") or {})
        values = [float(evidence.get(key, 0)) / 100 for key in ("semantic_completeness", "presentation_naturalness", "template_alignment") if evidence.get(key) is not None]
        if values:
            best = max(best, sum(values) / len(values))
    return max(0.0, min(1.0, best))


def _best_highlight(db: Session, timeline: Timeline) -> dict[str, float | str]:
    clips = _main_clips(timeline)
    if not clips:
        raise ValueError("Timeline has no enabled main-video clips")
    asset_ids = [clip.source_asset_id for clip, _, _ in clips]
    assets = {asset.id: asset for asset in db.scalars(select(MediaAsset).where(MediaAsset.id.in_(asset_ids))).all()}
    analyses: dict[object, AIAnalysis] = {}
    for item in db.scalars(select(AIAnalysis).where(AIAnalysis.media_asset_id.in_(asset_ids), AIAnalysis.status == "completed").order_by(AIAnalysis.created_at.desc())).all():
        analyses.setdefault(item.media_asset_id, item)
    best: tuple[float, Clip, float] | None = None
    for clip, output_start, _ in clips:
        duration = float(clip.source_end) - float(clip.source_start)
        if duration <= .25:
            continue
        score = _asset_motion(assets.get(clip.source_asset_id)) * .55 + _analysis_score(analyses[clip.source_asset_id], float(clip.source_start), float(clip.source_end)) * .45 if clip.source_asset_id in analyses else _asset_motion(assets.get(clip.source_asset_id))
        if best is None or score > best[0]:
            best = (score, clip, duration)
    if best is None:
        _, clip, _ = 0.0, clips[0][0], max(.25, float(clips[0][0].source_end) - float(clips[0][0].source_start))
    else:
        _, clip, _ = best
    duration = min(2.0, float(clip.source_end) - float(clip.source_start))
    output_start = next((start for item, start, _ in clips if item.id == clip.id), 0.0)
    return {"source_asset_id": str(clip.source_asset_id), "source_start": float(clip.source_start), "source_end": round(float(clip.source_start) + duration, 3), "timeline_start": round(output_start, 3), "duration": round(duration, 3), "score": round((best[0] if best else .2) * 100, 1)}


def analyze_opening_hook(db: Session, timeline: Timeline) -> dict[str, Any]:
    settings = dict(timeline.settings_json or {}); clips = _main_clips(timeline)
    if not clips:
        raise ValueError("Timeline has no enabled main-video clips")
    opening = [(clip, start, end) for clip, start, end in clips if start < HOOK_WINDOW_SECONDS]
    cuts = max(0, sum(1 for _, start, _ in opening if start > .001))
    cut_rate = cuts / HOOK_WINDOW_SECONDS
    weighted_motion = sum(_asset_motion(clip.source_asset) * max(0.0, min(end, HOOK_WINDOW_SECONDS) - start) for clip, start, end in opening) / max(.01, min(HOOK_WINDOW_SECONDS, opening[-1][2]))
    subtitles = _opening_subtitles(settings)
    has_kinetic = any(any(str(word.get("animation_preset", "none")) != "none" for word in item.get("words", []) if isinstance(word, dict)) for item in subtitles)
    has_voice = bool(subtitles)
    has_audio = _audio_impact(settings, has_voice=has_voice)
    static = cut_rate < .34 and weighted_motion < .18
    score = min(100, round(cut_rate / .66 * 24 + weighted_motion * 32 + (20 if has_kinetic else 0) + (19 if has_voice else 0) + (5 if has_audio else 0)))
    warnings: list[str] = []; suggestions: list[str] = []
    if static and not has_audio:
        warnings.append("開場前三秒接近單一靜態畫面且沒有可辨識的人聲或音效，容易形成高流失風險。")
        suggestions.append("使用「幫我優化開場」前插高動態 2 秒片段，並以黑白轉彩色與 Boom 建立懸念。")
    if not has_kinetic:
        warnings.append("開場沒有動態花字。")
        suggestions.append("在第一句的關鍵詞加入彈跳或卡拉 OK 字幕，讓觀眾更快接收主題。")
    if not has_voice:
        warnings.append("開場前三秒沒有已對齊的 ASR 字幕，無法確認是否有人聲。")
        suggestions.append("加入一句明確的問題、反差或結果預告，並重新產生字幕。")
    traffic = "red" if static and not has_audio else "yellow" if score < 62 else "green"
    metrics = [
        {"label": "畫面切換", "value": f"{cut_rate:.2f} cuts/s", "passed": cut_rate >= .34},
        {"label": "畫面動態", "value": f"{round(weighted_motion * 100)} / 100", "passed": weighted_motion >= .18},
        {"label": "動態花字", "value": "已偵測" if has_kinetic else "未偵測", "passed": has_kinetic},
        {"label": "人聲／聲音", "value": "已偵測" if has_audio else "未偵測", "passed": has_audio},
    ]
    return {"score": score, "traffic_light": traffic, "cut_rate_per_second": round(cut_rate, 3), "visual_motion_score": round(weighted_motion * 100, 1), "has_kinetic_captions": has_kinetic, "has_voice": has_voice, "has_audio_impact": has_audio, "is_static_opening": static, "metrics": metrics, "warnings": warnings, "suggestions": suggestions, "highlight_candidate": _best_highlight(db, timeline)}


def apply_hook_rescue(db: Session, timeline: Timeline) -> Timeline:
    candidate = _best_highlight(db, timeline)
    target = clone_timeline_version(db, timeline, label="黃金 Hook 救援版")
    original_orders = {clip.id: clip.order_index for clip in target.clips}
    for clip in target.clips:
        clip.order_index += 10_000
    db.flush()
    for clip in target.clips:
        clip.order_index = original_orders[clip.id] + 1 if clip.track == TrackType.MAIN_VIDEO else original_orders[clip.id] + 10_000
    rescue_clip = Clip(timeline=target, source_asset_id=UUID(str(candidate["source_asset_id"])), source_start=candidate["source_start"], source_end=candidate["source_end"], track=TrackType.MAIN_VIDEO, z_index=0, audio_enabled=True, audio_effects=[], order_index=0, enabled=True)
    db.add(rescue_clip); db.flush()
    materialise_confirmed_timeline(target)
    settings = copy.deepcopy(target.settings_json or {})
    confirmed = dict(settings.get("confirmed_timeline") or {})
    tracks = list(confirmed.get("tracks") or [])
    tracks.append({"id": "hook-rescue-sfx", "type": "audio_overlay", "z_index": 30, "clips": [{"id": "hook-synthetic-boom", "timeline_start": .38, "source_start": 0, "source_end": .45, "action": "keep", "audio_enabled": True, "kind": "synthetic_boom", "reason": "Hook rescue impact sound"}]})
    rescue = {"status": "applied", "inserted_clip_id": str(rescue_clip.id), "highlight": candidate, "grayscale_seconds": .35, "color_fade_seconds": .55, "synthetic_boom": {"timeline_start": .38, "duration": .45, "frequency_hz": 54}}
    settings["confirmed_timeline"] = {**confirmed, "tracks": tracks}
    settings["multitrack_timeline"] = settings["confirmed_timeline"]
    settings["hook_rescue"] = rescue
    target.settings_json = settings
    db.query(Timeline).filter(Timeline.project_id == timeline.project_id, Timeline.is_current.is_(True)).update({Timeline.is_current: False}, synchronize_session=False)
    target.is_current = True
    return target


def build_hook_boom_mix_command(*, video_path: str, output_path: str, timeline_start: float, duration: float, frequency_hz: float) -> list[str]:
    delay_ms = max(0, round(timeline_start * 1000))
    return ["ffmpeg", "-y", "-i", video_path, "-f", "lavfi", "-i", f"sine=frequency={frequency_hz:.1f}:sample_rate=48000:duration={duration:.3f}", "-filter_complex", f"[1:a]volume=0.9,afade=t=out:st={max(.01, duration - .18):.3f}:d=.18,adelay={delay_ms}:all=1[boom];[0:a][boom]amix=inputs=2:duration=first:normalize=0[mix]", "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-shortest", output_path]
