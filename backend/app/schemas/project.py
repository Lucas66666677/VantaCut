from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    # Optional: the studio creates a workspace before the visitor has named
    # anything, so a blank body must be valid. `name` is bounded to the
    # column's own width so a long value is a 422 rather than a database error.
    name: str = Field(default="未命名專案", min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    lifecycle_state: str
    created_at: datetime
    updated_at: datetime

    # Deliberately no `owner_id`: every route that returns this already scopes
    # to the caller, so echoing the id adds nothing a client can use and
    # nothing a reviewer has to re-check.

    model_config = {"from_attributes": True}
