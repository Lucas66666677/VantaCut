import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.db.session import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class MediaType(str, enum.Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"


class AnalysisType(str, enum.Enum):
    TEMPLATE = "template"
    ROUGH_CUT = "rough_cut"
    TRANSCRIPTION = "transcription"
    MOOD = "mood"
    GAMING_HIGHLIGHTS = "gaming_highlights"
    SPEAKER_STATE = "speaker_state"
    SCREEN_FOCUS = "screen_focus"
    RELIGHTING = "relighting"


class RenderStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ComputeNodeStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    BANNED = "banned"


class DistributedBatchStatus(str, enum.Enum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentEditStatus(str, enum.Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"


class AutoDirectorStatus(str, enum.Enum):
    """Persistent stages for an unattended documentary generation run."""

    QUEUED = "queued"
    SCRIPTING = "scripting"
    RESEARCHING = "researching"
    NARRATING = "narrating"
    EDITING = "editing"
    READY_FOR_REVIEW = "ready_for_review"
    FAILED = "failed"


class ReviewStatus(str, enum.Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class CommentStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class ReviewRole(str, enum.Enum):
    REVIEWER = "reviewer"
    APPROVER = "approver"


class MediaStatus(str, enum.Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class VoiceProfileStatus(str, enum.Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"


class TrackType(str, enum.Enum):
    MAIN_VIDEO = "main_video"
    B_ROLL = "b_roll"
    AUDIO_OVERLAY = "audio_overlay"
    MULTICAM_VIDEO = "multicam_video"


class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"


class SocialPlatform(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"


class SocialPostStatus(str, enum.Enum):
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    AWAITING_CREATOR = "awaiting_creator"
    FAILED = "failed"


class ThumbnailExperimentStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INSUFFICIENT_DATA = "insufficient_data"
    FAILED = "failed"


class MarketplaceTemplateStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class TemplateLicenseStatus(str, enum.Enum):
    CHECKOUT_PENDING = "checkout_pending"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    APPLIED = "applied"
    RENDERING = "rendering"
    FULFILLED = "fulfilled"
    PAYMENT_FAILED = "payment_failed"
    REFUNDED = "refunded"
    REVOKED = "revoked"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        Enum(
            SubscriptionTier,
            name="subscription_tier",
            values_callable=lambda members: [member.value for member in members],
        ),
        default=SubscriptionTier.FREE,
        nullable=False,
    )
    render_credits: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    ai_feedback_entries: Mapped[list["AIFeedback"]] = relationship(back_populates="user")
    authored_review_comments: Mapped[list["ReviewComment"]] = relationship(
        back_populates="author", foreign_keys="ReviewComment.author_id"
    )
    review_participations: Mapped[list["ReviewParticipant"]] = relationship(back_populates="user")
    social_accounts: Mapped[list["SocialAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    platform_api_keys: Mapped[list["PlatformAPIKey"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    marketplace_templates: Mapped[list["MarketplaceTemplate"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan", foreign_keys="MarketplaceTemplate.creator_id"
    )
    purchased_template_licenses: Mapped[list["TemplateLicense"]] = relationship(
        back_populates="buyer", foreign_keys="TemplateLicense.buyer_id"
    )
    creator_connect_account: Mapped["CreatorConnectAccount | None"] = relationship(
        back_populates="creator", cascade="all, delete-orphan", uselist=False
    )
    storage_retention_notices: Mapped[list["StorageRetentionNotice"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    owner: Mapped[User] = relationship(back_populates="projects")
    media_assets: Mapped[list["MediaAsset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    templates: Mapped[list["Template"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    timelines: Mapped[list["Timeline"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    render_jobs: Mapped[list["RenderJob"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    ai_feedback_entries: Mapped[list["AIFeedback"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    agent_edit_runs: Mapped[list["AgentEditRun"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    auto_director_runs: Mapped[list["AutoDirectorRun"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    review_comments: Mapped[list["ReviewComment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    workspace_preferences: Mapped[list["WorkspacePreference"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    camera_devices: Mapped[list["CameraDevice"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    camera_ingest_sessions: Mapped[list["CameraIngestSession"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    spatial_video_jobs: Mapped[list["SpatialVideoJob"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    hydration_jobs: Mapped[list["MediaHydrationJob"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class WorkspacePreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned, user-owned editor workspace state; never affects render output."""

    __tablename__ = "workspace_preferences"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_workspace_preferences_user_project"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    layout_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    project: Mapped[Project] = relationship(back_populates="workspace_preferences")


class PlatformAPIKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Only a one-way HMAC is stored; the raw key is returned exactly once."""

    __tablename__ = "platform_api_keys"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    key_prefix: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    encrypted_webhook_secret: Mapped[str | None] = mapped_column(Text)
    rate_limit_rps: Mapped[float] = mapped_column(Numeric(8, 3), default=2.0)
    burst_limit: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    owner: Mapped[User] = relationship(back_populates="platform_api_keys")
    jobs: Mapped[list["PlatformJob"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")
    usage_events: Mapped[list["PlatformUsageEvent"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")
    invoices: Mapped[list["PlatformInvoice"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")


class PlatformJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_jobs"
    __table_args__ = (UniqueConstraint("api_key_id", "idempotency_key", name="uq_platform_jobs_key_idempotency"),)

    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_api_keys.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    webhook_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_webhook_status: Mapped[int | None] = mapped_column(Integer)

    api_key: Mapped[PlatformAPIKey] = relationship(back_populates="jobs")
    usage_events: Mapped[list["PlatformUsageEvent"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class PlatformUsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only quantities; invoices are reproducible from these source events."""

    __tablename__ = "platform_usage_events"

    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_api_keys.id", ondelete="CASCADE"), index=True)
    platform_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_jobs.id", ondelete="SET NULL"), index=True)
    metric: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[float] = mapped_column(Numeric(16, 4))
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    api_key: Mapped[PlatformAPIKey] = relationship(back_populates="usage_events")
    job: Mapped[PlatformJob | None] = relationship(back_populates="usage_events")


class PlatformInvoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "platform_invoices"
    __table_args__ = (UniqueConstraint("api_key_id", "period_start", name="uq_platform_invoices_key_period"),)

    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("platform_api_keys.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    totals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    api_key: Mapped[PlatformAPIKey] = relationship(back_populates="invoices")


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_assets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="media_type"))
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.UPLOADING, index=True
    )
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    thumbnail_key: Mapped[str | None] = mapped_column(String(1000))
    audio_key: Mapped[str | None] = mapped_column(String(1000))
    proxy_key: Mapped[str | None] = mapped_column(String(1000))
    fps: Mapped[float | None] = mapped_column(Numeric(8, 3))
    video_codec: Mapped[str | None] = mapped_column(String(50))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512), nullable=True)
    archive_status: Mapped[str] = mapped_column(String(32), default="hot", index=True)
    archive_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    restore_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    restore_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    raw_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    project: Mapped[Project] = relationship(back_populates="media_assets")
    analyses: Mapped[list["AIAnalysis"]] = relationship(
        back_populates="media_asset", cascade="all, delete-orphan"
    )
    source_clips: Mapped[list["Clip"]] = relationship(back_populates="source_asset")
    embedding_segments: Mapped[list["MediaEmbeddingSegment"]] = relationship(
        back_populates="media_asset", cascade="all, delete-orphan"
    )
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(back_populates="source_media_asset")
    hydration_job_items: Mapped[list["MediaHydrationItem"]] = relationship(back_populates="media_asset")


class MediaHydrationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Project-level view of one or more asynchronous Glacier restore requests."""

    __tablename__ = "media_hydration_jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    estimated_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="hydration_jobs")
    items: Mapped[list["MediaHydrationItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class MediaHydrationItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_hydration_items"
    __table_args__ = (UniqueConstraint("hydration_job_id", "media_asset_id", name="uq_hydration_item_asset"),)

    hydration_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_hydration_jobs.id", ondelete="CASCADE"), index=True
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    restore_header: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    job: Mapped[MediaHydrationJob] = relationship(back_populates="items")
    media_asset: Mapped[MediaAsset] = relationship(back_populates="hydration_job_items")


class InteractivePlaybackSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Pseudonymous viewer playback session; no account identity is required for public stories."""

    __tablename__ = "interactive_playback_sessions"

    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    viewer_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    current_node_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_watch_seconds: Mapped[float] = mapped_column(Numeric(14, 3), default=0)

    timeline: Mapped[Timeline] = relationship(back_populates="interactive_sessions")
    events: Mapped[list["InteractivePlaybackEvent"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="InteractivePlaybackEvent.created_at"
    )


class InteractivePlaybackEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only edge/node telemetry used to produce creator Sankey aggregates."""

    __tablename__ = "interactive_playback_events"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interactive_playback_sessions.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)  # node_entered, choice_selected, session_ended
    node_id: Mapped[str] = mapped_column(String(120), index=True)
    edge_id: Mapped[str | None] = mapped_column(String(120), index=True)
    target_node_id: Mapped[str | None] = mapped_column(String(120), index=True)
    watch_seconds: Mapped[float] = mapped_column(Numeric(14, 3), default=0)
    event_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    session: Mapped[InteractivePlaybackSession] = relationship(back_populates="events")


class AvatarProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Creator-owned licensed avatar asset and its neutral rig/blendshape mapping."""

    __tablename__ = "avatar_profiles"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    renderer: Mapped[str] = mapped_column(String(32), default="unreal_mrq")
    asset_bundle_key: Mapped[str] = mapped_column(String(1000))
    rig_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    consent_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)


class AvatarRenderJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One consented source segment mapped to avatar blendshapes + IK and rendered with alpha."""

    __tablename__ = "avatar_render_jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    timeline_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="SET NULL"), index=True)
    avatar_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("avatar_profiles.id", ondelete="RESTRICT"), index=True)
    source_asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True)
    source_start: Mapped[float] = mapped_column(Numeric(12, 3))
    source_end: Mapped[float] = mapped_column(Numeric(12, 3))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    blendshape_key: Mapped[str | None] = mapped_column(String(1000))
    motion_key: Mapped[str | None] = mapped_column(String(1000))
    rgba_video_key: Mapped[str | None] = mapped_column(String(1000))
    output_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class StorageRetentionNotice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Delivery ledger for the 60/75/85-day free-tier raw-footage notices."""

    __tablename__ = "storage_retention_notices"
    __table_args__ = (UniqueConstraint("user_id", "inactive_day_threshold", name="uq_retention_notice_user_threshold"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    inactive_day_threshold: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="storage_retention_notices")


class VoiceProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Consent-bound, project-scoped reference voice; latent vectors live encrypted in object storage."""

    __tablename__ = "voice_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    provider_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[VoiceProfileStatus] = mapped_column(
        Enum(VoiceProfileStatus, name="voice_profile_status", values_callable=lambda values: [item.value for item in values]),
        default=VoiceProfileStatus.QUEUED, nullable=False, index=True,
    )
    reference_audio_key: Mapped[str | None] = mapped_column(String(1000))
    conditioning_artifact_key: Mapped[str | None] = mapped_column(String(1000))
    language: Mapped[str | None] = mapped_column(String(20))
    reference_start_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    reference_duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    quality_score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    consent_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="voice_profiles")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    source_media_asset: Mapped[MediaAsset] = relationship(back_populates="voice_profiles")


class MediaEmbeddingSegment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A keyframe or transcript segment, searchable with a precise source timestamp."""

    __tablename__ = "media_embedding_segments"

    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    modality: Mapped[str] = mapped_column(String(24), index=True)  # keyframe | transcript
    source_start: Mapped[float] = mapped_column(Numeric(12, 3))
    source_end: Mapped[float] = mapped_column(Numeric(12, 3))
    embedding: Mapped[list[float]] = mapped_column(Vector(512))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    media_asset: Mapped[MediaAsset] = relationship(back_populates="embedding_segments")


class CameraDevice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A project-scoped camera or edge gateway. The HMAC secret is encrypted at rest."""

    __tablename__ = "camera_devices"
    __table_args__ = (UniqueConstraint("project_id", "device_identifier", name="uq_camera_device_project_identifier"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    device_identifier: Mapped[str] = mapped_column(String(160), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    device_type: Mapped[str] = mapped_column(String(80), default="camera")
    encrypted_hmac_secret: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    project: Mapped[Project] = relationship(back_populates="camera_devices")
    ingest_sessions: Mapped[list["CameraIngestSession"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class CameraIngestSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One active camera recording whose independently playable chunks grow a timeline."""

    __tablename__ = "camera_ingest_sessions"
    __table_args__ = (UniqueConstraint("device_id", "capture_id", name="uq_camera_ingest_device_capture"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_devices.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    capture_id: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(32), default="capturing", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_duration_seconds: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="camera_ingest_sessions")
    device: Mapped[CameraDevice] = relationship(back_populates="ingest_sessions")
    timeline: Mapped["Timeline"] = relationship(foreign_keys=[timeline_id])
    chunks: Mapped[list["CameraIngestChunk"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="CameraIngestChunk.sequence_number"
    )


class CameraIngestChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable, HMAC-verified camera segment. Source and proxy remain independently addressable."""

    __tablename__ = "camera_ingest_chunks"
    __table_args__ = (UniqueConstraint("session_id", "sequence_number", name="uq_camera_ingest_session_sequence"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_ingest_sessions.id", ondelete="CASCADE"), index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(1000), unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(120), default="video/mp4")
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 3))
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    camera_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    proxy_key: Mapped[str | None] = mapped_column(String(1000))
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    session: Mapped[CameraIngestSession] = relationship(back_populates="chunks")
    media_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[media_asset_id])


class Template(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "templates"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    structure_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    project: Mapped[Project] = relationship(back_populates="templates")
    source_asset: Mapped[MediaAsset | None] = relationship(foreign_keys=[source_asset_id])
    marketplace_listing: Mapped["MarketplaceTemplate | None"] = relationship(
        back_populates="source_template", cascade="all, delete-orphan", uselist=False
    )


class MarketplaceTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Public catalogue metadata plus an encrypted, worker-only template payload."""

    __tablename__ = "marketplace_templates"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("templates.id", ondelete="CASCADE"), unique=True, index=True
    )
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=MarketplaceTemplateStatus.DRAFT.value, index=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    encrypted_payload: Mapped[str] = mapped_column(Text)
    encryption_key_version: Mapped[str] = mapped_column(String(32), default="v1")
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    safe_preview_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    source_template: Mapped[Template] = relationship(back_populates="marketplace_listing")
    creator: Mapped[User] = relationship(back_populates="marketplace_templates", foreign_keys=[creator_id])
    licenses: Mapped[list["TemplateLicense"]] = relationship(
        back_populates="marketplace_template", cascade="all, delete-orphan"
    )


class CreatorConnectAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One Stripe Connect account per creator; no Stripe secret is stored here."""

    __tablename__ = "creator_connect_accounts"

    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    stripe_account_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    details_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    charges_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    creator: Mapped[User] = relationship(back_populates="creator_connect_account")


class TemplateLicense(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single paid use of a marketplace template, fulfilled only after a successful export."""

    __tablename__ = "template_licenses"

    marketplace_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("marketplace_templates.id", ondelete="RESTRICT"), index=True
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="SET NULL"), index=True
    )
    render_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_jobs.id", ondelete="SET NULL"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=TemplateLicenseStatus.CHECKOUT_PENDING.value, index=True)
    gross_amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    creator_share_cents: Mapped[int] = mapped_column(Integer)
    platform_share_cents: Mapped[int] = mapped_column(Integer)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    stripe_charge_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    stripe_transfer_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    transfer_group: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    template_payload_sha256: Mapped[str] = mapped_column(String(64))
    blackbox_render_only: Mapped[bool] = mapped_column(Boolean, default=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    marketplace_template: Mapped[MarketplaceTemplate] = relationship(back_populates="licenses")
    buyer: Mapped[User] = relationship(back_populates="purchased_template_licenses", foreign_keys=[buyer_id])
    render_job: Mapped["RenderJob | None"] = relationship(back_populates="marketplace_license")
    ledger_entries: Mapped[list["MarketplaceLedgerEntry"]] = relationship(
        back_populates="license", cascade="all, delete-orphan"
    )


class MarketplaceLedgerEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable financial events. Corrections are compensating rows, never updates."""

    __tablename__ = "marketplace_ledger_entries"

    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("template_licenses.id", ondelete="CASCADE"), index=True
    )
    entry_type: Mapped[str] = mapped_column(String(48), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # credit or debit
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stripe_object_id: Mapped[str | None] = mapped_column(String(255), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    license: Mapped[TemplateLicense] = relationship(back_populates="ledger_entries")


class Timeline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "timelines"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="Untitled timeline")
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_timeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="SET NULL"), index=True
    )
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    project: Mapped[Project] = relationship(back_populates="timelines")
    clips: Mapped[list["Clip"]] = relationship(
        back_populates="timeline", cascade="all, delete-orphan", order_by="Clip.order_index"
    )
    ai_feedback_entries: Mapped[list["AIFeedback"]] = relationship(
        back_populates="timeline", cascade="all, delete-orphan"
    )
    parent_timeline: Mapped["Timeline | None"] = relationship(
        remote_side="Timeline.id", back_populates="child_timelines"
    )
    child_timelines: Mapped[list["Timeline"]] = relationship(back_populates="parent_timeline")
    source_agent_runs: Mapped[list["AgentEditRun"]] = relationship(
        back_populates="source_timeline", foreign_keys="AgentEditRun.source_timeline_id"
    )
    result_agent_runs: Mapped[list["AgentEditRun"]] = relationship(
        back_populates="result_timeline", foreign_keys="AgentEditRun.result_timeline_id"
    )
    review: Mapped["TimelineReview | None"] = relationship(
        back_populates="timeline", cascade="all, delete-orphan", uselist=False
    )
    review_comments: Mapped[list["ReviewComment"]] = relationship(
        back_populates="timeline", cascade="all, delete-orphan", order_by="ReviewComment.frame_number"
    )
    review_participants: Mapped[list["ReviewParticipant"]] = relationship(
        back_populates="timeline", cascade="all, delete-orphan"
    )
    interactive_sessions: Mapped[list["InteractivePlaybackSession"]] = relationship(
        back_populates="timeline", cascade="all, delete-orphan"
    )
    social_posts: Mapped[list["SocialPost"]] = relationship(
        back_populates="timeline", cascade="all, delete-orphan"
    )


class Clip(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clips"
    __table_args__ = (UniqueConstraint("timeline_id", "order_index"),)

    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    source_start: Mapped[float] = mapped_column(Numeric(12, 3))
    source_end: Mapped[float] = mapped_column(Numeric(12, 3))
    track: Mapped[TrackType] = mapped_column(
        Enum(TrackType, name="track_type"), default=TrackType.MAIN_VIDEO, index=True
    )
    z_index: Mapped[int] = mapped_column(Integer, default=0)
    audio_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    audio_effects: Mapped[list[str]] = mapped_column(JSONB, default=list)
    order_index: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    timeline: Mapped[Timeline] = relationship(back_populates="clips")
    source_asset: Mapped[MediaAsset] = relationship(back_populates="source_clips")
    ai_feedback_entries: Mapped[list["AIFeedback"]] = relationship(back_populates="clip")


class AIAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_analyses"

    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    analysis_type: Mapped[AnalysisType] = mapped_column(Enum(AnalysisType, name="analysis_type"))
    model_name: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    error_message: Mapped[str | None] = mapped_column(Text)

    media_asset: Mapped[MediaAsset] = relationship(back_populates="analyses")


class AIFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable human correction event used to train future rough-cut ranking models."""

    __tablename__ = "ai_feedback"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clips.id", ondelete="SET NULL"), index=True
    )
    original_ai_decision: Mapped[str] = mapped_column(String(16))
    user_final_decision: Mapped[str] = mapped_column(String(16))
    clip_context_features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    user: Mapped[User] = relationship(back_populates="ai_feedback_entries")
    project: Mapped[Project] = relationship(back_populates="ai_feedback_entries")
    timeline: Mapped[Timeline] = relationship(back_populates="ai_feedback_entries")
    clip: Mapped[Clip | None] = relationship(back_populates="ai_feedback_entries")


class AgentEditRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditable asynchronous request to transform a Timeline through constrained tools."""

    __tablename__ = "agent_edit_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="RESTRICT"), index=True
    )
    result_timeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="SET NULL"), index=True
    )
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[AgentEditStatus] = mapped_column(
        Enum(
            AgentEditStatus,
            name="agent_edit_status",
            values_callable=lambda members: [member.value for member in members],
        ),
        default=AgentEditStatus.QUEUED,
        index=True,
    )
    provider_name: Mapped[str | None] = mapped_column(String(120))
    tool_calls_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="agent_edit_runs")
    source_timeline: Mapped[Timeline] = relationship(
        back_populates="source_agent_runs", foreign_keys=[source_timeline_id]
    )
    result_timeline: Mapped[Timeline | None] = relationship(
        back_populates="result_agent_runs", foreign_keys=[result_timeline_id]
    )


class AutoDirectorRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditable parent run for Director → Scripter → Researcher → Editor orchestration."""

    __tablename__ = "auto_director_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(Text)
    creative_brief_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[AutoDirectorStatus] = mapped_column(
        Enum(
            AutoDirectorStatus,
            name="auto_director_status",
            values_callable=lambda members: [member.value for member in members],
        ),
        default=AutoDirectorStatus.QUEUED,
        index=True,
    )
    provider_name: Mapped[str | None] = mapped_column(String(120))
    script_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    research_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    narration_key: Mapped[str | None] = mapped_column(String(1000))
    result_timeline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="SET NULL"), index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="auto_director_runs")
    requested_by: Mapped[User] = relationship(foreign_keys=[requested_by_id])
    result_timeline: Mapped[Timeline | None] = relationship(foreign_keys=[result_timeline_id])


class TimelineReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One approval state per immutable Timeline version."""

    __tablename__ = "timeline_reviews"

    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status", values_callable=lambda members: [member.value for member in members]),
        default=ReviewStatus.DRAFT, nullable=False, index=True,
    )
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text)

    timeline: Mapped[Timeline] = relationship(back_populates="review")
    requested_by: Mapped[User | None] = relationship(foreign_keys=[requested_by_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])


class ReviewParticipant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Timeline-scoped reviewer/approver access until project-wide RBAC is introduced."""

    __tablename__ = "review_participants"
    __table_args__ = (UniqueConstraint("timeline_id", "user_id"),)

    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[ReviewRole] = mapped_column(
        Enum(ReviewRole, name="review_role", values_callable=lambda members: [member.value for member in members]),
        default=ReviewRole.REVIEWER, nullable=False,
    )

    timeline: Mapped[Timeline] = relationship(back_populates="review_participants")
    user: Mapped[User] = relationship(back_populates="review_participations")


class ReviewComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Frame-accurate note and vector annotation, stored in normalized canvas coordinates."""

    __tablename__ = "review_comments"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    frame_number: Mapped[int] = mapped_column(Integer, index=True)
    frame_rate: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    time_seconds: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    body: Mapped[str] = mapped_column(Text)
    annotation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[CommentStatus] = mapped_column(
        Enum(CommentStatus, name="comment_status", values_callable=lambda members: [member.value for member in members]),
        default=CommentStatus.OPEN, nullable=False, index=True,
    )

    project: Mapped[Project] = relationship(back_populates="review_comments")
    timeline: Mapped[Timeline] = relationship(back_populates="review_comments")
    author: Mapped[User] = relationship(back_populates="authored_review_comments", foreign_keys=[author_id])


class RenderJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "render_jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[RenderStatus] = mapped_column(Enum(RenderStatus, name="render_status"), default=RenderStatus.QUEUED)
    output_key: Mapped[str | None] = mapped_column(String(1000))
    output_format: Mapped[str] = mapped_column(String(20), default="mp4")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    provenance_key: Mapped[str | None] = mapped_column(String(1000))
    forensic_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    project: Mapped[Project] = relationship(back_populates="render_jobs")
    timeline: Mapped[Timeline] = relationship()
    spatial_video_jobs: Mapped[list["SpatialVideoJob"]] = relationship(
        back_populates="source_render_job", cascade="all, delete-orphan"
    )
    marketplace_license: Mapped["TemplateLicense | None"] = relationship(back_populates="render_job", uselist=False)


class ComputeNode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A consented desktop/browser worker identified by an Ed25519 public key."""

    __tablename__ = "compute_nodes"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(160))
    public_key: Mapped[str] = mapped_column(String(512), unique=True)
    node_kind: Mapped[str] = mapped_column(String(24), default="browser")
    status: Mapped[ComputeNodeStatus] = mapped_column(Enum(ComputeNodeStatus, name="compute_node_status", values_callable=lambda members: [member.value for member in members]), default=ComputeNodeStatus.PENDING, index=True)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    consent_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    renderer_image_digest: Mapped[str | None] = mapped_column(String(255))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reputation_score: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[User] = relationship(foreign_keys=[owner_id])


class DistributedRenderBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable, chunk-safe render plan for one normal RenderJob."""

    __tablename__ = "distributed_render_batches"

    render_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("render_jobs.id", ondelete="CASCADE"), unique=True, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[DistributedBatchStatus] = mapped_column(Enum(DistributedBatchStatus, name="distributed_batch_status", values_callable=lambda members: [member.value for member in members]), default=DistributedBatchStatus.QUEUED, index=True)
    chunk_seconds: Mapped[int] = mapped_column(Integer, default=5)
    replication_factor: Mapped[int] = mapped_column(Integer, default=2)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    final_output_key: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)
    render_job: Mapped[RenderJob] = relationship(foreign_keys=[render_job_id])


class DistributedRenderChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "distributed_render_chunks"
    __table_args__ = (UniqueConstraint("batch_id", "chunk_index", name="uq_distributed_render_chunk_index"),)

    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("distributed_render_batches.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    output_start_seconds: Mapped[float] = mapped_column(Numeric(12, 3))
    output_end_seconds: Mapped[float] = mapped_column(Numeric(12, 3))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    required_replicas: Mapped[int] = mapped_column(Integer, default=2)
    accepted_checksum: Mapped[str | None] = mapped_column(String(64))
    accepted_object_key: Mapped[str | None] = mapped_column(String(1000))
    batch: Mapped[DistributedRenderBatch] = relationship(foreign_keys=[batch_id])


class DistributedRenderAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "distributed_render_assignments"
    __table_args__ = (UniqueConstraint("chunk_id", "node_id", name="uq_distributed_assignment_chunk_node"), UniqueConstraint("ticket_nonce", name="uq_distributed_assignment_ticket_nonce"))

    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("distributed_render_chunks.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("compute_nodes.id", ondelete="CASCADE"), index=True)
    ticket_nonce: Mapped[str] = mapped_column(String(80))
    ticket_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="assigned", index=True)
    output_checksum: Mapped[str | None] = mapped_column(String(64))
    decoded_fingerprint: Mapped[str | None] = mapped_column(String(64))
    renderer_image_digest: Mapped[str | None] = mapped_column(String(255))
    output_object_key: Mapped[str | None] = mapped_column(String(1000))
    node_signature: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    chunk: Mapped[DistributedRenderChunk] = relationship(foreign_keys=[chunk_id])
    node: Mapped[ComputeNode] = relationship(foreign_keys=[node_id])


class ComputeCreditLedger(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable contribution-credit events; reversals are compensating rows."""

    __tablename__ = "compute_credit_ledger"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_compute_credit_ledger_idempotency"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("compute_nodes.id", ondelete="SET NULL"), nullable=True, index=True)
    assignment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("distributed_render_assignments.id", ondelete="SET NULL"), nullable=True, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(48))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SpatialVideoJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Separate derivation job; a stereo export never overwrites its approved 2D render."""

    __tablename__ = "spatial_video_jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    source_render_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_jobs.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_key: Mapped[str | None] = mapped_column(String(1000))
    verification_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="spatial_video_jobs")
    timeline: Mapped[Timeline] = relationship(foreign_keys=[timeline_id])
    source_render_job: Mapped[RenderJob] = relationship(back_populates="spatial_video_jobs")


class SocialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A server-side OAuth connection. OAuth secrets are encrypted before persistence."""

    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("user_id", "platform", "platform_account_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        Enum(SocialPlatform, name="social_platform", values_callable=lambda values: [item.value for item in values]),
        index=True,
    )
    platform_account_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scopes_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    user: Mapped[User] = relationship(back_populates="social_accounts")
    posts: Mapped[list["SocialPost"]] = relationship(
        back_populates="social_account", cascade="all, delete-orphan"
    )


class SocialPost(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "social_posts"

    social_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True
    )
    timeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timelines.id", ondelete="CASCADE"), index=True
    )
    render_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("render_jobs.id", ondelete="RESTRICT"), index=True
    )
    platform_post_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[SocialPostStatus] = mapped_column(
        Enum(SocialPostStatus, name="social_post_status", values_callable=lambda values: [item.value for item in values]),
        default=SocialPostStatus.QUEUED, index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)

    social_account: Mapped[SocialAccount] = relationship(back_populates="posts")
    timeline: Mapped[Timeline] = relationship(back_populates="social_posts")
    render_job: Mapped[RenderJob] = relationship()
    thumbnail_experiment: Mapped["ThumbnailExperiment | None"] = relationship(
        back_populates="social_post", cascade="all, delete-orphan", uselist=False
    )


class ThumbnailExperiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rotates one active YouTube thumbnail at a time; candidate files live in object storage."""

    __tablename__ = "thumbnail_experiments"

    social_post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[ThumbnailExperimentStatus] = mapped_column(
        Enum(ThumbnailExperimentStatus, name="thumbnail_experiment_status", values_callable=lambda values: [item.value for item in values]),
        default=ThumbnailExperimentStatus.ACTIVE, index=True,
    )
    candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    active_candidate_index: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    winner_candidate_id: Mapped[str | None] = mapped_column(String(120))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    social_post: Mapped[SocialPost] = relationship(back_populates="thumbnail_experiment")
    observations: Mapped[list["ThumbnailObservation"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class ThumbnailObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "thumbnail_observations"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("thumbnail_experiments.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[str] = mapped_column(String(120), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    impressions: Mapped[int | None] = mapped_column(Integer)
    click_through_rate: Mapped[float | None] = mapped_column(Numeric(8, 6))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    experiment: Mapped[ThumbnailExperiment] = relationship(back_populates="observations")
