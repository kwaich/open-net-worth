"""Regression pins for the balance-mismatch-tracer starter — each one a real turn that went
wrong without it."""

from __future__ import annotations

from backend.db.skill_store import STARTER_SKILLS

BODY = next(s for s in STARTER_SKILLS if s["id"] == "balance-mismatch-tracer")["body"]


def test_no_verdict_from_the_ledger_alone():
    # A user-written tracer re-added the ledger's own rows and answered "everything reconciles".
    assert "The ledger always agrees with itself." in BODY
    assert "Without the bank's side (step 2) there is no verdict." in BODY


def test_a_small_gap_is_rows_not_rounding():
    assert "A gap is rows until proven otherwise — cents included." in BODY
    assert 'Never call it rounding or "just a few cents"' in BODY


def test_a_row_that_differs_is_traced_before_its_cause_is_named():
    # 2026-10-15, real turn: a statement imported cleanly, one row was then edited by 0.15, and
    # the model said "the import carried that row at 112.24" without reading the row's history.
    assert "An imported row can still be wrong." in BODY
    assert "Never blame the import, or guess, without that check." in BODY
    assert "`trace_transactions` on it says whether it was edited after it was booked" in BODY
