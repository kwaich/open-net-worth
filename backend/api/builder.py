"""Auth-gated human API for the Open Net Worth Builder workspace."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.services.builder import (
    ActiveRunError,
    BuilderValidationError,
    EngineUnavailableError,
    RunNotFoundError,
    builder_manager,
)

router = APIRouter()


class BuilderRunBody(BaseModel):
    engine: Literal["codex", "claude"]
    mode: Literal["plan", "build"]
    prompt: str


@router.get("/api/builder/status")
def get_builder_status():
    return builder_manager.status()


@router.post("/api/builder/runs")
async def start_builder_run(body: BuilderRunBody):
    try:
        return await builder_manager.start(body.engine, body.mode, body.prompt)
    except BuilderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ActiveRunError, EngineUnavailableError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/builder/runs/{run_id}")
def get_builder_run(run_id: str, after: int = Query(default=0, ge=0)):
    try:
        return builder_manager.get(run_id, after=after)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Builder run not found.") from exc


@router.delete("/api/builder/runs/{run_id}")
async def stop_builder_run(run_id: str):
    try:
        return await builder_manager.cancel(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Builder run not found.") from exc
