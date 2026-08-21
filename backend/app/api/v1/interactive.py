from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.dependencies import get_current_user
from app.models.entities import InteractivePlaybackEvent, InteractivePlaybackSession, MediaAsset, Timeline, User
from app.schemas.interactive import (
    InteractiveAnalyticsResponse, InteractiveEventRequest, InteractiveGraph, InteractiveManifest,
    InteractiveManifestNode, SankeyLink, SankeyNode, SaveInteractiveGraphRequest, StartInteractiveSessionResponse,
)
from app.services.storage import create_download_url


creator_router = APIRouter(prefix="/timelines", tags=["interactive-authoring"])
player_router = APIRouter(prefix="/interactive", tags=["interactive-player"])


def _graph(timeline: Timeline) -> InteractiveGraph:
    raw = dict(timeline.settings_json or {}).get("interactive_graph")
    if not raw:
        raise HTTPException(status_code=404, detail="Interactive graph is not configured")
    try:
        return InteractiveGraph.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Interactive graph is invalid") from exc


def _assert_owner(timeline: Timeline, current_user: User) -> None:
    if timeline.project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="User cannot modify this interactive timeline")


def _assert_graph_assets(db: Session, timeline: Timeline, graph: InteractiveGraph) -> None:
    assets = {asset.id: asset for asset in db.scalars(select(MediaAsset).where(
        MediaAsset.project_id == timeline.project_id,
        MediaAsset.id.in_([node.media_asset_id for node in graph.nodes]),
    ))}
    if len(assets) != len({node.media_asset_id for node in graph.nodes}):
        raise HTTPException(status_code=422, detail="Every interactive node must reference project media")
    if graph.published and any(not assets[node.media_asset_id].proxy_key for node in graph.nodes):
        raise HTTPException(status_code=422, detail="Published interactive nodes require a hot playback proxy")


@creator_router.put("/{timeline_id}/interactive-graph", response_model=InteractiveGraph)
def save_interactive_graph(timeline_id: UUID, payload: SaveInteractiveGraphRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> InteractiveGraph:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    _assert_owner(timeline, current_user)
    _assert_graph_assets(db, timeline, payload.graph)
    timeline.settings_json = {**dict(timeline.settings_json or {}), "interactive_graph": payload.graph.model_dump(mode="json")}
    db.commit()
    return payload.graph


@player_router.get("/timelines/{timeline_id}/manifest", response_model=InteractiveManifest)
def interactive_manifest(timeline_id: UUID, db: Session = Depends(get_db)) -> InteractiveManifest:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Interactive timeline not found")
    graph = _graph(timeline)
    if not graph.published:
        raise HTTPException(status_code=404, detail="Interactive timeline is not published")
    assets = {asset.id: asset for asset in db.scalars(select(MediaAsset).where(
        MediaAsset.project_id == timeline.project_id,
        MediaAsset.id.in_([node.media_asset_id for node in graph.nodes]),
    ))}
    nodes = []
    for node in graph.nodes:
        asset = assets.get(node.media_asset_id)
        if asset is None or not asset.proxy_key:
            raise HTTPException(status_code=503, detail="An interactive proxy is temporarily unavailable")
        nodes.append(InteractiveManifestNode(**node.model_dump(), media_url=create_download_url(asset.proxy_key, expires_in=7_200)))
    return InteractiveManifest(timeline_id=timeline.id, graph=graph, nodes=nodes)


@player_router.post("/timelines/{timeline_id}/sessions", response_model=StartInteractiveSessionResponse, status_code=status.HTTP_201_CREATED)
def start_interactive_session(timeline_id: UUID, db: Session = Depends(get_db)) -> StartInteractiveSessionResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Interactive timeline not found")
    graph = _graph(timeline)
    if not graph.published:
        raise HTTPException(status_code=404, detail="Interactive timeline is not published")
    session = InteractivePlaybackSession(timeline_id=timeline.id, current_node_id=graph.entry_node_id)
    db.add(session); db.commit(); db.refresh(session)
    return StartInteractiveSessionResponse(session_id=session.id, entry_node_id=graph.entry_node_id)


@player_router.post("/sessions/{session_id}/events", status_code=status.HTTP_202_ACCEPTED)
def record_interactive_event(session_id: UUID, payload: InteractiveEventRequest, db: Session = Depends(get_db)) -> dict[str, bool]:
    session = db.get(InteractivePlaybackSession, session_id)
    if session is None or session.status != "active":
        raise HTTPException(status_code=404, detail="Interactive playback session not found")
    graph = _graph(session.timeline)
    node_ids = {node.id for node in graph.nodes}
    if payload.node_id not in node_ids:
        raise HTTPException(status_code=422, detail="Event node is not in the interactive graph")
    target_node_id = None
    if payload.event_type == "choice_selected":
        edge = next((edge for edge in graph.edges if edge.id == payload.edge_id), None)
        if edge is None or edge.source_node_id != payload.node_id or session.current_node_id != payload.node_id:
            raise HTTPException(status_code=409, detail="Choice does not match the current interactive node")
        target_node_id, session.current_node_id = edge.target_node_id, edge.target_node_id
    elif payload.event_type == "node_entered":
        if session.current_node_id not in {None, payload.node_id}:
            raise HTTPException(status_code=409, detail="Node entry does not follow the selected choice")
        session.current_node_id = payload.node_id
    else:
        session.status, session.ended_at = "ended", datetime.now(UTC)
    session.total_watch_seconds = float(session.total_watch_seconds or 0) + payload.watch_seconds
    db.add(InteractivePlaybackEvent(
        session_id=session.id, event_type=payload.event_type, node_id=payload.node_id,
        edge_id=payload.edge_id, target_node_id=target_node_id, watch_seconds=payload.watch_seconds,
        event_json=payload.metadata,
    ))
    db.commit()
    return {"accepted": True}


@creator_router.get("/{timeline_id}/interactive-analytics", response_model=InteractiveAnalyticsResponse)
def interactive_analytics(timeline_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> InteractiveAnalyticsResponse:
    timeline = db.get(Timeline, timeline_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    _assert_owner(timeline, current_user)
    graph = _graph(timeline)
    sessions = int(db.scalar(select(func.count(InteractivePlaybackSession.id)).where(
        InteractivePlaybackSession.timeline_id == timeline.id
    )) or 0)
    visit_rows = db.execute(
        select(InteractivePlaybackEvent.node_id, func.count(InteractivePlaybackEvent.id))
        .join(InteractivePlaybackSession)
        .where(InteractivePlaybackSession.timeline_id == timeline.id, InteractivePlaybackEvent.event_type == "node_entered")
        .group_by(InteractivePlaybackEvent.node_id)
    ).all()
    dwell_rows = db.execute(
        select(InteractivePlaybackEvent.node_id, func.avg(InteractivePlaybackEvent.watch_seconds))
        .join(InteractivePlaybackSession)
        .where(InteractivePlaybackSession.timeline_id == timeline.id, InteractivePlaybackEvent.event_type.in_(["choice_selected", "session_ended"]))
        .group_by(InteractivePlaybackEvent.node_id)
    ).all()
    visits, dwell = dict(visit_rows), {key: float(value or 0) for key, value in dwell_rows}
    edge_rows = db.execute(
        select(InteractivePlaybackEvent.edge_id, func.count(InteractivePlaybackEvent.id))
        .join(InteractivePlaybackSession)
        .where(InteractivePlaybackSession.timeline_id == timeline.id, InteractivePlaybackEvent.event_type == "choice_selected")
        .group_by(InteractivePlaybackEvent.edge_id)
    ).all()
    edges_by_id = {edge.id: edge for edge in graph.edges}
    totals_by_source: dict[str, int] = {}
    for edge_id, count in edge_rows:
        if edge := edges_by_id.get(str(edge_id)):
            totals_by_source[edge.source_node_id] = totals_by_source.get(edge.source_node_id, 0) + int(count)
    return InteractiveAnalyticsResponse(
        timeline_id=timeline.id, sessions=sessions,
        nodes=[SankeyNode(id=node.id, label=node.title, visits=int(visits.get(node.id, 0)), average_dwell_seconds=round(dwell.get(node.id, 0), 2)) for node in graph.nodes],
        links=[SankeyLink(
            source=edge.source_node_id, target=edge.target_node_id, edge_id=edge.id, label=edge.choice_text,
            value=int(count), choice_share_percent=round(int(count) * 100 / totals_by_source[edge.source_node_id], 1),
        ) for edge_id, count in edge_rows if (edge := edges_by_id.get(str(edge_id)))],
    )
