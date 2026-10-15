"""Daily briefing fixes from persona QA round 5 (2026-10-14), reproduced with synthetic data.

* It named "Xanadu -3.3%" the weakest holding while a smaller position sat at -8.5%: the holdings
  list is cut to the largest twelve, so the weakest LINE was not the weakest HOLDING. The note
  is now handed the whole ranking.
* It went stale within a new user's first minutes (a card "had no envelope", "link your
  categories" after they were linked) because it was cached for the whole day-part. The cache
  now carries a fingerprint of the book's structure.
* It advised that cash "can absorb the card comfortably" when every dollar was assigned. Whether
  cash is free is a Ready-to-Assign question, and the context says so.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

import backend.api.briefing as brf
from backend.services import accounts as accounts_service
from backend.services import briefing_context as facts


def _row(symbol: str, name: str, value: float, day_pct: float) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "value": value,
        "weight": 1.0,
        "day_pct": day_pct,
        "currency": "SGD",
        "qty": 1.0,
        "price": value,
    }


def _book_of(rows: list[dict]) -> dict:
    return {"merged_rows": rows}


# ── the weakest holding ───────────────────────────────────────────────────────


def test_the_weakest_holding_is_found_beyond_the_largest_twelve():
    rows = [_row(f"BIG{n}", f"Big {n}", 100_000.0 - n, -3.3 + n * 0.1) for n in range(12)]
    rows += [_row("ASTS", "Small Cap Space", 900.0, -8.5), _row("UPUP", "Riser", 800.0, 4.1)]

    lines = facts.movers_lines(_book_of(rows))

    assert lines[0] == (
        "  Weakest holding today: Small Cap Space (ASTS) -8.5%. Strongest: Riser (UPUP) +4.1%."
    )
    ranking = lines[1]
    assert ranking.index("ASTS") < ranking.index("BIG0")
    assert ranking.count("(") == 14  # every holding is ranked, not a top-N


def test_holdings_with_no_move_are_counted_not_ranked():
    rows = [_row("AAA", "Alpha", 500.0, 0.0), _row("BBB", "Beta", 400.0, -1.2)]

    lines = facts.movers_lines(_book_of(rows))

    assert "Weakest holding today: Beta (BBB) -1.2%" in lines[0]
    assert "AAA" not in lines[1]
    assert lines[-1] == "  (1 more holdings with no move today)"


def test_no_moves_at_all_adds_no_section():
    assert facts.movers_lines(_book_of([_row("AAA", "Alpha", 500.0, 0.0)])) == []


def test_the_context_names_the_weakest_across_the_whole_book(db_isolated, fx_rates, monkeypatch):
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    rows = [_row(f"BIG{n}", f"Big {n}", 100_000.0 - n, -3.3) for n in range(12)]
    rows.append(_row("ASTS", "Small Cap Space", 900.0, -8.5))
    book = {**_book_of(rows)}

    ctx = brf._build_context(0.0, book)

    assert "TODAY'S MOVES ACROSS ALL HOLDINGS" in ctx
    assert "Weakest holding today: Small Cap Space (ASTS) -8.5%" in ctx


# ── what cash is free ─────────────────────────────────────────────────────────


def _budget(monkeypatch, *, assigned: float, ready: float) -> None:
    table = {"ready_to_assign": ready, "totals": {"assigned": assigned}}
    monkeypatch.setattr(facts.planning_service, "budget_month", lambda ym: table)
    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "zero_based")


def test_with_every_dollar_assigned_cash_cannot_absorb_a_card(monkeypatch):
    _budget(monkeypatch, assigned=3_000.0, ready=0.0)

    lines = facts.cash_availability_lines("2026-10", "SGD", card_owed=1_200.0)

    assert lines[0] == "  Ready to Assign (cash not yet given a job): S$0"
    assert "every dollar of cash is already assigned, so do NOT say cash can absorb it" in lines[1]


def test_unassigned_cash_is_what_can_take_on_a_card(monkeypatch):
    _budget(monkeypatch, assigned=3_000.0, ready=800.0)

    lines = facts.cash_availability_lines("2026-10", "SGD", card_owed=1_200.0)

    assert "cash can take on at most S$800 of it" in lines[1]


def test_no_budget_means_no_claim_either_way(monkeypatch):
    _budget(monkeypatch, assigned=0.0, ready=0.0)
    assert facts.cash_availability_lines("2026-10", "SGD", card_owed=1_200.0) == []

    monkeypatch.setattr(facts.budget_engines, "home_engine_id", lambda: "proportional")
    assert facts.cash_availability_lines("2026-10", "SGD", card_owed=1_200.0) == []


def test_the_context_carries_the_cash_availability_section(db_isolated, fx_rates, monkeypatch):
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    _budget(monkeypatch, assigned=3_000.0, ready=0.0)
    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 5_000.0}
    )
    accounts_service.create_account(
        {"name": "Visa", "type": "credit", "currency": "SGD", "initial_balance": -1_200.0}
    )

    ctx = brf._build_context(0.0, {"merged_rows": []})

    assert "CASH AVAILABLE TO SPEND" in ctx
    assert "Credit cards owe S$1,200: every dollar of cash is already assigned" in ctx


# ── a stale note ──────────────────────────────────────────────────────────────


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "briefing_cache.json"
    monkeypatch.setattr(brf, "CACHE_FILE", path)
    return path


def test_a_new_card_makes_the_cached_note_stale(db_isolated, fx_rates, cache_file):
    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 5_000.0}
    )
    before = brf._current_fingerprint()
    brf._save_cache({"note": "Welcome", "actions": [], "chat_suggestion": ""}, before)

    assert brf._load_cache(before)["note"] == "Welcome"

    accounts_service.create_account(
        {"name": "Visa", "type": "credit", "currency": "SGD", "initial_balance": 0.0}
    )
    after = brf._current_fingerprint()

    assert after != before
    assert brf._load_cache(after) is None


def test_linking_categories_into_an_envelope_makes_the_note_stale(db_isolated, fx_rates):
    from backend.db.envelope_store import EnvelopeStore

    before = brf._current_fingerprint()
    EnvelopeStore().create({"name": "Groceries", "category_ids": ["cat_aaaaaaaaaaaa"]})

    assert brf._current_fingerprint() != before


def test_a_new_transaction_does_not_make_the_note_stale(db_isolated, fx_rates):
    from backend.services import ledger as ledger_service

    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 5_000.0}
    )
    before = brf._current_fingerprint()
    ledger_service.create_transaction(
        {
            "date": datetime.now().date().isoformat(),
            "kind": "expense",
            "description": "Coffee",
            "from_account_id": "Current",
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )

    assert brf._current_fingerprint() == before


def test_a_cache_written_without_a_fingerprint_is_stale_once_one_is_known(cache_file):
    cache_file.write_text(
        json.dumps(
            {
                "note": "Old",
                "actions": [],
                "chat_suggestion": "",
                "generated_at": datetime.now().isoformat(),
            }
        ),
        encoding="utf-8",
    )

    assert brf._load_cache() is not None
    assert brf._load_cache("abc123") is None


def test_the_fingerprint_is_never_served_to_the_client(cache_file):
    saved = brf._save_cache({"note": "Hi", "actions": [], "chat_suggestion": ""}, "abc123")

    assert saved["book_fingerprint"] == "abc123"
    assert "book_fingerprint" not in brf._without_fingerprint(saved)
