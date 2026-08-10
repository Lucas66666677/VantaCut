# Language teaching review

`POST /api/v1/analysis/language-review` queues an ASR-grounded teaching review for a completed rough-cut analysis and confirmed Timeline. The LLM receives immutable word indices and may only create a correction when its quoted span exactly matches those ASR words. This prevents a model-generated timestamp from drifting onto an unrelated phrase.

The result is stored in `timeline.settings_json.language_review`, including provisional IELTS-aligned scores, evidence, confidence, a pronunciation proxy, issues, and `effect_tracks.language-teaching-review` overlay JSON. The exported video renders red strike-through + green correction cards and advanced-synonym cards as an alpha WebM layer.

Scores are teaching estimates, not official IELTS results. Fluency, lexical resource, grammar range/accuracy and pronunciation follow the four official speaking criteria; only certified IELTS examiners can issue an official score. Pronunciation is deliberately labelled provisional because ASR confidence and timing do not constitute phoneme-level assessment.
