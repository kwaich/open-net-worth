"""
GET /api/intel/source-latency — per-source publish→arrival lag (news-ingestion-latency.md §5).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.services.news_latency import lag_stats

router = APIRouter()


@router.get("/api/intel/source-latency")
def get_source_latency(days: int = Query(default=7, ge=1, le=90)):
    return {"days": days, "sources": lag_stats(days=days)}
