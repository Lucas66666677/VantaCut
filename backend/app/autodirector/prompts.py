from __future__ import annotations

from app.autodirector.contracts import DocumentaryScript


SCRIPTER_SYSTEM_PROMPT = """You are Scripter Agent in an autonomous documentary studio.
Write a concise, factual, cinematic narration plan that can be illustrated ONLY by footage
retrieved from the creator's historical media library. Do not invent claims, locations,
weather readings, events, shots, or interview quotes. Every beat must have a retrieval-ready
visual_query made of concrete visible nouns/actions, and narration that is safe to read aloud.
Return JSON that exactly conforms to the supplied schema."""


def scripter_user_prompt(*, topic: str, brief: dict[str, object]) -> str:
    return (
        f"TOPIC: {topic.strip()}\n"
        f"CREATIVE_BRIEF: {brief}\n\n"
        "Create 3–8 beats. Match the requested duration if present, otherwise make a 90-second documentary."
    )


def script_schema() -> dict[str, object]:
    return DocumentaryScript.model_json_schema()
