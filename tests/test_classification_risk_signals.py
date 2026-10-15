"""
Unit tests for the region overweight finding added to services.risk_signals.

Covers:
- Book with >=70% in one region emits kind='region' finding.
- Book with <70% in any region emits no region finding.
- Blank-region rows are excluded (no fabricated concentration).
"""

from __future__ import annotations

from backend.services.risk_signals import compute_risk


def _make_rows(specs: list[dict]) -> list[dict]:
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
                "prev_price": None,
                "day_change": None,
                "day_change_pct": None,
                "tags": [],
            }
        )
    return rows


def _make_profile(tolerance: str = "moderate") -> dict:
    return {"base_currency": "USD", "risk_tolerance": tolerance}


def _setup(monkeypatch, iid_to_region: dict[str, str]):
    """Wire classify_instrument and InstrumentStore so risk_signals sees desired regions."""

    # Patch classify_instrument at the site risk_signals imported it from.
    def _fake_classify(inst: dict) -> dict:
        iid = inst.get("id", "")
        return {"sector": "", "region": iid_to_region.get(iid, ""), "source": "heuristic"}

    monkeypatch.setattr("backend.services.risk_signals.classify_instrument", _fake_classify)

    # Patch InstrumentStore (locally imported inside compute_risk) at its source module.
    class _FakeStore:
        def all(self):
            return [{"id": iid} for iid in iid_to_region]

    import backend.db.instrument_store as _is_mod

    monkeypatch.setattr(_is_mod, "InstrumentStore", lambda: _FakeStore())


def test_region_overweight_fires_at_100pct(monkeypatch):
    """100% United States → fires kind='region'."""
    _setup(monkeypatch, {"us1": "United States", "us2": "United States"})
    rows = _make_rows(
        [
            {"instrument_id": "us1", "ccy": "USD", "price": 100, "quantity": 80},
            {"instrument_id": "us2", "ccy": "USD", "price": 100, "quantity": 20},
        ]
    )
    findings = compute_risk(rows=rows, rates={"USD": 1.0}, profile=_make_profile())
    region_findings = [f for f in findings if f["kind"] == "region"]
    assert len(region_findings) >= 1
    labels = {f["label"] for f in region_findings}
    assert "United States" in labels


def test_region_overweight_does_not_fire_below_70pct(monkeypatch):
    """60 / 40 split → no region exceeds 70% → no region finding."""
    _setup(monkeypatch, {"us1": "United States", "sg1": "Singapore"})
    rows = _make_rows(
        [
            {"instrument_id": "us1", "ccy": "USD", "price": 100, "quantity": 60},
            {"instrument_id": "sg1", "ccy": "USD", "price": 100, "quantity": 40},
        ]
    )
    findings = compute_risk(rows=rows, rates={"USD": 1.0}, profile=_make_profile())
    region_findings = [f for f in findings if f["kind"] == "region"]
    assert region_findings == []


def test_blank_region_rows_excluded(monkeypatch):
    """Instruments with blank region are excluded from the region bucket entirely."""
    _setup(monkeypatch, {"blank": "", "us1": "United States"})
    rows = _make_rows(
        [
            {"instrument_id": "blank", "ccy": "USD", "price": 100, "quantity": 90},
            {"instrument_id": "us1", "ccy": "USD", "price": 100, "quantity": 10},
        ]
    )
    findings = compute_risk(rows=rows, rates={"USD": 1.0}, profile=_make_profile())
    region_findings = [f for f in findings if f["kind"] == "region"]
    # 'blank' has no region → not counted; 'us1' is only 10% → no overweight
    assert region_findings == []


def test_region_finding_shape(monkeypatch):
    """Region finding carries all required fields."""
    _setup(monkeypatch, {"us1": "United States", "us2": "United States"})
    rows = _make_rows(
        [
            {"instrument_id": "us1", "ccy": "USD", "price": 100, "quantity": 80},
            {"instrument_id": "us2", "ccy": "USD", "price": 100, "quantity": 20},
        ]
    )
    findings = compute_risk(rows=rows, rates={"USD": 1.0}, profile=_make_profile())
    region_findings = [f for f in findings if f["kind"] == "region"]
    assert len(region_findings) >= 1
    f = region_findings[0]
    required = {
        "kind",
        "label",
        "ticker",
        "severity",
        "metric_pct",
        "threshold_pct",
        "status",
        "status_reason",
        "portfolio_impact",
        "action_items",
        "affected_instrument_ids",
    }
    assert required <= set(f.keys())
    assert f["threshold_pct"] == 70.0
    assert f["kind"] == "region"
