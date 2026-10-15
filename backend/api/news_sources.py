"""
GET    /api/intel/sources          — every source row joined with live health (latency + streak)
PATCH  /api/intel/sources/{id}     — update a source's policy: enabled / cadence / min_interval_sec
POST   /api/intel/sources          — add a user-supplied custom firehose feed
DELETE /api/intel/sources/{id}     — remove a user-added source (builtin rows: 409, disable instead)

INT9 Phase B (.claude/rfcs/news-source-management.md §3). The read side joins TWO independently
computed signals per row on top of ``services.news_sources.list_sources`` (which already embeds
per-`via` latency + `last_ok_at`): the hot-tier poller's own in-process failure-streak/backoff
state (``monitoring.rss_poller_hot.source_health_snapshot``). That peek happens HERE, at the API
layer, rather than in ``services/news_sources.py`` — ``monitoring.rss_poller_hot`` already
imports ``services.news_sources`` (for ``active_sources``), so a
``services.news_sources -> monitoring.rss_poller_hot`` import would cycle straight back through
that very module. ``api -> monitoring`` is NOT constrained by any of the 5 import-linter
contracts (existing precedent: ``api/intel_assets.py``, ``api/briefing.py``,
``api/intel_digest.py`` all reach into ``backend.monitoring`` directly), so the join happens
here instead.

A row's `failure_streak` is `null` when the hot-tier poller has no recorded state for its `via`
— meaning "healthy/unknown", not "confirmed zero failures". Only cadence="hot" rows the hot tier
has actually ticked at least once (regulatory/outlet/topic_engine/custom-firehose) ever carry a
non-null value; a `fast`/`slow` row is always `null` in Phase B (those tiers aren't wired to the
store yet — RFC §5).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import news_sources as news_sources_service

router = APIRouter()


class SourcePolicyPatch(BaseModel):
    """Loosely typed on purpose: `cadence`/`min_interval_sec` are validated by
    ``NewsSourceStore.update_policy`` (structural, field-prefixed ``ValidationError``) rather
    than a Pydantic ``Literal``/``Field(ge=...)`` — a Pydantic-level rejection would return
    FastAPI's own structured error shape instead of the plain "field: reason" string the UI
    routes verbatim (RFC §3 UI contract item 5)."""

    enabled: Optional[bool] = None
    cadence: Optional[str] = None
    min_interval_sec: Optional[int] = None


class SourceCreate(BaseModel):
    label: str
    url: str
    cadence: str = "slow"


@router.get("/api/intel/sources")
def get_sources(days: int = Query(default=7, ge=1, le=90)):
    from backend.monitoring.rss_poller_hot import source_health_snapshot

    rows = news_sources_service.list_sources(days=days)
    health = source_health_snapshot()
    for row in rows:
        row["failure_streak"] = health.get(row.get("via"))
    return {"sources": rows}


@router.patch("/api/intel/sources/{source_id}")
def patch_source(source_id: str, body: SourcePolicyPatch):
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no fields to update")
    try:
        return news_sources_service.update_source_policy(source_id, changes)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/intel/sources", status_code=201)
def create_source(body: SourceCreate):
    try:
        return news_sources_service.create_user_source(
            label=body.label, url=body.url, cadence=body.cadence
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/intel/sources/{source_id}")
def delete_source(source_id: str):
    try:
        deleted = news_sources_service.delete_user_source(source_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "deleted_id": deleted["id"]}
