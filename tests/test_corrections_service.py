"""
Unit tests for corrections_service — the learn-from-corrections detector (RFC P2-SP2).

Pure + offline: ``suggestions`` is exercised by passing events/rules/dismissals in
directly (no I/O), and ``_payee_key`` / ``record_correction`` are tested against
in-memory dicts (a fake store for the recorder). No LLM, no network.

Covers (RFC §test strategy): payee normalization, threshold fires at exactly
min_count, modal category on mixed corrections, suppression of a payee already
covered by an enabled rule, suppression of a dismissed pair, lookback window,
and the recorder gates (kind, no-op edit, missing payee).

Run:
    .venv/bin/pytest tests/test_corrections_service.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services import corrections as cs

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _ev(payee_key="starbucks", to="food", sample="STARBUCKS", days_ago=0, kind="expense"):
    """A correction event `days_ago` before the fixed `NOW`."""
    ts = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "payee_key": payee_key,
        "payee_sample": sample,
        "to_category": to,
        "kind": kind,
        "created_at": ts,
    }


def _automation(keyword: str, category: str) -> dict:
    return {
        "id": f"automation-{keyword}",
        "name": keyword,
        "status": "active",
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"description_contains": keyword},
            "limit": "each",
        },
        "execution": {
            "kind": "transaction_transform",
            "set": {"category": category},
            "stop": True,
        },
    }


# ── _payee_key normalization ─────────────────────────────────────────────────────


def test_payee_key_strips_digits_and_punct():
    # The MVP normalizer drops digits + punctuation (so the "#123" trailing ref folds
    # away) but keeps alphabetic words — it is deliberately simple (the seam P2-SP4's
    # payee-merge replaces). "STARBUCKS #123" → "starbucks"; "STARBUCKS @ ION" keeps the
    # ION word.
    assert cs._payee_key("STARBUCKS #123") == "starbucks"
    assert cs._payee_key("STARBUCKS @ ION") == "starbucks ion"
    assert cs._payee_key("starbucks") == "starbucks"


def test_payee_key_collapses_whitespace():
    assert cs._payee_key("  Cold   Storage  ") == "cold storage"


def test_payee_key_blank_returns_empty():
    assert cs._payee_key("") == ""
    assert cs._payee_key(None) == ""
    assert cs._payee_key("12345 #!@") == ""  # all stripped → empty


# ── suggestions: threshold ───────────────────────────────────────────────────────


def test_fires_at_exactly_min_count():
    events = [_ev() for _ in range(3)]
    out = cs.suggestions(events=events, enabled_rules=[], dismissals=[], min_count=3, now=NOW)
    assert len(out) == 1
    s = out[0]
    assert s["payee_key"] == "starbucks"
    assert s["target_category"] == "food"
    assert s["count"] == 3
    assert s["suggested_rule"]["match"]["description_contains"] == "starbucks"
    assert s["suggested_rule"]["set"]["category"] == "food"


def test_below_min_count_no_suggestion():
    events = [_ev() for _ in range(2)]
    out = cs.suggestions(events=events, enabled_rules=[], dismissals=[], min_count=3, now=NOW)
    assert out == []


# ── suggestions: modal category on mixed corrections ─────────────────────────────


def test_modal_category_wins_on_mixed():
    # 3× food, 1× shopping → suggest food
    events = [_ev(to="food") for _ in range(3)] + [_ev(to="shopping")]
    out = cs.suggestions(events=events, enabled_rules=[], dismissals=[], min_count=3, now=NOW)
    assert len(out) == 1
    assert out[0]["target_category"] == "food"
    assert out[0]["count"] == 3


def test_mixed_below_threshold_for_modal_no_suggestion():
    # 2× food, 2× shopping → modal count is only 2, below min_count=3
    events = [_ev(to="food"), _ev(to="food"), _ev(to="shopping"), _ev(to="shopping")]
    out = cs.suggestions(events=events, enabled_rules=[], dismissals=[], min_count=3, now=NOW)
    assert out == []


# ── suggestions: suppression ─────────────────────────────────────────────────────


def test_a_matching_automation_no_longer_suppresses_the_suggestion():
    """This asserted suppression until 2026-08-02. A rule used to be a declarative row this
    service could match a payee against; it is a Lucius run judging the match itself after
    commit now, so nothing here can answer "would an Automation handle this?" without the
    model (services/categorize._covered_by_enabled_rule).

    The cost is a redundant suggestion — offered, never applied."""
    events = [_ev() for _ in range(3)]
    rule = _automation("starbucks", "food")
    out = cs.suggestions(events=events, enabled_rules=[rule], dismissals=[], min_count=3, now=NOW)
    assert len(out) == 1


def test_not_suppressed_by_unrelated_rule():
    events = [_ev() for _ in range(3)]
    rule = _automation("grab", "transport")
    out = cs.suggestions(events=events, enabled_rules=[rule], dismissals=[], min_count=3, now=NOW)
    assert len(out) == 1


def test_suppressed_by_dismissal():
    events = [_ev() for _ in range(3)]
    dismissals = [{"payee_key": "starbucks", "target_category": "food"}]
    out = cs.suggestions(
        events=events, enabled_rules=[], dismissals=dismissals, min_count=3, now=NOW
    )
    assert out == []


def test_dismissal_of_different_target_does_not_suppress():
    events = [_ev(to="food") for _ in range(3)]
    dismissals = [{"payee_key": "starbucks", "target_category": "transport"}]
    out = cs.suggestions(
        events=events, enabled_rules=[], dismissals=dismissals, min_count=3, now=NOW
    )
    assert len(out) == 1  # the food suggestion is independent of the transport dismissal


# ── suggestions: lookback ────────────────────────────────────────────────────────


def test_lookback_excludes_old_corrections():
    # 2 recent + 1 way old → only 2 within window, below threshold
    events = [_ev(days_ago=1), _ev(days_ago=2), _ev(days_ago=400)]
    out = cs.suggestions(
        events=events, enabled_rules=[], dismissals=[], min_count=3, lookback_days=180, now=NOW
    )
    assert out == []
    # all 3 within a wider window → fires
    out2 = cs.suggestions(
        events=events, enabled_rules=[], dismissals=[], min_count=3, lookback_days=500, now=NOW
    )
    assert len(out2) == 1


# ── suggestions: ordering ────────────────────────────────────────────────────────


def test_suggestions_sorted_by_count_desc():
    events = [_ev(payee_key="grab", sample="GRAB", to="transport") for _ in range(5)] + [
        _ev() for _ in range(3)
    ]
    out = cs.suggestions(events=events, enabled_rules=[], dismissals=[], min_count=3, now=NOW)
    assert [s["payee_key"] for s in out] == ["grab", "starbucks"]


# ── record_correction gates ──────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self):
        self.recorded = []

    def record(self, event):
        stored = {"id": "x", **event}
        self.recorded.append(stored)
        return stored


def test_record_correction_writes_on_category_change():
    store = _FakeStore()
    old = {"id": "t1", "kind": "expense", "category": "shopping", "description": "STARBUCKS #5"}
    new = {"id": "t1", "kind": "expense", "category": "food", "description": "STARBUCKS #5"}
    ev = cs.record_correction(old, new, store=store)
    assert ev is not None
    assert len(store.recorded) == 1
    assert store.recorded[0]["payee_key"] == "starbucks"
    assert store.recorded[0]["from_category"] == "shopping"
    assert store.recorded[0]["to_category"] == "food"


def test_record_correction_skips_noop_category():
    store = _FakeStore()
    old = {"id": "t1", "kind": "expense", "category": "food", "description": "STARBUCKS"}
    new = {"id": "t1", "kind": "expense", "category": "food", "description": "STARBUCKS"}
    assert cs.record_correction(old, new, store=store) is None
    assert store.recorded == []


def test_record_correction_skips_non_correctable_kind():
    store = _FakeStore()
    # transfers never carry an expense category → not a correction
    old = {"id": "t1", "kind": "transfer", "category": None, "description": "Move"}
    new = {"id": "t1", "kind": "transfer", "category": None, "description": "Move"}
    assert cs.record_correction(old, new, store=store) is None
    assert store.recorded == []


def test_record_correction_skips_blank_payee():
    store = _FakeStore()
    old = {"id": "t1", "kind": "expense", "category": "shopping", "description": "12345"}
    new = {"id": "t1", "kind": "expense", "category": "food", "description": "12345"}
    # payee key normalizes to empty → skip
    assert cs.record_correction(old, new, store=store) is None
    assert store.recorded == []


def test_record_correction_records_income():
    store = _FakeStore()
    old = {"id": "t1", "kind": "income", "category": "income", "description": "ACME PAYROLL"}
    new = {"id": "t1", "kind": "income", "category": "freelance", "description": "ACME PAYROLL"}
    ev = cs.record_correction(old, new, store=store)
    assert ev is not None and store.recorded[0]["kind"] == "income"
