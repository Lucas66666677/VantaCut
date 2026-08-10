"""Strict prompts and JSON contracts for the Auto-Narrative pipeline."""
from __future__ import annotations


AUTO_NARRATIVE_VISION_SYSTEM = """You are a precise video logger. Inspect the supplied sampled frames from one source video.
Describe only visible actions, places, objects, and mood. Do not infer identities, private facts, or events not visible.
Return strict JSON matching the provided schema. Choose one compelling usable time range from the source video."""

AUTO_NARRATIVE_SCRIPT_SYSTEM = """You are an expert short-form Vlogger and editor. Create a coherent 20-45 second narration-led vlog from the ordered visual notes.
Keep every line grounded in the supplied visuals. The requested tone may be funny or emotional, but never invent facts.
Use the clips in their supplied order, write concise spoken Traditional Chinese, and return only strict JSON matching the schema.
Each beat must point to a supplied asset_id and should fit its target duration."""


def vision_response_schema() -> dict[str, object]:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["asset_id", "summary", "moments", "best_source_start", "best_source_end", "mood", "confidence"],
        "properties": {
            "asset_id": {"type": "string"}, "summary": {"type": "string"},
            "moments": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
            "best_source_start": {"type": "number", "minimum": 0}, "best_source_end": {"type": "number", "exclusiveMinimum": 0},
            "mood": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def narrative_response_schema() -> dict[str, object]:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["title", "summary", "script", "beats"],
        "properties": {
            "title": {"type": "string"}, "summary": {"type": "string"}, "script": {"type": "string"},
            "beats": {
                "type": "array", "minItems": 1, "maxItems": 10,
                "items": {"type": "object", "additionalProperties": False,
                    "required": ["id", "asset_id", "narration", "source_start", "source_end", "target_duration_seconds", "visual_role"],
                    "properties": {
                        "id": {"type": "string"}, "asset_id": {"type": "string"}, "narration": {"type": "string"},
                        "source_start": {"type": "number", "minimum": 0}, "source_end": {"type": "number", "exclusiveMinimum": 0},
                        "target_duration_seconds": {"type": "number", "exclusiveMinimum": .3, "maximum": 12},
                        "visual_role": {"type": "string", "enum": ["hook", "journey", "detail", "payoff", "closing"]},
                    },
                },
            },
        },
    }
