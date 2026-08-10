from typing import Any

from fastapi import APIRouter, Depends

from app.ai.providers.base import MultimodalProvider
from app.ai.providers.factory import get_vision_provider

router = APIRouter(prefix="/ai", tags=["ai"])


def vision_provider_dependency() -> MultimodalProvider:
    return get_vision_provider()


@router.post("/analyze-video")
def analyze_video(
    video_uri: str,
    prompt: str,
    provider: MultimodalProvider = Depends(vision_provider_dependency),
) -> dict[str, Any]:
    return provider.analyze_video(video_uri, prompt)

