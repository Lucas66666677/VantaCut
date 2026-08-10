from typing import Any

from app.ai.providers.base import MultimodalProvider, TextAnalysisProvider


class GeminiVideoProvider(MultimodalProvider, TextAnalysisProvider):
    def __init__(self, api_key: str | None = None, model: str = "gemini-video-model") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def name(self) -> str:
        return "gemini"

    def analyze_video(
        self,
        video_uri: str,
        prompt: str,
        *,
        response_schema: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # TODO: upload video_uri with Gemini Files API, then request JSON output.
        # Keep the provider contract independent from the Google SDK response type.
        if context and context.get("task") == "rough_cut_scoring":
            return {
                "segment_scores": [
                    {
                        "segment_id": segment["id"],
                        "semantic_completeness": 75,
                        "presentation_naturalness": 75,
                        "template_alignment": 75,
                        "recommended_action": "keep",
                        "reason": "Mock multimodal score for local development.",
                    }
                    for segment in context.get("segments", [])
                ]
            }
        if context and context.get("task") == "bgm_recommendation":
            return {
                "mood": "bright, focused educational vlog",
                "tempo": {"min_bpm": 100, "max_bpm": 118},
                "search_keywords": ["Upbeat synth", "Bright corporate pop", "Minimal electronic", "Positive vlog"],
            }
        if context and context.get("task") == "soundscape_planning":
            duration = float(context.get("output_duration", 8))
            return {"events": [{"id": "ambient-bed", "kind": "ambient", "generation_prompt": "subtle cinematic environmental ambience", "start_time": 0, "end_time": duration, "gain_db": -24, "position": {"x": 0, "y": 0, "z": 0}}]}
        return {
            "scenes": [{
                "start": 0,
                "end": 1,
                "shot_type": "unknown",
                "purpose": "hook",
                "pace": "medium",
                "dialogue_prompt": "Introduce the topic clearly.",
                "filming_instruction": "Keep the subject centered and well lit.",
            }],
            "template_name": "Mock reference template",
            "summary": "Mock provider response for local development.",
            "aspect_ratio": "unknown",
            "overall_pacing": "medium",
        }

    def extract_education_keywords(
        self,
        transcript_text: str,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        # TODO: call Gemini with JSON schema / response MIME type application/json.
        return {"keywords": []}

    def generate_structured_json(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]:
        # Keep the Gemini SDK isolated until it is enabled in the production image.
        # The contract is identical to OpenAI: response MIME type application/json
        # and response_schema must be forwarded to the Gemini request.
        raise NotImplementedError(
            "Gemini autonomous scripting adapter is not installed; set AI_DIRECTOR_PROVIDER=openai or mock"
        )
