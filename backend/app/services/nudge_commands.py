"""Constrained natural-language-to-timeline nudge planning with deterministic fallbacks."""
from __future__ import annotations

from typing import Any

from app.ai.providers.base import TextAnalysisProvider
from app.schemas.nudge import NudgeCommand


NUDGE_SYSTEM_PROMPT = """You are a conservative video-editing assistant. Convert an imprecise user request into only the allowed edit operations: adjust_visual, set_speed_curve, set_transform, enable_beat_sync. Never delete media or alter source in/out points. Quantify adverbs precisely: 'slightly brighten' is exposure_delta +0.5; 'too bright, make it a little darker' is exposure_delta -0.2. Keep saturation_delta and contrast_delta in [-30,30], exposure_delta in [-2,2], and multiplier/scale in [0.8,1.3]. Return Chinese explanation text and strict JSON only."""

NUDGE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["commands", "explanation"],
    "properties": {
        "commands": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["operation", "target_clip_ids", "parameters"], "properties": {
            "operation": {"type": "string", "enum": ["adjust_visual", "set_speed_curve", "set_transform", "enable_beat_sync"]},
            "target_clip_ids": {"type": "array", "items": {"type": "string"}}, "parameters": {"type": "object"},
        }}},
        "explanation": {"type": "string"},
    },
}


def _amount(text: str, *, brightening: bool) -> float:
    if "一點點" in text or "有點" in text:
        return 0.2
    if "稍微" in text or "微微" in text:
        return 0.5 if brightening else 0.2
    if "很" in text or "大幅" in text:
        return 1.0 if brightening else 0.6
    return 0.5 if brightening else 0.2


def fallback_nudge_plan(instruction: str, target_clip_ids: list[str]) -> tuple[list[NudgeCommand], str]:
    """Safe local interpretation when a provider is unavailable or returns invalid JSON."""
    text, targets = instruction.strip().lower(), target_clip_ids
    if any(token in text for token in ("活力", "有精神", "活潑", "energetic")):
        return [
            NudgeCommand(operation="adjust_visual", target_clip_ids=targets, parameters={"saturation_delta": 15}),
            NudgeCommand(operation="set_transform", target_clip_ids=targets, parameters={"scale": 1.1}),
            NudgeCommand(operation="set_speed_curve", target_clip_ids=targets, parameters={"multiplier": 1.1}),
            NudgeCommand(operation="enable_beat_sync", target_clip_ids=targets, parameters={"enabled": True}),
        ], "✨ 已為您套用：飽和度 +15%、1.1x 微動態與節拍卡點，讓畫面更有活力。"
    if any(token in text for token in ("調亮", "亮一點", "提亮", "brighten")):
        amount = _amount(text, brightening=True)
        return [NudgeCommand(operation="adjust_visual", target_clip_ids=targets, parameters={"exposure_delta": amount})], f"✨ 已為您套用：曝光度 +{amount:g}，保留原始素材不變。"
    if any(token in text for token in ("太亮", "暗一點", "調暗", "darker")):
        amount = _amount(text, brightening=False)
        return [NudgeCommand(operation="adjust_visual", target_clip_ids=targets, parameters={"exposure_delta": -amount})], f"✨ 已為您套用：曝光度 {(-amount):g}，壓低過亮區域。"
    if any(token in text for token in ("對比", "更立體")):
        return [NudgeCommand(operation="adjust_visual", target_clip_ids=targets, parameters={"contrast_delta": 8})], "✨ 已為您套用：微幅提高對比度，讓主體更清晰。"
    return [], "我暫時無法安全量化這個指令；試試「稍微調亮」或「讓這段更有活力」。"


def _bounded(value: Any, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return 0.0


def sanitise_nudge_plan(raw: dict[str, Any], target_clip_ids: list[str]) -> tuple[list[NudgeCommand], str] | None:
    """Treat LLM JSON as untrusted: whitelist operations, targets, and numeric ranges."""
    allowed_targets, commands = set(target_clip_ids), []
    for item in raw.get("commands", []):
        if not isinstance(item, dict) or item.get("operation") not in {"adjust_visual", "set_speed_curve", "set_transform", "enable_beat_sync"}:
            continue
        targets = [str(value) for value in item.get("target_clip_ids", []) if str(value) in allowed_targets] or target_clip_ids
        if not targets:
            continue
        parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        operation = item["operation"]
        if operation == "adjust_visual":
            cleaned = {key: _bounded(parameters.get(key), -2 if key == "exposure_delta" else -30, 2 if key == "exposure_delta" else 30) for key in ("saturation_delta", "contrast_delta", "exposure_delta") if key in parameters}
            if not cleaned:
                continue
        elif operation == "set_speed_curve":
            cleaned = {"multiplier": _bounded(parameters.get("multiplier", 1), .8, 1.3)}
        elif operation == "set_transform":
            cleaned = {"scale": _bounded(parameters.get("scale", 1), .8, 1.3)}
        else:
            cleaned = {"enabled": bool(parameters.get("enabled", True))}
        commands.append(NudgeCommand(operation=operation, target_clip_ids=targets, parameters=cleaned))
    explanation = str(raw.get("explanation", "")).strip()
    return (commands, explanation) if commands and explanation else None


def plan_nudge(provider: TextAnalysisProvider, *, instruction: str, target_clip_ids: list[str]) -> tuple[list[NudgeCommand], str, str]:
    user_prompt = f"NUDGE_COMMAND_PLAN\nUSER_INSTRUCTION: {instruction}\nALLOWED_TARGET_CLIP_IDS: {target_clip_ids}\nReturn an edit plan for only these clip IDs."
    try:
        parsed = sanitise_nudge_plan(provider.generate_structured_json(system_prompt=NUDGE_SYSTEM_PROMPT, user_prompt=user_prompt, response_schema=NUDGE_SCHEMA), target_clip_ids)
        if parsed:
            return *parsed, provider.name
    except Exception:
        pass
    commands, explanation = fallback_nudge_plan(instruction, target_clip_ids)
    return commands, explanation, "heuristic_fallback"
