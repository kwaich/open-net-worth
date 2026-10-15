"""
card_statements service — deterministic statement-cycle math for credit cards.

Pure read service: computes the current cycle's charges, last statement balance,
payment status, and days until/overdue relative to today. No writes.

Layering: imports only db and core (never api or ai). Follows the ledger-service
pattern of re-reading the full transaction list once per call so callers don't need
to pre-load it.

Key design decisions (PRD §3.2, R3, R4):
- "Current cycle" = legs on/after the last statement close date, up to today.
- "Last statement" = legs in the previous closed cycle.
- Payment detected as any kind=transfer with to_account_id == card_id on or after
  the close of the statement being summarized (R3: balance transfers and partial
  payments both count) — the statement's OWN printed close when fresh uploaded facts
  drive the summary, else the derived last statement close.
- Recency ladder: fresh facts drive only while the statement is the card's CURRENT
  obligation — it documents the latest closed cycle, or an older one still (partially)
  unpaid. A fully-paid OLDER statement falls through to the newest closed cycle as an
  estimate, flagged statement_missing_for_cycle so the UI nudges for the upload.
- Day-of-month clamping reuses the same _monthly_candidate logic as scheduling.py
  so a statement_close_day=31 in a 30-day month lands on the 30th (R4).
- All money returned in the card's native currency; the caller can convert to base.
  The response also carries base_amount / base_currency for the two balance fields
  (CSSTMT-9) but amounts are always recomputed from native, never from stored base.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from backend.core.fx import base_currency, to_base
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore

_log = logging.getLogger(__name__)

# Module-level singletons (same pattern as ledger_service).
_accounts = AccountStore()
_transactions = TransactionStore()


@dataclass
class StatementSummary:
    """Result of statement_summary(). All money in the card's native currency."""

    account_id: str
    currency: str  # card's native currency
    cycle_start: str  # ISO date — start of the OPEN (current) cycle
    last_statement_close: str  # ISO date — end of the previous closed cycle
    next_due_date: Optional[
        str
    ]  # ISO date — next payment due date; None when no due day configured
    days_until_due: Optional[int]  # negative = overdue; None when no due day configured

    # Charge totals (positive = charges; negative = credits/refunds)
    current_cycle_charges: float  # sum of expense legs in the open cycle
    last_statement_balance: float  # sum of expense legs in the closed cycle

    # Payment status
    paid: bool  # True if any transfer to the card exists after the statement closed
    partial: bool  # True when paid but payment_amount < last_statement_balance (partial payment)
    payment_amount: Optional[float]  # actual transfer amount; None when not paid
    # The ONE "amount still owed" figure (MATH-45) — every caller (AI tool, UI) reads this
    # instead of re-deriving it. last_statement_balance minus any payment, clamped to >= 0:
    # an overpayment owes nothing BACK through this field, it just never goes negative.
    remaining_amount: float

    # FX context (recomputed, never the stale stored base_amount)
    base_currency: str
    current_cycle_base: float
    last_statement_base: float

    # Uploaded-statement facts (the printed figures on the newest statement RECORD covering
    # this card). When the statement documents the last closed cycle, they OVERRIDE the
    # estimated warning above — due date + amount become the printed truth — while the
    # account's own config/estimates are never modified (statement informs, never replaces).
    due_source: str = "config"  # "statement" when next_due_date is the printed one
    statement_id: Optional[str] = None
    statement_date: Optional[str] = None
    statement_amount_due: Optional[float] = None
    statement_minimum_payment: Optional[float] = None
    statement_period_start: Optional[str] = None
    statement_period_end: Optional[str] = None
    # True when the linked statement documents an OLDER (fully paid) cycle and a newer
    # derived close has passed without its statement — the figures above are the newest
    # cycle's LEDGER ESTIMATE and the UI should nudge for the upload. The missing cycle's
    # close date is last_statement_close.
    statement_missing_for_cycle: bool = False


def _clamp_day(year: int, month: int, day: int) -> date:
    """Return a date for (year, month, day) clamping day to the month's last day.

    Mirrors _monthly_candidate in scheduling.py — day 31 in a 30-day month → 30th."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _last_statement_close(today: date, close_day: int) -> date:
    """The most recent past statement close date (the last time the cycle turned).

    If today IS the close day we treat the cycle as having JUST closed (so the
    current cycle is empty and the last statement is the cycle that ended today)."""
    candidate = _clamp_day(today.year, today.month, close_day)
    if candidate <= today:
        return candidate
    # candidate is in the future — roll back to previous month
    if today.month == 1:
        return _clamp_day(today.year - 1, 12, close_day)
    return _clamp_day(today.year, today.month - 1, close_day)


def _next_due_date_from_due_day(today: date, due_day: int) -> date:
    """The next upcoming payment due date based on a recurring day-of-month."""
    candidate = _clamp_day(today.year, today.month, due_day)
    if candidate >= today:
        return candidate
    # Already past this month's due date — use next month
    if today.month == 12:
        return _clamp_day(today.year + 1, 1, due_day)
    return _clamp_day(today.year, today.month + 1, due_day)


def _cycle_charges(
    txns: list[dict],
    card_id: str,
    since: date,  # inclusive
    until: date,  # exclusive (open end)
) -> float:
    """Sum of expense/income legs where the card is the source (from_account_id)
    within [since, until).

    Credit cards record charges as expenses (money leaving the card account — the
    card account is a liability, so its from_leg is the 'charge'). Transfers in
    (payments) are excluded from cycle charges.

    Returns a positive value for net charges, negative for net credits."""
    total = 0.0
    since_iso = since.isoformat()
    until_iso = until.isoformat()
    for t in txns:
        if t.get("deleted"):
            continue
        d = t.get("date", "")
        if d < since_iso or d >= until_iso:
            continue
        kind = t.get("kind", "")
        # Expenses charged to the card: from_account_id == card_id
        if kind in ("expense",) and t.get("from_account_id") == card_id:
            amt = t.get("from_amount")
            if amt is not None:
                total += float(amt)
        # Credits/refunds: income posted to the card (to_account_id == card_id, kind=income)
        elif kind == "income" and t.get("to_account_id") == card_id:
            amt = t.get("to_amount")
            if amt is not None:
                total -= float(amt)
    return round(total, 2)


def _find_payment(
    txns: list[dict],
    card_id: str,
    since: date,  # inclusive
) -> tuple[bool, Optional[float]]:
    """Detect a payment (kind=transfer with to_account_id == card_id) on or after
    `since`. Returns (paid, total_payment_amount)."""
    since_iso = since.isoformat()
    total = 0.0
    found = False
    for t in txns:
        if t.get("deleted"):
            continue
        if t.get("kind") != "transfer":
            continue
        if t.get("to_account_id") != card_id:
            continue
        if t.get("date", "") < since_iso:
            continue
        amt = t.get("to_amount")
        if amt is not None:
            total += float(amt)
            found = True
    if not found:
        return False, None
    return True, round(total, 2)


def _statement_records() -> list[dict]:
    """All uploaded statement records — seam for the facts lookup (tests patch this)."""
    try:
        from backend.db.statement_store import StatementStore

        return StatementStore().list_statements()
    except Exception:  # noqa: BLE001 — statement facts are an enhancement, never a blocker
        return []


def _latest_statement_facts(account_id: str) -> Optional[dict]:
    """The newest uploaded statement documenting this card (matched by the record's
    account links) that carries a printed due date or amount due. Read-only."""
    best: Optional[tuple[str, dict, dict]] = None
    for r in _statement_records():
        if r.get("account_id") != account_id and account_id not in (r.get("account_ids") or []):
            continue
        meta = r.get("import_meta") or {}
        if not meta.get("payment_due_date") and meta.get("amount_due") is None:
            continue
        key = meta.get("statement_date") or (r.get("created_at") or "")[:10]
        if best is None or key > best[0]:
            best = (key, r, meta)
    if best is None:
        return None
    key, r, meta = best
    return {
        "statement_id": r.get("id"),
        "statement_date": meta.get("statement_date") or key,
        "payment_due_date": meta.get("payment_due_date"),
        "amount_due": meta.get("amount_due"),
        "minimum_payment": meta.get("minimum_payment"),
        "period_start": meta.get("period_start"),
        "period_end": meta.get("period_end"),
    }


def _statement_close_date(facts: Optional[dict], fallback: date) -> date:
    """The statement's own close date — printed statement_date, else period_end.

    The payment-detection window for a facts-driven summary anchors HERE, not at the
    config-derived cycle close: a fresh statement documenting the PREVIOUS cycle
    (printed 06-05, due 06-30) would otherwise hide a payment made correctly before
    the derived 07-05 close and report a paid card as overdue (owner repro 2026-07-16)."""
    meta = facts or {}
    raw = meta.get("statement_date") or meta.get("period_end") or ""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


def _offset_due_estimate(facts: Optional[dict], last_close: date) -> Optional[date]:
    """Estimate the newest cycle's due date from the linked statement's own observed
    close→due offset (statement 06-05, due 06-30 = +25d ⇒ close 07-05 → due ~07-30).
    None when the statement doesn't carry a usable printed pair."""
    meta = facts or {}
    try:
        printed_due = date.fromisoformat(meta.get("payment_due_date") or "")
        stmt_date = date.fromisoformat(meta.get("statement_date") or "")
    except ValueError:
        return None
    if printed_due < stmt_date:
        return None
    return last_close + (printed_due - stmt_date)


def _facts_drive_summary(
    txns: list[dict],
    card_id: str,
    facts: Optional[dict],
    facts_fresh: bool,
    facts_current: bool,
    last_close: date,
) -> tuple[bool, bool]:
    """Recency ladder (owner 2026-07-16): fresh statement facts drive the summary while
    the statement is the card's CURRENT obligation — it documents the latest closed
    cycle, or an older one still (partially) UNPAID (real debt outranks recency; never
    hide unpaid money). A fully-paid OLDER statement stops driving: a newer cycle has
    closed since, so the summary falls through to that cycle's ledger estimate — a
    true-but-stale "paid in full" must never mask the current obligation.

    Returns (facts_drive, statement_missing_for_cycle)."""
    if not facts_fresh:
        return False, False
    if facts_current:
        return True, False
    paid, amount = _find_payment(txns, card_id, _statement_close_date(facts, last_close))
    printed = (facts or {}).get("amount_due")
    fully_paid = paid and (printed is None or (amount or 0.0) >= round(float(printed), 2))
    return (False, True) if fully_paid else (True, False)


def statement_summary(
    account_id: str,
    *,
    today: Optional[date] = None,
    accounts_store: Optional[AccountStore] = None,
    transactions_store: Optional[TransactionStore] = None,
) -> Optional[StatementSummary]:
    """Compute the statement summary for a credit card account.

    Returns None when:
    - The account does not exist.
    - The account is not type=credit.
    - Neither statement_close_day nor payment_due_day is configured AND no uploaded
      statement documents the card (an uploaded statement can stand in for cycle config).

    Estimation vs facts: the day-of-month config produces the ESTIMATED cycle; when the
    newest uploaded statement documents the last closed cycle, its printed due date and
    amount due override the estimate for the warning (due_source="statement"). The config
    itself is never modified. Recency ladder: a fresh statement one cycle OLDER than the
    derived close keeps driving only while money is still owed on it — once fully paid,
    the summary reverts to the newest closed cycle's estimate with
    statement_missing_for_cycle=True (upload nudge) so a stale "paid in full" never masks
    the current obligation. Never raises — unconfigured or unknown account returns None."""
    acct_store = accounts_store or _accounts
    txn_store = transactions_store or _transactions

    account = acct_store.find_by_id(account_id)
    if account is None:
        return None
    if account.get("type") != "credit":
        return None

    facts = _latest_statement_facts(account_id)
    close_day = account.get("statement_close_day")
    if not close_day and not (facts and facts.get("statement_date")):
        # No cycle config and no statement to stand in — return None per CSSTMT-7
        return None

    today = today or user_today()
    currency = (account.get("currency") or "USD").upper()
    base = base_currency()

    # ── Cycle boundaries ──────────────────────────────────────────────────────
    if close_day:
        last_close = _last_statement_close(today, int(close_day))
        # One month before last_close is the start of the PREVIOUS cycle.
        if last_close.month == 1:
            prev_close = _clamp_day(last_close.year - 1, 12, int(close_day))
        else:
            prev_close = _clamp_day(last_close.year, last_close.month - 1, int(close_day))
    else:
        # Statement-derived cycle: the printed statement date IS the last close.
        last_close = date.fromisoformat(facts["statement_date"])  # type: ignore[index]
        if last_close.month == 1:
            prev_close = _clamp_day(last_close.year - 1, 12, last_close.day)
        else:
            prev_close = _clamp_day(last_close.year, last_close.month - 1, last_close.day)
    cycle_start = last_close + timedelta(days=1)  # the day after the last close

    # A statement is FRESH when it documents the last closed cycle or the one just before
    # (±3 days absorbs the drift between the configured close day and the bank's actual
    # statement date) — only then may its printed figures participate at all. CURRENT
    # narrows that to the LATEST closed cycle; a fresh-but-older statement only keeps
    # driving while money is still owed on it (_facts_drive_summary recency ladder).
    facts_fresh = bool(
        facts
        and facts.get("statement_date")
        and facts["statement_date"] >= (prev_close - timedelta(days=3)).isoformat()
    )
    facts_current = bool(
        facts_fresh and facts["statement_date"] >= (last_close - timedelta(days=3)).isoformat()  # type: ignore[index]
    )

    txns = txn_store.all()
    facts_drive, statement_missing_for_cycle = _facts_drive_summary(
        txns, account_id, facts, facts_fresh, facts_current, last_close
    )

    # ── Due date ─────────────────────────────────────────────────────────────
    # When neither payment_due_date nor payment_due_day is configured, we return
    # next_due_date=None / days_until_due=None so the widget suppresses the DUE label
    # rather than showing a misleading anchor against the statement close day (NIT-4).
    explicit_due = account.get("payment_due_date")
    due_day = account.get("payment_due_day")
    if explicit_due:
        try:
            next_due: Optional[date] = date.fromisoformat(explicit_due)
        except ValueError:
            next_due = _next_due_date_from_due_day(today, int(due_day)) if due_day else None
    elif due_day:
        next_due = _next_due_date_from_due_day(today, int(due_day))
    else:
        # Only close_day configured; no payment due date available.
        next_due = None

    # A driving statement's PRINTED due date beats the estimate — past or future (an unpaid
    # printed date in the past is a real overdue, where the estimate would misleadingly
    # roll to next month). The config values above stay untouched.
    due_source = "config"
    if facts_drive and facts.get("payment_due_date"):  # type: ignore[union-attr]
        try:
            next_due = date.fromisoformat(facts["payment_due_date"])  # type: ignore[index]
            due_source = "statement"
        except ValueError:
            pass
    elif statement_missing_for_cycle and next_due is None:
        # Fall-through estimate with no due-day config: derive the newest cycle's due from
        # the PAID statement's own observed close→due offset. Stays due_source="config".
        next_due = _offset_due_estimate(facts, last_close)

    days_until_due: Optional[int] = (next_due - today).days if next_due is not None else None

    # ── Transaction fold ─────────────────────────────────────────────────────
    # Current cycle: [cycle_start, today] (inclusive on both ends; use tomorrow as exclusive end)
    current_charges = _cycle_charges(txns, account_id, cycle_start, today + timedelta(days=1))

    # Last statement: [prev_close + 1 day, last_close] inclusive
    prev_cycle_start = prev_close + timedelta(days=1)
    last_stmt_balance = _cycle_charges(
        txns, account_id, prev_cycle_start, last_close + timedelta(days=1)
    )

    # The printed amount due is the AUTHORITATIVE balance for a driving statement — it
    # includes carried-over balance, interest and fees the charge fold can't see.
    # Estimate otherwise (including the fully-paid-older-statement fall-through).
    if facts_drive and facts.get("amount_due") is not None:  # type: ignore[union-attr]
        last_stmt_balance = round(float(facts["amount_due"]), 2)  # type: ignore[index]

    # Payment detection: transfers to the card on or after the close of the statement
    # being SUMMARIZED — the statement's own close when facts drive the due/amount
    # (the facts and the payment window must agree on which statement is being paid),
    # the derived cycle close otherwise.
    payment_since = _statement_close_date(facts, last_close) if facts_drive else last_close
    paid, payment_amount = _find_payment(txns, account_id, payment_since)

    # Partial payment: paid=True but the total payment is less than the last statement
    # balance. A partial payment must NOT suppress the reminder — the user still owes money.
    partial = paid and (payment_amount or 0.0) < last_stmt_balance

    # The ONE "still owed" figure (MATH-45) — clamped so an overpayment reads as 0, never
    # a negative "amount owed BACK".
    remaining_amount = round(max(0.0, last_stmt_balance - (payment_amount or 0.0)), 2)

    # ── FX ───────────────────────────────────────────────────────────────────
    current_base = round(to_base(current_charges, currency, base), 2)
    last_base = round(to_base(last_stmt_balance, currency, base), 2)

    return StatementSummary(
        account_id=account_id,
        currency=currency,
        cycle_start=cycle_start.isoformat(),
        last_statement_close=last_close.isoformat(),
        next_due_date=next_due.isoformat() if next_due is not None else None,
        days_until_due=days_until_due,
        current_cycle_charges=current_charges,
        last_statement_balance=last_stmt_balance,
        paid=paid,
        partial=partial,
        payment_amount=payment_amount,
        remaining_amount=remaining_amount,
        base_currency=base,
        current_cycle_base=current_base,
        last_statement_base=last_base,
        due_source=due_source,
        statement_id=(facts or {}).get("statement_id") if facts_drive else None,
        statement_date=(facts or {}).get("statement_date") if facts_drive else None,
        statement_amount_due=(facts or {}).get("amount_due") if facts_drive else None,
        statement_minimum_payment=(facts or {}).get("minimum_payment") if facts_drive else None,
        statement_period_start=(facts or {}).get("period_start") if facts_drive else None,
        statement_period_end=(facts or {}).get("period_end") if facts_drive else None,
        statement_missing_for_cycle=statement_missing_for_cycle,
    )
