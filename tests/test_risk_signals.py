"""
Unit tests for backend.services.risk_signals — deterministic, no LLM.

Covers:
- single-name concentration breach fires for moderate not aggressive at same weight
- sector check skips blank sector rows
- FX mismatch degrades gracefully when rate is missing
- drawdown check skips rows with unknown cost (0/None)
- idempotent emit: two runs produce same count (no duplicates)
- breach→clear flips status to 'monitoring'
- determinism: same inputs → same output
"""

from __future__ import annotations

import pytest


def _make_rows(specs: list[dict]) -> list[dict]:
    """Build minimal hydrated rows for testing."""
    rows = []
    for i, s in enumerate(specs):
        rows.append(
            {
                "instrument_id": s.get("instrument_id", f"inst-{i}"),
                "label": s.get("label", f"Asset{i}"),
                "ticker": s.get("ticker", f"TKR{i}"),
                "quantity": float(s.get("quantity", 10)),
                "price": float(s.get("price", 100)),
                "quote_per_unit": float(s.get("qpu", 1.0)),
                "quote_currency": s.get("ccy", "USD"),
                "currency": s.get("cost_ccy", s.get("ccy", "USD")),
                "asset_class": s.get("asset_class", "equity"),
                "sector": s.get("sector", ""),
                "bucket": s.get("bucket", "liquid"),
                "cost_basis": s.get("cost_basis"),
                "prev_price": s.get("prev_price"),
                "day_change": None,
                "day_change_pct": None,
                "tags": s.get("tags", []),
            }
        )
    return rows


def _make_profile(tolerance: str = "moderate") -> dict:
    return {"base_currency": "SGD", "risk_tolerance": tolerance}


# ── compute_risk unit tests ────────────────────────────────────────────────────


def test_single_name_breach_moderate():
    """A 20% position should breach the moderate 15% single-name threshold."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 0.74}
    # 2 rows: one at 80% weight, one at 20%
    rows = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 80},
            {"instrument_id": "i2", "price": 100, "quantity": 20},
        ]
    )
    findings = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    single_name = [f for f in findings if f["kind"] == "single_name"]
    ids = {f["affected_instrument_ids"][0] for f in single_name}
    assert "i1" in ids  # 80% clearly over 15%
    assert "i2" in ids  # 20% over 15%


def test_single_name_uses_the_risk_card_guideline_not_the_tolerance_table():
    """Single-name concentration has ONE authority — the guideline the user sets on
    Investments → Risk (`profile.risk_concentration_guideline`). It used to follow the
    tolerance table (15% moderate / 25% aggressive), so this story and the Risk card could
    disagree about the same holding (ADR 2026-09-25-concentration-today-and-benchmark-have-
    one-authority). Tolerance no longer moves the single-name verdict."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 18},
            {"instrument_id": "i2", "price": 100, "quantity": 82},
        ]
    )

    def single_ids(profile: dict) -> set[str]:
        found = compute_risk(rows=rows, rates=rates, profile=profile)
        return {
            i for f in found if f["kind"] == "single_name" for i in f["affected_instrument_ids"]
        }

    moderate = _make_profile("moderate")
    aggressive = _make_profile("aggressive")
    assert "i1" not in single_ids(moderate)  # 18% < default 20% guideline
    assert single_ids(moderate) == single_ids(aggressive)
    assert "i1" in single_ids({**moderate, "risk_concentration_guideline": 15.0})


def test_a_name_held_in_two_accounts_is_counted_once_at_its_full_size():
    """Regression: values were keyed `{instrument_id: value}`, so a name at two brokers kept only
    the LAST account's leg — 12% + 12% read as 12% (no breach) and the class/sector folds added
    that one leg twice. It is one 24% holding."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 1.0}
    rows = _make_rows(
        [
            {"instrument_id": "same", "ticker": "NVDA", "price": 100, "quantity": 12},
            {"instrument_id": "same", "ticker": "NVDA", "price": 100, "quantity": 12},
            *[{"instrument_id": f"o{i}", "price": 100, "quantity": 19} for i in range(4)],
        ]
    )
    found = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    single = [f for f in found if f["kind"] == "single_name"]
    assert len(single) == 1
    assert single[0]["affected_instrument_ids"] == ["same"]
    assert single[0]["metric_pct"] == pytest.approx(24.0)


def test_cash_positions_are_not_in_the_risk_denominator():
    """Risk is measured against the invested book, the same denominator the Risk card uses —
    a cash position must not dilute a concentration below the guideline."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 1.0}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 30},
            {"instrument_id": "i2", "price": 100, "quantity": 70},
            {"instrument_id": "cash", "price": 1, "quantity": 100_000, "asset_class": "cash"},
        ]
    )
    found = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    ids = {i for f in found if f["kind"] == "single_name" for i in f["affected_instrument_ids"]}
    assert ids == {"i1", "i2"}


def test_sector_skips_blank_sector():
    """Rows with blank sector should never generate a sector finding."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "sector": ""},
            {"instrument_id": "i2", "sector": ""},
        ]
    )
    findings = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    sector_findings = [f for f in findings if f["kind"] == "sector"]
    assert sector_findings == []


def test_fx_mismatch_degrades_when_rate_missing():
    """A holding whose currency has no FX rate cannot be valued. The share checks pause (a
    share of a partly valued book would be overstated) and ONE finding names the missing
    currency — never a 1:1 guess, never a concentration read off the partial book."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0}  # USD rate absent
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "price": 1000, "quantity": 100},
            {"instrument_id": "i2", "ccy": "SGD", "price": 100, "quantity": 1},
        ]
    )
    findings = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))

    assert [f["kind"] for f in findings] == ["fx_mismatch"]
    only = findings[0]
    assert only["metric_pct"] is None
    assert "USD" in only["status_reason"]
    assert "missing" in only["status_reason"].lower()
    assert only["affected_instrument_ids"] == ["i1"]


def test_missing_rate_still_runs_the_drawdown_check():
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD"},
            {"instrument_id": "i2", "ccy": "SGD", "price": 50, "quantity": 10, "cost_basis": 100},
        ]
    )
    kinds = {f["kind"] for f in compute_risk(rows=rows, rates=rates, profile=_make_profile())}
    assert kinds == {"fx_mismatch", "drawdown"}


def test_drawdown_skips_unknown_cost():
    """Drawdown check must skip rows where cost_basis is 0 or None."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "price": 50, "quantity": 10, "cost_basis": None},
            {"instrument_id": "i2", "ccy": "USD", "price": 50, "quantity": 10, "cost_basis": 0},
        ]
    )
    findings = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    drawdown_findings = [f for f in findings if f["kind"] == "drawdown"]
    assert drawdown_findings == []


def test_drawdown_fires_on_known_loss():
    """A position with >25% drawdown (moderate threshold) fires a drawdown finding."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 1.0}
    # cost 100/share, price now 60/share → drawdown = 40% > 25%
    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "USD", "price": 60, "quantity": 10, "cost_basis": 100.0},
        ]
    )
    findings = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    drawdown_findings = [f for f in findings if f["kind"] == "drawdown"]
    assert len(drawdown_findings) == 1
    assert drawdown_findings[0]["metric_pct"] == pytest.approx(40.0, abs=0.1)


def test_drawdown_combines_account_positions_before_comparing_cost():
    """A small second account must not replace the first account's market value."""
    from backend.services.risk_signals import compute_risk

    rows = _make_rows(
        [
            {
                "instrument_id": "btc",
                "label": "Bitcoin",
                "ticker": "BTC",
                "quantity": 0.04216098,
                "price": 79000,
                "cost_basis": 82523.455,
                "cost_ccy": "SGD",
            },
            {
                "instrument_id": "btc",
                "label": "Bitcoin",
                "ticker": "BTC",
                "quantity": 0.00072927,
                "price": 79000,
                "cost_basis": 67637.33,
                "cost_ccy": "USD",
            },
        ]
    )
    findings = compute_risk(
        rows=rows,
        rates={"SGD": 1.0, "USD": 0.74},
        profile=_make_profile("aggressive"),
    )
    assert [f for f in findings if f["kind"] == "drawdown"] == []


def test_single_name_concentration_sums_accounts_once():
    from backend.services.risk_signals import compute_risk

    rows = _make_rows(
        [
            {"instrument_id": "btc", "ticker": "BTC", "quantity": 30, "price": 1},
            {"instrument_id": "btc", "ticker": "BTC", "quantity": 20, "price": 1},
            {"instrument_id": "other", "quantity": 50, "price": 1},
        ]
    )
    findings = compute_risk(
        rows=rows,
        rates={"SGD": 1.0, "USD": 1.0},
        profile=_make_profile("aggressive"),
    )
    btc = [
        f
        for f in findings
        if f["kind"] == "single_name" and f["affected_instrument_ids"] == ["btc"]
    ]
    assert len(btc) == 1
    assert btc[0]["metric_pct"] == 50.0


def test_determinism():
    """Same inputs always produce same output (order-stable)."""
    from backend.services.risk_signals import compute_risk

    rates = {"SGD": 1.0, "USD": 0.74}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 80, "ccy": "USD"},
            {"instrument_id": "i2", "price": 100, "quantity": 20, "ccy": "USD"},
        ]
    )
    out1 = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    out2 = compute_risk(rows=rows, rates=rates, profile=_make_profile("moderate"))
    assert out1 == out2


# ── emit_risk_signals tests ────────────────────────────────────────────────────


@pytest.fixture
def signal_store(db_isolated):
    """Isolated IntelStoriesStore — db_isolated redirects the process-wide engine
    singleton to a fresh, empty database for the test."""
    from backend.db.intel_stories_store import IntelStoriesStore

    return IntelStoriesStore()


def test_emit_idempotent(monkeypatch, signal_store):
    """Running emit_risk_signals twice must not create duplicate stories."""
    from backend.services.risk_signals import emit_risk_signals

    rates = {"SGD": 1.0, "USD": 1.0}
    rows = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 80},
            {"instrument_id": "i2", "price": 100, "quantity": 20},
        ]
    )
    profile = _make_profile("moderate")

    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows)
    monkeypatch.setattr("backend.services.risk_signals.load_fx_cache", lambda: rates)
    monkeypatch.setattr("backend.services.risk_signals.read_profile", lambda: profile)

    emit_risk_signals(store=signal_store)
    count_after_first = len([s for s in signal_store.all() if s.get("type") == "risk"])

    emit_risk_signals(store=signal_store)
    count_after_second = len([s for s in signal_store.all() if s.get("type") == "risk"])

    assert count_after_first == count_after_second


def test_emit_breach_clear(monkeypatch, signal_store):
    """A resolved breach must flip status from action_needed to monitoring."""
    from backend.services.risk_signals import emit_risk_signals

    rates = {"SGD": 1.0, "USD": 1.0}
    # First run: one large position (80%) → breaches single-name
    rows_breach = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 80},
            {"instrument_id": "i2", "price": 100, "quantity": 20},
        ]
    )
    profile = _make_profile("moderate")

    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows_breach)
    monkeypatch.setattr("backend.services.risk_signals.load_fx_cache", lambda: rates)
    monkeypatch.setattr("backend.services.risk_signals.read_profile", lambda: profile)

    emit_risk_signals(store=signal_store)
    breached = [
        s
        for s in signal_store.all()
        if s.get("type") == "risk" and s.get("status") == "action_needed"
    ]
    assert len(breached) > 0

    # Second run: balanced portfolio → breach resolved
    rows_clear = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 10},
            {"instrument_id": "i2", "price": 100, "quantity": 10},
            {"instrument_id": "i3", "price": 100, "quantity": 10},
            {"instrument_id": "i4", "price": 100, "quantity": 10},
            {"instrument_id": "i5", "price": 100, "quantity": 10},
            {"instrument_id": "i6", "price": 100, "quantity": 10},
            {"instrument_id": "i7", "price": 100, "quantity": 10},
            {"instrument_id": "i8", "price": 100, "quantity": 10},
            {"instrument_id": "i9", "price": 100, "quantity": 10},
            {"instrument_id": "i10", "price": 100, "quantity": 10},
        ]
    )

    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows_clear)
    emit_risk_signals(store=signal_store)

    still_action_needed = [
        s
        for s in signal_store.all()
        if s.get("type") == "risk"
        and s.get("risk_kind") == "single_name"
        and s.get("status") == "action_needed"
        and any(iid in (s.get("affected_instrument_ids") or []) for iid in ("i1", "i2"))
    ]
    # The previously-breached single-name findings for i1/i2 should now be monitoring
    assert len(still_action_needed) == 0


def test_emit_does_not_resolve_a_breach_while_a_rate_is_missing(monkeypatch, signal_store):
    """The share checks pause during an FX outage; their silence must not read as "resolved"."""
    from backend.services.risk_signals import emit_risk_signals

    rows = _make_rows(
        [
            {"instrument_id": "i1", "ccy": "SGD", "price": 100, "quantity": 80},
            {"instrument_id": "i2", "ccy": "SGD", "price": 100, "quantity": 20},
        ]
    )
    profile = _make_profile("moderate")
    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows)
    monkeypatch.setattr("backend.services.risk_signals.load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr("backend.services.risk_signals.read_profile", lambda: profile)
    emit_risk_signals(store=signal_store)

    rows.append(_make_rows([{"instrument_id": "i3", "ccy": "USD"}])[0])  # no USD rate
    emit_risk_signals(store=signal_store)

    risks = [s for s in signal_store.all() if s.get("type") == "risk"]
    assert not any(s.get("resolved") for s in risks if s.get("risk_kind") == "single_name")
    assert any(s.get("risk_kind") == "fx_mismatch" and s.get("metric_pct") is None for s in risks)


def _balanced_rows() -> list[dict]:
    return _make_rows(
        [{"instrument_id": f"i{n}", "price": 100, "quantity": 10} for n in range(1, 11)]
    )


def test_emit_breach_auto_resolves(monkeypatch, signal_store):
    """A cleared breach auto-resolves: resolved=True with a human reason, so the card
    leaves the feed for the archive (recoverable via Reopen)."""
    from backend.services.risk_signals import emit_risk_signals

    monkeypatch.setattr(
        "backend.services.risk_signals.load_fx_cache", lambda: {"SGD": 1.0, "USD": 1.0}
    )
    monkeypatch.setattr(
        "backend.services.risk_signals.read_profile", lambda: _make_profile("moderate")
    )

    rows_breach = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 80},
            {"instrument_id": "i2", "price": 100, "quantity": 20},
        ]
    )
    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows_breach)
    emit_risk_signals(store=signal_store)

    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", _balanced_rows)
    emit_risk_signals(store=signal_store)

    resolved = [
        s
        for s in signal_store.all()
        if s.get("risk_kind") == "single_name"
        and any(iid in (s.get("affected_instrument_ids") or []) for iid in ("i1", "i2"))
    ]
    assert resolved, "expected the previously-breached single-name stories to persist"
    assert all(s.get("resolved") is True for s in resolved)
    assert all((s.get("resolved_reason") or "").strip() for s in resolved)


def test_emit_rebreach_clears_resolved(monkeypatch, signal_store):
    """A re-fired breach reopens an auto-resolved risk (resolved back to False, action_needed)."""
    from backend.services.risk_signals import emit_risk_signals

    monkeypatch.setattr(
        "backend.services.risk_signals.load_fx_cache", lambda: {"SGD": 1.0, "USD": 1.0}
    )
    monkeypatch.setattr(
        "backend.services.risk_signals.read_profile", lambda: _make_profile("moderate")
    )

    rows_breach = _make_rows(
        [
            {"instrument_id": "i1", "price": 100, "quantity": 80},
            {"instrument_id": "i2", "price": 100, "quantity": 20},
        ]
    )
    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows_breach)
    emit_risk_signals(store=signal_store)
    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", _balanced_rows)
    emit_risk_signals(store=signal_store)  # → resolved
    monkeypatch.setattr("backend.services.risk_signals.hydrated_positions", lambda: rows_breach)
    emit_risk_signals(store=signal_store)  # → re-breach

    reopened = [
        s
        for s in signal_store.all()
        if s.get("risk_kind") == "single_name"
        and s.get("status") == "action_needed"
        and "i1" in (s.get("affected_instrument_ids") or [])
    ]
    assert reopened, "the re-breached single-name story should be action_needed again"
    assert all(s.get("resolved") is False for s in reopened)
