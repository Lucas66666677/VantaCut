LECTURAS_SYSTEM_PROMPT = """You are Lecturas, an educational co-host who improves comprehension without taking over a lesson.
Review the supplied timestamped transcript and sampled video context. Identify only genuine logical jumps, undefined
technical terms, safety-critical caveats, or useful recap moments. Do not invent facts, demonstrations, citations,
or learner confusion that is not supported by the supplied material. Generate at most the requested number of short,
spoken interventions. Each intervention must be either a concise question that invites reflection or a concise summary.
Never interrupt mid-sentence, repeat the lecturer, shame the lecturer, or make an investment, medical, or legal claim.
Return JSON only and match the schema exactly."""


def lecturas_user_prompt(*, transcript: list[dict[str, object]], assistant_name: str, max_interventions: int) -> str:
    return f"""LECTURAS_ASSISTANT_NAME: {assistant_name}
MAX_INTERVENTIONS: {max_interventions}
TIMESTAMPED_TRANSCRIPT: {transcript}

For every intervention, choose an anchor_output_time at a natural boundary after the relevant explanation.
The script must fit 2-15 seconds of neutral speech. Use presentation_mode='freeze' for a dense concept that deserves
full attention; use 'pip' only for a very short recap that can coexist with the lecturer. Include a factual rationale
and a confidence between 0 and 1."""


def lecturas_response_schema() -> dict[str, object]:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["interventions"],
        "properties": {"interventions": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "anchor_output_time", "kind", "script", "rationale", "presentation_mode", "confidence"],
            "properties": {
                "id": {"type": "string"}, "anchor_output_time": {"type": "number", "minimum": 0},
                "kind": {"type": "string", "enum": ["question", "summary"]}, "script": {"type": "string", "minLength": 2, "maxLength": 280},
                "rationale": {"type": "string", "minLength": 2, "maxLength": 400},
                "presentation_mode": {"type": "string", "enum": ["freeze", "pip"]}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        }}},
    }
