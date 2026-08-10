"""Pseudocode for the scheduled feedback-to-training-dataset export.

Run this as an offline workflow (daily/weekly), not inside the request path.
"""

# 1. SELECT feedback where original_ai_decision != user_final_decision.
# 2. Flatten clip_context_features into stable numeric features, for example:
#    silence_duration_seconds, filler_word_count, semantic_score,
#    visual_naturalness_score, template_match_score, ai_confidence_score.
# 3. target = 1 for "remove" and 0 for "keep" (the human final decision).
# 4. De-identify project/user IDs; split by project, never random clip splitting,
#    to prevent clips from the same video leaking into train and validation data.
# 5. Version the exported Parquet/JSONL file, train a calibrated binary classifier,
#    evaluate precision/recall and only promote after offline + shadow validation.

from sqlalchemy import select

from app.models.entities import AIFeedback


def export_training_rows(session):  # pseudocode: add feature validation and PII removal in production
    corrections = session.scalars(
        select(AIFeedback).where(AIFeedback.original_ai_decision != AIFeedback.user_final_decision)
    )
    for feedback in corrections:
        features = feedback.clip_context_features
        yield {
            "silence_duration_seconds": float(features.get("silence_duration_seconds", 0)),
            "filler_word_count": int(features.get("filler_word_count", 0)),
            "semantic_score": float(features.get("semantic_score", 0)),
            "visual_naturalness_score": float(features.get("visual_naturalness_score", 0)),
            "template_match_score": float(features.get("template_match_score", 0)),
            "ai_confidence_score": float(features.get("confidence_score", 0)),
            "label_remove": int(feedback.user_final_decision == "remove"),
            "group_project_id": str(feedback.project_id),  # group split only; omit from model features
        }
