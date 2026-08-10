EDUCATION_KEYWORD_SYSTEM_PROMPT = """
You are an educational content editor. Identify only terms that materially improve learning:
advanced vocabulary, domain-specific terminology, named concepts, or grammar labels.
Do not select ordinary filler words, greetings, or basic vocabulary.
Return JSON only; do not use Markdown or extra keys.
""".strip()


EDUCATION_KEYWORD_USER_PROMPT = """
Read the transcript below. Select at most 12 teachable terms. For each term provide a brief,
learner-friendly explanation in the transcript language and classify it as advanced_vocabulary,
technical_term, proper_noun, or grammar_concept. Preserve the spelling found in the transcript.
""".strip()


def education_keyword_response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["keywords"],
        "properties": {
            "keywords": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["term", "category", "explanation", "importance"],
                    "properties": {
                        "term": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": ["advanced_vocabulary", "technical_term", "proper_noun", "grammar_concept"],
                        },
                        "explanation": {"type": "string"},
                        "importance": {"type": "number", "minimum": 0, "maximum": 100},
                    },
                },
            },
        },
    }

