"""intel scan parity for watched (not held) tickers — helpers + prompt context."""

from __future__ import annotations

from backend.monitoring import intel_agent as ia


def _t(value, **kw) -> dict:
    return {
        "type": "ticker",
        "value": value,
        "label": value.title(),
        "monitor_news": True,
        "added_by": "user",
        "asset_class": "equity",
        **kw,
    }


def test_watched_items_excludes_auto_synced_muted_held_and_topics():
    wl = {
        "items": [
            _t("NVDA"),
            _t("AMD", added_by="auto"),
            _t("TSLA", monitor_news=False),
            _t("MSFT"),
            {"type": "topic", "value": "AI", "monitor_news": True},
        ]
    }
    assert [i["value"] for i in ia._watched_items(wl, {"MSFT"})] == ["NVDA"]


def test_watch_item_row_shape_feeds_holding_resolver():
    row = ia._watch_item_as_row(_t("NVDA", instrument_id="ins_1"))
    assert row == {
        "instrument_id": "ins_1",
        "ticker": "NVDA",
        "label": "Nvda",
        "asset_class": "equity",
    }
    from backend.services.feeds import resolve_holding_feeds

    assert resolve_holding_feeds(row), "the RSS resolver must produce feeds for a watched ticker"


def test_rotate_windows_and_advances_the_cursor():
    q = ["a", "b", "c", "d", "e"]
    assert ia._rotate(q, 5, 3) == (q, 0)
    window, nxt = ia._rotate(q, 2, 0)
    assert window[:2] == ["a", "b"] and nxt == 2
    window, nxt = ia._rotate(q, 2, nxt)
    assert window[:2] == ["c", "d"] and nxt == 4
    window, nxt = ia._rotate(q, 2, nxt)
    assert window[:2] == ["e", "a"] and nxt == 1


def test_context_lists_watched_tickers_separately_from_holdings():
    rows = [{"label": "Held Co", "ticker": "HELD", "quantity": 1, "price": 10, "currency": "SGD"}]
    wl = {"items": [_t("NVDA", note="AI capex leader"), _t("HELD")]}
    ctx = ia._build_context(rows, {}, {}, wl, "SGD")
    watched = ctx.split("WATCHED TICKERS")[1].split("FOLLOWED TOPICS")[0]
    assert "Nvda (NVDA) — AI capex leader" in watched and "HELD" not in watched
    assert "PORTFOLIO" in ctx and "Held Co" in ctx.split("WATCHED TICKERS")[0]
