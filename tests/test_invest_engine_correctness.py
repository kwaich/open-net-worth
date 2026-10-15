"""
Regression tests for three Investments engine correctness fixes (I1/I2/I3).

I1 — benchmark comparison presents a sparse-history benchmark's short-period return as the
     full-period return (services/returns.py _compute_benchmark_twr sufficiency gate).
I2 — asset_detail lot rows drop fee amortization (services/asset_detail.py _lot_rows).
I3 — lot term boundary off-by-one vs the tax engine (services/asset_detail.py _lot_rows).

Run:
    .venv/bin/python3 -m pytest tests/test_invest_engine_correctness.py -q
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from backend.db.lot_store import LotStore

# ══════════════════════════════════════════════════════════════════════════════
# I1 — benchmark sufficiency gate misses synthetic carry-backward anchor
# ══════════════════════════════════════════════════════════════════════════════

_TODAY_I1 = date(2026, 6, 5)


@pytest.fixture
def bench_isolated(tmp_path, monkeypatch, db_isolated):
    """InstrumentStore and PriceHistoryStore both resolve against the per-test isolated
    database (db_isolated) — both are DB-backed (plan 2, tasks 3 and 5)."""

    def seed(instruments, price_history):
        from sqlalchemy import delete

        from backend.db.engine import get_engine
        from backend.db.instrument_store import InstrumentStore
        from backend.db.price_history_io import import_price_history
        from backend.db.tables import price_points

        InstrumentStore().replace_all(instruments)  # full replace, like the old file write
        with get_engine().begin() as conn:
            conn.execute(delete(price_points))  # full replace, like the old file write
            import_price_history(conn, price_history)

    return seed


def test_I1_benchmark_sparse_history_is_insufficient(bench_isolated):
    """A benchmark with only ~90 days of history on a 1Y window must NOT produce a return.

    The bug: _compute_benchmark_twr checks start >= PriceHistoryStore().earliest_date()
    (the GLOBAL earliest, across ALL instruments). If another instrument has old history,
    earliest_date() is early (e.g. 2020-01-01), so start(2025-06-05) >= 2020-01-01 → True.
    The benchmark instrument itself has only 90 days of history, so the carry-backward branch
    plants a synthetic anchor at start — but sufficient=True lets the short-period return
    through, labelled as the full 1Y return.

    Fix: check the benchmark instrument's OWN series for a real price at/before start.
    """
    from backend.services import returns as r

    bench_inst = {
        "id": "I-SPY",
        "symbol": "SPY",
        "name": "SPDR S&P 500",
        "price_source": "yahoo",
        "price_source_id": "SPY",
    }
    other_inst = {
        "id": "I-OTHER",
        "symbol": "OTHER",
        "name": "Old Instrument",
        "price_source": "yahoo",
        "price_source_id": "OTHER",
    }
    bench_isolated(
        [bench_inst, other_inst],
        {
            # OTHER has ancient history — makes global earliest_date() predate 1Y start.
            "OTHER": [
                {"date": "2020-01-01", "price": 10.0},
                {"date": "2026-06-05", "price": 20.0},
            ],
            # SPY benchmark: only 90 days of history before window end (2026-06-05).
            # No real price at/before 1Y start (2025-06-05) → carry-backward fires.
            "SPY": [
                {"date": "2026-03-05", "price": 500.0},
                {"date": "2026-06-05", "price": 550.0},
            ],
        },
    )

    out = r.benchmark_return("I-SPY", "1Y", today=_TODAY_I1)
    assert out is not None
    # The synthetic carry-backward anchor must be detected → insufficient → twr null.
    assert out["data_quality"]["sufficient"] is False, (
        "benchmark with only 90 days of history must be insufficient on a 1Y window "
        "(the global earliest_date from OTHER must not make it appear sufficient)"
    )
    assert out["twr"] is None


def test_I1_benchmark_full_coverage_still_computes(bench_isolated):
    """A benchmark WITH a real price at/before the window start must still compute correctly.

    This guards the regression: the fix must not break the normal case.
    """
    from backend.services import returns as r

    bench_inst = {
        "id": "I-SPY",
        "symbol": "SPY",
        "name": "SPDR S&P 500",
        "price_source": "yahoo",
        "price_source_id": "SPY",
    }
    bench_isolated(
        [bench_inst],
        {
            "SPY": [
                {"date": "2025-06-05", "price": 100.0},  # real price AT window start
                {"date": "2026-06-05", "price": 110.0},
            ]
        },
    )

    out = r.benchmark_return("I-SPY", "1Y", today=_TODAY_I1)
    assert out is not None
    assert out["data_quality"]["sufficient"] is True
    assert out["twr"] is not None
    assert out["twr"]["period_return"] == pytest.approx(0.10)


# ══════════════════════════════════════════════════════════════════════════════
# I2 — _lot_rows drops fee amortization (uses raw unit_cost instead of effective)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def lot_rows_isolated(db_isolated):
    """LotStore is DB-backed (plan 4b task 2) — db_isolated gives direct _lot_rows tests
    a fresh per-test database instead of a tmp_path file redirect. Seed via
    LotStore().bulk_insert(...) (verbatim rows, no _make_lot coercion)."""
    return None


def _make_lot(position_id: str, **overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "position_id": position_id,
        "acquired_at": (date.today() - timedelta(days=400)).isoformat(),
        "original_qty": 5.0,
        "remaining_qty": 5.0,
        "unit_cost": 100.0,
        "fees": 0.0,
        "closed": False,
    }
    base.update(overrides)
    return base


def test_I2_lot_rows_effective_unit_cost_includes_fees(lot_rows_isolated):
    """_lot_rows must amortize acquisition fees into effective_unit_cost and remaining_basis.

    Before the fix: uses raw unit_cost → effective_unit_cost=100.0, remaining_basis=500.0.
    After  the fix: uses effective_unit_cost() → effective_unit_cost=102.0, remaining_basis=510.0.
    """
    from backend.services.asset_detail import _lot_rows

    pos_id = str(uuid.uuid4())
    lot = _make_lot(
        pos_id,
        unit_cost=100.0,
        fees=10.0,  # 10 / 5 = 2.0 per unit
        original_qty=5.0,
        remaining_qty=5.0,
    )
    LotStore().bulk_insert([lot])

    rows = _lot_rows(pos_id)
    assert len(rows) == 1
    row = rows[0]

    # effective_unit_cost must include the amortized fee: 100 + 10/5 = 102.0
    assert row["effective_unit_cost"] == pytest.approx(102.0), (
        f"expected 102.0 (with fee amortization), got {row['effective_unit_cost']}"
    )
    # remaining_basis = 5 * 102.0 = 510.0
    assert row["remaining_basis"] == pytest.approx(510.0), (
        f"expected 510.0 (with fee amortization), got {row['remaining_basis']}"
    )


def test_I2_lot_rows_no_fees_unchanged(lot_rows_isolated):
    """A lot with no fees must produce the same effective_unit_cost as unit_cost."""
    from backend.services.asset_detail import _lot_rows

    pos_id = str(uuid.uuid4())
    lot = _make_lot(pos_id, unit_cost=50.0, fees=0.0, original_qty=3.0, remaining_qty=3.0)
    LotStore().bulk_insert([lot])

    rows = _lot_rows(pos_id)
    assert rows[0]["effective_unit_cost"] == pytest.approx(50.0)
    assert rows[0]["remaining_basis"] == pytest.approx(150.0)


# ══════════════════════════════════════════════════════════════════════════════
# I3 — lot term boundary off-by-one vs. classify_term in lots.py
# ══════════════════════════════════════════════════════════════════════════════


def test_I3_lot_term_exactly_365_days_is_long(lot_rows_isolated):
    """A lot held for EXACTLY 365 days must be 'long', matching classify_term (>= 365).

    Before the fix: asset_detail._lot_rows uses `days > 365`, so exactly 365 → 'short'.
    After  the fix: uses `days >= 365`, so exactly 365 → 'long'.
    """
    from backend.services.asset_detail import _lot_rows

    pos_id = str(uuid.uuid4())
    today = date.today()
    acquired = (today - timedelta(days=365)).isoformat()
    lot = _make_lot(pos_id, acquired_at=acquired)
    LotStore().bulk_insert([lot])

    rows = _lot_rows(pos_id)
    assert len(rows) == 1
    assert rows[0]["term_to_date"] == "long", (
        f"Expected 'long' for exactly 365 days held, got '{rows[0]['term_to_date']}'"
    )
