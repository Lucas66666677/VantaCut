SOUNDSCAPE_SYSTEM_PROMPT = """
You are a film supervising sound editor. Inspect only the supplied video frames and pacing data.
Create a restrained, editorially useful soundscape plan: ambience first, then clearly visible or
strongly implied foley such as footsteps. Do not invent dangerous events, dialogue, brands, or
specific locations not visually evidenced. Avoid music and do not mask spoken dialogue.
Every event must be placed in output-timeline seconds and a normalized 3D coordinate: x is left(-1)
to right(1), y is rear(-1) to front(1), and z is floor(-1) to ceiling(1).
Return JSON only, with no Markdown and no extra keys.
""".strip()


SOUNDSCAPE_USER_PROMPT = """
Infer terrain, weather, enclosed/open space, movement, and visible contact actions. For wide cold
landscapes, prefer a sparse wind bed with low ambient resonance; for a visible walking person, add
subtle, timed footsteps only when supported by the frames. Each event must include a concise
generation_prompt, start_time, end_time, kind, gain_db, and position. Keep the plan conservative.
""".strip()


def soundscape_response_schema() -> dict[str, object]:
    position = {
        "type": "object", "additionalProperties": False, "required": ["x", "y", "z"],
        "properties": {
            "x": {"type": "number", "minimum": -1, "maximum": 1},
            "y": {"type": "number", "minimum": -1, "maximum": 1},
            "z": {"type": "number", "minimum": -1, "maximum": 1},
        },
    }
    return {
        "type": "object", "additionalProperties": False, "required": ["events"],
        "properties": {
            "events": {
                "type": "array", "maxItems": 32,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "kind", "generation_prompt", "start_time", "end_time", "gain_db", "position"],
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["wind", "ambient", "footsteps", "water", "traffic", "room_tone", "other"]},
                        "generation_prompt": {"type": "string", "minLength": 3, "maxLength": 500},
                        "start_time": {"type": "number", "minimum": 0},
                        "end_time": {"type": "number", "exclusiveMinimum": 0},
                        "gain_db": {"type": "number", "minimum": -48, "maximum": 0},
                        "position": position,
                    },
                },
            },
        },
    }
