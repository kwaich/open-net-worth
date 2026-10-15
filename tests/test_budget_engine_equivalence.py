"""THE GATE for RFC 2026-08-22-pluggable-budget-engines: asserts ZeroBasedEngine.fold()'s
ViewModel carries the exact same NUMBERS as services.planning.budget_month() on a seeded
book. planning.py is untouched by this RFC (Decision D) — zero_based.py only adapts its
output, so if the ViewModel can't faithfully express what budget_month() already computes,
this test is the trip wire: the interface is wrong and must be fixed before anything else
(reporting, dashboards, Lucius) is built to depend on it.

(``zero_based.py`` was named ``envelope.py`` until the 2026-08-23 naming correction.)

The `env` fixture mirrors tests/test_planning_service.py's `env` fixture (same isolation
strategy: tmp_path + db_isolated + a fake FX cache) — duplicated rather than imported,
matching this test suite's existing convention of each planning-adjacent test file owning
its own copy (tests/test_credit_card_payment_envelope.py does the same).
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.db.profile_store import ProfileStore
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning
from backend.services.budget_engines.zero_based import ZERO_BASED_ENGINE

# FX pivot on SGD; 1 USD = 1.35 SGD — unused by these tests directly, kept for parity with
# test_planning_service.py's fixture shape in case a future case needs foreign-currency rows.
_FX = {"SGD": 1.0, "USD": 1.35}

_FOOD = "cat_13095ba70000"  # Food & Dining
_TRANSPORT = "cat_db5642ab0000"  # Transport


def _opening_txn(account_id, amount, d, currency="SGD"):
    row = {
        "id": f"open_{account_id}",
        "date": d,
        "kind": "adjustment",
        "description": "Starting Balance",
        "category": None,
        "source": "manual",
        "cleared": True,
    }
    if amount >= 0:
        row.update({"to_account_id": account_id, "to_amount": amount, "to_currency": currency})
    else:
        row.update(
            {"from_account_id": account_id, "from_amount": -amount, "from_currency": currency}
        )
    return row


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    """Isolate every disk dependency of services.planning into tmp_path (plus the money-core
    DB tables via db_isolated) — see tests/test_planning_service.py's `env` for the fuller
    explanation this mirrors."""
    profile_store = ProfileStore()

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    profile_store.set_raw({"base_currency": "SGD"})
    default_accounts = [
        {
            "id": "bank",
            "name": "Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0.0,
        },
    ]

    openings: dict[str, dict] = {}
    scenario_txns: list[dict] = []

    def _write_txns():
        from backend.core.ids import new_id

        seeded_ids = {t.get("id") for t in scenario_txns if t.get("id")}
        seed = [row for row in openings.values() if row["id"] not in seeded_ids]
        rows = [
            row if row.get("id") else {**row, "id": new_id("txn")} for row in seed + scenario_txns
        ]
        svc.transactions.replace_all(rows)

    def _normalize_accounts(rows):
        out = []
        for a in rows:
            a = dict(a)
            if "initial_balance" in a or "initial_balance_date" in a:
                amount = round(float(a.pop("initial_balance", 0) or 0), 2)
                as_of = a.pop("initial_balance_date", None)
                if amount == 0 and not as_of:
                    openings.pop(a["id"], None)
                    a.setdefault("opening_txn_id", None)
                else:
                    as_of = as_of or date.today().isoformat()
                    row = _opening_txn(a["id"], amount, as_of, a.get("currency", "SGD"))
                    openings[a["id"]] = row
                    a["opening_txn_id"] = row["id"]
            out.append(a)
        return out

    svc.accounts.replace_all(_normalize_accounts(default_accounts))
    _write_txns()

    def _set_txns(rows):
        scenario_txns[:] = rows
        _write_txns()

    # First-boot migration (mirrors backend.main's boot sequence): mints a real 1:1 envelope
    # for every BUILTIN expense category, exactly what a fresh install gets.
    envelopes_service.ensure_envelopes_migrated()

    return type("Env", (), {"set_txns": staticmethod(_set_txns)})


def _expense(txn_date, category, amount, currency="SGD"):
    return {
        "kind": "expense",
        "date": txn_date,
        "category": category,
        "from_account_id": "bank",
        "from_amount": amount,
        "from_currency": currency,
        "base_amount": amount,
    }


def _income(txn_date, amount, category="", currency="SGD"):
    return {
        "kind": "income",
        "date": txn_date,
        "category": category,
        "to_account_id": "bank",
        "to_amount": amount,
        "to_currency": currency,
        "base_amount": amount,
    }


def _env_id(category_key):
    return envelopes_service.env_of()[category_key]


def _by_id(table):
    """category-row lookup by envelope id (== the row's `key`), across every real group —
    excludes __unsorted__ (key=None; that row becomes `unmapped`, never a Row)."""
    out = {}
    for g in table["groups"]:
        if g["id"] == "__unsorted__":
            continue
        for c in g["categories"]:
            out[c["key"]] = c
    return out


def _seed(env):
    """One month with two funded envelopes, an overspend, and uncategorized spend — enough
    surface to exercise groups/rows/measures/health/unmapped all at once."""
    env.set_txns(
        [
            _income("2026-06-01", 2000),
            _expense("2026-06-10", _FOOD, 600),
            _expense("2026-06-12", _TRANSPORT, 850),  # will overspend its envelope
            _expense("2026-06-15", None, 75),  # uncategorized -> __unsorted__ / unmapped
        ]
    )
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": _env_id(_FOOD), "amount": 800}
    )
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": _env_id(_TRANSPORT), "amount": 500}
    )


# ── The gate ──────────────────────────────────────────────────────────────────────────


def test_headline_matches_ready_to_assign(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)
    assert vm.headline.value == table["ready_to_assign"]
    assert vm.headline.unit == "money"


def test_engine_id_period_and_base_currency(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)
    assert vm.engine_id == "zero_based"
    assert vm.period == table["month"] == "2026-06"
    assert vm.base_currency == table["base_currency"] == "SGD"


def test_group_subtotals_match(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)

    # __unsorted__ is deliberately excluded from groups[] — it becomes `unmapped` instead
    # (a category=None row has no valid opaque id to carry as Row.id). Every OTHER group
    # (real groups + the synthetic __unbudgeted__, when present) must match 1:1.
    expected_groups = {g["id"]: g for g in table["groups"] if g["id"] != "__unsorted__"}
    actual_groups = {g.id: g for g in vm.groups}
    assert set(actual_groups) == set(expected_groups)

    for gid, expected in expected_groups.items():
        actual = actual_groups[gid]
        assert actual.label == expected.get("name", "")
        measures = {m.key: m.amount_base for m in actual.subtotal}
        assert measures["assigned"] == expected["subtotal"]["assigned"]
        assert measures["activity"] == expected["subtotal"]["activity"]
        assert measures["available"] == expected["subtotal"]["available"]


def test_every_row_measure_matches_budget_month(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)

    expected_rows = _by_id(table)
    actual_rows = {row.id: row for g in vm.groups for row in g.rows}
    assert set(actual_rows) == set(expected_rows)

    for row_id, expected in expected_rows.items():
        actual = actual_rows[row_id]
        assert actual.label == expected["label"]
        assert actual.label  # every row carries a non-empty label
        assert actual.status == expected["status"]
        measures = {m.key: m.amount_base for m in actual.measures}
        assert measures["assigned"] == expected["assigned"]
        assert measures["activity"] == expected["activity"]
        assert measures["available"] == expected["available"]


def test_row_and_group_counts_match(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)

    real_groups = [g for g in table["groups"] if g["id"] != "__unsorted__"]
    assert len(vm.groups) == len(real_groups)

    expected_row_count = sum(len(g["categories"]) for g in real_groups)
    actual_row_count = sum(len(g.rows) for g in vm.groups)
    assert actual_row_count == expected_row_count == len(_by_id(table))


def test_specific_rows_carry_the_right_numbers(env):
    """A concrete, hand-checked cross-section — not just set equality — so a bug that
    happened to preserve totals/counts but scrambled which row got which numbers would
    still be caught."""
    _seed(env)
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)
    rows = {row.id: row for g in vm.groups for row in g.rows}

    food = rows[_env_id(_FOOD)]
    food_measures = {m.key: m.amount_base for m in food.measures}
    assert food_measures == {"assigned": 800.0, "activity": -600.0, "available": 200.0}
    assert food.status != "underfunded"

    transport = rows[_env_id(_TRANSPORT)]
    transport_measures = {m.key: m.amount_base for m in transport.measures}
    assert transport_measures == {"assigned": 500.0, "activity": -850.0, "available": -350.0}


def test_unmapped_reflects_unsorted_activity(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)

    unsorted_group = next(g for g in table["groups"] if g["id"] == "__unsorted__")
    assert vm.unmapped is not None
    assert vm.unmapped.amount_base == unsorted_group["subtotal"]["activity"] == -75.0
    # And it really is excluded from groups[] (Decision D's mapping), not double-counted.
    assert "__unsorted__" not in {g.id for g in vm.groups}


def test_unmapped_is_none_when_nothing_uncategorized(env):
    env.set_txns(
        [
            _income("2026-06-01", 1000),
            _expense("2026-06-10", _FOOD, 200),
        ]
    )
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": _env_id(_FOOD), "amount": 300}
    )
    table = planning.budget_month("2026-06")
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)
    assert all(g["id"] != "__unsorted__" for g in table["groups"])
    assert vm.unmapped is None


def test_health_reflects_budget_health(env):
    _seed(env)
    table = planning.budget_month("2026-06")
    expected_health = planning.budget_health(table=table)
    vm = ZERO_BASED_ENGINE.fold("2026-06", {}, {}, None)

    assert vm.headline.status == expected_health["rta_status"]
    # Transport was overspent by 350 in _seed() -> at least one underfunded/attention signal.
    assert expected_health["overspent_count"] >= 1
    assert vm.health.verdict in ("good", "attention", "bad", "unknown")
    assert vm.health.reasons  # never an empty list — always at least one line


def test_empty_book_still_folds(env):
    """A month before any data — budget_month's own documented all-zeros case — must still
    fold cleanly (no fabricated Assigned/Available, no crash)."""
    table = planning.budget_month("2026-01")
    vm = ZERO_BASED_ENGINE.fold("2026-01", {}, {}, None)
    assert vm.headline.value == table["ready_to_assign"] == 0.0
    assert vm.unmapped is None
    for g in vm.groups:
        for m in g.subtotal:
            assert m.amount_base == 0.0
