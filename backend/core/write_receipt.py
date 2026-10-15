"""Per-turn ledger WRITE COLLECTION — the seam behind the write-then-verify chip.

## Why this is a ContextVar seam and not a parameter

The chip has to answer "what did that turn actually change to my book?" — and the only
place that knows, for every write path at once, is the ledger's own audit choke
(``services/audit.py``, which every create/update/delete already funnels through). The
turn boundary, though, is owned by ``ai/assistant.py``, three layers up. Threading a
collector down would mean widening ``create_transaction`` / ``bulk_*`` /
``update_transaction`` and every caller and test.

So this is the same ContextVar seam ``core.audit_context`` already is, for the same
reason and with the same shape: the upper layer OPENS a receipt for the turn, the audit
choke RECORDS into it (``services`` → ``core``, no reverse import), the upper layer
DRAINS it at turn end. It lives in ``core`` (leaf, stdlib only) because it crosses a
layer boundary; import-linter contracts 1 and 3 are why it cannot live anywhere else.

## Closed by default — this is load-bearing, not defensive

``record_write`` is a NO-OP when no receipt is open. Every non-chat write path — a REST
edit from the register, an import, a migration script, a test — therefore pays nothing
and behaves byte-identically to before. Only a turn that explicitly opened a receipt
collects one, so "the chip shows what THIS turn wrote" is true by construction rather
than by filtering timestamps afterwards.

Collection is deliberately THIN: op + entity TYPE + entity id + whatever provenance the
choke already had in hand. It stores no rows, does no store reads and resolves no labels
— all of that happens ONCE, at turn end, in ``services.write_receipt`` (rows) and
``services.mentions.labels`` (chip labels). A write path must never pay for a chip that
may never be rendered.

## Why a TYPE, and why it must be a registered mention type

Every recorded row renders as a real mention chip, so ``entity_type`` must be a type the
mention registry knows how to label and draw (``services.mentions.registry.REGISTRY``).
A row with no chip renderer is worse than no row, so an unregistered type is dropped by
the reader and the pairing is asserted by a test at import time
(``tests/test_write_receipt.py::test_every_recorded_entity_type_is_a_registered_mention_type``)
rather than discovered on screen.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

# The per-turn collection buffer. ``None`` (the default) means "no receipt open" — the
# ordinary state for every write path outside an assistant turn.
_writes: ContextVar[Optional[list]] = ContextVar("turn_write_receipt", default=None)

# A runaway turn must not accumulate an unbounded list in memory. Past this many writes
# the receipt stops collecting DETAIL but keeps counting, so the chip can still say "412
# transactions" honestly instead of silently reporting the first 500.
MAX_COLLECTED = 500


def open_receipt() -> "Token[Optional[list]]":
    """Open a fresh collection buffer for this turn. Pair with ``close_receipt(token)``
    in a ``finally`` — the token restores the OUTER buffer, so a nested turn (an agent
    dispatched mid-turn recursing into ``assistant.run``) cannot null out the still
    running outer turn's receipt. Same nesting contract as ``core.audit_context``."""
    return _writes.set([])


def close_receipt(token: "Optional[Token[Optional[list]]]" = None) -> None:
    """Reset the buffer at turn end. Tolerates a token created in a different
    ``contextvars.Context`` (an abandoned generator finalised from a forked context)
    the same way ``core.audit_context.clear_audit_scope`` does — falling back to a plain
    ``set(None)`` rather than raising past the caller's own turn-end cleanup."""
    if token is not None:
        try:
            _writes.reset(token)
            return
        except ValueError:
            pass
    _writes.set(None)


def record_write(
    op: str,
    entity_id: str,
    *,
    entity_type: str = "transaction",
    meta: Optional[dict] = None,
    automation_applied: Optional[dict] = None,
) -> None:
    """Record one ENTITY mutation onto the open receipt. A NO-OP when none is open.

    ``op`` is a CLOSED vocabulary — ``create`` | ``update`` | ``delete``. A domain verb
    (acquire / dispose / vest / merge / assign / expire) maps onto one of those three at
    its own choke and rides in ``meta["verb"]``; the chip head must never grow a
    per-domain verb vocabulary the UI has to know about.

    ``entity_type`` defaults to ``"transaction"`` so the ledger's audit choke needs no
    special entity metadata.

    Never raises: this is called from inside chokes that are themselves best-effort and
    must never be able to fail a write they are only describing."""
    try:
        buf = _writes.get()
        if buf is None or not entity_id or not entity_type:
            return
        if len(buf) >= MAX_COLLECTED:
            # Keep counting past the cap so the total stays honest; drop the detail. The
            # TYPE is kept, so a capped mixed turn can still say "412 transactions, 30 lots".
            buf.append({"type": entity_type, "op": op, "id": "", "meta": None})
            return
        if automation_applied:
            meta = {**(meta or {}), "automation_applied": automation_applied}
        buf.append({"type": entity_type, "op": op, "id": entity_id, "meta": meta or None})
    except Exception:  # noqa: BLE001 - never break a write to describe it
        return


def collected() -> list:
    """The writes recorded so far this turn (empty when no receipt is open)."""
    return list(_writes.get() or [])
