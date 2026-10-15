"""PendingPlanStore — token-bound staged LEDGER write calls (the confirm gate).

A ledger write is the app's highest-frequency money mutation, and forensics over 289 real
sessions found 10.5% of write turns "succeeded and were still wrong": the row committed
cleanly and did not say what the user meant. ``update_transaction`` was the worst — 39 of
231 calls were a redo of an edit the user had just rejected. The structural cause is that
category precedence (explicit > rule > payee-default > None) and the spend-rules engine
resolve SILENTLY inside ``create_transaction``, so the value the user actually wanted is
only discoverable AFTER the write.

The gate: a gated write tool called WITHOUT a token stages its validated args here under a
freshly minted single-use token, returns a confirm card built from a PURE preview of what
those args resolve to, and commits nothing. The card's ✓ re-calls the SAME tool with the
token; the handler ``pop``s the stored args and executes THOSE.

Security properties (identical to ``McpPendingCallStore``, which this deliberately mirrors
rather than inventing a new shape; ``AlpacaPendingOrderStore`` was a third instance of the
same pattern until the Alpaca trading half was removed 2026-07-19):
  * **Args are authoritative-from-storage.** The confirming call's own transaction fields are
    DISCARDED — only the token is read from it. So a card showing "$12.40 Groceries" cannot
    commit "$1,240 Rent" no matter what the model reconstructs from conversation history (or
    what a prompt injection substitutes). This is the property that makes a one-tool
    preview/confirm split as safe as a two-tool propose/apply one.
  * **Single-use:** ``pop(token)`` deletes the entry, so a confirmed write can't be replayed
    by re-sending the same prose.
  * **No valid token ⇒ no stored-args write.** An unknown/expired/used token REFUSES rather
    than falling through to the model's args — a confirm can never fire without a real prior
    preview.
  * **Tool-bound:** the staged row records which tool staged it and ``pop_for_tool`` refuses a
    cross-tool redemption, so an ``update_transaction`` token can't apply an
    ``add_transactions`` payload.
  * **Expiring — LEGACY TOKEN ROWS ONLY:** a stale minted-token *preview* is refused
    (``_TTL_SECONDS``), because a preview promises resolved values (category, base amount)
    that really do go stale against the book. An **awaiting-approval row never expires** — see
    below.

**An awaiting-approval row is DURABLE** (ADR ``2026-07-30-approvals-never-expire``). It used to
share the 15-minute TTL and be reaped by a scheduler sweep, which meant a card left on screen
for 16 minutes was silently deleted and the ✓ then failed with "this approval is no longer
pending" — after the UI had already painted it approved. A permission card is a QUESTION PUT TO
A HUMAN, and a human is allowed to take as long as they like; the card also shows the literal
arguments that will run, so there is no resolved preview to go stale. Drift is guarded where it
actually lives (``ai.tools._confirm_gate.outcome_of`` binds the promised outcome and
``outcome_mismatch_result`` refuses on mismatch), not by a clock. An awaiting row is consumed
by exactly three things: approval, denial, and being superseded by the user's next message
(``assistant._abort_stale_pending``).

A flat keyed dict in the ``pending_plans`` table (WAVE 5D2b, JSON->DB — was
``pending_plans.json``, written atomically + ``chmod 600``) — capped (overflow drops token
rows first), and **checkpoint-EXCLUDED** (``ai.checkpoint._policy._EXCLUDED_TABLES``; it is
written INSIDE an interactive turn's CheckpointContext: stage persists, confirm pops) +
**NON_RESTORABLE** (``core.sensitive.NON_RESTORABLE_TABLES``: a restore must not un-pop an
already-confirmed call). The pre-migration file lived in ``DATA_DIR`` beside the sessions it
pauses, NOT in ``CACHE_DIR`` where it began life as a 15-minute queue (moved 2026-07-30) — a
durable pause is state a live transcript depends on, and a disposable-cache home for it was the
same bug in a different costume; a stale pre-2026-07-30 CACHE_DIR file is adopted once, at the
Wave 5D2b cutover itself (revision 0031), not per-construction the way the file store's own
``_migrate_from_cache_dir`` used to (there is no longer a "target path" to adopt INTO). Data-only
repository (import-linter: ``db`` may import ``core``/``models`` only).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import insert as sa_insert
from sqlalchemy import select

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import pending_plans

logger = logging.getLogger(__name__)

MAX_PENDING = 200  # cap the queue so a runaway preview loop can't grow the table unbounded

# A staged LEGACY-TOKEN plan is a snapshot of a preview the user was shown. 15 minutes is long
# enough for a real "let me check my statement" pause and short enough that the book underneath
# it hasn't meaningfully moved. Past it the model must re-preview — which re-resolves rules, the
# payee default and the FX rate against the CURRENT book rather than firing a stale resolution.
# This does NOT apply to an awaiting-approval row (see the module docstring).
_TTL_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PendingPlanStore(PayloadTableStore):
    def __init__(self, engine=None):
        # id_field documentation only — every method below is hand-rolled against
        # _row/_use_conn directly (mirrors SkillStateStore's own comment): a pending-plan
        # entry's key is a TOKEN or a tool_use_id, never a field inside its own payload.
        super().__init__(pending_plans, id_field="id", engine=engine)

    # ── raw read / write (WAVE 5D2b: DB-backed; was atomic_write_text + chmod 600) ──────

    def _read_raw(self) -> dict:
        with self._eng().connect() as conn:
            return {
                r.id: r.payload
                for r in conn.execute(select(self._table).order_by(self._table.c.seq))
            }

    def _write_raw(self, items: dict) -> None:
        """Whole-dict replace, in ONE transaction — mirrors
        ``SkillStateStore.replace_all_keyed``'s wipe-and-reinsert shape exactly (each
        entry's id is the DICT KEY — a token or a tool_use_id — never a field inside its
        own payload). Non-atomic ACROSS separate ``_read_raw``/``_write_raw`` calls, same as
        the pre-migration file store (no cross-call lock there either — only this one
        replace itself is transactional)."""
        with self._use_conn(None) as conn:
            existing_map = {r.id: dict(r._mapping) for r in conn.execute(select(self._table))}
            hook_rows = list(existing_map.items())
            hook_rows += [(k, None) for k in items if k not in existing_map]
            self._fire_hook(conn, hook_rows)
            conn.execute(self._table.delete())
            if items:
                conn.execute(
                    sa_insert(self._table),
                    [self._insert_row(i, k, v) for i, (k, v) in enumerate(items.items())],
                )

    def _prune(self, items: dict, *, reserve: int = 0) -> dict:
        """Drop expired TOKEN rows, then FIFO-trim so ``reserve`` more rows still fit under the
        cap.

        An AWAITING-APPROVAL row is never dropped for AGE — it is a question put to a human,
        who may answer it tomorrow (ADR ``2026-07-30-approvals-never-expire``). Cap overflow is
        the one thing that can still discard a live approval, so it spends its budget on token
        rows first and says so in the log when it cannot.

        ``reserve=1`` is what ``stage`` passes: trimming to exactly MAX_PENDING before adding
        one would leave MAX_PENDING+1 behind."""
        cutoff = _now() - timedelta(seconds=_TTL_SECONDS)
        live = {
            k: v
            for k, v in items.items()
            if v.get("state") == self._AWAITING
            or _parse_iso(v.get("created_at")) is None
            or _parse_iso(v["created_at"]) > cutoff
        }
        limit = MAX_PENDING - reserve
        if len(live) > limit:
            order = sorted(
                live.items(),
                key=lambda kv: (
                    kv[1].get("state") == self._AWAITING,
                    kv[1].get("created_at", ""),
                ),
            )
            for key, entry in order[: len(live) - limit]:
                if entry.get("state") == self._AWAITING:
                    logger.warning(
                        "pending plans: queue full at %d — dropping the awaiting approval %s",
                        MAX_PENDING,
                        key,
                    )
                live.pop(key, None)
        return live

    # ── API ────────────────────────────────────────────────────────────────────

    def peek(self, token: str) -> Optional[dict]:
        """Non-destructive read (for tests / inspection) — does not consume the token."""
        if not token:
            return None
        return self._read_raw().get(token)

    # ── approval-as-pending-tool_use (RFC 2026-07-22) ────────────────────────────
    #
    # THE staging path — a staged call is keyed by its OWN ``tool_use_id``: on approval the
    # SERVER pops it and appends the real result to that SAME id. One call, no re-call, no
    # token, and the model is never in the loop. Entries carry ``state="awaiting_approval"``
    # so ``_fix_dangling_tool_uses`` can tell a legitimate pause apart from a crash-orphan
    # (the crux of the RFC).
    #
    # A second, minted-token path used to live above this one, for the tools that build their
    # own resolved preview. It required the MODEL to re-call with the token — which on the
    # web nothing ever did, so a ✓ on a position/lot/vest card drew a second identical card
    # and never wrote (2026-08-25). Those tools stage here now; the token API is deleted.

    _AWAITING = "awaiting_approval"

    def stage_pending(
        self,
        tool_use_id: str,
        tool: str,
        args: dict,
        *,
        change_type: str = "",
        outcome: Optional[dict] = None,
        session_id: Optional[str] = None,
        change: Optional[dict] = None,
    ) -> None:
        """Stage a gated call AWAITING APPROVAL, keyed by its ``tool_use_id``.

        No token is minted — the tool_use_id IS the handle. ``args`` is stored VERBATIM (it,
        not any later re-send, is what commits on approve), with the same authoritative-from-
        storage property the token path has. ``session_id`` scopes the IDOR check on resolve.

        ``change`` is the card exactly as the live turn emitted it — DISPLAY only, never read
        on approve (``outcome`` is the re-verified promise). Reopening the conversation
        rebuilds the card from it; without it a self-staged card (a sale's lots and realized
        gain) came back as a generic raw-arguments card."""
        if not tool_use_id:
            return
        items = self._prune(self._read_raw(), reserve=1)
        items[tool_use_id] = {
            "tool_use_id": tool_use_id,
            "tool": tool,
            "args": args if isinstance(args, dict) else {},
            "change_type": change_type,
            "outcome": outcome or {},
            "state": self._AWAITING,
            "session_id": session_id,
            "change": change if isinstance(change, dict) else None,
            "created_at": _now().isoformat(),
        }
        self._write_raw(items)

    def get_pending(self, tool_use_id: str) -> Optional[dict]:
        """Non-destructive: the awaiting-approval entry for ``tool_use_id``, or ``None``.

        This is ``_fix_dangling_tool_uses``' check — a result-less tool_use WITH a pending
        record is a legitimate pause (skip it); WITHOUT one it is a crash-orphan (stub it)."""
        if not tool_use_id:
            return None
        entry = self._read_raw().get(tool_use_id)
        return entry if entry and entry.get("state") == self._AWAITING else None

    def pop_pending(self, tool_use_id: str) -> Optional[dict]:
        """Return + DELETE the awaiting-approval entry (single-use), or ``None``.

        Single-use is what guards a double-approve from two tabs: the first pop wins, the
        second gets ``None`` and is a no-op. ``None`` also for an unknown id or one already
        resolved. There is no TTL to enforce — an awaiting row is durable until it is answered
        or superseded (ADR ``2026-07-30-approvals-never-expire``)."""
        if not tool_use_id:
            return None
        items = self._read_raw()
        entry = items.get(tool_use_id)
        if entry is None or entry.get("state") != self._AWAITING:
            return None
        items.pop(tool_use_id, None)
        self._write_raw(items)
        return entry

    def list_awaiting(self) -> list[dict]:
        """Every awaiting-approval entry — the queue of cards still open, for inspection."""
        return [v for v in self._read_raw().values() if v.get("state") == self._AWAITING]

    def pop_pending_for_tool(self, tool_use_id: str, tool_name: str) -> Optional[dict]:
        """``pop_pending`` + the same tool-bound/TTL refusal semantics ``pop_for_tool`` gives
        the legacy token path — the ONE redemption choke ``ai.tools._confirm_gate.redeem``
        calls for the tool_use_id-keyed mechanism (RFC 2026-07-22).

        A cross-tool redemption (``entry["tool"] != tool_name``) is left STAGED and refused
        (``None``) — the entry survives so the real tool can still redeem it, mirroring
        ``pop_for_tool``'s "leave it staged, refuse this call". AGE is NOT a refusal reason
        here (ADR ``2026-07-30-approvals-never-expire``): however long the card sat, ✓ executes
        exactly the arguments it displayed."""
        entry = self.get_pending(tool_use_id)
        if entry is None:
            return None
        if entry.get("tool") != tool_name:
            return None
        return self.pop_pending(tool_use_id)


def _parse_iso(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
