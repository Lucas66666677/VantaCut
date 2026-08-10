from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.providers.factory import get_vision_provider
from app.ai.template_prompts import (
    TEMPLATE_SYSTEM_PROMPT,
    TEMPLATE_USER_PROMPT,
    template_response_schema,
)
from app.models.entities import MediaAsset, Template
from app.schemas.template import TemplateDocument


class TemplateExtractionError(RuntimeError):
    pass


def extract_template(db: Session, media_asset_id: UUID) -> Template:
    asset = db.get(MediaAsset, media_asset_id)
    if asset is None:
        raise LookupError("Media asset not found")

    provider = get_vision_provider()
    prompt = f"{TEMPLATE_SYSTEM_PROMPT}\n\n{TEMPLATE_USER_PROMPT}"
    raw_result = provider.analyze_video(
        asset.storage_key,
        prompt,
        response_schema=template_response_schema(),
    )

    # Providers may return the document directly or wrap it in a `template` key.
    candidate: dict[str, Any] = raw_result.get("template", raw_result)
    try:
        document = TemplateDocument.model_validate(candidate)
    except Exception as exc:
        raise TemplateExtractionError("Provider returned invalid template JSON") from exc

    template = Template(
        project_id=asset.project_id,
        source_asset_id=asset.id,
        name=document.template_name,
        description=document.summary,
        structure_json=document.model_dump(mode="json"),
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template

