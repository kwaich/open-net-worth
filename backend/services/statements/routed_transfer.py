"""A review decision that routes one statement row as a transfer to another of the owner's
accounts (``edits: {to_account_id}``), booked in the same import batch through the ledger choke.

The routing RULE paths (``importer.route_transfer_draft`` for Automations and Lucius-pass ops) fall
back silently to the plain draft when the target is unusable, because nobody asked for that
specific outcome. An explicit decision did ask, so every unusable target RAISES here instead.
"""

from __future__ import annotations

from backend.core.errors import recovery_hint
from backend.core.fx import known_rate
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.statements.canonical import CanonicalRow
from backend.services.statements.tiers import StatementParseError


def transfer_target(edits: dict) -> str | None:
    """The ``to_account_id`` ref a decision's edits route the row to, or None for a plain row.
    ``to_account_id`` alone implies a transfer; ``kind`` may only say ``transfer``."""
    kind = edits.get("kind")
    ref = str(edits.get("to_account_id") or "").strip()
    if kind is not None and str(kind).strip().lower() != "transfer":
        raise StatementParseError(
            f"edits.kind can only be 'transfer' (got {kind!r}); a statement row is an "
            "expense or income by default"
        )
    if kind is not None and not ref:
        raise StatementParseError("edits.kind='transfer' needs edits.to_account_id")
    return ref or None


def routed_transfer_draft(
    draft: dict, row: CanonicalRow, account_id: str, currency: str, to_ref: str, idx: int
) -> dict:
    """Reshape a plain single-leg draft into a two-leg transfer between the statement's own
    account (``account_id``, holding ``currency``) and the account ``to_ref`` names.

    The statement's figure belongs to the statement account's leg. The other leg is stated in
    the statement's currency and converted by the choke into its own account's currency at the
    current rate — never the raw number relabelled; an unknown rate RAISES rather than guess.
    Dedup identity is the plain draft's ``external_refs`` verbatim, so a re-import still
    L0-dedups against this transfer."""
    rows = ledger_service.accounts.all()
    target_id = accounts_service.resolve_account_ref(to_ref, rows=rows)
    if target_id is None:
        names = [(a.get("name") or "").strip() for a in rows]
        raise StatementParseError(
            f"row {idx}: transfer destination {to_ref!r} is not an account."
            + recovery_hint(to_ref, names, tool_hint="list_accounts")
        )
    if target_id == account_id:
        raise StatementParseError(
            f"row {idx}: a transfer needs two different accounts — {to_ref!r} is the statement's "
            "own account. Leave the row as it is, or name the other account."
        )
    target = next(a for a in rows if a["id"] == target_id)
    if target.get("archived"):
        raise StatementParseError(
            f"row {idx}: {target.get('name') or target_id!r} is archived — new transfers can't "
            "be routed to it."
        )
    target_ccy = (target.get("currency") or currency).upper()
    if target_ccy != currency and known_rate(currency, target_ccy) is None:
        raise StatementParseError(
            f"row {idx}: no exchange rate for {currency} → {target_ccy}, so the "
            f"{target.get('name') or target_id!r} leg can't be derived. Refresh exchange rates "
            "and retry, or book the row as a plain expense/income."
        )
    outbound = row.direction == "OUT"
    transfer = {
        "date": draft["date"],
        "kind": "transfer",
        "description": draft["description"],
        "source": draft["source"],
        "external_refs": [dict(e) for e in (draft.get("external_refs") or [])],
        "from_account_id": account_id if outbound else target_id,
        "from_amount": row.amount,
        "from_currency": currency,
        "to_account_id": target_id if outbound else account_id,
        "to_amount": row.amount,
        "to_currency": currency,
    }
    if draft.get("category"):
        transfer["category"] = draft["category"]
    if draft.get("tags"):
        transfer["tags"] = list(draft["tags"])
    return transfer
