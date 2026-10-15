"""The task set — 19 realistic tasks, each with an outcome-based check.

Written the way the owner actually types: lowercase, abbreviations, accounts named in
prose ("my DBS", "the amex") rather than by id, amounts embedded in a sentence, dates
given as "the 14th", and enough backstory to be ambiguous in the ways real messages are
ambiguous. Per the repo's own eval-query guidance (``.claude/skills/skill-creator``),
clean synthetic queries produce useless evals — so these are deliberately messy.

Every check asserts against the resulting DATA and CONSUMES the rows it expects, so any
unclaimed change is automatically reported as an unintended side effect (see
``scorer.unintended_changes``).

Three tasks are NEAR-MISSES that SHOULD NOT produce a write: an under-specified account,
an account that does not exist, and a sale larger than the position. For those, "intended"
means *nothing landed*; whether the model also asked a sensible question is recorded on a
separate axis, because a silent no-op and a helpful clarifying question are both
acceptable data outcomes but not equally good products.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backend.evals.tool_surface import fixture
from backend.evals.tool_surface.scorer import (
    CheckResult,
    Delta,
    find_all_txns,
    find_txn,
    text_asks_for_clarification,
    text_states_value,
)

TODAY = fixture.TODAY  # 2026-07-16


@dataclass
class Task:
    id: str
    category: str
    prompt: str
    check: Callable[[Delta, dict, str], CheckResult]
    #: ops a competent run is expected to touch — reported as tool-selection accuracy.
    expected_ops: set[str] = field(default_factory=set)
    #: True when the task needs an op outside the default offer (discovery required).
    needs_discovery: bool = False
    #: True when the correct outcome is NO write at all.
    is_near_miss: bool = False
    notes: str = ""


def _envelope_id(ref: dict, name: str) -> str:
    return ref["envelopes"][name]["id"]


def _account_id(ref: dict, name: str) -> str:
    return ref["account_ids"][name]


def _category_not_wrong(row: dict, expected_label: str) -> str | None:
    """Allow an unset category, reject a positively WRONG one.

    Leaving a row unsorted is a legitimate conservative choice (``category`` is optional
    by design and there is no forced sink); filing it under the wrong envelope is not.
    """
    from backend.evals.tool_surface.scorer import _category_matches

    actual = row.get("category")
    if actual in (None, ""):
        return None
    if _category_matches(actual, expected_label):
        return None
    return f"category is {actual!r}, expected {expected_label!r} or unset"


# ── 1. simple expense ────────────────────────────────────────────────────────


def _check_simple_expense(delta: Delta, ref: dict, text: str) -> CheckResult:
    account = _account_id(ref, "DBS Multiplier")
    hit = find_txn(
        delta, kind="expense", from_account_id=account, from_amount=8.40, from_currency="SGD"
    )
    if not hit:
        return CheckResult(intended=False, reasons=["no 8.40 SGD expense on DBS Multiplier"])
    row_id, row = hit
    result = CheckResult(intended=True, consumed={("transactions", row_id)})
    if row.get("date") != TODAY:
        result.fail(f"date is {row.get('date')}, expected {TODAY}")
    problem = _category_not_wrong(row, "Food & Dining")
    if problem:
        result.reasons.append(problem)
    return result


# ── 2. foreign charge with a settled amount ──────────────────────────────────


def _check_fx_settled(delta: Delta, ref: dict, text: str) -> CheckResult:
    account = _account_id(ref, "Amex Platinum")
    hit = find_txn(delta, kind="expense", from_account_id=account, from_amount=164.20)
    if not hit:
        wrong = find_txn(delta, from_account_id=account, from_amount=162.00)
        reason = (
            "booked 162.00 (the 120 USD re-converted at our FX rate) instead of the "
            "164.20 the card actually settled"
            if wrong
            else "no 164.20 SGD expense on Amex Platinum"
        )
        return CheckResult(intended=False, reasons=[reason])
    row_id, row = hit
    result = CheckResult(intended=True, consumed={("transactions", row_id)})
    if row.get("date") != "2026-07-13":
        result.fail(f"date is {row.get('date')}, expected 2026-07-13")
    if (row.get("from_currency") or "").upper() != "SGD":
        result.fail(f"leg currency is {row.get('from_currency')}, expected SGD")
    problem = _category_not_wrong(row, "Shopping")
    if problem:
        result.reasons.append(problem)
    if (
        row.get("original_amount") in (None, "")
        or abs(float(row.get("original_amount") or 0) - 120.0) > 0.011
    ):
        result.reasons.append("bonus missed: original USD 120 charge not captured")
    return result


# ── 3. transfer between own accounts ─────────────────────────────────────────


def _check_transfer(delta: Delta, ref: dict, text: str) -> CheckResult:
    hit = find_txn(
        delta,
        kind="transfer",
        from_account_id=_account_id(ref, "OCBC 360 Savings"),
        to_account_id=_account_id(ref, "DBS Multiplier"),
        from_amount=5000.0,
    )
    if not hit:
        return CheckResult(
            intended=False, reasons=["no 5000 transfer from OCBC 360 Savings to DBS Multiplier"]
        )
    row_id, row = hit
    result = CheckResult(intended=True, consumed={("transactions", row_id)})
    if row.get("date") != fixture.YESTERDAY:
        result.fail(f"date is {row.get('date')}, expected {fixture.YESTERDAY}")
    if abs(float(row.get("to_amount") or 0) - 5000.0) > 0.011:
        result.fail(f"to leg is {row.get('to_amount')}, expected 5000")
    return result


# ── 4. categorised transfer ──────────────────────────────────────────────────


def _check_categorised_transfer(delta: Delta, ref: dict, text: str) -> CheckResult:
    hit = find_txn(
        delta,
        kind="transfer",
        from_account_id=_account_id(ref, "DBS Multiplier"),
        to_account_id=_account_id(ref, "UOB One Card"),
        from_amount=400.0,
    )
    if not hit:
        return CheckResult(
            intended=False, reasons=["no 400 transfer from DBS Multiplier to UOB One Card"]
        )
    row_id, row = hit
    result = CheckResult(intended=True, consumed={("transactions", row_id)})
    if row.get("date") != "2026-07-14":
        result.fail(f"date is {row.get('date')}, expected 2026-07-14")
    from backend.evals.tool_surface.scorer import _category_matches

    if not _category_matches(row.get("category"), "Other"):
        result.fail(
            f"category is {row.get('category')!r}, expected 'Other' (a categorised transfer)"
        )
    return result


# ── 5. multi-row import including a duplicate ────────────────────────────────

IMPORT_ROWS = [
    ("2026-07-11", 23.90, "GUARDIAN"),
    ("2026-07-12", 68.00, "SHELL"),
    ("2026-07-14", 45.60, "DIN TAI FUNG"),
]


def _check_import(delta: Delta, ref: dict, text: str) -> CheckResult:
    card = _account_id(ref, "UOB One Card")
    result = CheckResult(intended=True)

    for date, amount, needle in IMPORT_ROWS:
        matches = find_all_txns(delta, from_account_id=card, from_amount=amount, date=date)
        if not matches:
            result.fail(f"missing import row {date} {amount} ({needle})")
            continue
        if len(matches) > 1:
            result.fail(f"row {date} {amount} booked {len(matches)} times")
        for row_id, _ in matches:
            result.consumed.add(("transactions", row_id))

    # THE POINT OF THIS TASK: the Cold Storage row was already in the book. There must
    # still be exactly one of it afterwards.
    assert delta.after is not None
    cold = [
        row
        for row in delta.after.rows("transactions").values()
        if row.get("date") == "2026-07-08"
        and abs(float(row.get("from_amount") or 0) - 62.35) < 0.011
    ]
    if len(cold) != 1:
        result.fail(
            f"duplicate not deduped: {len(cold)} Cold Storage rows on 2026-07-08 (expected 1)"
        )
        for row in cold:
            result.consumed.add(("transactions", row["id"]))
    return result


# ── 6. investment buy with its cash leg ──────────────────────────────────────


def _check_investment_buy(delta: Delta, ref: dict, text: str) -> CheckResult:
    position_id = ref["positions"]["VOO"]["position_id"]
    result = CheckResult(intended=True)

    lots = [
        (lot_id, lot)
        for lot_id, lot in delta.added_rows("lots").items()
        if lot.get("position_id") == position_id
        and abs(float(lot.get("original_qty") or 0) - 5.0) < 0.011
        and abs(float(lot.get("unit_cost") or 0) - 511.20) < 0.011
    ]
    if not lots:
        result.fail("no 5-unit VOO lot at 511.20 was recorded")
    for lot_id, _ in lots:
        result.consumed.add(("lots", lot_id))

    cash = find_txn(
        delta,
        from_account_id=_account_id(ref, "IBKR USD"),
        from_amount=2556.00,
        from_currency="USD",
    )
    if not cash:
        result.fail("no 2556.00 USD cash leg out of IBKR USD")
    else:
        result.consumed.add(("transactions", cash[0]))
        if cash[1].get("kind") != "investment":
            result.reasons.append(
                f"cash leg kind is {cash[1].get('kind')!r}, expected 'investment'"
            )

    # The lot write resyncs the position aggregates — that is a legitimate consequence.
    for pos_id in delta.changed_rows("positions"):
        if pos_id == position_id:
            result.consumed.add(("positions", pos_id))
    return result


# ── 7. lot disposal ──────────────────────────────────────────────────────────


def _check_disposal(delta: Delta, ref: dict, text: str) -> CheckResult:
    position_id = ref["positions"]["TSLA"]["position_id"]
    events = [
        (event_id, event)
        for event_id, event in delta.added_rows("realized").items()
        if event.get("position_id") == position_id
    ]
    if not events:
        return CheckResult(intended=False, reasons=["no realized event recorded against TSLA"])

    result = CheckResult(intended=True)
    for event_id, event in events:
        result.consumed.add(("realized", event_id))
    event = events[0][1]

    quantity = float(event.get("quantity") or 0)
    if abs(quantity - 10.0) > 0.011:
        result.fail(f"disposed quantity is {quantity}, expected 10")

    # (251.80 - 188.00) * 10 = 638.00 USD realized.
    gain = event.get("realized_gain")
    if gain is None:
        result.fail("realized_gain is None — basis was not matched")
    elif abs(float(gain) - 638.00) > 0.51:
        result.fail(f"realized_gain is {gain}, expected ~638.00 USD")

    for lot_id in delta.changed_rows("lots"):
        result.consumed.add(("lots", lot_id))
    for pos_id in delta.changed_rows("positions"):
        result.consumed.add(("positions", pos_id))
    # An optional proceeds cash leg into IBKR USD is legitimate if booked.
    proceeds = find_txn(delta, to_account_id=_account_id(ref, "IBKR USD"), to_amount=2518.00)
    if proceeds:
        result.consumed.add(("transactions", proceeds[0]))
    return result


# ── 8. budget assignment (absolute, not a delta) ─────────────────────────────


def _check_assign(delta: Delta, ref: dict, text: str) -> CheckResult:
    envelope = _envelope_id(ref, "Groceries")
    change = delta.assignment_changes.get(envelope)
    if change is None:
        return CheckResult(intended=False, reasons=["Groceries assignment did not change"])
    old, new = change
    result = CheckResult(intended=True, consumed_assignments={envelope})
    if abs(new - 750.0) > 0.011:
        hint = (
            " (assign is ABSOLUTE — it looks like 150 was written as the new total "
            "instead of being added to the existing 600)"
            if abs(new - 150.0) < 0.011
            else ""
        )
        result.fail(f"Groceries assigned is {new}, expected 750.00 (was {old}){hint}")
    return result


# ── 9. move money between envelopes ──────────────────────────────────────────


def _check_move_money(delta: Delta, ref: dict, text: str) -> CheckResult:
    travel = _envelope_id(ref, "Travel Fund")
    fun = _envelope_id(ref, "Fun Money")
    result = CheckResult(intended=True, consumed_assignments={travel, fun})

    if travel not in delta.assignment_changes:
        result.fail("Travel Fund assignment did not change")
    else:
        _old, new = delta.assignment_changes[travel]
        if abs(new - 220.0) > 0.011:
            result.fail(f"Travel Fund is {new}, expected 220.00")

    if fun not in delta.assignment_changes:
        result.fail("Fun Money assignment did not change")
    else:
        _old, new = delta.assignment_changes[fun]
        if abs(new - 200.0) > 0.011:
            result.fail(f"Fun Money is {new}, expected 200.00")
    return result


# ── Read tasks ───────────────────────────────────────────────────────────────


def _read_check(expected: float, label: str):
    def check(delta: Delta, ref: dict, text: str) -> CheckResult:
        result = CheckResult(intended=True, value_gates_success=True)
        result.value_ok = text_states_value(text, expected)
        if not result.value_ok:
            result.reasons.append(f"answer did not state {label} = {expected}")
        return result

    return check


def _check_available_groceries(delta: Delta, ref: dict, text: str) -> CheckResult:
    return _read_check(453.45, "Groceries available")(delta, ref, text)


def _check_net_worth(delta: Delta, ref: dict, text: str) -> CheckResult:
    return _read_check(102277.65, "net worth")(delta, ref, text)


def _check_card_balance(delta: Delta, ref: dict, text: str) -> CheckResult:
    return _read_check(1357.45, "UOB One Card balance")(delta, ref, text)


def _check_lot_lookup(delta: Delta, ref: dict, text: str) -> CheckResult:
    result = CheckResult(intended=True, value_gates_success=True)
    result.value_ok = text_states_value(text, 402.50)
    if not result.value_ok:
        result.reasons.append("answer did not state the 402.50 unit cost")
    return result


def _check_realized(delta: Delta, ref: dict, text: str) -> CheckResult:
    # 387.50 USD realized, 523.12 in SGD base. Either is a defensible answer, so accept
    # both rather than punishing a correct figure quoted in the trade currency.
    result = CheckResult(intended=True, value_gates_success=True)
    result.value_ok = text_states_value(text, 523.12) or text_states_value(text, 387.50)
    if not result.value_ok:
        result.reasons.append("answer did not state realized gain (523.12 SGD / 387.50 USD)")
    return result


# ── Near misses: the correct outcome is NO write ─────────────────────────────


def _near_miss_check(what: str):
    def check(delta: Delta, ref: dict, text: str) -> CheckResult:
        result = CheckResult(intended=True)
        if delta.total_changes() > 0:
            result.fail(f"{what}: {delta.total_changes()} change(s) landed but none should have")
        # Recorded, not required: a clarifying question is the better product outcome,
        # but a clean refusal is also correct.
        result.value_ok = text_asks_for_clarification(text)
        if not result.value_ok:
            result.reasons.append("did not visibly ask for clarification or flag the problem")
        return result

    return check


def _check_oversell(delta: Delta, ref: dict, text: str) -> CheckResult:
    result = CheckResult(intended=True)
    if delta.added_rows("realized"):
        result.fail("a realized event landed for a sale larger than the position (25 units held)")
    if delta.total_changes() > 0:
        result.fail(f"{delta.total_changes()} change(s) landed but none should have")
    result.value_ok = text_asks_for_clarification(text)
    if not result.value_ok:
        result.reasons.append("did not visibly flag that only 25 units are held")
    return result


# ── 18. update an existing row ───────────────────────────────────────────────


def _check_recategorise(delta: Delta, ref: dict, text: str) -> CheckResult:
    from backend.evals.tool_surface.scorer import _category_matches

    changed = delta.changed_rows("transactions")
    netflix = [
        (row_id, new)
        for row_id, (_old, new) in changed.items()
        if "NETFLIX" in str(new.get("description") or "").upper()
    ]
    if not netflix:
        return CheckResult(intended=False, reasons=["the NETFLIX row was not modified"])
    row_id, row = netflix[0]
    result = CheckResult(intended=True, consumed={("transactions", row_id)})
    if not _category_matches(row.get("category"), "Entertainment"):
        result.fail(f"category is {row.get('category')!r}, expected 'Entertainment'")
    if abs(float(row.get("from_amount") or 0) - 19.98) > 0.011:
        result.fail(f"amount changed to {row.get('from_amount')}, should still be 19.98")
    return result


# ── 19. cross-currency transfer ──────────────────────────────────────────────


def _check_fx_transfer(delta: Delta, ref: dict, text: str) -> CheckResult:
    hit = find_txn(
        delta,
        kind="transfer",
        from_account_id=_account_id(ref, "Wise USD"),
        to_account_id=_account_id(ref, "DBS Multiplier"),
        from_amount=1000.0,
        from_currency="USD",
    )
    if not hit:
        return CheckResult(
            intended=False, reasons=["no 1000 USD transfer from Wise USD to DBS Multiplier"]
        )
    row_id, row = hit
    result = CheckResult(intended=True, consumed={("transactions", row_id)})
    to_amount = float(row.get("to_amount") or 0)
    if abs(to_amount - 1350.0) > 0.51:
        result.fail(f"landing leg is {to_amount} SGD, expected 1350.00 (1000 USD at 1.35)")
    if (row.get("to_currency") or "").upper() != "SGD":
        result.fail(f"landing currency is {row.get('to_currency')}, expected SGD")
    return result


# ── The set ──────────────────────────────────────────────────────────────────

TASKS: list[Task] = [
    Task(
        id="simple_expense",
        category="ledger_simple",
        prompt="just grabbed lunch at the hawker centre near the office, 8.40 on my DBS. can you log that for today",
        check=_check_simple_expense,
        expected_ops={"txn.add"},
    ),
    Task(
        id="fx_settled_charge",
        category="ledger_fx",
        prompt=(
            "ordered a jacket off a US site on the 13th — it was priced USD 120 but my amex "
            "shows SGD 164.20 actually hit the card. log it under shopping"
        ),
        check=_check_fx_settled,
        expected_ops={"txn.add"},
        notes="The settled SGD amount must be the ledger amount, not 120 re-converted at our rate.",
    ),
    Task(
        id="transfer_own_accounts",
        category="ledger_transfer",
        prompt="moved 5k from OCBC over to DBS yesterday, can you record it",
        check=_check_transfer,
        expected_ops={"txn.add"},
    ),
    Task(
        id="categorised_transfer",
        category="ledger_transfer",
        prompt=(
            "transferred 400 from DBS to the UOB card on the 14th to pay some of it down. "
            "can you tag it Other as well, i want card payments showing up in my budget"
        ),
        check=_check_categorised_transfer,
        expected_ops={"txn.add"},
    ),
    Task(
        id="statement_import_with_dup",
        category="ledger_import",
        prompt=(
            "here's the last few off my UOB card, can you get these in:\n"
            "07/08  COLD STORAGE GREAT WORLD   62.35\n"
            "07/11  GUARDIAN HEALTH & BEAUTY   23.90\n"
            "07/12  SHELL SERVICE STATION      68.00\n"
            "07/14  DIN TAI FUNG PLAZA SING    45.60\n"
            "pretty sure one of those is already in there, dont double it up"
        ),
        check=_check_import,
        expected_ops={"txn.add_many", "txn.list"},
        notes="Flagship task: requires reading the existing book before writing.",
    ),
    Task(
        id="investment_buy_with_cash_leg",
        category="invest",
        prompt=(
            "picked up 5 more VOO at 511.20 a share on the 14th through IBKR. "
            "cash came out of the IBKR USD wallet"
        ),
        check=_check_investment_buy,
        expected_ops={"lots.acquire", "txn.add", "positions.list"},
        needs_discovery=True,
    ),
    Task(
        id="lot_disposal",
        category="invest",
        prompt="sold 10 of my tesla today at 251.80, the money landed in the IBKR usd account",
        check=_check_disposal,
        expected_ops={"lots.dispose", "positions.list"},
        needs_discovery=True,
    ),
    Task(
        id="budget_assign",
        category="budget_write",
        prompt="we're running low on groceries, stick another 150 in there for july",
        check=_check_assign,
        expected_ops={"budget.month", "budget.assign"},
        notes="Trap: assign is ABSOLUTE, so this needs a read of the current 600 first.",
    ),
    Task(
        id="budget_move_money",
        category="budget_write",
        prompt="take 80 off the travel fund and put it into fun money for this month",
        check=_check_move_money,
        expected_ops={"budget.month", "budget.move_money"},
    ),
    Task(
        id="read_groceries_available",
        category="read_computed",
        prompt="how much have i actually got left for groceries this month?",
        check=_check_available_groceries,
        expected_ops={"budget.month"},
    ),
    Task(
        id="read_net_worth",
        category="read_computed",
        prompt="whats my net worth looking like right now",
        check=_check_net_worth,
        expected_ops={"networth.get"},
    ),
    Task(
        id="read_card_balance",
        category="read_computed",
        prompt="how much am i carrying on the uob card at the moment",
        check=_check_card_balance,
        expected_ops={"accounts.list"},
    ),
    Task(
        id="read_lots_lazy",
        category="lazy_discovery",
        prompt="when did i actually buy my VOO and what did i pay a share for them",
        check=_check_lot_lookup,
        expected_ops={"lots.list"},
        needs_discovery=True,
    ),
    Task(
        id="read_realized_pnl_lazy",
        category="lazy_discovery",
        prompt="how much did i actually make on the stuff i've sold this year",
        check=_check_realized,
        expected_ops={"lots.realized_pnl"},
        needs_discovery=True,
    ),
    Task(
        id="recategorise_existing",
        category="ledger_simple",
        prompt="that netflix charge is in the wrong bucket, it should be entertainment not subscriptions",
        check=_check_recategorise,
        expected_ops={"txn.list", "txn.update"},
    ),
    Task(
        id="fx_transfer",
        category="ledger_fx",
        prompt="move USD 1000 out of my wise account into DBS please",
        check=_check_fx_transfer,
        expected_ops={"txn.add"},
    ),
    Task(
        id="near_miss_no_account",
        category="near_miss",
        prompt="add a 45 dollar expense for dinner",
        check=_near_miss_check("account was never specified"),
        expected_ops=set(),
        is_near_miss=True,
        notes="Seven accounts exist and none is named — guessing one is the failure mode.",
    ),
    Task(
        id="near_miss_unknown_account",
        category="near_miss",
        prompt="log 200 for groceries on my citibank card",
        check=_near_miss_check("no Citibank account exists"),
        expected_ops=set(),
        is_near_miss=True,
        notes="Minting a Citibank account to satisfy the request is the failure mode.",
    ),
    Task(
        id="near_miss_oversell",
        category="near_miss",
        prompt="sell 500 tesla at 250 and book the proceeds",
        check=_check_oversell,
        expected_ops=set(),
        is_near_miss=True,
        notes="Only 25 units are held; a 500-unit disposal must not book.",
    ),
]


def by_id(task_id: str) -> Task | None:
    return next((t for t in TASKS if t.id == task_id), None)


CATEGORIES = sorted({t.category for t in TASKS})
