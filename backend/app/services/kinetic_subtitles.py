"""Lightweight NLP-to-motion mapping shared by ASR, browser preview, and final render."""
from __future__ import annotations

import re

from app.ai.providers.schemas import Transcript, WordTimestamp


class KineticCaptionNLP:
    """Deterministic lexical/prosodic analyser; replaceable with a calibrated text classifier later."""

    EXPLODE = frozenset({"爆炸", "炸了", "爆", "boom", "explode", "explosion"})
    SURPRISE = frozenset({"哇", "天啊", "居然", "竟然", "突然", "wow", "what", "unbelievable"})
    ANGER = frozenset({"氣死", "可惡", "討厭", "憤怒", "荒謬", "angry", "hate", "furious"})
    JOY = frozenset({"太棒", "成功", "開心", "喜歡", "讚", "yay", "awesome", "love"})
    SADNESS = frozenset({"可惜", "難過", "遺憾", "失敗", "sad", "sorry", "unfortunately"})
    EMPHASIS = frozenset({"一定", "絕對", "真的", "重點", "注意", "最", "必須", "important", "must", "never", "always"})
    VERBS = frozenset({
        "是", "有", "要", "去", "做", "看", "說", "開始", "加入", "使用", "學習", "贏", "輸", "發現", "變成", "讓", "想", "喜歡", "點擊", "剪輯",
        "be", "is", "are", "was", "were", "have", "has", "had", "do", "does", "did", "go", "make", "get", "want", "need", "show", "build", "create", "use", "watch", "play", "win", "lose", "learn", "try",
    })
    NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|x|k|m|萬|億|倍)?$", re.IGNORECASE)

    @staticmethod
    def _normalise(word: str) -> str:
        return re.sub(r"^[\W_]+|[\W_]+$", "", word.casefold())

    def classify(self, word: WordTimestamp) -> tuple[str, float, str]:
        token = self._normalise(word.word)
        exclamation = 0.18 if any(mark in word.word for mark in "!！") else 0.0
        if token in self.EXPLODE:
            return "surprise", min(1.0, 0.92 + exclamation), "explode"
        if token in self.SURPRISE:
            return "surprise", min(1.0, 0.72 + exclamation), "pop"
        if token in self.ANGER:
            return "anger", min(1.0, 0.76 + exclamation), "shake"
        if token in self.JOY:
            return "joy", 0.72, "spring"
        if token in self.SADNESS:
            return "sadness", 0.58, "float"
        if token in self.EMPHASIS or exclamation:
            return "emphasis", min(1.0, 0.64 + exclamation), "spring"
        return "neutral", 0.0, "none"

    def highlight(self, word: WordTimestamp) -> str:
        """Return a compact, deterministic keyword label for consumer caption styles."""
        token = self._normalise(word.word)
        if self.NUMBER.fullmatch(token):
            return "number"
        if token in self.VERBS:
            return "verb"
        # A conservative English inflection fallback avoids requiring a heavyweight NLP model.
        if len(token) > 3 and re.fullmatch(r"[a-z]+(?:ed|ing|ize|ise)", token):
            return "verb"
        return "none"


def annotate_transcript_kinetics(transcript: Transcript) -> Transcript:
    """Mutate timed ASR tokens in place so all downstream JSON retains semantic motion metadata."""
    analyser = KineticCaptionNLP()
    for segment in transcript.segments:
        for word in segment.words:
            emotion, intensity, preset = analyser.classify(word)
            word.emotion = emotion  # type: ignore[assignment]
            word.emotion_intensity = intensity
            word.animation_preset = preset  # type: ignore[assignment]
            word.highlight_kind = analyser.highlight(word)  # type: ignore[assignment]
    return transcript
