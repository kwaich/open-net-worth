"""Tests for the options data model (sys-options-data-model RFC).

Covers:
  - intrinsic_per_share: call/put, ITM/OTM/ATM, None price
  - moneyness: call vs put, None → "unknown"
  - mark_value_base: long = +intrinsic, SHORT = −intrinsic (load-bearing liability case),
    expired/closed → 0, None underlying → None, FX conversion, multiplier × contracts applied once
  - premium total arithmetic and CSP collateral formula
  - summary: by-strategy split, expiry buckets, empty book → zeros, uncovered call flagged
  - Store: _make_option_contract coercion, round-trip CRUD, find_by_idempotency_key
  - apply_option_change (choke point): open creates; idempotent on idempotency_key;
    double-book guard; close/expire/update/delete; expiry sweep flips only past-expiry open
  - Expiry sweep: deterministic lifecycle flip, ITM flagging
  - Endpoint smoke: GET /api/options, GET /api/options/{id}, POST, PATCH, DELETE
  - Regression: short ITM call drives NW DOWN; expired OTM → 0; uncovered call flagged

Isolation: option store + instrument/account stores redirected to tmp_path. No LLM, no network.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.db.option_contract_store import OptionContractStore, _make_option_contract
from backend.services import options as opt_svc


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def isolated_stores(tmp_path, monkeypatch, db_isolated):
    """Redirect all relevant stores to tmp_path; stub FX to 1:1.

    AccountStore is DB-backed (plan 3 task 4) — db_isolated redirects it (and every
    other money store) to a fresh, empty, per-test database; no per-instance path needed.
    """
    from backend.core import fx as fx_mod
    from backend.services import lots as lots_service

    monkeypatch.setattr(fx_mod, "base_currency", lambda: "USD")
    monkeypatch.setattr(fx_mod, "to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2))
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    return tmp_path


@pytest.fixture
def seed_instruments(isolated_stores):
    """Create a minimal account + instrument for contract tests."""
    from backend.db.account_store import AccountStore
    from backend.db.instrument_store import InstrumentStore

    acct = AccountStore().create(
        {
            "name": "Vantage Brokerage",
            "kind": "asset",
            "type": "investment",
            "currency": "USD",
        }
    )
    inst = InstrumentStore().create(
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "manual",
            "manual_price": 150.0,
        }
    )
    return {"account": acct, "instrument": inst}


# ── Pure mark math ────────────────────────────────────────────────────────────


class TestIntrinsicPerShare:
    def test_call_itm(self):
        c = {"option_type": "call", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, 150.0) == 50.0

    def test_call_otm(self):
        c = {"option_type": "call", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, 80.0) == 0.0

    def test_call_atm(self):
        c = {"option_type": "call", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, 100.0) == 0.0

    def test_put_itm(self):
        c = {"option_type": "put", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, 80.0) == 20.0

    def test_put_otm(self):
        c = {"option_type": "put", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, 120.0) == 0.0

    def test_put_atm(self):
        c = {"option_type": "put", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, 100.0) == 0.0

    def test_none_price_returns_zero(self):
        c = {"option_type": "call", "strike": 100.0}
        assert opt_svc.intrinsic_per_share(c, None) == 0.0


class TestMoneyness:
    def test_call_itm(self):
        assert opt_svc.moneyness({"option_type": "call", "strike": 100.0}, 150.0) == "ITM"

    def test_call_otm(self):
        assert opt_svc.moneyness({"option_type": "call", "strike": 100.0}, 80.0) == "OTM"

    def test_call_atm(self):
        assert opt_svc.moneyness({"option_type": "call", "strike": 100.0}, 100.0) == "ATM"

    def test_put_itm(self):
        assert opt_svc.moneyness({"option_type": "put", "strike": 100.0}, 80.0) == "ITM"

    def test_put_otm(self):
        assert opt_svc.moneyness({"option_type": "put", "strike": 100.0}, 120.0) == "OTM"

    def test_none_price_unknown(self):
        assert opt_svc.moneyness({"option_type": "call", "strike": 100.0}, None) == "unknown"


class TestMarkValueBase:
    def _c(
        self,
        side="long",
        option_type="call",
        strike=100.0,
        contracts=1.0,
        multiplier=100,
        status="open",
        expiry=None,
        premium_currency="USD",
    ):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return {
            "option_type": option_type,
            "side": side,
            "strike": strike,
            "contracts": contracts,
            "multiplier": multiplier,
            "status": status,
            "expiry": expiry or tomorrow,
            "premium_currency": premium_currency,
        }

    def _rates(self):
        return {"USD": 1.0}

    def test_long_call_itm_positive(self):
        """Long ITM call → positive mark (asset)."""
        c = self._c(side="long", option_type="call", strike=100.0, contracts=1.0, multiplier=100)
        val = opt_svc.mark_value_base(c, 110.0, date.today(), "USD", self._rates())
        assert val == pytest.approx(1000.0)  # (110-100) × 1 × 100

    def test_short_call_itm_negative(self):
        """SHORT ITM call → NEGATIVE mark (liability). THE load-bearing test."""
        c = self._c(side="short", option_type="call", strike=100.0, contracts=2.0, multiplier=100)
        val = opt_svc.mark_value_base(c, 120.0, date.today(), "USD", self._rates())
        assert val == pytest.approx(-4000.0)  # −(120-100) × 2 × 100

    def test_expired_returns_zero(self):
        """Expired contract contributes 0 to live NW."""
        c = self._c(status="expired")
        val = opt_svc.mark_value_base(c, 120.0, date.today(), "USD", self._rates())
        assert val == 0.0

    def test_closed_returns_zero(self):
        c = self._c(status="closed")
        val = opt_svc.mark_value_base(c, 120.0, date.today(), "USD", self._rates())
        assert val == 0.0

    def test_none_underlying_returns_none(self):
        """Unknown underlying price → None (no fabricated 0)."""
        c = self._c(status="open")
        val = opt_svc.mark_value_base(c, None, date.today(), "USD", self._rates())
        assert val is None

    def test_past_expiry_open_returns_zero(self):
        """An open contract past its expiry date → 0 (cliff)."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        c = self._c(status="open", expiry=yesterday)
        val = opt_svc.mark_value_base(c, 150.0, date.today(), "USD", self._rates())
        assert val == 0.0

    def test_otm_long_call_zero(self):
        """OTM long call → 0 (no intrinsic value)."""
        c = self._c(side="long", option_type="call", strike=150.0)
        val = opt_svc.mark_value_base(c, 100.0, date.today(), "USD", self._rates())
        assert val == 0.0

    def test_put_short_itm_negative(self):
        """SHORT ITM put → NEGATIVE mark."""
        c = self._c(side="short", option_type="put", strike=100.0, contracts=1.0)
        val = opt_svc.mark_value_base(c, 80.0, date.today(), "USD", self._rates())
        assert val == pytest.approx(-2000.0)  # −(100-80) × 1 × 100

    def test_multiplier_applied_once(self):
        """Check that multiplier is applied exactly once (not squared)."""
        c = self._c(side="long", option_type="call", strike=100.0, contracts=1.0, multiplier=50)
        val = opt_svc.mark_value_base(c, 110.0, date.today(), "USD", self._rates())
        assert val == pytest.approx(500.0)  # (110-100) × 1 × 50

    def test_contracts_applied(self):
        """Check contracts multiplier."""
        c = self._c(side="long", option_type="call", strike=100.0, contracts=3.0, multiplier=100)
        val = opt_svc.mark_value_base(c, 110.0, date.today(), "USD", self._rates())
        assert val == pytest.approx(3000.0)  # (110-100) × 3 × 100


# ── Premium / collateral helpers ─────────────────────────────────────────────
def test_total_premium_native_arithmetic():
    """contracts × multiplier × premium_per_share."""
    c = {"contracts": 2.0, "multiplier": 100, "premium_per_share": 2.50}
    assert opt_svc.total_premium_native(c) == pytest.approx(500.0)


def test_csp_collateral_formula():
    """CSP collateral = strike × contracts × multiplier."""
    c = {"strategy_tag": "csp", "strike": 150.0, "contracts": 1.0, "multiplier": 100}
    assert opt_svc.csp_collateral_native(c) == pytest.approx(15000.0)


def test_csp_collateral_non_csp_returns_none():
    c = {"strategy_tag": "covered_call", "strike": 150.0, "contracts": 1.0, "multiplier": 100}
    assert opt_svc.csp_collateral_native(c) is None


# ── Store coercion ────────────────────────────────────────────────────────────


class TestMakeOptionContract:
    def test_bad_enum_clamps_to_default(self):
        row = _make_option_contract(
            {
                "account_id": "acc1",
                "underlying_instrument_id": "inst1",
                "option_type": "CALL",  # uppercase — should normalize
                "side": "LONG",  # uppercase
                "strike": 100.0,
                "expiry": "2027-06-20",
                "contracts": 1.0,
            }
        )
        assert row["option_type"] == "call"
        assert row["side"] == "long"

    def test_unknown_enum_clamps_to_default(self):
        row = _make_option_contract(
            {
                "account_id": "acc1",
                "underlying_instrument_id": "inst1",
                "option_type": "spread",  # invalid
                "side": "neutral",  # invalid
                "strike": 100.0,
                "expiry": "2027-06-20",
                "contracts": 1.0,
            }
        )
        assert row["option_type"] == "call"
        assert row["side"] == "long"

    def test_multiplier_defaults_100(self):
        row = _make_option_contract(
            {
                "account_id": "a",
                "underlying_instrument_id": "i",
                "option_type": "put",
                "side": "short",
                "strike": 50.0,
                "expiry": "2027-01-01",
                "contracts": 2.0,
            }
        )
        assert row["multiplier"] == 100

    def test_id_generated(self):
        row = _make_option_contract(
            {
                "account_id": "a",
                "underlying_instrument_id": "i",
                "option_type": "call",
                "side": "long",
                "strike": 50.0,
                "expiry": "2027-01-01",
                "contracts": 1.0,
            }
        )
        assert row["id"].startswith("opt_")

    def test_roundtrip_crud(self, isolated_stores):
        store = OptionContractStore()
        data = {
            "account_id": "acc1",
            "underlying_instrument_id": "inst1",
            "option_type": "call",
            "side": "short",
            "strike": 200.0,
            "expiry": "2027-12-20",
            "contracts": 5.0,
            "multiplier": 100,
            "premium_per_share": 3.50,
            "strategy_tag": "covered_call",
        }
        created = store.create_contract(data)
        assert created["id"]
        found = store.find_by_id(created["id"])
        assert found["strike"] == 200.0
        assert found["strategy_tag"] == "covered_call"
        store.delete(created["id"])
        assert store.find_by_id(created["id"]) is None


# ── find_by_idempotency_key ───────────────────────────────────────────────────
def test_find_by_idempotency_key(isolated_stores):
    store = OptionContractStore()
    store.create_contract(
        {
            "account_id": "a",
            "underlying_instrument_id": "i",
            "option_type": "call",
            "side": "long",
            "strike": 100.0,
            "expiry": "2027-01-01",
            "contracts": 1.0,
            "idempotency_key": "order-001",
        }
    )
    found = store.find_by_idempotency_key("order-001")
    assert found is not None
    assert found["idempotency_key"] == "order-001"

    assert store.find_by_idempotency_key("order-999") is None


# ── apply_option_change (choke point) ────────────────────────────────────────


class TestApplyOptionChange:
    def test_open_creates_contract(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        result = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 150.0,
                "expiry": "2027-06-20",
                "contracts": 1.0,
            }
        )
        assert result.get("ok") is True
        assert result.get("contract_id")

    def test_open_idempotent_on_idempotency_key(self, seed_instruments):
        """Second open with same idempotency_key → no-op, same id returned."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        base_change = {
            "type": "open",
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "call",
            "side": "short",
            "strike": 160.0,
            "expiry": "2027-07-20",
            "contracts": 2.0,
            "idempotency_key": "broker-order-xyz",
        }
        r1 = opt_svc.apply_option_change(base_change)
        assert r1.get("ok") is True
        r2 = opt_svc.apply_option_change(base_change)
        assert r2.get("ok") is True
        assert r2.get("idempotent") is True
        assert r2["contract_id"] == r1["contract_id"]

    def test_open_double_book_refused(self, seed_instruments):
        """Duplicate open signature (no key) → error with existing_contract_id."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        change = {
            "type": "open",
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "call",
            "side": "long",
            "strike": 175.0,
            "expiry": "2027-09-20",
            "contracts": 1.0,
        }
        r1 = opt_svc.apply_option_change(change)
        assert r1.get("ok") is True
        r2 = opt_svc.apply_option_change(change)
        assert "error" in r2
        assert "existing_contract_id" in r2

    def test_open_allow_duplicate_bypasses_guard(self, seed_instruments):
        """allow_duplicate=true allows a second identical open."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        change = {
            "type": "open",
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "put",
            "side": "short",
            "strike": 140.0,
            "expiry": "2027-08-20",
            "contracts": 3.0,
        }
        r1 = opt_svc.apply_option_change(change)
        r2 = opt_svc.apply_option_change({**change, "allow_duplicate": True})
        assert r2.get("ok") is True
        assert r2["contract_id"] != r1["contract_id"]

    def test_open_missing_required_field_error(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        result = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                # missing: strike, expiry, contracts
            }
        )
        assert "error" in result

    def test_open_zero_contracts_error(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        result = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 100.0,
                "expiry": "2027-06-20",
                "contracts": 0.0,
            }
        )
        assert "error" in result

    def test_close_sets_status(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": 200.0,
                "expiry": "2027-10-20",
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        cr = opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        assert cr.get("ok") is True
        from backend.db.option_contract_store import OptionContractStore

        c = OptionContractStore().find_by_id(cid)
        assert c["status"] == "closed"

    def test_close_idempotent(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "put",
                "side": "long",
                "strike": 180.0,
                "expiry": "2027-11-20",
                "contracts": 2.0,
            }
        )
        cid = r["contract_id"]
        opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        r2 = opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        assert r2.get("idempotent") is True

    def test_update_metadata(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": 210.0,
                "expiry": "2027-12-20",
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        opt_svc.apply_option_change(
            {
                "type": "update",
                "contract_id": cid,
                "strategy_tag": "covered_call",
                "note": "test note",
            }
        )
        from backend.db.option_contract_store import OptionContractStore

        c = OptionContractStore().find_by_id(cid)
        assert c["strategy_tag"] == "covered_call"
        assert c["note"] == "test note"

    def test_update_collateral_account_is_checked_like_open(self, seed_instruments):
        # 2026-10-13 audit: open checks collateral_account_id, the service update stored it
        # unchecked — and the covered-call test keys on it.
        from backend.db.option_contract_store import OptionContractStore

        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        cid = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "put",
                "side": "short",
                "strike": 140.0,
                "expiry": "2027-12-20",
                "contracts": 1.0,
            }
        )["contract_id"]

        bad = opt_svc.apply_option_change(
            {"type": "update", "contract_id": cid, "collateral_account_id": "acc_gone"}
        )
        assert "Unknown account 'acc_gone'" in bad["error"]
        assert OptionContractStore().find_by_id(cid).get("collateral_account_id") is None

        opt_svc.apply_option_change(
            {"type": "update", "contract_id": cid, "collateral_account_id": "Vantage Brokerage"}
        )
        assert OptionContractStore().find_by_id(cid)["collateral_account_id"] == acct["id"]

    def test_delete_open_contract(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 130.0,
                "expiry": "2027-06-20",
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        dr = opt_svc.apply_option_change({"type": "delete", "contract_id": cid})
        assert dr.get("ok") is True
        assert OptionContractStore().find_by_id(cid) is None

    def test_delete_non_open_refused_without_force(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": 220.0,
                "expiry": "2027-06-20",
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        dr = opt_svc.apply_option_change({"type": "delete", "contract_id": cid})
        assert "error" in dr

    def test_delete_non_open_allowed_with_force(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "put",
                "side": "long",
                "strike": 90.0,
                "expiry": "2027-06-20",
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        opt_svc.apply_option_change({"type": "close", "contract_id": cid})
        dr = opt_svc.apply_option_change({"type": "delete", "contract_id": cid, "force": True})
        assert dr.get("ok") is True


# ── Expiry sweep ──────────────────────────────────────────────────────────────


class TestExpirySweep:
    def test_sweep_flips_past_expiry_open_contracts(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 300.0,
                "expiry": yesterday,
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        result = opt_svc.run_expiry_sweep()
        assert result["expired"] >= 1
        from backend.db.option_contract_store import OptionContractStore

        c = OptionContractStore().find_by_id(cid)
        assert c["status"] == "expired"

    def test_sweep_does_not_flip_future_contracts(self, seed_instruments):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        future = (date.today() + timedelta(days=30)).isoformat()
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "put",
                "side": "short",
                "strike": 140.0,
                "expiry": future,
                "contracts": 2.0,
            }
        )
        cid = r["contract_id"]
        opt_svc.run_expiry_sweep()
        from backend.db.option_contract_store import OptionContractStore

        c = OptionContractStore().find_by_id(cid)
        assert c["status"] == "open"

    def test_sweep_flags_itm_at_expiry(self, seed_instruments, monkeypatch):
        """An expired ITM contract appears in itm_flagged."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        r = opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 100.0,
                "expiry": yesterday,
                "contracts": 1.0,
            }
        )
        cid = r["contract_id"]
        # Underlying price 150 > strike 100 → call is ITM at expiry
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        result = opt_svc.run_expiry_sweep()
        assert cid in result["itm_flagged"]

    def test_sweep_empty_book(self, isolated_stores):
        """Empty book → expired=0."""
        result = opt_svc.run_expiry_sweep()
        assert result["expired"] == 0
        assert result["itm_flagged"] == []


# ── Summary ───────────────────────────────────────────────────────────────────


class TestSummary:
    def test_empty_book_returns_zeros(self, isolated_stores, monkeypatch):
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: None)
        s = opt_svc.summary()
        assert s["total_mark_base"] == 0.0
        assert s["open_count"] == 0
        assert s["holdings"] == []
        assert s["uncovered_call_count"] == 0

    def test_by_strategy_split(self, seed_instruments, monkeypatch):
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        future = (date.today() + timedelta(days=60)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": 180.0,
                "expiry": future,
                "contracts": 1.0,
                "strategy_tag": "covered_call",
            }
        )
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "put",
                "side": "short",
                "strike": 120.0,
                "expiry": future,
                "contracts": 1.0,
                "strategy_tag": "csp",
            }
        )
        # Both OTM at underlying=150 → marks are 0
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        s = opt_svc.summary()
        assert "covered_call" in s["by_strategy"]
        assert "csp" in s["by_strategy"]

    def test_expiry_bucket_membership(self, seed_instruments, monkeypatch):
        """A contract expiring in ≤7 days appears in d7 and d30 and d90 buckets."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        in_3_days = (date.today() + timedelta(days=3)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 140.0,
                "expiry": in_3_days,
                "contracts": 1.0,
            }
        )
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        s = opt_svc.summary()
        # d7 should include this contract's mark (≥0 for OTM or ITM)
        assert s["expiry_buckets"]["d7"] >= 0.0

    def test_encumbered_cash_for_csp(self, seed_instruments, monkeypatch):
        """CSP total_encumbered_cash_base = strike × contracts × multiplier."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        future = (date.today() + timedelta(days=30)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "put",
                "side": "short",
                "strike": 100.0,
                "expiry": future,
                "contracts": 1.0,
                "multiplier": 100,
                "strategy_tag": "csp",
                "premium_currency": "USD",
            }
        )
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 110.0)
        s = opt_svc.summary()
        assert s["total_encumbered_cash_base"] == pytest.approx(10000.0)

    def test_short_itm_call_drives_nw_negative(self, seed_instruments, monkeypatch):
        """Regression: a short ITM call makes total_mark_base negative."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        future = (date.today() + timedelta(days=30)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": 100.0,
                "expiry": future,
                "contracts": 1.0,
                "multiplier": 100,
            }
        )
        # Underlying at 120: intrinsic = 20, mark = −20×1×100 = −2000
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 120.0)
        s = opt_svc.summary()
        assert s["total_mark_base"] == pytest.approx(-2000.0)

    def test_expired_otm_contributes_zero(self, seed_instruments):
        """Expired OTM contract → 0 contribution to total_mark_base."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "long",
                "strike": 200.0,
                "expiry": yesterday,
                "contracts": 1.0,
            }
        )
        # Sweep to expire it
        opt_svc.run_expiry_sweep()
        s = opt_svc.summary()
        assert s["total_mark_base"] == 0.0
        assert s["open_count"] == 0

    def test_uncovered_call_flagged(self, seed_instruments, monkeypatch):
        """Short call with no collateral_account_id → coverage='unknown', uncovered_call_count >= 1."""
        acct = seed_instruments["account"]
        inst = seed_instruments["instrument"]
        future = (date.today() + timedelta(days=30)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": 180.0,
                "expiry": future,
                "contracts": 1.0,
                # No collateral_account_id → will not be "covered"
            }
        )
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        # Patch positions so coverage check sees no shares
        monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])
        s = opt_svc.summary()
        coverages = [h.get("coverage") for h in s["holdings"]]
        assert "unknown" in coverages


# ── Expiry sweep ──────────────────────────────────────────────────────────────
#
# Two tests pinned `option_expiry_sweep` as a TaskAction enum member and a member of
# scheduling.VALID_ACTIONS. Both went with Scheduled Tasks (2026-08-02): there is no action
# enum any more, because an Automation runs Lucius with instructions rather than dispatching
# a named action. The sweep itself is unaffected — it is reached by an Automation whose
# instructions say to run it, and its own behaviour is covered by the tests above.


# ── Endpoint smoke ────────────────────────────────────────────────────────────
@pytest.fixture
def options_client(seed_instruments, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.options import router

    monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
    monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), seed_instruments


def test_get_options_endpoint_empty_book(options_client):
    """Empty book returns OptionsBook envelope with zeros — never crashes."""
    client, _ = options_client
    resp = client.get("/api/options")
    assert resp.status_code == 200
    body = resp.json()
    assert "base_currency" in body
    assert "summary" in body
    assert "positions" in body
    assert isinstance(body["positions"], list)
    s = body["summary"]
    assert s["open_positions"] == 0
    assert s["premium_collected"] == 0
    assert s["capital_committed"] == 0
    assert s["nearest_expiry_date"] is None
    assert s["nearest_expiry_dte"] is None
    assert s["nearest_expiry_underlying"] is None


def test_get_options_endpoint_position_fields(options_client):
    """A booked contract appears in positions with all OptionContract field names."""
    client, seeds = options_client
    acct = seeds["account"]
    inst = seeds["instrument"]
    future = (date.today() + timedelta(days=60)).isoformat()
    r = client.post(
        "/api/options",
        json={
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "call",
            "side": "short",
            "strike": 160.0,
            "expiry": future,
            "contracts": 1.0,
            "strategy_tag": "covered_call",
        },
    )
    assert r.status_code == 200

    resp = client.get("/api/options")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    # All required OptionContract fields must be present
    for field in (
        "id",
        "option_type",
        "side",
        "underlying_symbol",
        "strategy_tag",
        "strategy_group",
        "contracts",
        "strike",
        "expiry",
        "dte",
        "premium",
        "mark",
        "status",
        "collateral",
        "note",
    ):
        assert field in pos, f"missing field: {field}"
    assert pos["option_type"] == "CALL"
    assert pos["side"] == "short"
    assert pos["underlying_symbol"] == "AAPL"
    assert pos["strategy_group"] == "income"
    assert pos["status"] in ("ITM", "OTM")
    s = body["summary"]
    assert s["open_positions"] == 1


def test_post_options_creates_contract(options_client):
    client, seeds = options_client
    acct = seeds["account"]
    inst = seeds["instrument"]
    future = (date.today() + timedelta(days=60)).isoformat()
    payload = {
        "account_id": acct["id"],
        "underlying_instrument_id": inst["id"],
        "option_type": "call",
        "side": "long",
        "strike": 155.0,
        "expiry": future,
        "contracts": 1.0,
    }
    resp = client.post("/api/options", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("contract_id")


def test_get_option_by_id_smoke(options_client):
    client, seeds = options_client
    acct = seeds["account"]
    inst = seeds["instrument"]
    future = (date.today() + timedelta(days=60)).isoformat()
    r = client.post(
        "/api/options",
        json={
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "put",
            "side": "short",
            "strike": 145.0,
            "expiry": future,
            "contracts": 2.0,
        },
    )
    cid = r.json()["contract_id"]
    resp = client.get(f"/api/options/{cid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == cid
    assert "moneyness" in body
    assert "mark_value_base" in body
    assert "days_to_expiry" in body


def test_get_option_not_found(options_client):
    client, _ = options_client
    resp = client.get("/api/options/opt_nonexistent")
    assert resp.status_code == 404


def test_patch_option_close(options_client):
    client, seeds = options_client
    acct = seeds["account"]
    inst = seeds["instrument"]
    future = (date.today() + timedelta(days=60)).isoformat()
    r = client.post(
        "/api/options",
        json={
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "call",
            "side": "short",
            "strike": 170.0,
            "expiry": future,
            "contracts": 1.0,
        },
    )
    cid = r.json()["contract_id"]
    resp = client.patch(f"/api/options/{cid}", json={"type": "close"})
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_delete_option_endpoint(options_client):
    client, seeds = options_client
    acct = seeds["account"]
    inst = seeds["instrument"]
    future = (date.today() + timedelta(days=60)).isoformat()
    r = client.post(
        "/api/options",
        json={
            "account_id": acct["id"],
            "underlying_instrument_id": inst["id"],
            "option_type": "put",
            "side": "long",
            "strike": 135.0,
            "expiry": future,
            "contracts": 1.0,
        },
    )
    cid = r.json()["contract_id"]
    resp = client.delete(f"/api/options/{cid}")
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_post_options_missing_field_400(options_client):
    client, seeds = options_client
    # Missing 'side' and other required fields
    resp = client.post(
        "/api/options",
        json={
            "account_id": seeds["account"]["id"],
            "underlying_instrument_id": seeds["instrument"]["id"],
            "option_type": "call",
            # no side, strike, expiry, contracts
        },
    )
    assert resp.status_code == 400


# ── AssetClass.option enumeration ────────────────────────────────────────────
def test_asset_class_has_option():
    from backend.models.instrument import AssetClass

    assert AssetClass.option == "option"
    assert "option" in {a.value for a in AssetClass}


def test_asset_classes_set_has_option():
    from backend.db.instrument_store import ASSET_CLASSES

    assert "option" in ASSET_CLASSES


# ── Portable manifest ─────────────────────────────────────────────────────────
def test_option_contracts_in_portable_files():
    from backend.core.portable import PORTABLE_FILES

    names = {p.name for p in PORTABLE_FILES}
    assert "option_contracts.json" in names


def test_post_duplicate_open_returns_409(options_client):
    """The double-book refusal surfaces as HTTP 409 (not a generic 400) so the client
    can offer 'Record anyway' structurally instead of string-matching prose —
    the api/lots.py acquire precedent (options-e2e review fix)."""
    client, seeds = options_client
    payload = {
        "account_id": seeds["account"]["id"],
        "underlying_instrument_id": seeds["instrument"]["id"],
        "option_type": "call",
        "side": "short",
        "strike": 160.0,
        "expiry": (date.today() + timedelta(days=60)).isoformat(),
        "contracts": 1.0,
    }
    assert client.post("/api/options", json=payload).status_code == 200
    dup = client.post("/api/options", json=payload)
    assert dup.status_code == 409
    ok = client.post("/api/options", json={**payload, "allow_duplicate": True})
    assert ok.status_code == 200
    bad = client.post("/api/options", json={**payload, "strike": 0})
    assert bad.status_code == 400


class TestModelStoreAlignment:
    """The Pydantic OptionContract model must validate what the store factory actually
    persists — the model drifted once (premium_per_contract vs the stored
    premium_per_share; missing quote_currency/close_premium_per_share) and nothing
    caught it because the model isn't wired as a validator."""

    def test_model_validates_factory_output(self):
        from backend.models.option import OptionContract

        row = _make_option_contract(
            {
                "account_id": "acct1",
                "underlying_instrument_id": "inst1",
                "option_type": "put",
                "side": "short",
                "strike": 100.0,
                "expiry": "2027-01-15",
                "contracts": 2.0,
                "premium_per_share": 2.5,
                "premium_currency": "SGD",
                "quote_currency": "USD",
                "close_premium_per_share": 1.1,
            }
        )
        model = OptionContract(**row)
        assert model.premium_per_share == 2.5
        assert model.quote_currency == "USD"
        assert model.close_premium_per_share == 1.1


class TestCollateralCurrency:
    """CSP collateral is strike-denominated — the underlying's quote_currency —
    NOT the premium leg's currency. Regression for cross-currency contracts
    (premium paid in SGD on a USD-quoted underlying) where encumbered cash and
    capital-committed converted the strike at the premium rate."""

    def _fx_to_sgd(self, monkeypatch):
        from backend.core import fx as fx_mod

        rate = {"USD": 1.35, "SGD": 1.0}
        monkeypatch.setattr(fx_mod, "base_currency", lambda: "SGD")
        monkeypatch.setattr(
            fx_mod,
            "to_base",
            lambda a, c, b=None, rates=None: round(
                float(a or 0) * rate.get((c or "USD").upper(), 1.0), 2
            ),
        )

    def _open_cross_currency_csp(self, seeds):
        future = (date.today() + timedelta(days=60)).isoformat()
        opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": seeds["account"]["id"],
                "underlying_instrument_id": seeds["instrument"]["id"],
                "option_type": "put",
                "side": "short",
                "strike": 100.0,
                "expiry": future,
                "contracts": 1.0,
                "strategy_tag": "csp",
                "premium_currency": "SGD",
                "quote_currency": "USD",
            }
        )

    def test_summary_encumbered_cash_uses_quote_currency(self, seed_instruments, monkeypatch):
        self._fx_to_sgd(monkeypatch)
        self._open_cross_currency_csp(seed_instruments)
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        s = opt_svc.summary()
        # 100 strike × 1 contract × 100 multiplier = 10,000 USD → 13,500 SGD
        # (the old premium_currency conversion would have said 10,000).
        assert s["total_encumbered_cash_base"] == 13500.0

    def test_book_collateral_uses_quote_currency(self, seed_instruments, monkeypatch):
        self._fx_to_sgd(monkeypatch)
        self._open_cross_currency_csp(seed_instruments)
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        b = opt_svc.book()
        assert b["positions"][0]["collateral"] == 13500.0


class TestResolveUnderlyingPrice:
    """Regression: marks must resolve from cached price history for NON-manual
    underlyings. A nonexistent PriceHistoryStore.get_series call used to raise
    AttributeError inside a blanket except → None for every yahoo/coingecko
    underlying, so only manual-priced instruments ever marked."""

    @pytest.fixture
    def yahoo_underlying(self, isolated_stores, monkeypatch, tmp_path, db_isolated):
        """PriceHistoryStore resolves against the per-test isolated database (db_isolated)."""
        from backend.db.instrument_store import InstrumentStore

        return InstrumentStore().create(
            {
                "symbol": "NVDA",
                "name": "NVIDIA Corporation",
                "asset_class": "equity",
                "quote_currency": "USD",
                "price_source": "yahoo",
                "price_source_id": "NVDA",
            }
        )

    def test_yahoo_underlying_resolves_from_price_history(self, yahoo_underlying):
        from backend.db.price_history_store import PriceHistoryStore

        PriceHistoryStore().record("NVDA", 187.5, source="live")
        price = opt_svc._resolve_underlying_price(
            {"id": "c1", "underlying_instrument_id": yahoo_underlying["id"]}
        )
        assert price == 187.5

    def test_yahoo_underlying_without_history_is_none(self, yahoo_underlying):
        price = opt_svc._resolve_underlying_price(
            {"id": "c1", "underlying_instrument_id": yahoo_underlying["id"]}
        )
        assert price is None

    def test_manual_price_still_wins(self, isolated_stores):
        from backend.db.instrument_store import InstrumentStore

        inst = InstrumentStore().create(
            {
                "symbol": "PRIV",
                "name": "Private Co",
                "asset_class": "equity",
                "quote_currency": "USD",
                "price_source": "manual",
                "manual_price": 42.0,
            }
        )
        price = opt_svc._resolve_underlying_price(
            {"id": "c1", "underlying_instrument_id": inst["id"]}
        )
        assert price == 42.0


class TestSummaryIsOpenOnly:
    """Regression: `summary` promised open-only but shipped every dead contract.

    `holdings.append` and the uncovered tally sat OUTSIDE the `if status == "open"` guard
    that protected the mark/bucket/collateral folds, so a book accumulated closed and
    expired contracts in the lens forever — wrong per the docstring, and a steadily
    growing tool result.
    """

    def _open_one(self, acct, inst, strike, expiry):
        return opt_svc.apply_option_change(
            {
                "type": "open",
                "account_id": acct["id"],
                "underlying_instrument_id": inst["id"],
                "option_type": "call",
                "side": "short",
                "strike": strike,
                "expiry": expiry,
                "contracts": 1.0,
                "strategy_tag": "covered_call",
            }
        )

    def test_closed_contract_is_excluded_from_holdings(self, seed_instruments, monkeypatch):
        acct, inst = seed_instruments["account"], seed_instruments["instrument"]
        future = (date.today() + timedelta(days=45)).isoformat()
        kept = self._open_one(acct, inst, 180.0, future)
        closed = self._open_one(acct, inst, 190.0, future)
        opt_svc.apply_option_change(
            {"type": "close", "contract_id": closed.get("id") or closed.get("contract_id")}
        )
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])

        s = opt_svc.summary()
        ids = {h.get("id") for h in s["holdings"]}
        kept_id = kept.get("id") or kept.get("contract_id")
        closed_id = closed.get("id") or closed.get("contract_id")
        assert kept_id in ids
        assert closed_id not in ids, "closed contract leaked into the open-only lens"
        assert s["open_count"] == 1
        assert len(s["holdings"]) == s["open_count"], "holdings must match open_count"

    def test_closed_contract_not_counted_as_uncovered(self, seed_instruments, monkeypatch):
        """A dead contract has no coverage question — it must not inflate the risk tally."""
        acct, inst = seed_instruments["account"], seed_instruments["instrument"]
        future = (date.today() + timedelta(days=45)).isoformat()
        closed = self._open_one(acct, inst, 190.0, future)
        opt_svc.apply_option_change(
            {"type": "close", "contract_id": closed.get("id") or closed.get("contract_id")}
        )
        monkeypatch.setattr(opt_svc, "_resolve_underlying_price", lambda c: 150.0)
        monkeypatch.setattr("backend.services.positions.hydrated", lambda: [])

        s = opt_svc.summary()
        assert s["holdings"] == []
        assert s["uncovered_call_count"] == 0
        assert s["open_count"] == 0
