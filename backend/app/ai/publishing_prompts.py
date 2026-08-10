PUBLISHING_METADATA_SYSTEM_PROMPT = """You are a senior video growth strategist. Produce compelling but truthful platform metadata from the supplied transcript, retained timeline and visual samples. Never invent claims, people, outcomes, prices, statistics, or sponsorships. Respect the requested language. Return JSON only, matching the supplied schema exactly."""

PUBLISHING_METADATA_USER_PROMPT = """Create:
- 3 distinct, high-intent titles (under 100 characters each),
- one YouTube description with a concise hook, value summary, SEO keywords naturally woven in, and 3-8 relevant hashtags,
- 5-12 standalone SEO keyword phrases,
- chronological YouTube chapters with an opening 00:00 chapter. Chapter titles must describe actual content.

Base every claim only on the supplied transcript and sampled frames. Avoid keyword stuffing, clickbait promises, and copyrighted brand claims."""


def publishing_metadata_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["titles", "description", "seo_keywords", "hashtags", "chapters"],
        "properties": {
            "titles": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string", "maxLength": 100}},
            "description": {"type": "string", "maxLength": 5000},
            "seo_keywords": {"type": "array", "minItems": 5, "maxItems": 12, "items": {"type": "string"}},
            "hashtags": {"type": "array", "minItems": 3, "maxItems": 8, "items": {"type": "string", "pattern": "^#"}},
            "chapters": {
                "type": "array", "minItems": 1,
                "items": {"type": "object", "additionalProperties": False, "required": ["start_time", "title"], "properties": {"start_time": {"type": "number", "minimum": 0}, "title": {"type": "string", "maxLength": 100}}},
            },
        },
    }
