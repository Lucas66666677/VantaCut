BGM_SYSTEM_PROMPT = """
You are a music supervisor for short-form video. Analyze the supplied sampled frames and final-edit pacing.
Assess color palette, lighting, composition, narrative energy, emotional tone, and any distinctive aesthetic.
Consider nuanced moods as well as common styles: for example cool Nordic cinematic restraint, bright casual vlog,
editorial minimalism, intimate documentary, or unconventional/indie tension.

Recommend only searchable music descriptors. Do not claim that a specific song exists.
Return JSON only, with no Markdown, prose, or extra keys.
""".strip()


BGM_USER_PROMPT = """
Recommend background-music search terms for this final edit. Use the supplied frame timestamps and pace metadata.

Return:
- mood: a concise overall emotional/aesthetic description
- tempo: an object with integer min_bpm and max_bpm
- search_keywords: 3 to 5 English music-library search phrases, such as "Cinematic ambient" or "Upbeat synth"
""".strip()


def bgm_response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["mood", "tempo", "search_keywords"],
        "properties": {
            "mood": {"type": "string"},
            "tempo": {
                "type": "object",
                "additionalProperties": False,
                "required": ["min_bpm", "max_bpm"],
                "properties": {
                    "min_bpm": {"type": "integer", "minimum": 40, "maximum": 220},
                    "max_bpm": {"type": "integer", "minimum": 40, "maximum": 220},
                },
            },
            "search_keywords": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {"type": "string"},
            },
        },
    }

