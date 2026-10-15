"""Bounded tick-level counts for the news materiality screen (``monitoring.news_screen``)
— how many candidate news clusters were evaluated vs ruled material each poller tick, so a
self-hoster can sanity-check the pipeline (owner ask 2026-09-01: "I received pretty few
pushes tonight ... not sure if it's working properly") without needing per-item reasons,
just the aggregate breakdown.

Mirrors ``market_data_observability_store``'s own choice of ``table=runtime_state``
(checkpoint-EXCLUDED at the table level — see ``ai/checkpoint/_policy.py`` — this is
operational telemetry, never user data; a ``/rewind`` must never resurrect a stale count)
and the ``NamespacedSettingsStore`` shape (one row, no new Alembic migration) instead of a
full ``PayloadTableStore`` — the volume here (one entry per poller tick, three fields)
doesn't need per-row addressing.
"""

from __future__ import annotations

import time

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

_MAX_TICKS = 2000  # generous — weeks of poller ticks at typical cadence, bounded growth


class NewsScreenObservabilityStore(NamespacedSettingsStore):
    def __init__(self, engine=None):
        super().__init__("news_screen_observability", table=runtime_state, engine=engine)

    def record_tick(self, candidates: int, material: int, *, now: float | None = None) -> None:
        """One entry per ``news_screen._screen()`` invocation that actually reached the LLM.
        ``candidates`` = how many news clusters were sent to the screen this tick;
        ``material`` = how many it ruled material (enqueued). The rest were suppressed.
        ``now`` (test seam) overrides ``time.time()``."""
        doc = self.get()
        ticks = list(doc.get("ticks") or [])
        ts = time.time() if now is None else now
        ticks.append({"ts": ts, "candidates": int(candidates), "material": int(material)})
        self.save({"ticks": ticks[-_MAX_TICKS:]})

    def summary(self, hours: float = 24.0, *, now: float | None = None) -> dict:
        """Aggregate counts over the trailing window — the breakdown a self-hoster checks to
        sanity the pipeline isn't over- or under-suppressing. ``now`` (test seam) overrides
        ``time.time()``."""
        doc = self.get()
        cutoff = (time.time() if now is None else now) - hours * 3600
        recent = [t for t in (doc.get("ticks") or []) if t.get("ts", 0) >= cutoff]
        candidates_total = sum(int(t.get("candidates", 0)) for t in recent)
        material_total = sum(int(t.get("material", 0)) for t in recent)
        return {
            "window_hours": hours,
            "ticks": len(recent),
            "candidates_total": candidates_total,
            "material_total": material_total,
            "suppressed_total": candidates_total - material_total,
            "material_rate": (material_total / candidates_total) if candidates_total else None,
        }
