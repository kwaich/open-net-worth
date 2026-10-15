"""Regression tests for the two FIRE fields added to update_profile's _num_fields:
  - fire_volatility_pct
  - fire_life_expectancy

Verifies both are float-coerced and persisted correctly; uses the same
isolated_profile fixture pattern as tests/test_tools_profile.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool

# ── helpers ───────────────────────────────────────────────────────────────────


def call(tool: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── path isolation fixture ────────────────────────────────────────────────────


@pytest.fixture()
def isolated_profile(tmp_path, monkeypatch):
    import backend.core.paths as core_paths
    from backend.db.profile_store import ProfileStore

    fx_path = tmp_path / "fx_cache.json"

    # profile is DB-backed now (Wave 5, JSON->DB) — no more PROFILE_FILE/PROFILE_JSON
    # module attrs on services.profile/services.budgets/core.paths to monkeypatch.
    ProfileStore().set_raw({})
    monkeypatch.setattr(core_paths, "FX_CACHE_JSON", fx_path)
    monkeypatch.setattr("backend.paths.FX_CACHE_JSON", fx_path)

    yield


# ══════════════════════════════════════════════════════════════════════════════
# fire_volatility_pct
# ══════════════════════════════════════════════════════════════════════════════


class TestFireVolatilityPct:
    def test_persists_float_value(self, isolated_profile):
        call("update_profile", changes={"fire_volatility_pct": 15.0})
        body, _ = call("get_profile")
        assert body["fire_volatility_pct"] == 15.0
        assert isinstance(body["fire_volatility_pct"], float)

    def test_coerces_string_to_float(self, isolated_profile):
        """The AI may send a numeric string; it must be coerced to float."""
        call("update_profile", changes={"fire_volatility_pct": "12.5"})
        body, _ = call("get_profile")
        assert body["fire_volatility_pct"] == 12.5
        assert isinstance(body["fire_volatility_pct"], float)

    def test_coerces_integer_string(self, isolated_profile):
        call("update_profile", changes={"fire_volatility_pct": "20"})
        body, _ = call("get_profile")
        assert body["fire_volatility_pct"] == 20.0
        assert isinstance(body["fire_volatility_pct"], float)

    def test_returns_ok_true(self, isolated_profile):
        body, _ = call("update_profile", changes={"fire_volatility_pct": 18.0})
        assert body["ok"] is True

    def test_proposed_is_none(self, isolated_profile):
        _, proposed = call("update_profile", changes={"fire_volatility_pct": 10.0})
        assert proposed is None

    def test_invalid_string_is_rejected_not_crashed(self, isolated_profile):
        """A non-coercible string for fire_volatility_pct is rejected at the schema boundary,
        not silently accepted or left to crash the handler."""
        body, _ = call("update_profile", changes={"fire_volatility_pct": "not-a-number"})
        assert "error" in body
        assert "fire_volatility_pct" in body["error"]


# ══════════════════════════════════════════════════════════════════════════════
# fire_life_expectancy
# ══════════════════════════════════════════════════════════════════════════════


class TestFireLifeExpectancy:
    def test_persists_float_value(self, isolated_profile):
        call("update_profile", changes={"fire_life_expectancy": 90.0})
        body, _ = call("get_profile")
        assert body["fire_life_expectancy"] == 90.0

    def test_coerces_string_to_float(self, isolated_profile):
        """The AI may send a numeric string; it must be coerced to a number."""
        call("update_profile", changes={"fire_life_expectancy": "85"})
        body, _ = call("get_profile")
        assert body["fire_life_expectancy"] == 85.0

    def test_coerces_integer_input(self, isolated_profile):
        call("update_profile", changes={"fire_life_expectancy": 95})
        body, _ = call("get_profile")
        assert body["fire_life_expectancy"] == 95.0

    def test_returns_ok_true(self, isolated_profile):
        body, _ = call("update_profile", changes={"fire_life_expectancy": 90.0})
        assert body["ok"] is True

    def test_proposed_is_none(self, isolated_profile):
        _, proposed = call("update_profile", changes={"fire_life_expectancy": 90.0})
        assert proposed is None

    def test_invalid_string_is_rejected_not_crashed(self, isolated_profile):
        """A non-coercible string for fire_life_expectancy is rejected at the schema boundary,
        not silently accepted or left to crash the handler."""
        body, _ = call("update_profile", changes={"fire_life_expectancy": "ninety"})
        assert "error" in body
        assert "fire_life_expectancy" in body["error"]


# ══════════════════════════════════════════════════════════════════════════════
# both fields together
# ══════════════════════════════════════════════════════════════════════════════


class TestBothFireFieldsTogether:
    def test_both_persist_in_one_call(self, isolated_profile):
        """Setting both fields in a single call persists both correctly."""
        call(
            "update_profile",
            changes={"fire_volatility_pct": "14.0", "fire_life_expectancy": "88"},
        )
        body, _ = call("get_profile")
        assert body["fire_volatility_pct"] == 14.0
        assert body["fire_life_expectancy"] == 88.0

    def test_both_are_floats_after_coercion(self, isolated_profile):
        call(
            "update_profile",
            changes={"fire_volatility_pct": "16", "fire_life_expectancy": "92"},
        )
        body, _ = call("get_profile")
        assert isinstance(body["fire_volatility_pct"], float)
        assert isinstance(body["fire_life_expectancy"], float)

    def test_alongside_existing_fire_fields(self, isolated_profile):
        """New fields coexist with the original five FIRE fields without interference."""
        call(
            "update_profile",
            changes={
                "fire_expected_return_pct": "7",
                "fire_withdrawal_rate_pct": "4",
                "fire_monthly_retirement_spending": "5000",
                "fire_volatility_pct": "15",
                "fire_life_expectancy": "90",
            },
        )
        body, _ = call("get_profile")
        assert body["fire_expected_return_pct"] == 7.0
        assert body["fire_withdrawal_rate_pct"] == 4.0
        assert body["fire_monthly_retirement_spending"] == 5000.0
        assert body["fire_volatility_pct"] == 15.0
        assert body["fire_life_expectancy"] == 90.0


def test_update_profile_persists_monthly_retirement_spending_as_float(isolated_profile):
    call("update_profile", changes={"fire_monthly_retirement_spending": "3500"})

    body, _ = call("get_profile")
    assert body["fire_monthly_retirement_spending"] == 3500.0


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", 1e308])
def test_update_profile_rejects_invalid_monthly_retirement_spending_without_writing(
    isolated_profile, value
):
    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        call("update_profile", changes={"fire_monthly_retirement_spending": value})
    body, _ = call("get_profile")
    assert body["fire_monthly_retirement_spending"] is None


def test_fire_projection_accepts_monthly_retirement_spending_override(
    isolated_profile, monkeypatch
):
    from backend.services import fire as fire_svc

    captured = {}

    def fake_compute(**kwargs):
        captured.update(kwargs)
        return {"fire_number": 1_000_000}

    monkeypatch.setattr(fire_svc, "compute_fire", fake_compute)
    call(
        "get_fire_projection",
        current_nw_base=100_000,
        monthly_retirement_spending_override=2_500,
    )

    assert captured["monthly_retirement_spending"] == 2_500
