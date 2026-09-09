from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectTimelineCreateRequest(BaseModel):
    #: The uploaded asset the first cut is made of. Required: a timeline with
    #: no source has no keep segments, and `POST /timelines/{id}/render`
    #: rejects that with a 400 rather than producing anything.
    source_asset_id: UUID
    #: Bounded to the column's own width so a long value is a 422 instead of a
    #: database error, the same way `ProjectCreateRequest.name` is.
    name: str = Field(default="未命名時間軸", min_length=1, max_length=200)


class ProjectTimelineResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    version: int
    is_current: bool
    created_at: datetime
    updated_at: datetime

    # Deliberately no `settings_json`: it carries the whole edit document and
    # grows without bound, and a client that has just been handed the id can
    # read it through the timeline routes that already exist.

    model_config = {"from_attributes": True}


__all__ = ["ProjectTimelineCreateRequest", "ProjectTimelineResponse"]
