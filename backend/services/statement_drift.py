"""Does the ledger agree with the bank at each statement close?

The one check that catches a wrong balance regardless of CAUSE — a mis-derived opening, a
deleted real charge, a double-booked payment, a bad merge. Every one of those shows up the same
way: the ledger's balance on the statement's close date does not equal the balance the bank
printed on it.

Why this and not another dedup rule
-----------------------------------
The owner's opening-balance error survived three weeks of imports, a duplicate-review queue
and two data migrations, and was only found by hand-comparing against a statement. Nothing in the
system asked the one question the bank had already answered. A per-row rule could not have
caught it: no single row was wrong — the OPENING was, and every row after it was fine.

Reading the result
------------------
``drift`` is signed, ledger-minus-bank in the account's own sign convention, so a positive drift
means the ledger thinks you have MORE (or owe LESS) than the bank does.

Three questions get answered, in this order, because each one narrows the next:

1. **Is it an error at all?** A charge dated on the cutoff day that the bank posts after it makes
   the ledger legitimately run ahead, and that CLEARS at the next close. Only drift that persists
   counts (``resolved_at_next_close``). On the newest statement this is not yet knowable, and the
   result says so rather than guessing (``pending``).
2. **Was it there from the start?** The same drift at every unreconciled close, *including the
   earliest one checked*, means a wrong opening (``constant_drift``).
3. **Otherwise, where?** ``drift_change`` + ``window_from`` name the window between two closes
   that introduced it — a date range to search instead of a whole account.

What it deliberately does not do
--------------------------------
It never guesses which side is right, and it never edits. A statement whose metadata read failed
(``meta_unavailable``) contributes NOTHING rather than a null-derived zero — the 2026-08-01 scar
was exactly a failed read cached as fact.
"""

from __future__ import annotations

import logging

from backend.services import ledger as ledger_service

logger = logging.getLogger(__name__)

# Below this, a difference is rounding/FX noise rather than a real disagreement.
DRIFT_TOLERANCE = 0.01


def _bank_sign(account: dict) -> int:
    """Statements print a POSITIVE figure for what you owe on a liability, while the ledger
    folds a liability negative (a charge is an out-leg). An asset agrees in both. So the
    comparison is ``ledger * sign`` vs the printed closing balance."""
    return -1 if (account.get("kind") == "liability") else 1


def statement_drift(
    accounts_rows: list[dict] | None = None,
    txns: list[dict] | None = None,
    statements: list[dict] | None = None,
) -> dict:
    """``{"rows": [...], "accounts": {account_id: {...}}}`` — one row per checkable statement.

    A statement is checkable when it carries BOTH a printed ``closing_balance`` and a
    ``statement_date``, and its account still exists. Everything else is skipped and counted,
    never silently dropped.
    """
    from backend.db.statement_store import StatementStore

    accounts_rows = accounts_rows if accounts_rows is not None else ledger_service.accounts.all()
    txns = txns if txns is not None else ledger_service.transactions.all()
    statements = statements if statements is not None else StatementStore().all()
    by_id = {a["id"]: a for a in accounts_rows}

    rows: list[dict] = []
    skipped = 0
    for record in statements:
        meta = record.get("import_meta") or {}
        # A FAILED metadata read is not an absent one. Its nulls are UNKNOWN, and deriving a
        # drift from them would manufacture a disagreement out of a provider outage.
        if meta.get("meta_unavailable"):
            skipped += 1
            continue
        closing, on_date = meta.get("closing_balance"), meta.get("statement_date")
        account = by_id.get(record.get("account_id") or "")
        if closing is None or not on_date or account is None:
            skipped += 1
            continue
        # Legs are stored in the ACCOUNT's currency, and the statement prints its own. Comparing
        # across two currencies would yield a large, confident, meaningless drift — so when the
        # statement states a currency and it disagrees, skip rather than subtract.
        stated = (meta.get("currency") or "").strip().upper()
        if stated and stated != (account.get("currency") or "").strip().upper():
            skipped += 1
            continue
        day = str(on_date)[:10]
        opening_date = ledger_service.opening_date(account, txns)
        ledger_bal = ledger_service.account_balance_as_of(account, day, txns, opening_date)
        as_bank_states_it = ledger_bal * _bank_sign(account)
        drift = round(as_bank_states_it - float(closing), 2)
        rows.append(
            {
                "account_id": account["id"],
                "account_name": account.get("name"),
                "statement_id": record.get("id"),
                "filename": record.get("filename"),
                "statement_date": day,
                "bank_closing_balance": round(float(closing), 2),
                "ledger_balance": round(as_bank_states_it, 2),
                "drift": drift,
                "in_tolerance": abs(drift) <= DRIFT_TOLERANCE,
            }
        )

    rows.sort(key=lambda r: (str(r["account_name"] or ""), r["statement_date"]))
    _localise(rows)
    return {"rows": rows, "accounts": _per_account(rows), "skipped": skipped}


def _localise(rows: list[dict]) -> None:
    """Stamp each row with how the drift MOVED, and whether a later close cleared it.

    Two things a bare drift column cannot tell you, both learned from a real production book:

    **Where.** The absolute drift is inherited — a wrong opening makes every later close wrong by
    the same amount, so "this close is off by X" does not mean the error is in THIS statement's
    rows. The CHANGE does: a drift that moves by X between two closes was introduced in that
    window, which is a searchable date range rather than the whole account.

    **Whether it is an error at all.** A charge dated on the cutoff day that the bank posts after
    it puts the ledger legitimately ahead — on a real production book one close was off by exactly
    ONE such row, and reconciled to 0.00 at the next close once the bank posted it. That is
    normal transaction-date vs posting-date lag, and it CLEARS at the next statement. Only drift
    that PERSISTS is a real disagreement, so a statement whose successor reconciles is reported
    as timing. Without this the check fires on almost every card that has a charge near its
    cutoff, and a check that cries wolf is one nobody reads.
    """
    previous: dict[str, dict] = {}
    for row in rows:
        row["resolved_at_next_close"] = None
        prior = previous.get(row["account_id"])
        if prior is None:
            row["drift_change"], row["window_from"] = None, None
        else:
            row["drift_change"] = round(row["drift"] - prior["drift"], 2)
            row["window_from"] = prior["statement_date"]
            # The drift is cumulative, so a clean later close proves everything before it
            # reconciles — whatever the earlier statement was missing has since posted.
            prior["resolved_at_next_close"] = row["in_tolerance"]
        previous[row["account_id"]] = row


def _per_account(rows: list[dict]) -> dict:
    """Per account: does anything still disagree, and — when it does — where is it?

    ``clean`` counts only UNRESOLVED drift. A statement that drifted and whose successor
    reconciles was posting-date lag, and calling that an error is how a check earns its way into
    being ignored. What remains is either still-open (the newest statement, no successor yet) or
    genuinely stuck.
    """
    out: dict[str, dict] = {}
    for row in rows:
        entry = out.setdefault(
            row["account_id"],
            {"account_name": row["account_name"], "statements": 0, "timing_only": 0, "open": []},
        )
        entry["statements"] += 1
        still_open = not row["in_tolerance"] and row["resolved_at_next_close"] is not True
        # Rows arrive oldest-first per account. Whether the EARLIEST checked close reconciled
        # decides if the opening can be blamed at all — see the constant-drift test below.
        if entry["statements"] == 1:
            entry["_earliest_open"] = still_open
        if row["in_tolerance"]:
            continue
        if not still_open:
            entry["timing_only"] += 1
        else:
            entry["open"].append(row)

    for entry in out.values():
        open_rows = entry.pop("open")
        earliest_open = entry.pop("_earliest_open", False)
        entry["drifting"] = len(open_rows)
        entry["clean"] = not open_rows
        # `bool(open_rows) and …` because an empty all() is vacuously true, which would report a
        # fully reconciled account as "undecided" — the one verdict it has definitively earned.
        entry["pending"] = bool(open_rows) and all(
            r["resolved_at_next_close"] is None for r in open_rows
        )
        drifts = [r["drift"] for r in open_rows]
        # Two conditions, and both are load-bearing. TWO drifting statements, because one close
        # off by X is equally consistent with a wrong opening and a single bad row — naming a
        # cause on that evidence would be a guess dressed as a finding. And the EARLIEST checked
        # close must itself be drifting: if an earlier statement reconciled, the opening was
        # provably right at that date, so a later constant drift cannot be the opening no matter
        # how convincingly it repeats.
        entry["constant_drift"] = (
            round(drifts[0], 2)
            if earliest_open and len(drifts) >= 2 and max(drifts) - min(drifts) <= DRIFT_TOLERANCE
            else None
        )
        if entry["constant_drift"] is not None:
            entry["likely_cause"] = (
                "opening balance — the same drift at every unreconciled close means the error "
                "predates the earliest one and propagated unchanged"
            )
        elif entry["pending"] and open_rows:
            entry["likely_cause"] = (
                "not yet decidable — this is the newest statement, so charges dated before its "
                "cutoff but posted after it look identical to missing rows until the next "
                "statement arrives"
            )
        elif open_rows:
            entry["likely_cause"] = (
                "a row between two closes — the drift CHANGES and never reconciles, so it was "
                "introduced in that window rather than carried from the start"
            )
    return out
