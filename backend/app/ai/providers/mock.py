import time
import re
from typing import Any

from app.ai.providers.base import ASRProvider, MultimodalProvider, TextAnalysisProvider
from app.ai.providers.schemas import Transcript, TranscriptSegment, WordTimestamp


class MockMultimodalProvider(MultimodalProvider, TextAnalysisProvider):
    """Deterministic development-only provider that preserves production response contracts."""

    def __init__(self, delay_seconds: float = 0.35) -> None:
        self.delay_seconds = max(0.0, delay_seconds)

    @property
    def name(self) -> str:
        return "mock_multimodal"

    def _delay(self) -> None:
        time.sleep(self.delay_seconds)

    def analyze_video(
        self,
        video_uri: str,
        prompt: str,
        *,
        response_schema: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._delay()
        if context and context.get("task") == "semantic_keyframe_caption":
            return {"scene": "outdoor travel scene", "objects": ["person", "road", "landscape"], "people_emotion": "curious", "action": "walking through the scene"}
        if context and context.get("task") == "auto_narrative_visual_understanding":
            duration = float(context.get("duration_seconds", 5))
            asset_id = str(context.get("asset_id", ""))
            return {
                "asset_id": asset_id,
                "summary": "旅人正在移動並探索眼前的場景，畫面有明確的生活感與環境細節。",
                "moments": ["抵達或移動中的畫面", "人物與環境互動", "值得停留的景色細節"],
                "best_source_start": round(max(0, duration * .2), 3), "best_source_end": round(min(duration, max(.6, duration * .8)), 3),
                "mood": "輕鬆、好奇、旅行感", "confidence": .84,
            }
        if context and context.get("task") == "rough_cut_scoring":
            return {
                "segment_scores": [
                    {
                        "segment_id": segment["id"],
                        "semantic_completeness": 84,
                        "presentation_naturalness": 78,
                        "template_alignment": 81,
                        "recommended_action": "keep",
                        "reason": "完整說明主題，講者畫面自然且節奏符合模板。",
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
            return {"events": [
                {"id": "ambient-bed", "kind": "ambient", "generation_prompt": "subtle open-air cinematic ambience, no music", "start_time": 0, "end_time": duration, "gain_db": -24, "position": {"x": 0, "y": 0, "z": 0}},
                {"id": "footsteps", "kind": "footsteps", "generation_prompt": "soft natural footsteps matching a walking person", "start_time": min(1.0, duration / 2), "end_time": min(duration, 4.5), "gain_db": -19, "position": {"x": .15, "y": .45, "z": -1}},
            ]}
        if context and context.get("task") == "audio_description":
            # Short enough for the minimum development gap. Production receives
            # strict word/character ceilings in the prompt and response schema.
            return {"description": "畫面推進。", "visual_focus": "場景動作", "word_count": 1}
        if context and context.get("task") == "lecturas_interventions":
            transcript = list(context.get("transcript", []))
            anchor = float(transcript[min(1, len(transcript) - 1)].get("end_time", 2.0)) if transcript else 2.0
            return {"interventions": [{
                "id": "lecturas-recap-1", "anchor_output_time": anchor, "kind": "summary",
                "script": "先停一下：這裡的重點是把前一步的條件和現在的操作連起來。",
                "rationale": "主講者剛由概念轉入操作，適合用一句話整理兩者的關係。",
                "presentation_mode": "freeze", "confidence": .82,
            }]}
        if context and context.get("task") == "publishing_metadata":
            return {
                "titles": ["Speak English More Clearly in 5 Minutes", "A Simple Way to Sound More Articulate", "Practice Clear English with This Quick Lesson"],
                "description": "Learn a practical way to speak more clearly and use the present perfect with confidence.\n\n#EnglishSpeaking #IELTS #LearnEnglish",
                "seo_keywords": ["English speaking practice", "articulate English", "present perfect lesson", "IELTS speaking", "English fluency"],
                "hashtags": ["#EnglishSpeaking", "#IELTS", "#LearnEnglish"],
                "chapters": [{"start_time": 0, "title": "Clear speaking practice"}, {"start_time": 2.7, "title": "Present perfect example"}],
            }
        return {
            "template_name": "知識短影音教學模板",
            "summary": "以快速問題開場、分段解說重點並以行動呼籲收尾。",
            "aspect_ratio": "9:16",
            "overall_pacing": "fast",
            "scenes": [
                {
                    "start": 0.0,
                    "end": 4.0,
                    "shot_type": "close_up",
                    "purpose": "hook",
                    "pace": "fast",
                    "dialogue_prompt": "先提出一個讓觀眾好奇的問題。",
                    "filming_instruction": "人物置中，明亮正面光，快速進入主題。",
                }
            ],
        }

    def extract_education_keywords(
        self,
        transcript_text: str,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        self._delay()
        return {
            "keywords": [
                {
                    "term": "articulate",
                    "category": "advanced_vocabulary",
                    "explanation": "able to express ideas clearly and effectively",
                    "importance": 92,
                },
                {
                    "term": "present perfect",
                    "category": "grammar_concept",
                    "explanation": "a tense connecting a past action with the present",
                    "importance": 88,
                },
                {
                    "term": "coherence",
                    "category": "technical_term",
                    "explanation": "logical connection between ideas in speech or writing",
                    "importance": 84,
                },
            ]
        }

    def generate_structured_json(
        self, *, system_prompt: str, user_prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]:
        del system_prompt, response_schema
        self._delay()
        if "BILINGUAL_SUBTITLE_TRANSLATION" in user_prompt:
            raw_cues = user_prompt.split("CUES_TO_TRANSLATE:", 1)[-1].strip()
            try:
                cues = __import__("json").loads(raw_cues)
            except ValueError:
                cues = []
            return {
                "translations": [
                    {"id": str(cue.get("id", "")), "text": f"[EN] {str(cue.get('source_text', '')).strip()}"}
                    for cue in cues if str(cue.get("id", "")).strip()
                ]
            }
        if "AUTO_NARRATIVE_SCRIPT" in user_prompt:
            raw = user_prompt.split("ASSET_UNDERSTANDINGS:", 1)[-1].strip()
            try:
                understandings = __import__("json").loads(raw)
            except ValueError:
                understandings = []
            duration_match = re.search(r"TARGET_DURATION_SECONDS:\s*(\d+)", user_prompt)
            target = max(20, min(45, int(duration_match.group(1)) if duration_match else 30))
            per_beat = round(target / max(1, len(understandings)), 2)
            roles = ["hook", "journey", "detail", "payoff", "closing"]
            beats = []
            for index, item in enumerate(understandings):
                summary = str(item.get("summary", "眼前的旅行片段"))
                beats.append({
                    "id": f"scene-{index + 1}", "asset_id": str(item.get("asset_id", "")),
                    "narration": f"第 {index + 1} 站，{summary}。這趟旅程的驚喜，往往就藏在這些沒寫進行程的小片刻。",
                    "source_start": float(item.get("best_source_start", 0)), "source_end": float(item.get("best_source_end", per_beat)),
                    "target_duration_seconds": per_beat, "visual_role": roles[min(index, len(roles) - 1)],
                })
            return {
                "title": "今天的隨性小旅行", "summary": "把零散旅行素材串成一段有節奏的日常 Vlog。",
                "script": " ".join(str(item["narration"]) for item in beats), "beats": beats,
            }
        if "LANGUAGE_REVIEW_TRANSCRIPT" in user_prompt or "ASR_WORDS:" in user_prompt:
            return {
                "issues": [{
                    "id": "grammar-1", "word_start_index": 1, "word_end_index": 2,
                    "category": "agreement", "original_text": "we will", "correction": "we are going to",
                    "explanation": "依語境選擇更自然的未來表達。", "confidence": .88,
                    "synonyms": [{"term": "intend to", "reason": "較正式地表達計畫"}],
                }],
                "scores": {
                    "fluency_coherence": {"band_estimate": 6.0, "confidence": .62, "evidence": "句子銜接清楚，但樣本很短。", "improvement": "加入連接詞並延長回答。"},
                    "lexical_resource": {"band_estimate": 5.5, "confidence": .58, "evidence": "詞彙以常見字為主。", "improvement": "練習精準同義詞與搭配。"},
                    "grammatical_range_accuracy": {"band_estimate": 6.0, "confidence": .63, "evidence": "能使用基本結構。", "improvement": "加入受詞子句與條件句。"},
                    "pronunciation": {"band_estimate": 6.0, "confidence": .35, "evidence": "僅根據 ASR 信心代理，無法作音素判定。", "improvement": "以教師或音素模型複核重音與連音。"},
                },
                "overall_feedback": "先穩定句型正確度，再逐步提升詞彙精準度。",
                "disclaimer": "AI 教學估分，非官方 IELTS 成績。",
            }
        if "ACADEMIC_TRANSCRIPT" in user_prompt:
            return {"sections": [
                {"kind": "motivation", "target_percent": 22, "evidence_or_visuals": ["Personal research question"], "narration_guidance": "State the specific scientific motivation in one precise claim.", "editorial_risk": "Avoid generic prestige claims."},
                {"kind": "methodology", "target_percent": 38, "evidence_or_visuals": ["Experimental setup or model diagram"], "narration_guidance": "Explain the method, variable, and validation path.", "editorial_risk": "Do not imply reproducibility without enough detail."},
                {"kind": "results", "target_percent": 27, "evidence_or_visuals": ["Measured result or comparison"], "narration_guidance": "Show one result and its limitation.", "editorial_risk": "Do not overstate a preliminary result."},
                {"kind": "future_works", "target_percent": 13, "evidence_or_visuals": ["Next research question"], "narration_guidance": "Connect a realistic next step to the programme.", "editorial_risk": "Avoid claiming admission or guaranteed impact."},
            ], "overall_note": "Use precise evidence, calm pacing, and explicit limitations."}
        topic = user_prompt.split("TOPIC:", 1)[-1].split("\n", 1)[0].strip() or "your documentary"
        duration_match = re.search(r"target_duration_seconds['\"]?\s*[:=]\s*(\d+)", user_prompt)
        duration = max(20, min(900, int(duration_match.group(1)) if duration_match else 90))
        first = max(4, round(duration * 0.2))
        second = max(4, round(duration * 0.5))
        third = duration - first - second
        if third < 4:
            second -= 4 - third
            third = 4
        return {
            "title": f"{topic} | A cinematic field documentary",
            "summary": f"A concise, narration-led documentary about {topic}.",
            "total_duration_seconds": duration,
            "beats": [
                {"id": "hook", "purpose": "hook", "narration": f"This journey begins with one question: what does {topic} reveal?", "visual_query": "wide establishing landscape, travel arrival", "target_duration_seconds": first},
                {"id": "journey", "purpose": "journey", "narration": "We follow the route, the weather, and the small decisions that shape the experience.", "visual_query": "snow driving road, dashboard travel, walking outdoors", "target_duration_seconds": second},
                {"id": "closing", "purpose": "closing", "narration": "The destination is only part of the story; the conditions are what make it unforgettable.", "visual_query": "night sky aurora, final scenic landscape", "target_duration_seconds": third},
            ],
        }


class MockASRProvider(ASRProvider):
    """Return sentence and word-level timestamps suitable for editor and subtitle UI development."""

    def __init__(self, delay_seconds: float = 0.35) -> None:
        self.delay_seconds = max(0.0, delay_seconds)

    @property
    def name(self) -> str:
        return "mock_asr"

    def transcribe(
        self,
        audio_uri: str,
        *,
        language: str | None = None,
        word_timestamps: bool = True,
    ) -> Transcript:
        time.sleep(self.delay_seconds)
        words = [
            WordTimestamp(word="Today", start=0.0, end=0.35, confidence=0.99),
            WordTimestamp(word="we", start=0.38, end=0.52, confidence=0.99),
            WordTimestamp(word="will", start=0.55, end=0.75, confidence=0.98),
            WordTimestamp(word="practice", start=0.78, end=1.22, confidence=0.98),
            WordTimestamp(word="articulate", start=1.25, end=1.78, confidence=0.97),
            WordTimestamp(word="English.", start=1.82, end=2.2, confidence=0.99),
        ]
        second_sentence_words = [
            WordTimestamp(word="Use", start=2.7, end=2.95, confidence=0.99),
            WordTimestamp(word="the", start=2.98, end=3.1, confidence=0.99),
            WordTimestamp(word="present", start=3.13, end=3.48, confidence=0.98),
            WordTimestamp(word="perfect", start=3.51, end=3.92, confidence=0.98),
            WordTimestamp(word="for", start=3.95, end=4.08, confidence=0.98),
            WordTimestamp(word="experience.", start=4.11, end=4.65, confidence=0.98),
        ]
        return Transcript(
            language=language or "en",
            text="Today we will practice articulate English. Use the present perfect for experience.",
            segments=[
                TranscriptSegment(text="Today we will practice articulate English.", start=0.0, end=2.2, words=words if word_timestamps else []),
                TranscriptSegment(text="Use the present perfect for experience.", start=2.7, end=4.65, words=second_sentence_words if word_timestamps else []),
            ],
            provider=self.name,
            model="mock-asr-v1",
            metadata={"audio_uri": audio_uri, "word_timestamps": word_timestamps, "_mock": True},
        )
