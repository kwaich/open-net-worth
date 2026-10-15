"""Per-model latency + structured-output-validity snapshot — a read-only projection of
ModelTelemetryStore's rolling window. No writes, no money math, never raises."""

from __future__ import annotations

import logging

from backend.services.cost import provider_of_model

logger = logging.getLogger(__name__)


def model_telemetry(store=None) -> list[dict]:
    """One row per model that has ever recorded a sample, most-measured first.

    Row: {model, provider, ttft_ms_p50, duration_ms_p50, tok_per_sec_p50,
          error_rate, errors, samples}. `provider` is a DERIVED display label
          (never a recorded fact) and is None when the catalog doesn't know the id.

    Sorted server-side: `samples` descending, then `model` ascending — the frontend must
    not have to invent an order. A store read failure degrades to `[]` (matches
    `cost._load_records`'s precedent) rather than surfacing a 500 for a normal empty read.
    """
    from backend.db.model_telemetry_store import ModelTelemetryStore

    store = store or ModelTelemetryStore()
    try:
        summaries = store.all_summaries()
    except Exception:
        logger.warning("model_telemetry: failed to read model_telemetry", exc_info=True)
        return []

    rows: list[dict] = []
    for model, summary in summaries.items():
        if summary is None:
            continue
        label = provider_of_model(model)
        rows.append(
            {
                "model": model,
                "provider": None if label == "Unknown" else label,
                **summary,
            }
        )
    rows.sort(key=lambda row: (-row["samples"], row["model"]))
    return rows
