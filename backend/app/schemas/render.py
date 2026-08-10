from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class RenderTimelineRequest(BaseModel):
    user_id: UUID
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    video_codec: Literal["auto", "h264", "hevc"] = "auto"
    dynamic_range: Literal["sdr", "hdr10", "hlg"] = "sdr"
    bit_depth: Literal[10, 12] = 10
    audio_loudness_target: Literal["broadcast", "streaming"] = "streaming"
    audio_layout: Literal["stereo", "5.1", "7.1.4"] = "stereo"
    spatial_delivery: Literal["channel_bed", "dolby_atmos"] = "channel_bed"
    container_format: Literal["mp4", "mov"] = "mp4"
    include_stem_tracks: bool = False
    execution_mode: Literal["centralized", "decentralized"] = "centralized"
    template_license_id: UUID | None = None


class RenderTimelineResponse(BaseModel):
    render_job_id: UUID
    task_id: str
    subscription_tier: Literal["free", "pro"]
    render_credits_remaining: int
    watermark_applied: bool


class MatrixExportRequest(BaseModel):
    user_id: UUID
    resolution: Literal["720p", "1080p"] = "1080p"
    video_codec: Literal["auto", "h264", "hevc"] = "auto"
    container_format: Literal["mp4", "mov"] = "mp4"


class MatrixExportVariantResponse(BaseModel):
    key: Literal["landscape", "vertical", "square"]
    aspect_ratio: Literal["16:9", "9:16", "1:1"]
    render_job_id: UUID
    status: str
    progress: int
    preview_url: str | None = None
    download_url: str | None = None
    message: str | None = None


class MatrixExportResponse(BaseModel):
    batch_id: UUID
    status: str
    variants: list[MatrixExportVariantResponse]
    zip_download_url: str | None = None
    zip_status: str | None = None
    distribution_targets: list[dict[str, Any]] = []
