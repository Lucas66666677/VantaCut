import base64
from pathlib import Path
from typing import Any

from app.ai.providers.base import ASRProvider, MultimodalProvider, TextAnalysisProvider
from app.ai.providers.schemas import Transcript


class OpenAIMultimodalProvider(MultimodalProvider, TextAnalysisProvider):
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def name(self) -> str:
        return "openai"

    def analyze_video(
        self,
        video_uri: str,
        prompt: str,
        *,
        response_schema: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Auto-Narrative deliberately supplies a small chronological frame set. Keeping the
        # loading here means callers never need OpenAI-specific image payload logic.
        if context and context.get("task") == "auto_narrative_visual_understanding":
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY is required for Auto-Narrative visual understanding")
            frame_paths = [Path(str(item)) for item in context.get("frame_paths", [])]
            if not frame_paths or any(not path.is_file() for path in frame_paths):
                raise RuntimeError("Auto-Narrative requires readable sampled frame paths")
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI

            content: list[dict[str, Any]] = [{"type": "text", "text": prompt + "\nFrames are in chronological order."}]
            for path in frame_paths:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
            model = ChatOpenAI(model=self.model, api_key=self.api_key, temperature=0)
            response = model.with_structured_output(response_schema or {}, method="json_schema", strict=True).invoke([
                SystemMessage(content="Return only the requested structured visual analysis."), HumanMessage(content=content),
            ])
            return dict(response)
        # TODO: upload full video with a provider-native video API when required.
        # response_schema should be passed as a strict structured-output schema.
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
        # TODO: call OpenAI Responses API with strict JSON structured output.
        return {"keywords": []}

    def generate_structured_json(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for autonomous scripting")
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model=self.model, api_key=self.api_key, temperature=0.35)
        response = model.with_structured_output(response_schema, method="json_schema", strict=True).invoke([
            SystemMessage(content=system_prompt), HumanMessage(content=user_prompt),
        ])
        return dict(response)


class OpenAIWhisperProvider(ASRProvider):
    def __init__(self, api_key: str | None = None, model: str = "whisper-1") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def name(self) -> str:
        return "openai_whisper"

    def transcribe(
        self,
        audio_uri: str,
        *,
        language: str | None = None,
        word_timestamps: bool = True,
    ) -> Transcript:
        # TODO: download/open audio_uri and call the OpenAI transcription endpoint
        # with response_format=json/verbose_json and timestamp granularities.
        return Transcript(
            language=language,
            text="",
            segments=[],
            provider=self.name,
            model=self.model,
            metadata={"audio_uri": audio_uri, "word_timestamps": word_timestamps, "_mock": True},
        )
