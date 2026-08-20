from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ParallaxLayerRequest(BaseModel):
    depth_model: Literal["auto", "depth_anything", "midas_small"] = "auto"
    use_proxy: bool = True


class ParallaxLayerTaskResponse(BaseModel):
    task_id: str
    media_asset_id: UUID
    project_id: UUID
    status: str
    status_sse_path: str
    status_websocket_path: str
