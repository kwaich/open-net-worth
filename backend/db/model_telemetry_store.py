"""ModelTelemetryStore — rolling per-model latency/health stats from real turns.

Providers stamp ``ttft_ms`` / ``duration_ms`` on their usage events and
``assistant.run`` feeds one sample per model turn.

**WRITE-ONLY as of 2026-08-06.** Its only reader was the auto-routing engine's scoring
pass, deleted with automatic model switching (ADR
``2026-08-06-auto-model-switching-removed``). The owner kept the measurement as raw
observability, so ``summary``/``all_summaries`` are the documented way to read the file
even though nothing in the app calls them — do not "clean up" the write hook on the
grounds that nothing consumes it.

Keyed by BARE model id (the same model id is the same model regardless of which
provider ref invoked it). Rolling window of the last ``_MAX_SAMPLES`` per model.
Checkpoint-EXCLUDED, degradable (a never-measured model simply has no row).

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``model_telemetry``) via
``PayloadTableStore`` — the ONE hand-rolled store this wave (like ``plan_state``/
``budget_assignments`` in plan 4a): subclasses ``PayloadTableStore`` for its plumbing
only (``_use_conn``/``_row``/``_fire_hook``/``_next_seq``/``_insert_row``), never its
domain methods (``insert``/``upsert``/``update_by_id``/``query``/``all`` all assume the
id lives INSIDE the payload dict, which is false here — the shape is
``{model: {samples,errors,total}}``, no ``"model"`` key inside the value). ``id_field``
is accepted at construction for symmetry with every other store but is never actually
consumed — every domain method below is hand-rolled directly against ``_row``/``find_by_id``.
The old ``fail_closed_on_corrupt=False`` opt-out has no DB-era equivalent — dropped, not
ported.
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import model_telemetry

_MAX_SAMPLES = 50


def _p50(values: list) -> Optional[float]:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    return float(vals[len(vals) // 2])


class ModelTelemetryStore(PayloadTableStore):
    def __init__(self, engine=None):
        # id_field="model" is documentation only — every method below is hand-rolled and
        # never touches self._id_field (see module docstring).
        super().__init__(model_telemetry, id_field="model", engine=engine)

    def record_sample(
        self,
        model: str,
        ttft_ms: Optional[float] = None,
        duration_ms: Optional[float] = None,
        output_tokens: int = 0,
        error: bool = False,
    ) -> None:
        """Append one turn's measurement (best-effort — callers must not die on this).

        Both a success and an error entry land in ``entry["samples"]`` (the rolling window
        `summary()` reads) — D1 (RFC 2026-09-04-ai-model-performance-telemetry §0): errors must
        be visible to a "lately" question, which a lifetime-only counter cannot answer.
        ``entry["errors"]``/``entry["total"]`` keep incrementing, unbounded, as the honest
        lifetime record — nothing reads them today, but they are not removed.
        """
        model = (model or "").strip()
        if not model:
            return
        tok_per_sec: Optional[float] = None
        if duration_ms and duration_ms > 0 and output_tokens > 0:
            tok_per_sec = output_tokens / (duration_ms / 1000.0)
        with self._use_conn(None) as conn:
            existing = self._row(conn, model)
            entry = (
                dict(existing["payload"]) if existing else {"samples": [], "errors": 0, "total": 0}
            )
            entry["total"] = int(entry.get("total") or 0) + 1
            samples = list(entry.get("samples") or [])
            if error:
                entry["errors"] = int(entry.get("errors") or 0) + 1
                samples.append({"error": True, "ts": time.time()})
            else:
                samples.append(
                    {
                        "ttft_ms": ttft_ms,
                        "duration_ms": duration_ms,
                        "tok_per_sec": tok_per_sec,
                        "ts": time.time(),
                    }
                )
            entry["samples"] = samples[-_MAX_SAMPLES:]
            self._fire_hook(conn, [(model, existing)])
            if existing is None:
                seq = self._next_seq(conn)
                conn.execute(sa_insert(self._table).values(**self._insert_row(seq, model, entry)))
            else:
                conn.execute(
                    sa_update(self._table).where(self._table.c.id == model).values(payload=entry)
                )

    def summary(self, model: str) -> Optional[dict]:
        """{ttft_ms_p50, duration_ms_p50, tok_per_sec_p50, error_rate, errors, samples} or None
        if unmeasured. Percentiles are computed only over non-error samples in the window;
        `error_rate`/`errors` are windowed too (D1) — `errors_in_window / len(window)`, not the
        lifetime `errors`/`total` counters. `samples` is the count of LATENCY samples (excludes
        error entries), so it still never exceeds `_MAX_SAMPLES`.
        """
        entry = self.find_by_id((model or "").strip())  # find_by_id never touches id_field — fine
        if not isinstance(entry, dict):
            return None
        window = [s for s in entry.get("samples") or [] if isinstance(s, dict)]
        total = int(entry.get("total") or 0)
        if not window and not total:
            return None
        latency_samples = [s for s in window if not s.get("error")]
        errors_in_window = sum(1 for s in window if s.get("error"))
        return {
            "ttft_ms_p50": _p50([s.get("ttft_ms") for s in latency_samples]),
            "duration_ms_p50": _p50([s.get("duration_ms") for s in latency_samples]),
            "tok_per_sec_p50": _p50([s.get("tok_per_sec") for s in latency_samples]),
            "error_rate": (errors_in_window / len(window)) if window else 0.0,
            "errors": errors_in_window,
            "samples": len(latency_samples),
        }

    def all_summaries(self) -> dict:
        with self._eng().connect() as conn:
            models = [r.id for r in conn.execute(select(self._table.c.id))]
        return {m: self.summary(m) for m in models}
