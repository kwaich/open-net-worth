"""What the book looks like RIGHT NOW to the daily briefing: how old its history is, what has
been imported, and the Dashboard's own "Needs attention" flags.

Persona QA round 6 (2026-10-14) caught the note describing a book it did not have: "Import your
DBS statement from last month" after the user had; "a single S$12 spend over the last three days"
on a book one day old; "Everything else looks in order" beside a Dashboard flag that cash dips
below the safety floor in February. Each block here reads the service the matching screen reads
(``portfolio_analytics``'s ``attention`` and ``commitments.coverage`` for the flags; the ledger and
its import batches for the history) and does no money math of its own.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from backend.core.user_time import user_today
from backend.db.recurring_stream_store import RecurringStreamStore
from backend.services import commitments as commitments_service
from backend.services import ledger as ledger_service
from backend.services import upcoming_bills
from backend.services.briefing_context import money
from backend.services.fx_cache import load_fx_cache

RECENT_DAYS = 3
# A new book's first transactions each change what the note should say, and so does each of the
# first few still-uncategorised ones being labelled; past this many the count stops mattering
# and only an import or a flag change makes the note stale.
_FIRST_TRANSACTIONS = 10


def _live_entries(txns: list[dict]) -> list[dict]:
    """The rows that are the user's own activity: not deleted, not an account's Starting Balance."""
    openings = ledger_service.opening_txn_ids()
    return [t for t in txns if not t.get("deleted") and t.get("id") not in openings]


def history_age_days(today: date, txns: list[dict]) -> int | None:
    """Days from the earliest real transaction to ``today`` (0 = it all began today); None when
    the book has no activity yet."""
    dates = sorted(d for t in _live_entries(txns) if (d := t.get("date")))
    if not dates:
        return None
    try:
        return max(0, (today - date.fromisoformat(dates[0][:10])).days)
    except ValueError:
        return None


def recent_window(age_days: int | None) -> tuple[int, str]:
    """(days, phrase) for "recently logged": three days, but never wider than the book is old."""
    days = RECENT_DAYS if age_days is None else min(RECENT_DAYS, age_days + 1)
    return days, "today" if days == 1 else f"in the last {days} days"


def _age_phrase(age_days: int) -> str:
    if age_days == 0:
        return "history begins today"
    if age_days == 1:
        return "history begins yesterday"
    return f"{age_days} days of history"


def _active_batches() -> list[dict]:
    batches = [b for b in ledger_service.import_batches.all() if b.get("status") == "active"]
    return sorted(batches, key=lambda b: b.get("created_at") or "")


def history_lines(today: date, txns: list[dict]) -> list[str]:
    """How much history the book holds and what was imported, so the note neither tells the user
    to import or log what is already there nor describes a period the book does not cover."""
    live = _live_entries(txns)
    if not live:
        return ["  No transactions yet beyond the accounts' starting balances."]
    dates = sorted(d[:10] for t in live if (d := t.get("date")))
    age = history_age_days(today, txns)
    lines = [
        f"  {len(live)} transaction{'' if len(live) == 1 else 's'} on file, dated "
        f"{dates[0]} to {dates[-1]}" + (f" ({_age_phrase(age)})" if age is not None else "")
    ]
    batches = _active_batches()
    if batches:
        last = batches[-1]
        label = (last.get("description") or "an import").strip()
        lines.append(
            f"  Statement imports on file: {len(batches)}. Most recent: {label} "
            f"({last.get('transaction_count', 0)} rows, {(last.get('created_at') or '')[:10]})"
        )
    lines.append(
        "  Describe any period only as far back as this history goes. Never tell the client to "
        "import or log anything listed here, and never hedge with 'if you haven't already'."
    )
    return lines


def _month_label(year_month: str) -> str:
    return date.fromisoformat(f"{year_month[:7]}-01").strftime("%b %Y")


def _shortfall_text(cov: dict, base: str) -> str:
    first = cov.get("earliest_shortfall_date")
    lowest = cov.get("lowest_balance_month")
    timing = []
    if first:
        timing.append(f"cash first dips below the floor in {_month_label(first)}")
    if lowest and (not first or lowest[:7] != first[:7]):
        timing.append(f"bottoms out in {_month_label(lowest)}")
    tail = f" — {' and '.join(timing)}" if timing else ""
    return (
        f"Funding shortfall: commitments leave the client "
        f"{money(cov['total_shortfall_base'], base)} short of their "
        f"{money(cov['low_balance_threshold'], base)} cash floor at the lowest point within the "
        f"next {cov['horizon_months']} months{tail}."
    )


def attention_flags(base: str) -> list[dict]:
    """The flags the Dashboard's Needs-attention card shows, in its order, as ``{id, text}``.

    The card is composed from the funding coverage, the portfolio analytics' ``attention``
    block and the card statements due soon — and so is this: same sources, same thresholds,
    only the wording is the briefing's own."""
    from backend.services.portfolio_analytics import portfolio_analytics
    from backend.services.portfolio_brief import compute_brief

    cov = commitments_service.coverage(base=base)
    a = portfolio_analytics(base)["attention"]
    # Concentration has one authority, the Portfolio Brief (ADR 2026-10-14 amendment); the card
    # lists its open decisions. `attention.top_equity` no longer exists.
    concentration = next(
        (sig for sig in compute_brief()["signals"] if sig.get("id") == "concentration"), None
    )
    flags: list[dict] = []
    if cov.get("at_risk"):
        flags.append({"id": "shortfall", "text": _shortfall_text(cov, base)})
    if cov.get("over_committed"):
        recon = cov["reconciliation"]
        flags.append(
            {
                "id": "over-earmarked",
                "text": f"Cash over-earmarked: {money(recon['earmarked_base'], base)} earmarked "
                f"but only {money(recon['liquid_base'], base)} liquid.",
            }
        )
    if concentration:
        flags.append(
            {"id": "concentration", "text": f"Position concentration: {concentration['narrative']}"}
        )
    # A card balance alone is not a flag; a statement due within a week or overdue is — the same
    # `cards_due_soon` list the card reads (owner decision, 2026-10-16).
    for card in upcoming_bills.cards_due_soon(base, load_fx_cache(), user_today()):
        estimated = " (estimated from the card's statement cycle)" if card["estimated"] else ""
        flags.append(
            {
                "id": "card-bill-" + card["account_id"],
                "text": f"Card bill: {card['label']}, {money(card['amount_base'], base)} still "
                f"owed on the last statement, {_due_in(card['days_until_due'])}{estimated}.",
            }
        )
    if a.get("contingent_flagged"):
        flags.append(
            {
                "id": "contingent",
                "text": f"{a['contingent_pct']:.0f}% contingent: depends on unvested equity or "
                "money owed to them.",
            }
        )
    if a.get("thin_cash"):
        cash = money(a["cash_total"], base)
        covers = (
            f"covers about {a['cash_months']} months of their spending"
            if a.get("cash_months") is not None
            else "is all the liquid cash there is"
        )
        flags.append({"id": "thin-cash", "text": f"Thin cash buffer: {cash} in cash {covers}."})
    return flags


def _due_in(days: int) -> str:
    if days < 0:
        return f"overdue by {-days} day{'' if days == -1 else 's'}"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due in {days} days"


def attention_lines(base: str) -> list[str]:
    flags = attention_flags(base)
    if not flags:
        return ["  No flags: the Dashboard's Needs attention card is clear."]
    return [f"  {i}. {f['text']}" for i, f in enumerate(flags, 1)]


def uncategorized_lines(base: str) -> list[str]:
    """How many transactions still have no category, from the ledger's own count — and the plain
    statement that there are none, so a note written before the client labelled them cannot keep
    asking them to (a PayNow labelled before opening the Dashboard was still on the to-do list)."""
    summary = ledger_service.uncategorized_summary(limit=3)
    if not summary["count"]:
        return [
            "  Every transaction has a category: do NOT tell the client to label or sort anything."
        ]
    seen = "; ".join(
        f"{(r['description'] or 'no description').strip()} {money(r['amount_base'], base)}"
        for r in summary["recent"]
    )
    return [
        f"  Uncategorised: {summary['count']} transaction{'' if summary['count'] == 1 else 's'} "
        f"({money(summary['total_base'], base)}), most recent: {seen}"
    ]


def _stream_state() -> list[tuple]:
    """Declared recurring streams and the user's confirm/dismiss corrections, by what changes the
    projection (never the row's timestamps)."""
    return sorted(
        (
            r.get("type"),
            r["id"],
            r.get("status"),
            r.get("direction"),
            r.get("amount"),
            r.get("currency"),
            r.get("cadence"),
            r.get("anchor_date"),
            r.get("start_date"),
            r.get("end_date"),
        )
        for r in RecurringStreamStore().all()
    )


def activity_fingerprint(base: str) -> str:
    """A digest of what a note must stay in step with that is not the book's structure: whether
    a statement has been imported, how many of a new book's first transactions exist, how many
    transactions are still uncategorised (labelling one changes what the note may ask), which
    Needs-attention flags are up, and which recurring streams the user declared or corrected (the
    note quotes "your salary arrives on the 1st" — a deleted stream must not keep saying so). Deliberately coarse — every transaction of an established book
    would regenerate the note — and DEBOUNCED by the caller, since an import lands hundreds of
    rows and a flag can flap with the day's balances."""
    txns = _live_entries(ledger_service.transactions.all())
    activity = {
        "imports": sorted(str(b.get("id")) for b in _active_batches()),
        "first_transactions": min(len(txns), _FIRST_TRANSACTIONS),
        "uncategorized": min(
            ledger_service.uncategorized_summary(limit=0)["count"], _FIRST_TRANSACTIONS
        ),
        "flags": [f["id"] for f in attention_flags(base)],
        "streams": _stream_state(),
    }
    return hashlib.sha256(json.dumps(activity, default=str).encode()).hexdigest()[:16]
