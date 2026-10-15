"""The owned-asset value rule (RFC assets-create-edit-redesign §2.1), on pure dicts — no DB.

Stored marks are valuations only. The purchase point is DERIVED from (category, cost,
cost_date); it joins the series only when it is dated on or before the newest valuation, and
a valuation outranks it on the same day. So today's value is always the newest valuation.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from backend.services import owned_assets_valuation as valuation


def _v(day: str, value: float, *, created_at: str | None = None, mark_id: str = "") -> dict:
    return {
        "id": mark_id or f"mk_{day.replace('-', '')}",
        "date": day,
        "value": value,
        "note": "",
        "by": "you",
        "created_at": created_at or f"{day}T09:00:00+00:00",
        "edited_at": None,
    }


def _asset(marks: list[dict], **kw) -> dict:
    asset = {
        "id": "oa_test",
        "name": "Test asset",
        "category": "property",
        "cost": None,
        "cost_date": None,
        "share_pct": 100.0,
        "currency": "SGD",
        "archived": False,
        "created_at": "2019-03-15T08:00:00+00:00",
        "marks": sorted(marks, key=lambda m: (m["date"], m["created_at"], m["id"]), reverse=True),
    }
    asset.update(kw)
    return asset


def _kinds(asset: dict) -> list[tuple[str, str]]:
    return [(p["kind"], p["date"]) for p in valuation.series(asset)]


def test_newest_valuation_is_current_even_when_purchase_is_newer():
    asset = _asset([_v("2024-01-10", 500.0)], cost=400.0, cost_date="2025-06-01")
    assert valuation.value_as_of(asset, None) == 500.0
    assert valuation.current_mark(asset)["kind"] == "valuation"
    assert _kinds(asset) == [("valuation", "2024-01-10")]  # the later purchase is left out


def test_valuation_outranks_purchase_on_the_same_day():
    asset = _asset([_v("2026-09-26", 1_150_000.0)], cost=900_000.0, cost_date="2026-09-26")
    assert valuation.value_as_of(asset, None) == 1_150_000.0
    assert valuation.value_as_of(asset, "2026-09-26") == 1_150_000.0
    assert _kinds(asset) == [("valuation", "2026-09-26"), ("purchase", "2026-09-26")]


def test_purchase_point_fills_history_before_first_valuation():
    asset = _asset([_v("2026-09-26", 1_150_000.0)], cost=900_000.0, cost_date="2019-03-15")
    assert valuation.value_as_of(asset, "2019-03-14") is None
    assert valuation.value_as_of(asset, "2019-03-15") == 900_000.0
    assert valuation.value_as_of(asset, "2024-01-01") == 900_000.0
    assert valuation.value_as_of(asset, None) == 1_150_000.0
    assert valuation.series(asset)[-1] == {
        "id": "purchase",
        "kind": "purchase",
        "date": "2019-03-15",
        "value": 900_000.0,
        "note": "",
        "by": "you",
        "created_at": "2019-03-15T08:00:00+00:00",
        "edited_at": None,
    }


def test_purchase_between_valuations_keeps_history_semantics():
    """Legacy row: a value from 2018, a purchase in 2020, a value in 2026 — 2020..2026 reads
    the cost, exactly as the stored purchase mark read before the purchase became derived."""
    asset = _asset(
        [_v("2018-06-01", 300.0), _v("2026-01-01", 600.0)], cost=400.0, cost_date="2020-01-01"
    )
    assert valuation.value_as_of(asset, "2019-01-01") == 300.0
    assert valuation.value_as_of(asset, "2021-01-01") == 400.0
    assert valuation.value_as_of(asset, None) == 600.0
    assert _kinds(asset) == [
        ("valuation", "2026-01-01"),
        ("purchase", "2020-01-01"),
        ("valuation", "2018-06-01"),
    ]


def test_policy_has_no_purchase_point():
    asset = _asset(
        [_v("2026-09-26", 42_000.0)], category="policy", cost=18_000.0, cost_date="2019-06-01"
    )
    assert "policy" in valuation.PURCHASELESS_CATEGORIES
    assert valuation.purchase_point(asset) is None
    assert _kinds(asset) == [("valuation", "2026-09-26")]
    assert valuation.value_as_of(asset, "2020-01-01") is None  # premiums are not a value


def test_price_without_date_has_no_purchase_point():
    asset = _asset([_v("2026-09-26", 15_000.0)], cost=10_000.0, cost_date=None)
    assert valuation.purchase_point(asset) is None
    assert _kinds(asset) == [("valuation", "2026-09-26")]


@pytest.mark.parametrize("cost", [0, 0.0, -5.0, float("nan"), float("inf"), None])
def test_non_positive_cost_has_no_purchase_point(cost):
    asset = _asset([_v("2026-09-26", 15_000.0)], cost=cost, cost_date="2020-01-01")
    assert valuation.purchase_point(asset) is None
    assert valuation.value_as_of(asset, "2021-01-01") is None


def test_markless_asset_has_no_value():
    """An imported row with a cost and a date but no valuations: the purchase point needs a
    valuation to stand before, so there is no value at all — never the cost."""
    asset = _asset([], cost=900_000.0, cost_date="2019-03-15")
    assert valuation.series(asset) == []
    assert valuation.current_mark(asset) is None
    assert valuation.value_as_of(asset, None) is None
    assert valuation.value_as_of(asset, "2025-01-01") is None


def _random_day(rng: random.Random, start: date, end: date) -> str:
    return (start + timedelta(days=rng.randint(0, (end - start).days))).isoformat()


def test_purchase_edits_never_move_todays_value():
    """Property: over random valuation histories, no cost / cost_date / category edit —
    including a purchase dated after every valuation, a zero or missing cost, or a switch to
    a policy — moves today's value away from the newest valuation."""
    rng = random.Random(20260926)
    today = date.today()
    for _ in range(400):
        marks = []
        for i in range(rng.randint(1, 6)):
            day = _random_day(rng, date(2015, 1, 1), today)
            stamp = f"{day}T{rng.randint(0, 23):02d}:00:00.{rng.randint(0, 999999):06d}+00:00"
            marks.append(
                _v(
                    day,
                    round(rng.uniform(1, 2_000_000), 2),
                    created_at=stamp,
                    mark_id=f"mk_{i:012x}",
                )
            )
        newest = max(marks, key=lambda m: (m["date"], m["created_at"], m["id"]))
        asset = _asset(marks)
        for _ in range(8):
            asset["cost"] = rng.choice([None, 0, -1.0, round(rng.uniform(1, 3_000_000), 2)])
            asset["cost_date"] = rng.choice([None, _random_day(rng, date(2010, 1, 1), today)])
            asset["category"] = rng.choice(["property", "policy", "watch", "c_vinyl_ab12"])
            assert valuation.value_as_of(asset, None) == newest["value"]
            assert valuation.value_as_of(asset, today.isoformat()) == newest["value"]
            assert valuation.current_mark(asset)["id"] == newest["id"]


def test_values_by_category_sum_equals_values_by_band():
    rates = {"SGD": 1.0, "USD": 1.35}
    rows = [
        _asset([_v("2026-01-01", 1_000_001.0)], id="oa_home", category="property", share_pct=50),
        _asset([_v("2026-02-01", 8_000.33)], id="oa_gold", category="metal"),
        _asset([_v("2026-03-01", 700.0)], id="oa_watch", category="watch", currency="USD"),
        _asset([_v("2026-03-01", 99.99)], id="oa_art", category="art", share_pct=33.3),
        _asset([_v("2026-04-01", 5_000.0)], id="oa_sold", category="watch", archived=True),
        _asset([], id="oa_imported", category="art", cost=10.0, cost_date="2020-01-01"),
    ]
    by_category = valuation.values_by_category_from_rows(rows, "SGD", rates=rates)
    by_band = valuation.values_by_band_from_rows(rows, "SGD", rates=rates)

    assert by_category == {
        "property": 500_000.5,
        "metal": 8_000.33,
        "watch": 945.0,  # 700 USD × 1.35, the sold watch excluded
        "art": 33.3,  # 99.99 × 33.3% — the markless import adds nothing
    }
    assert math.isclose(sum(by_category.values()), sum(by_band.values()), abs_tol=0.005)
    assert valuation.share_adjusted_base(rows[0], "SGD", rates=rates) == 500_000.5
    assert valuation.share_adjusted_base(rows[5], "SGD", rates=rates) is None
