"""Failure-isolated tick counters for the news materiality screen — see
``db.news_screen_observability_store``'s own docstring for what this tracks and why."""

from __future__ import annotations

import logging

from backend.db.news_screen_observability_store import NewsScreenObservabilityStore

_LOG = logging.getLogger(__name__)


def record_tick(candidates: int, material: int) -> None:
    """Persist one tick's counts; telemetry can never fail the screen itself."""
    try:
        NewsScreenObservabilityStore().record_tick(candidates, material)
    except Exception as error:  # pragma: no cover - defensive operational isolation
        _LOG.debug("news-screen telemetry unavailable: %s", type(error).__name__)


def summary(hours: float = 24.0) -> dict:
    """The candidates-vs-material breakdown over the trailing window, or a zeroed shape if
    telemetry is unavailable — status stays available even when the counters aren't."""
    try:
        return NewsScreenObservabilityStore().summary(hours=hours)
    except Exception:
        return {
            "window_hours": hours,
            "ticks": 0,
            "candidates_total": 0,
            "material_total": 0,
            "suppressed_total": 0,
            "material_rate": None,
        }
