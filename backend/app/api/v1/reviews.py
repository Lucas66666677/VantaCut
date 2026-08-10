from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_db
from app.models.entities import CommentStatus, ReviewComment, ReviewParticipant, ReviewRole, ReviewStatus, Timeline, TimelineReview, User
from app.schemas.review import (
    AddReviewParticipantRequest, CreateReviewCommentRequest, ReviewCommentResponse, ReviewDecisionRequest, ReviewDecisionResponse, UpdateReviewCommentRequest,
)
from app.services.review_exports import build_review_csv, build_review_pdf, frame_to_timecode, review_rows


router = APIRouter(prefix="/timelines", tags=["reviews"])


def _timeline_for_user(db: Session, timeline_id: UUID, user_id: UUID) -> tuple[Timeline, str]:
    timeline = db.get(Timeline, timeline_id)
    user = db.get(User, user_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    if user is None:
        raise HTTPException(status_code=403, detail="User cannot review this Timeline")
    if timeline.project.owner_id == user.id:
        return timeline, "owner"
    participant = db.scalar(select(ReviewParticipant).where(ReviewParticipant.timeline_id == timeline.id, ReviewParticipant.user_id == user.id))
    if participant is None:
        raise HTTPException(status_code=403, detail="User cannot review this Timeline")
    return timeline, participant.role.value


def _comment_response(comment: ReviewComment) -> ReviewCommentResponse:
    return ReviewCommentResponse(
        id=comment.id, status=comment.status.value, time_seconds=float(comment.time_seconds),
        timecode=frame_to_timecode(comment.frame_number, float(comment.frame_rate)), frame_number=comment.frame_number,
        frame_rate=float(comment.frame_rate), body=comment.body, annotation=comment.annotation_json,
        author_name=comment.author.display_name or comment.author.email,
    )


@router.get("/{timeline_id}/review/comments", response_model=list[ReviewCommentResponse])
def list_review_comments(timeline_id: UUID, user_id: UUID, db: Session = Depends(get_db)) -> list[ReviewCommentResponse]:
    _timeline_for_user(db, timeline_id, user_id)
    comments = db.scalars(
        select(ReviewComment).where(ReviewComment.timeline_id == timeline_id).options(selectinload(ReviewComment.author)).order_by(ReviewComment.frame_number)
    ).all()
    return [_comment_response(comment) for comment in comments]


@router.post("/{timeline_id}/review/comments", response_model=ReviewCommentResponse, status_code=201)
def create_review_comment(timeline_id: UUID, payload: CreateReviewCommentRequest, db: Session = Depends(get_db)) -> ReviewCommentResponse:
    timeline, _ = _timeline_for_user(db, timeline_id, payload.user_id)
    comment = ReviewComment(
        project_id=timeline.project_id, timeline_id=timeline.id, author_id=payload.user_id,
        frame_number=payload.frame_number, frame_rate=payload.frame_rate, time_seconds=payload.frame_number / payload.frame_rate,
        body=payload.body, annotation_json=payload.annotation.model_dump(mode="json"), status=CommentStatus.OPEN,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment, attribute_names=["author"])
    return _comment_response(comment)


@router.patch("/{timeline_id}/review/comments/{comment_id}", response_model=ReviewCommentResponse)
def update_review_comment(timeline_id: UUID, comment_id: UUID, payload: UpdateReviewCommentRequest, db: Session = Depends(get_db)) -> ReviewCommentResponse:
    _timeline_for_user(db, timeline_id, payload.user_id)
    comment = db.get(ReviewComment, comment_id)
    if comment is None or comment.timeline_id != timeline_id:
        raise HTTPException(status_code=404, detail="Review comment not found")
    comment.status = CommentStatus(payload.status)
    db.commit()
    db.refresh(comment, attribute_names=["author"])
    return _comment_response(comment)


@router.post("/{timeline_id}/review/decision", response_model=ReviewDecisionResponse)
def decide_review(timeline_id: UUID, payload: ReviewDecisionRequest, db: Session = Depends(get_db)) -> ReviewDecisionResponse:
    timeline, role = _timeline_for_user(db, timeline_id, payload.user_id)
    if role not in {"owner", ReviewRole.APPROVER.value}:
        raise HTTPException(status_code=403, detail="Only an approver can decide this review")
    review = db.scalar(select(TimelineReview).where(TimelineReview.timeline_id == timeline.id).with_for_update())
    if review is None:
        review = TimelineReview(timeline_id=timeline.id, requested_by_id=payload.user_id)
        db.add(review)
    review.status = ReviewStatus(payload.status)
    review.decided_by_id = payload.user_id
    review.decision_note = payload.note
    db.commit()
    return ReviewDecisionResponse(timeline_id=timeline.id, status=review.status.value, note=review.decision_note)


@router.get("/{timeline_id}/review/export")
def export_review(
    timeline_id: UUID, user_id: UUID, format: str = Query(pattern="^(csv|pdf|json)$"), db: Session = Depends(get_db)
) -> Response:
    timeline, _ = _timeline_for_user(db, timeline_id, user_id)
    comments = db.scalars(
        select(ReviewComment).where(ReviewComment.timeline_id == timeline.id).options(selectinload(ReviewComment.author)).order_by(ReviewComment.frame_number)
    ).all()
    rows = review_rows(timeline, comments)
    if format == "json":
        return Response(
            json.dumps({"timeline_id": str(timeline.id), "timeline_version": timeline.version, "items": rows}, ensure_ascii=False),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="review-{timeline.id}.json"'},
        )
    if format == "csv":
        return Response(
            build_review_csv(rows), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="review-{timeline.id}.csv"'},
        )
    return Response(
        build_review_pdf(timeline.name, rows), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="review-{timeline.id}.pdf"'},
    )


@router.post("/{timeline_id}/review/participants", status_code=201)
def add_review_participant(timeline_id: UUID, payload: AddReviewParticipantRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    timeline, role = _timeline_for_user(db, timeline_id, payload.user_id)
    if role != "owner":
        raise HTTPException(status_code=403, detail="Only the project owner can invite reviewers")
    if db.get(User, payload.participant_user_id) is None:
        raise HTTPException(status_code=404, detail="Participant user not found")
    participant = db.scalar(select(ReviewParticipant).where(
        ReviewParticipant.timeline_id == timeline.id, ReviewParticipant.user_id == payload.participant_user_id,
    ))
    if participant is None:
        participant = ReviewParticipant(timeline_id=timeline.id, user_id=payload.participant_user_id)
        db.add(participant)
    participant.role = ReviewRole(payload.role)
    db.commit()
    return {"timeline_id": str(timeline.id), "user_id": str(participant.user_id), "role": participant.role.value}
