"""`prices.last_session_post_print`: Friday's after-hours print survives the weekend."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.prices import last_session_post_print

_FRI_CLOSE = int(datetime(2026, 9, 18, 20, 4, 48, tzinfo=timezone.utc).timestamp())  # 16:04 ET
_FRI_POST = int(datetime(2026, 9, 18, 23, 59, 55, tzinfo=timezone.utc).timestamp())  # 19:59 ET


def _q(**over):
    base = {
        "marketState": "CLOSED",
        "regularMarketPrice": 147.61,
        "regularMarketTime": _FRI_CLOSE,
        "postMarketPrice": 147.55,
        "postMarketChangePercent": -0.040646,
        "postMarketTime": _FRI_POST,
    }
    base.update(over)
    return base


def test_closed_market_quote_still_yields_fridays_post_print_stamped_with_its_session():
    post = last_session_post_print(_q(), "America/New_York")
    assert post["session_date"] == "2026-09-18"
    assert post["price"] == 147.55 and post["session"] == "post"
    assert post["change_pct"] == -0.0406


def test_post_print_from_another_day_or_a_phantom_copy_is_dropped():
    monday = int(datetime(2026, 9, 21, 20, 1, tzinfo=timezone.utc).timestamp())
    assert last_session_post_print(_q(regularMarketTime=monday), "America/New_York") is None
    assert last_session_post_print(_q(postMarketPrice=147.61), "America/New_York") is None
    assert last_session_post_print(_q(postMarketPrice=None), "America/New_York") is None
    assert last_session_post_print(_q(postMarketTime=None), "America/New_York") is None
