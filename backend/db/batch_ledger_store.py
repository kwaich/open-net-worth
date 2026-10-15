"""BatchLedgerStore — persists pending/completed provider batch jobs.

Each row records one submitted batch (anthropic/openai Message Batch API).
The scheduler reap sweep polls ``pending`` rows each tick, applies completed
results, and marks rows ``done``/``failed``/``expired``.

Schema::

    {
        "handle":       "<provider_batch_id>",   # opaque string from provider
        "task":         "intel",                  # owning subsystem
        "transport":    "anthropic",              # "anthropic" | "openai"
        "source":       "intel_scan",             # cost_tracker source label
        "model":        "claude-haiku-4-5",
        "custom_ids":   ["<id>", ...],
        "submitted_at": "2026-06-11T00:00:00+00:00",
        "status":       "pending",                # pending | done | failed | expired
        "poll_count":   0,
        "last_polled_at": null
    }

Secrets are never written here — the handle is the batch job id returned by
the provider API, not a credential.

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``batch_ledger`` — backend/db/tables.py) via
``PayloadTableStore``. Checkpoint-EXCLUDED (ai/checkpoint._EXCLUDED_TABLES) — runtime
scheduler state, re-fetchable from the provider, never snapshotted (mirrors the legacy
BATCH_LEDGER_JSON file exclusion). The old ``fail_closed_on_corrupt=False`` opt-out has no
DB-era equivalent — dropped, not ported (a DB read either succeeds or the database itself
is broken, orthogonal to this store).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import batch_ledger

# A batch older than this window is treated as expired regardless of provider.
# Anthropic batches expire after 24h; OpenAI after 24h — use 25h to be safe.
_MAX_AGE_HOURS = 25


class BatchLedgerStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(batch_ledger, id_field="handle", engine=engine)

    def pending(self) -> list[dict]:
        return [r for r in self.all() if r.get("status") == "pending"]

    def add(
        self,
        handle: str,
        task: str,
        transport: str,
        source: str,
        model: str,
        custom_ids: list[str],
        metadata: dict | None = None,
    ) -> dict:
        """Insert a new batch ledger row.

        ``metadata`` carries task-specific context needed by the reap handler to apply
        results (e.g. ``url_to_date`` mapping for intel scan).  It is stored as a
        JSON-serializable dict and never logged or exposed via any API.
        """
        row: dict = {
            "handle": handle,
            "task": task,
            "transport": transport,
            "source": source,
            "model": model,
            "custom_ids": custom_ids,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "poll_count": 0,
            "last_polled_at": None,
        }
        if metadata:
            row["metadata"] = metadata
        self.insert(row)
        return row

    def mark_polled(self, handle: str) -> None:
        # Preserves the ORIGINAL's own two-call shape (find_by_id then update_by_id, not a
        # single atomic RMW) — this store's poll-count race already existed pre-migration and
        # is benign (best-effort telemetry); not hardened here, per the behavior-preservation
        # mandate.
        self.update_by_id(
            handle,
            {
                "last_polled_at": datetime.now(timezone.utc).isoformat(),
                "poll_count": (self.find_by_id(handle) or {}).get("poll_count", 0) + 1,
            },
        )

    def mark_done(self, handle: str) -> None:
        self.update_by_id(handle, {"status": "done"})

    def mark_failed(self, handle: str) -> None:
        self.update_by_id(handle, {"status": "failed"})

    def mark_expired(self, handle: str) -> None:
        self.update_by_id(handle, {"status": "expired"})

    def is_expired(self, row: dict) -> bool:
        """True when the row is older than _MAX_AGE_HOURS."""
        submitted = row.get("submitted_at") or ""
        if not submitted:
            return True
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(submitted)
            return age.total_seconds() > _MAX_AGE_HOURS * 3600
        except (ValueError, TypeError):
            return True
