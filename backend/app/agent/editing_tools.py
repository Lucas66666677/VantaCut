"""Pydantic-validated editing tools exposed to a LangChain chat model.

These functions deliberately only describe operations.  Database mutation happens later,
inside the transaction that creates a new immutable Timeline version.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class TrimClipInput(BaseModel):
    clip_id: UUID
    source_start: Annotated[float, Field(ge=0, description="New in-point in source seconds")]
    source_end: Annotated[float, Field(gt=0, description="New out-point in source seconds")]

    @model_validator(mode="after")
    def validate_range(self) -> "TrimClipInput":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class InsertBRollInput(BaseModel):
    source_asset_id: UUID
    source_start: Annotated[float, Field(ge=0)]
    source_end: Annotated[float, Field(gt=0)]
    timeline_start: Annotated[float, Field(ge=0, description="Placement in final edited seconds")]
    z_index: Annotated[int, Field(ge=1, le=100)] = 10

    @model_validator(mode="after")
    def validate_range(self) -> "InsertBRollInput":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class AdjustAudioLevelInput(BaseModel):
    clip_id: UUID
    gain_db: Annotated[float, Field(ge=-24, le=24, description="Gain in decibels")]


class ApplyLUTInput(BaseModel):
    lut_key: Annotated[str, Field(min_length=1, max_length=1000, description="Approved MinIO .cube object key")]
    intensity: Annotated[float, Field(ge=0, le=1)] = 1.0


class AddBGMInput(BaseModel):
    """A non-destructive request for the existing generated-music pipeline.

    The planning phase deliberately stores only the creative intent.  It never
    fabricates a music asset or starts a paid music-generation request.
    """

    mood: Annotated[str, Field(min_length=2, max_length=240)]
    mix_level: Annotated[float, Field(ge=0.05, le=0.5)] = 0.16


ToolName = Literal["trim_clip", "insert_b_roll", "adjust_audio_level", "apply_lut", "add_bgm"]
TOOL_ARGUMENT_SCHEMAS: dict[str, type[BaseModel]] = {
    "trim_clip": TrimClipInput,
    "insert_b_roll": InsertBRollInput,
    "adjust_audio_level": AdjustAudioLevelInput,
    "apply_lut": ApplyLUTInput,
    "add_bgm": AddBGMInput,
}


class PlannedToolCall(BaseModel):
    name: ToolName
    arguments: dict[str, Any]

    def validated_arguments(self) -> BaseModel:
        return TOOL_ARGUMENT_SCHEMAS[self.name].model_validate(self.arguments)

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.validated_arguments().model_dump(mode="json")}


def _validated_tool_response(tool_name: str, arguments: BaseModel) -> dict[str, Any]:
    return {"status": "validated", "tool": tool_name, "arguments": arguments.model_dump(mode="json")}


def trim_clip(clip_id: UUID, source_start: float, source_end: float) -> dict[str, Any]:
    """Change the in/out point of an existing clip identified in the latest Timeline context."""
    return _validated_tool_response("trim_clip", TrimClipInput(clip_id=clip_id, source_start=source_start, source_end=source_end))


def insert_b_roll(
    source_asset_id: UUID, source_start: float, source_end: float, timeline_start: float, z_index: int = 10
) -> dict[str, Any]:
    """Place an approved project asset on the muted B-Roll overlay track."""
    return _validated_tool_response(
        "insert_b_roll",
        InsertBRollInput(
            source_asset_id=source_asset_id, source_start=source_start, source_end=source_end,
            timeline_start=timeline_start, z_index=z_index,
        ),
    )


def adjust_audio_level(clip_id: UUID, gain_db: float) -> dict[str, Any]:
    """Set a non-destructive gain adjustment for one existing timeline clip."""
    return _validated_tool_response("adjust_audio_level", AdjustAudioLevelInput(clip_id=clip_id, gain_db=gain_db))


def apply_lut(lut_key: str, intensity: float = 1.0) -> dict[str, Any]:
    """Apply an approved .cube LUT object from project storage to the output Timeline."""
    return _validated_tool_response("apply_lut", ApplyLUTInput(lut_key=lut_key, intensity=intensity))


def add_bgm(mood: str, mix_level: float = 0.16) -> dict[str, Any]:
    """Request original BGM generation; the human must accept the proposal first."""
    return _validated_tool_response("add_bgm", AddBGMInput(mood=mood, mix_level=mix_level))


def langchain_editing_tools() -> list[Any]:
    """Return LangChain StructuredTools; importing LangChain stays isolated to this boundary."""
    from langchain_core.tools import StructuredTool

    return [
        StructuredTool.from_function(trim_clip, args_schema=TrimClipInput),
        StructuredTool.from_function(insert_b_roll, args_schema=InsertBRollInput),
        StructuredTool.from_function(adjust_audio_level, args_schema=AdjustAudioLevelInput),
        StructuredTool.from_function(apply_lut, args_schema=ApplyLUTInput),
        StructuredTool.from_function(add_bgm, args_schema=AddBGMInput),
    ]
