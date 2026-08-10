ACADEMIC_NARRATIVE_SYSTEM_PROMPT = """You are an academic video editor for applicants to research-intensive European STEM programmes.
Build a clear evidence-led personal pitch, not a marketing vlog. Use the following target rhythm as a flexible guideline:
Motivation 20-25%, Methodology 35-40%, Results 25-30%, Future Works 10-15%. Prefer precise claims, reproducible
methods, concrete evidence, limitations, and a realistic next research question. Do not invent publications, scores,
experimental results, affiliations, admissions criteria, or causal claims. Preserve the applicant's terminology.
Return strict JSON only."""


def academic_narrative_prompt(*, transcript: list[dict[str, object]], target_programmes: list[str]) -> str:
    return f"""ACADEMIC_TRANSCRIPT: {transcript}
TARGET_PROGRAMMES: {target_programmes}

Create four ordered sections exactly: motivation, methodology, results, future_works.
For each, give target_percent, evidence_or_visuals to retain, concise narration guidance, and an editorial risk to avoid.
Use only evidence contained in the transcript. Flag a missing-results section rather than fabricating it."""


def academic_narrative_response_schema() -> dict[str, object]:
    return {"type": "object", "additionalProperties": False, "required": ["sections", "overall_note"], "properties": {
        "sections": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "object", "additionalProperties": False,
            "required": ["kind", "target_percent", "evidence_or_visuals", "narration_guidance", "editorial_risk"],
            "properties": {"kind": {"type": "string", "enum": ["motivation", "methodology", "results", "future_works"]}, "target_percent": {"type": "number", "minimum": 0, "maximum": 100}, "evidence_or_visuals": {"type": "array", "items": {"type": "string"}, "maxItems": 5}, "narration_guidance": {"type": "string"}, "editorial_risk": {"type": "string"}}}},
        "overall_note": {"type": "string"},
    }}
