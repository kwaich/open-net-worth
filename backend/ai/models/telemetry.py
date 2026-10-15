"""Shared structured-output-error telemetry helper for the CLI providers.

Mirrors `ai/assistant.py:_record_model_telemetry`'s discipline exactly: function-local store
import, bare `except Exception`, `logger.debug` on failure — a measurement must never break a
turn or perturb the caller's retry handling.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def record_structured_output_error(model: str) -> None:
    """One FAILED structured-output/completion ATTEMPT for `model`. Best-effort:
    a measurement must never break a turn or perturb the caller's retry handling."""
    try:
        from backend.db.model_telemetry_store import ModelTelemetryStore

        ModelTelemetryStore().record_sample(model, error=True)
    except Exception:  # noqa: BLE001 - telemetry must never break a turn
        log.debug("structured-output telemetry write failed", exc_info=True)
