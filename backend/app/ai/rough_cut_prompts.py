FINAL_CUT_SYSTEM_PROMPT = """
You are a senior video editor reviewing short-form educational and knowledge-sharing video.
Score every supplied transcript segment using its sampled video frames and the supplied reference template.

Evaluate only the evidence provided. Do not invent visual defects or missing context.
The optional speaker_state_features are landmark/pose-derived delivery observations, not identity,
emotion, personality, or health claims. Use them only as secondary evidence and never make a
biometric inference. Low delivery scores require a user-review hint, not an automatic removal.
Return JSON only, with no Markdown or extra keys.
""".strip()


FINAL_CUT_USER_PROMPT = """
For each segment, score 0 to 100:
- semantic_completeness: whether it contains a complete, useful thought
- presentation_naturalness: whether the speaker looks natural, engaged and visually clear;
  penalize clearly distracted eye contact, unnatural pauses, or visibly rigid delivery only when evidenced
- template_alignment: whether its role, pacing and framing support the supplied template

Use speaker_state_features, when present, to corroborate visible eye contact, posture or delivery
issues. Do not penalize a calm speaking style merely for limited gestures.

Then recommend keep or remove and give a concise reason. Use every segment_id exactly once.
""".strip()


def final_cut_response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["segment_scores"],
        "properties": {
            "segment_scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "segment_id", "semantic_completeness", "presentation_naturalness",
                        "template_alignment", "recommended_action", "reason",
                    ],
                    "properties": {
                        "segment_id": {"type": "string"},
                        "semantic_completeness": {"type": "number", "minimum": 0, "maximum": 100},
                        "presentation_naturalness": {"type": "number", "minimum": 0, "maximum": 100},
                        "template_alignment": {"type": "number", "minimum": 0, "maximum": 100},
                        "recommended_action": {"type": "string", "enum": ["keep", "remove"]},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    }
