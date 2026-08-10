TEMPLATE_SYSTEM_PROMPT = """
You are an expert short-form video director and video structure analyst.
Analyze the supplied reference video and extract a reusable filming template.

Rules:
1. Identify every meaningful scene. A scene is a continuous shot or a coherent
   narrative unit; do not create a scene for every individual sentence.
2. Preserve chronological order and use seconds from the beginning of the video.
3. Infer the visual and narrative function of each scene, not just what is visible.
4. Create practical dialogue prompts or filming instructions that a creator can follow.
5. Never invent exact dialogue unless it is clearly audible. Summarize or convert
   it into a reusable prompt instead.
6. Return JSON only. Do not include Markdown fences, commentary, or extra keys.
""".strip()


TEMPLATE_USER_PROMPT = """
Extract a reusable filming template from this reference video.

For each scene, provide:
- start and end time in seconds
- shot_type: camera framing or shot type, such as close_up, medium_shot,
  wide_shot, over_shoulder, screen_recording, b_roll, or unknown
- purpose: the core narrative purpose, such as hook, context, explanation,
  proof, transition, emotional_peak, call_to_action, or outro
- pace: slow, medium, or fast
- dialogue_prompt: a reusable line or speaking instruction for a new creator
- filming_instruction: practical guidance about framing, movement, action, or visuals

Also provide a concise template_name, summary, aspect_ratio, and overall_pacing.
Use the exact JSON schema supplied by the caller.
""".strip()


def template_response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["template_name", "summary", "aspect_ratio", "overall_pacing", "scenes"],
        "properties": {
            "template_name": {"type": "string"},
            "summary": {"type": "string"},
            "aspect_ratio": {"type": "string"},
            "overall_pacing": {"type": "string", "enum": ["slow", "medium", "fast", "mixed"]},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "start", "end", "shot_type", "purpose", "pace",
                        "dialogue_prompt", "filming_instruction",
                    ],
                    "properties": {
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "shot_type": {"type": "string"},
                        "purpose": {"type": "string"},
                        "pace": {"type": "string", "enum": ["slow", "medium", "fast"]},
                        "dialogue_prompt": {"type": "string"},
                        "filming_instruction": {"type": "string"},
                    },
                },
            },
        },
    }

