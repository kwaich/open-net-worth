"""Checkpoint-excluded durable queue, authority, and ownership state for Automations.

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``automation_runtime``) via
``PayloadTableStore``. Checkpoint-EXCLUDED (``ai/checkpoint._EXCLUDED_TABLES``) — durable
scheduler queue/outbox; restoring an older snapshot would replay or lose events.

``_mutate_state`` is the ONE rewritten method — every closure-based caller below is
byte-identical to the pre-migration JsonStore version (Design Note 3, plan 4c): each
``mutate: state -> (changed, result)`` closure is pure, no I/O, and only
``_mutate_state``'s OWN body (the persistence engine) changed — from
``self._write_lock`` + ``_load_items``/``_save_items`` to ``PayloadTableStore``'s
``_use_conn``/``_row``/``_fire_hook``/``_next_seq``/``_insert_row`` seam. Preserves:
single-transaction atomicity, the empty-state-deletes-the-row rule, and the
``(changed, result)`` contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import automation_runtime

_T = TypeVar("_T")


class AutomationRuntimeStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(automation_runtime, id_field="automation_id", engine=engine)

    def get_for_automation(self, automation_id: str) -> dict | None:
        row = self.find_by_id(automation_id)  # UNCHANGED — find_by_id never touches id_field
        return dict(row["state"]) if row else None

    def _mutate_state(
        self,
        automation_id: str,
        mutate: Callable[[dict], tuple[bool, _T]],
    ) -> _T:
        """Apply one state mutation against the latest row, atomically."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, automation_id)
            state = dict(existing["payload"].get("state") or {}) if existing else {}
            changed, result = mutate(state)
            if not changed:
                return result
            if state:
                payload = {"automation_id": automation_id, "state": state}
                self._fire_hook(conn, [(automation_id, existing)])
                if existing is None:
                    seq = self._next_seq(conn)
                    conn.execute(
                        sa_insert(self._table).values(
                            **self._insert_row(seq, automation_id, payload)
                        )
                    )
                else:
                    conn.execute(
                        sa_update(self._table)
                        .where(self._table.c.id == automation_id)
                        .values(payload=payload)
                    )
            elif existing is not None:
                self._fire_hook(conn, [(automation_id, existing)])
                conn.execute(sa_delete(self._table).where(self._table.c.id == automation_id))
        return result

    def put_for_automation(self, automation_id: str, state: dict) -> dict:
        """Replace a complete row.

        Production callers that own only part of runtime state must use the field-specific
        mutation methods below so they cannot overwrite run ownership or queued work.

        An UNCONDITIONAL replace — the base ``upsert()`` already does exactly this
        (``id_field="automation_id"`` resolves off the item dict correctly).
        """
        normalized = dict(state)
        self.upsert({"automation_id": automation_id, "state": normalized})
        return dict(normalized)

    def clear_fields(self, automation_id: str, fields: Iterable[str]) -> bool:
        """Remove selected fields while preserving all unrelated runtime authority."""
        selected = frozenset(fields)

        def mutate(state: dict) -> tuple[bool, bool]:
            changed = any(key in state for key in selected)
            for key in selected:
                state.pop(key, None)
            return changed, changed

        return self._mutate_state(automation_id, mutate)

    def update_event_state(self, automation_id: str, updates: dict) -> dict:
        """Merge event-observation fields without replacing ownership, grants, or queues."""
        normalized = dict(updates)

        def mutate(state: dict) -> tuple[bool, dict]:
            changed = any(state.get(key) != value for key, value in normalized.items())
            state.update(normalized)
            return changed, dict(state)

        return self._mutate_state(automation_id, mutate)

    def ensure_trigger_revision(
        self,
        automation_id: str,
        revision: str,
        trigger_fields: Iterable[str],
    ) -> dict:
        """Activate one trigger revision, discarding state derived by any older trigger."""
        selected = frozenset(trigger_fields)

        def mutate(state: dict) -> tuple[bool, dict]:
            if state.get("trigger_revision") == revision:
                queue = list(state.get("event_queue") or [])
                stale_keys = {
                    str(item.get("event_key") or "")
                    for item in queue
                    if not isinstance(item, dict) or item.get("trigger_revision") != revision
                }
                if not stale_keys:
                    return False, dict(state)
                state["event_queue"] = [
                    item
                    for item in queue
                    if isinstance(item, dict) and item.get("trigger_revision") == revision
                ]
                state["event_seen"] = [
                    key for key in state.get("event_seen") or [] if str(key) not in stale_keys
                ]
                return True, dict(state)
            for key in selected:
                state.pop(key, None)
            state["trigger_revision"] = revision
            return True, dict(state)

        return self._mutate_state(automation_id, mutate)

    def enqueue_events(
        self,
        automation_id: str,
        events: Sequence[dict],
        *,
        trigger_revision: str,
        max_queue: int,
        max_seen: int,
        state_updates: dict | None = None,
    ) -> tuple[int, int]:
        """Atomically merge observations and append unseen event items.

        Returns ``(written, dropped)``. The mutation always starts from the latest row, so
        a concurrent run claim or permission grant cannot be erased by queue persistence.
        """
        candidates = [dict(item) for item in events]
        updates = dict(state_updates or {})

        def mutate(state: dict) -> tuple[bool, tuple[int, int]]:
            if state.get("trigger_revision") != trigger_revision:
                return False, (0, 0)
            original = dict(state)
            queue = list(state.get("event_queue") or [])
            seen = list(state.get("event_seen") or [])
            seen_set = set(seen)
            written = 0
            dropped = 0
            overflow_at = None
            for item in candidates:
                key = str(item.get("event_key") or "")
                if not key or key in seen_set:
                    continue
                if len(queue) >= max_queue:
                    dropped += 1
                    overflow_at = item.get("queued_at")
                    continue
                queue.append(item)
                seen.append(key)
                seen_set.add(key)
                written += 1
            if written:
                state["event_queue"] = queue
                state["event_seen"] = seen[-max_seen:]
            if dropped:
                overflow = dict(state.get("event_overflow") or {})
                overflow["dropped"] = int(overflow.get("dropped") or 0) + dropped
                overflow["last_at"] = overflow_at
                overflow["error"] = f"Automation event queue full ({max_queue})"
                state["event_overflow"] = overflow
            state.update(updates)
            return state != original, (written, dropped)

        return self._mutate_state(automation_id, mutate)

    def claim_event_batch(
        self,
        automation_id: str,
        run_id: str,
        event_ids: Sequence[str],
        fired_at: str,
        trigger_revision: str,
    ) -> bool:
        """Durably move the queue head to an idempotent run-linked inflight batch."""
        expected = list(event_ids)
        if not expected:
            return False

        def mutate(state: dict) -> tuple[bool, bool]:
            if state.get("trigger_revision") != trigger_revision:
                return False, False
            inflight = state.get("event_inflight")
            if isinstance(inflight, dict):
                same = inflight.get("run_id") == run_id and inflight.get("event_ids") == expected
                return False, bool(same)
            queue = list(state.get("event_queue") or [])
            selected = queue[: len(expected)]
            if [item.get("event_key") for item in selected] != expected:
                return False, False
            if any(item.get("trigger_revision") != trigger_revision for item in selected):
                return False, False
            state["event_queue"] = queue[len(expected) :]
            state["event_inflight"] = {
                "run_id": run_id,
                "event_ids": expected,
                "items": selected,
                "fired_at": fired_at,
            }
            return True, True

        return self._mutate_state(automation_id, mutate)

    def complete_event_batch(self, automation_id: str, run_id: str) -> bool:
        """Acknowledge only the batch durably linked to the named run."""

        def mutate(state: dict) -> tuple[bool, bool]:
            inflight = state.get("event_inflight")
            if not isinstance(inflight, dict) or inflight.get("run_id") != run_id:
                return False, False
            state.pop("event_inflight", None)
            state["event_last_fired_at"] = inflight.get("fired_at")
            return True, True

        return self._mutate_state(automation_id, mutate)

    def restore_event_batch(self, automation_id: str, run_id: str) -> bool:
        """Return an unstarted inflight batch to the queue without duplicating items."""

        def mutate(state: dict) -> tuple[bool, bool]:
            inflight = state.get("event_inflight")
            if not isinstance(inflight, dict) or inflight.get("run_id") != run_id:
                return False, False
            queue = list(state.get("event_queue") or [])
            queued = {item.get("event_key") for item in queue}
            restored = [
                dict(item)
                for item in inflight.get("items") or []
                if isinstance(item, dict) and item.get("event_key") not in queued
            ]
            state["event_queue"] = [*restored, *queue]
            state.pop("event_inflight", None)
            return True, True

        return self._mutate_state(automation_id, mutate)

    def reconcile_event_batches(self, existing_runs: Iterable[tuple[str, str]]) -> dict[str, int]:
        """Consume started batches and restore batches that never created a run row.

        The ONE genuinely bulk (cross-automation) method — no closure reuse, a direct
        scan+conditional-update inside one transaction.
        """
        existing = set(existing_runs)
        consumed = 0
        restored = 0
        with self._use_conn(None) as conn:
            rows = list(conn.execute(select(self._table)))
            for r in rows:
                state = dict(r.payload.get("state") or {})
                inflight = state.get("event_inflight")
                if not isinstance(inflight, dict):
                    continue
                automation_id = str(r.payload.get("automation_id") or "")
                run_id = str(inflight.get("run_id") or "")
                if run_id and (automation_id, run_id) in existing:
                    state.pop("event_inflight", None)
                    state["event_last_fired_at"] = inflight.get("fired_at")
                    consumed += 1
                else:
                    queue = list(state.get("event_queue") or [])
                    queued = {item.get("event_key") for item in queue}
                    items = [
                        dict(item)
                        for item in inflight.get("items") or []
                        if isinstance(item, dict) and item.get("event_key") not in queued
                    ]
                    state["event_queue"] = [*items, *queue]
                    state.pop("event_inflight", None)
                    restored += 1
                self._fire_hook(conn, [(r.id, dict(r._mapping))])
                conn.execute(
                    sa_update(self._table)
                    .where(self._table.c.id == r.id)
                    .values(
                        payload={"automation_id": r.payload.get("automation_id"), "state": state}
                    )
                )
        return {"consumed": consumed, "restored": restored}

    def claim_run(
        self, automation_id: str, run_id: str, *, reclaim_from: str | None = None
    ) -> bool:
        """Atomically claim, or compare-and-set reclaim, the execution slot."""
        claimed, _ = self.claim_run_with_result(automation_id, run_id, reclaim_from=reclaim_from)
        return claimed

    def claim_run_with_result(
        self, automation_id: str, run_id: str, *, reclaim_from: str | None = None
    ) -> tuple[bool, bool]:
        """Return ``(claimed, acquired)`` so callers can safely roll back their own claim.

        Also fits ``_mutate_state`` — the "append a brand-new row when none exists yet"
        case is exactly its existing branch.
        """

        def mutate(state: dict) -> tuple[bool, tuple[bool, bool]]:
            current = state.get("running_run_id")
            if current == run_id:
                return False, (True, False)
            if current and current != reclaim_from:
                return False, (False, False)
            state["running_run_id"] = run_id
            return True, (True, True)

        return self._mutate_state(automation_id, mutate)

    def release_run(self, automation_id: str, run_id: str) -> bool:
        """Also fits ``_mutate_state`` — the "delete the row when state empties" case is
        exactly its existing branch."""

        def mutate(state: dict) -> tuple[bool, bool]:
            if state.get("running_run_id") != run_id:
                return False, False
            state.pop("running_run_id", None)
            return True, True

        return self._mutate_state(automation_id, mutate)

    def run_owners(self) -> list[tuple[str, str]]:
        """Return the persisted Automation-to-run ownership claims."""
        owners: list[tuple[str, str]] = []
        for row in self.all():  # UNCHANGED shape — row IS {"automation_id":.., "state":{...}}
            automation_id = row.get("automation_id")
            state = row.get("state") or {}
            run_id = state.get("running_run_id") if isinstance(state, dict) else None
            if isinstance(automation_id, str) and isinstance(run_id, str) and run_id:
                owners.append((automation_id, run_id))
        return owners

    def delete_for_automation(self, automation_id: str) -> bool:
        return self.delete(automation_id)  # base method — exact match for the original's shape

    def stage_event_delivery(self, kind: str, payload: object) -> str:
        """Durably stage one source observation before mutating edge/cooldown state.

        Both this and ``acknowledge_event_delivery`` are read-modify-write on the SAME
        "__outbox__" sentinel row — also fit ``_mutate_state`` (the fingerprint/sequence
        logic moves into the closure verbatim; only the persistence call changes).
        """
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(f"{kind}:{encoded}".encode()).hexdigest()[:20]

        def mutate(state: dict) -> tuple[bool, str]:
            deliveries = list(state.get("deliveries") or [])
            pending = next(
                (item for item in deliveries if item.get("fingerprint") == fingerprint),
                None,
            )
            if pending is not None:
                return False, str(pending["id"])
            if len(deliveries) >= 2_000:
                raise RuntimeError("Automation event delivery outbox is full")
            sequence = int(state.get("delivery_sequence") or 0) + 1
            delivery_id = hashlib.sha256(f"{fingerprint}:{sequence}".encode()).hexdigest()[:24]
            deliveries.append(
                {
                    "id": delivery_id,
                    "fingerprint": fingerprint,
                    "kind": kind,
                    "payload": payload,
                }
            )
            state["delivery_sequence"] = sequence
            state["deliveries"] = deliveries
            return True, delivery_id

        return self._mutate_state("__outbox__", mutate)

    def pending_event_deliveries(self) -> list[dict]:
        state = self.get_for_automation("__outbox__") or {}
        return [dict(item) for item in state.get("deliveries") or [] if isinstance(item, dict)]

    def acknowledge_event_delivery(self, delivery_id: str) -> None:
        def mutate(state: dict) -> tuple[bool, None]:
            deliveries = [
                item for item in state.get("deliveries") or [] if item.get("id") != delivery_id
            ]
            if deliveries == list(state.get("deliveries") or []):
                return False, None
            if deliveries:
                state["deliveries"] = deliveries
            else:
                state.pop("deliveries", None)
            return True, None

        self._mutate_state("__outbox__", mutate)
