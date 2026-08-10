"""Strict prompt contract for timestamped English-teaching review overlays."""

LANGUAGE_REVIEW_SYSTEM_PROMPT = """You are a careful English-language educator reviewing a timestamped speaking transcript.
Return teaching suggestions, never an official IELTS result. Follow the supplied word indices exactly.
Identify only high-confidence grammar, tense, voice, agreement, word-choice, or clearly basic-vocabulary issues.
Do not mark harmless accent variation, informal but grammatical speech, dialect, or stylistic preference as an error.
For pronunciation, use only the supplied ASR-confidence/prosody proxy and state it is provisional; never claim a phoneme-level diagnosis.
Give concise learner-friendly Traditional Chinese explanations. Suggest advanced synonyms only when they preserve the speaker's intended meaning.
Return JSON only, exactly matching the schema."""


def language_review_response_schema() -> dict[str, object]:
    issue = {
        "type": "object", "additionalProperties": False,
        "required": ["id", "word_start_index", "word_end_index", "category", "original_text", "correction", "explanation", "confidence", "synonyms"],
        "properties": {
            "id": {"type": "string"}, "word_start_index": {"type": "integer", "minimum": 0}, "word_end_index": {"type": "integer", "minimum": 0},
            "category": {"type": "string", "enum": ["tense", "voice", "agreement", "word_choice", "basic_vocabulary"]},
            "original_text": {"type": "string"}, "correction": {"type": "string"}, "explanation": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "synonyms": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False, "required": ["term", "reason"], "properties": {"term": {"type": "string"}, "reason": {"type": "string"}}}},
        },
    }
    score = {
        "type": "object", "additionalProperties": False, "required": ["band_estimate", "confidence", "evidence", "improvement"],
        "properties": {"band_estimate": {"type": "number", "minimum": 0, "maximum": 9}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "evidence": {"type": "string"}, "improvement": {"type": "string"}},
    }
    return {"type": "object", "additionalProperties": False, "required": ["issues", "scores", "overall_feedback", "disclaimer"], "properties": {
        "issues": {"type": "array", "maxItems": 30, "items": issue},
        "scores": {"type": "object", "additionalProperties": False, "required": ["fluency_coherence", "lexical_resource", "grammatical_range_accuracy", "pronunciation"], "properties": {name: score for name in ("fluency_coherence", "lexical_resource", "grammatical_range_accuracy", "pronunciation")}},
        "overall_feedback": {"type": "string"}, "disclaimer": {"type": "string"},
    }}


def build_language_review_prompt(*, words: list[dict[str, object]], fluency_features: dict[str, object], target: str) -> str:
    return f"""TARGET: {target}
Review the following ASR word tokens. `word_start_index` and `word_end_index` must point to this list inclusively.
Use only indices that exist. `original_text` must exactly reproduce the selected token span.
Do not produce an issue when confidence is below 0.78. Keep corrections brief enough for an on-video card.

ASR_WORDS: {words}

FLUENCY_AND_PRONUNCIATION_PROXY: {fluency_features}

Score each of the four IELTS-aligned dimensions from 0.0 to 9.0 as a provisional teaching estimate.
The pronunciation score must explicitly rely on the proxy, not on claims of phoneme analysis."""
