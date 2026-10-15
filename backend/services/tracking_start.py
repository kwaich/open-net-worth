"""Derivable detection of transactions that fall before an account's tracking start (the date
of its Starting Balance row). No new fields — pure queries over accounts + transactions. Feeds
the health surface and the write-time flag. Also owns the Preserve-align write. Layer: services
(engine-agnostic — takes no position on how a budget engine treats any of this)."""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.services import ledger as _ledger

_LEGS = (("from_account_id", "from_amount"), ("to_account_id", "to_amount"))

# Only accounts whose balance is pure cash may be auto-aligned: their opening is a
# plain cash figure and their hidden legs are genuine cash movements. Everything else
# (investments, loans, fixed deposits, retirement…) summarizes non-cash activity into the
# opening — rewinding it there manufactures fiction (the IBKR −$44K lesson).
_ALIGNABLE_TYPES = frozenset({"cash", "credit"})


def _by_id(accounts_rows: list[dict]) -> dict[str, dict]:
    return {a["id"]: a for a in accounts_rows}


def _is_actionable(account: dict | None) -> bool:
    """A pre-start leg is a PROBLEM worth surfacing only on a plain cash/card account
    (which can be aligned). On a summarizing account (brokerage/crypto/loan/property) the
    pre-start history is folded into the opening BY DESIGN — flagging it is crying wolf
    about correct data, so the health surface and write-flag ignore it."""
    return bool(account and account.get("type") in _ALIGNABLE_TYPES)


def hidden_legs(accounts_rows: list[dict], txns: list[dict]) -> dict[str, list[dict]]:
    """account_id → non-deleted legs dated before that account's tracking start."""
    cutoffs = _ledger.cutoffs_by_account(accounts_rows, txns)
    out: dict[str, list[dict]] = {}
    for t in txns:
        if t.get("deleted"):
            continue
        for acct_field, amt_field in _LEGS:
            aid = t.get(acct_field)
            if aid in cutoffs and _ledger.is_before_tracking_start(t.get("date"), cutoffs[aid]):
                out.setdefault(aid, []).append(
                    {
                        "txn_id": t.get("id"),
                        "date": t.get("date"),
                        "kind": t.get("kind"),
                        "amount": t.get(amt_field),
                        "description": t.get("description") or "",
                    }
                )
    return out


def straddling_transfers(
    accounts_rows: list[dict], txns: list[dict], cutoffs: dict[str, str | None]
) -> list[dict]:
    """Transfers with exactly ONE leg before its account's tracking start — the
    "money leaves with no matching arrival" case that reads as broken.

    ``txns`` is the set to SCAN and may be a single row (``flags_for_txn``); ``cutoffs`` is
    resolved separately, always from the full book — hence a parameter, not a local."""
    out: list[dict] = []
    for t in txns:
        if t.get("deleted"):
            continue
        f, to = t.get("from_account_id"), t.get("to_account_id")
        if not (f and to):
            continue
        fh = f in cutoffs and _ledger.is_before_tracking_start(t.get("date"), cutoffs[f])
        th = to in cutoffs and _ledger.is_before_tracking_start(t.get("date"), cutoffs[to])
        if fh ^ th:
            out.append(
                {
                    "txn_id": t.get("id"),
                    "date": t.get("date"),
                    "amount": t.get("from_amount") if fh else t.get("to_amount"),
                    "currency": t.get("from_currency") or t.get("to_currency"),
                    "hidden_account_id": f if fh else to,
                    "counted_account_id": to if fh else f,
                }
            )
    return out


def flags_for_txn(row: dict, accounts_rows: list[dict], txns: list[dict]) -> list[dict]:
    """Non-blocking warnings for a single freshly-written row: one per leg landing
    before its account's tracking start, plus a straddle note when it crosses one.

    ``txns`` is the full transaction list the cutoffs resolve against (each account's tracking
    start is the DATE of the row its ``opening_txn_id`` points at) — passed in rather than
    read here, so this stays a pure function of its inputs."""
    by_id = _by_id(accounts_rows)
    cutoffs = _ledger.cutoffs_by_account(accounts_rows, txns)
    flags: list[dict] = []
    for acct_field, _amt in _LEGS:
        aid = row.get(acct_field)
        acct = by_id.get(aid)
        if (
            acct
            and _is_actionable(acct)
            and _ledger.is_before_tracking_start(row.get("date"), cutoffs.get(aid))
        ):
            flags.append(
                {
                    "type": "before_tracking_start",
                    "account_id": aid,
                    "message": (
                        f"Dated before {acct.get('name') or aid}'s tracking start "
                        f"({cutoffs.get(aid)}) — summarized into its "
                        "opening, not counted."
                    ),
                }
            )
    strad = straddling_transfers(accounts_rows, [row], cutoffs)
    if strad and _is_actionable(by_id.get(strad[0]["hidden_account_id"])):
        flags.append(
            {
                "type": "straddle",
                "account_id": row.get("from_account_id"),
                "message": "This transfer won't balance — one leg is before its "
                "account's tracking start.",
            }
        )
    return flags


def tracking_health(
    accounts_rows: list[dict] | None = None, txns: list[dict] | None = None
) -> dict:
    """Per-account hidden counts + book-level straddle/hidden totals for the UI."""
    accounts_rows = (
        accounts_rows if accounts_rows is not None else _ledger.accounts.query_accounts()
    )
    txns = txns if txns is not None else _ledger.transactions.all()
    by_id = _by_id(accounts_rows)
    cutoffs = _ledger.cutoffs_by_account(accounts_rows, txns)
    # Actionable only: summarized history on brokerage/crypto/loan/property accounts is
    # correct by design and must never nag. Only pre-start legs / straddles on plain
    # cash/card accounts (which the user can align) are surfaced.
    hidden = {
        aid: legs
        for aid, legs in hidden_legs(accounts_rows, txns).items()
        if _is_actionable(by_id.get(aid))
    }
    strad = [
        s
        for s in straddling_transfers(accounts_rows, txns, cutoffs)
        if _is_actionable(by_id.get(s["hidden_account_id"]))
    ]
    acct_health = {
        a["id"]: {
            "name": a.get("name"),
            "tracking_start": cutoffs.get(a["id"]),
            "hidden_count": len(hidden.get(a["id"], [])),
        }
        for a in accounts_rows
    }
    return {
        "accounts": acct_health,
        "straddle_count": len(strad),
        "hidden_total": sum(len(v) for v in hidden.values()),
    }


def statement_rows_vs_start(account: dict, txns: list[dict], row_dates: list[str]) -> dict | None:
    """What an import's rows will do to a cash/card account's balance, stated as a fact for
    whoever decides — Lucius reads it in the import preview.

    Accounts open at today's balance, so a statement from before that is history: its rows
    never change the balance. Left unsaid, Lucius guessed — in QA he called the rows "counted
    twice" and proposed re-setting the opening to the statement's, which would have moved
    today's correct S$1,520 to S$1,467.35. None when no row predates the Starting Balance.
    """
    if not _is_actionable(account):
        return None
    start = _ledger.opening_date(account, txns)
    if not start:
        return None
    before = sum(1 for d in row_dates if d and d < start)
    if not before:
        return None
    after = sum(1 for d in row_dates if d and d >= start)
    balance = _ledger.account_balance(account, txns, start)
    currency = account.get("currency") or ""
    owed = balance < 0 and account.get("type") == "credit"
    shown = f"{abs(balance):,.2f} {currency}" + (" owed" if owed else "")
    note = (
        f"{before} of these rows are dated before this account's Starting Balance ({start}). "
        f"They become history and don't change its balance (now {shown}). Nothing to adjust."
    )
    if after:
        note += f" {after} rows are on or after {start} and will change the balance."
    return {
        "starting_balance_date": start,
        "balance_now": balance,
        "currency": currency,
        "rows_before_start": before,
        "rows_after_start": after,
        "note": note,
    }


# ── Preserve-align: move the tracking start earlier, rewind the opening ──────────


def assert_alignable(account: dict, *, allow_summarized: bool = False) -> None:
    if account.get("type") not in _ALIGNABLE_TYPES and not allow_summarized:
        raise ValidationError(
            f"Account type {account.get('type')!r} summarizes history into its opening; "
            "aligning would double-count it. Pass allow_summarized=True only if full "
            "detail is entered."
        )


def _leg_effect(txn: dict, account_id: str) -> float:
    """One transaction's signed effect on ``account_id``'s balance — the SAME fold
    ``ledger.account_balance`` applies (out-leg negative, in-leg positive), so a rewind and
    the balance it must preserve can never disagree about what a row is worth. A transfer
    between two tracked accounts touches both legs and nets correctly for each."""
    eff = 0.0
    if txn.get("from_account_id") == account_id and txn.get("from_amount") is not None:
        eff -= float(txn["from_amount"])
    if txn.get("to_account_id") == account_id and txn.get("to_amount") is not None:
        eff += float(txn["to_amount"])
    return eff


def compute_align(
    account: dict,
    new_date: str,
    txns: list[dict],
    old_start: str | None,
    old_opening: float,
) -> dict:
    """Preserve rewind: new_opening = old_opening − Σ(balance-effect of legs newly
    exposed by moving the start old→new_date). Current balance stays identical.

    Pure: ``old_start`` and ``old_opening`` are the account's CURRENT tracking start and
    opening, both read off its Starting Balance row by the caller
    (``ledger.opening_date`` / ``ledger.opening_amount``).

    Skips THIS account's own Starting Balance row: it's the current embodiment of
    ``old_opening`` itself, never a "newly exposed" leg to fold in — folding it too would
    double-count the old opening. Normally excluded anyway by the ``d < old_start`` window
    (it's dated exactly at the old cutoff), but this holds even when that coincidence
    doesn't (e.g. an account with no opening, where the window falls back unbounded)."""
    aid = account["id"]
    opening_id = account.get("opening_txn_id")
    window_end = old_start or "9999-12-31"
    eff = 0.0
    for t in txns:
        if t.get("deleted") or t.get("id") == opening_id:
            continue
        d = t.get("date") or ""
        if not (new_date <= d < window_end):
            continue
        eff += _leg_effect(t, aid)
    return {
        "old_start": old_start,
        "new_start": new_date,
        "old_opening": round(old_opening, 2),
        "new_opening": round(old_opening - eff, 2),
    }


def align_tracking_start(account_id: str, new_date: str, *, allow_summarized: bool = False) -> dict:
    """Move an account's tracking start to `new_date` and rewind its opening so the
    current balance is unchanged (Preserve). Refuses summarizing account types.

    ONE write: ``ledger.set_account_opening`` re-dates AND re-amounts the Starting Balance
    row in place. The row's date IS the tracking start, so there is no second field to move
    and no window in which the two disagree.

    "Preserve" is ENFORCED, not reported. This used to compute ``balance_unchanged`` AFTER the
    write and hand it back in the payload — a caller that never read the flag silently kept a
    re-derived opening that moved the balance, which is a wrong number on an account nobody was
    told to re-check. The neutrality is now simulated BEFORE writing and a violation raises, so
    the only way to move an opening non-neutrally is to say so explicitly via
    ``set_account_opening``.

    ``compute_align`` only folds in legs the rewind newly EXPOSES (``[new_date, old_start)``),
    so a new_date at or after the current start exposes nothing: the opening would stay put
    while rows before it stop counting. That direction is refused rather than silently
    mis-preserved — hiding history behind an opening is a different operation."""
    acct = _ledger.accounts.find_by_id(account_id)
    if not acct:
        raise ValidationError(f"unknown account {account_id}")
    assert_alignable(acct, allow_summarized=allow_summarized)
    txns = _ledger.transactions.all()
    old_start = _ledger.opening_date(acct, txns)
    if old_start and new_date >= old_start:
        raise ValidationError(
            f"align moves a tracking start EARLIER; {new_date} is not before the current "
            f"start {old_start}. Moving it later would hide history behind the opening — "
            "set the opening explicitly instead."
        )
    before = _ledger.account_balance(acct, txns, old_start)
    plan = compute_align(acct, new_date, txns, old_start, _ledger.opening_amount(acct, txns))
    # Simulate the post-write fold on the SAME rows, before touching the store: the opening row
    # is replaced by the planned one, every other leg is folded from the new start.
    simulated = plan["new_opening"] + sum(
        _leg_effect(t, acct["id"])
        for t in txns
        if not t.get("deleted")
        and t.get("id") != acct.get("opening_txn_id")
        and (t.get("date") or "") >= new_date
    )
    if round(simulated, 2) != round(before, 2):
        raise ValidationError(
            f"align would MOVE the balance ({before:,.2f} -> {simulated:,.2f}); refusing. "
            "The opening cannot be rewound neutrally over this window — usually a leg dated "
            "inside it that the fold and the rewind disagree about."
        )
    updated = _ledger.set_account_opening(account_id, plan["new_opening"], new_date)
    after_txns = _ledger.transactions.all()
    after = _ledger.account_balance(updated, after_txns, _ledger.opening_date(updated, after_txns))
    if round(before, 2) != round(after, 2):  # pragma: no cover — simulation should have caught it
        raise ValidationError(
            f"align moved the balance ({before:,.2f} -> {after:,.2f}) despite a clean "
            "simulation; the opening has been written and needs manual review."
        )
    return {"account": updated, **plan, "balance_unchanged": True}
