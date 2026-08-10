from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class InteractiveChoicePosition(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class InteractiveNode(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    title: str = Field(min_length=1, max_length=160)
    media_asset_id: UUID
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)

    @model_validator(mode="after")
    def end_after_start(self) -> "InteractiveNode":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class InteractiveEdge(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    source_node_id: str = Field(max_length=120)
    target_node_id: str = Field(max_length=120)
    choice_text: str = Field(min_length=1, max_length=100)
    choice_position: InteractiveChoicePosition


class InteractiveGraph(BaseModel):
    schema_version: Literal[1] = 1
    entry_node_id: str
    nodes: list[InteractiveNode] = Field(min_length=1, max_length=500)
    edges: list[InteractiveEdge] = Field(default_factory=list, max_length=2_000)
    published: bool = False

    @model_validator(mode="after")
    def validate_graph(self) -> "InteractiveGraph":
        node_ids = {node.id for node in self.nodes}
        edge_ids = [edge.id for edge in self.edges]
        if len(node_ids) != len(self.nodes) or len(set(edge_ids)) != len(edge_ids):
            raise ValueError("Node and edge IDs must be unique")
        if self.entry_node_id not in node_ids:
            raise ValueError("entry_node_id must reference a node")
        if any(edge.source_node_id not in node_ids or edge.target_node_id not in node_ids for edge in self.edges):
            raise ValueError("Every edge source and target must reference a node")
        positions = [(edge.source_node_id, round(edge.choice_position.x, 3), round(edge.choice_position.y, 3)) for edge in self.edges]
        if len(set(positions)) != len(positions):
            raise ValueError("Choices from the same node cannot share a position")
        return self


class SaveInteractiveGraphRequest(BaseModel):
    user_id: UUID
    graph: InteractiveGraph


class InteractiveManifestNode(InteractiveNode):
    media_url: str


class InteractiveManifest(BaseModel):
    timeline_id: UUID
    graph: InteractiveGraph
    nodes: list[InteractiveManifestNode]


class StartInteractiveSessionResponse(BaseModel):
    session_id: UUID
    entry_node_id: str


class InteractiveEventRequest(BaseModel):
    event_type: Literal["node_entered", "choice_selected", "session_ended"]
    node_id: str
    edge_id: str | None = None
    watch_seconds: float = Field(default=0, ge=0, le=86_400)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SankeyNode(BaseModel):
    id: str
    label: str
    visits: int
    average_dwell_seconds: float


class SankeyLink(BaseModel):
    source: str
    target: str
    edge_id: str
    label: str
    value: int
    choice_share_percent: float


class InteractiveAnalyticsResponse(BaseModel):
    timeline_id: UUID
    sessions: int
    nodes: list[SankeyNode]
    links: list[SankeyLink]
