"""watch_reader — Lucius's in-app read on every live watch story. The model, the price-movement
note and the asset brief are stubbed; stories live in an isolated DB. Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.monitoring import watch_analyst as wa
from backend.monitoring import watch_reader as wr
from backend.monitoring import watch_reader_prompt as rp
from backend.services import watch_signals

NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
READ = {
    "read": "A move this size is rare for AMD and brings it close to your buy zone.",
    "watch_for": ["Whether it holds above the 52-week low"],
    "source_ids": [],
}


def _ctx(now=NOW):
    hist = [
        ((date(2026, 9, 23) - timedelta(days=120 - i)).isoformat(), 100 + (i % 2) * 0.8)
        for i in range(120)
    ]
    item = {
        "id": "wli_AMD",
        "type": "ticker",
        "value": "AMD",
        "label": "AMD",
        "asset_class": "equity",
        "monitor_news": True,
        "added_by": "user",
        "watchlist_ids": ["wl"],
        "instrument_id": "ins_amd",
    }
    return {
        "now": now,
        "items": [item],
        "snaps": {
            "AMD": {
                "price": 91.0,
                "prev_close": 100.0,
                "name": "AMD",
                "exchange_tz": "America/New_York",
            }
        },
        "bench": {"symbol": "SPY", "day_pct": 0.2},
        "bench_series": [(d, 400 + (i % 3)) for i, (d, _) in enumerate(hist)],
        "alerts": {},
        "held_by_sector": {"Technology": 38.0},
        "sectors": {"AMD": "Technology"},
        "history": {"AMD": hist},
    }


@pytest.fixture
def stubs(monkeypatch):
    calls: dict = {"llm": 0, "reply": READ, "config": {}, "prompts": []}

    async def fake_complete_json(prompt, **kw):
        calls["llm"] += 1
        calls["prompts"].append(prompt)
        calls["system"] = kw.get("system")
        return calls["reply"]

    import backend.core.llm_seam as seam
    import backend.db.asset_brief_store as abs_mod
    import backend.db.price_movement_store as pms

    monkeypatch.setattr(seam, "complete_json", fake_complete_json)

    class _Notes:
        def get_note(self, iid, session):
            return None

    class _Briefs:
        def get(self, iid):
            return {
                "key_issues": [
                    {
                        "question": "Can MI400 ship on time?",
                        "bullish": {"text": "Demand of 12.5 GW"},
                    }
                ]
            }

    monkeypatch.setattr(pms, "PriceMovementStore", _Notes)
    monkeypatch.setattr(abs_mod, "AssetBriefStore", _Briefs)
    monkeypatch.setattr(wa, "_analyst", lambda symbol: None)
    monkeypatch.setattr(wa, "settings", lambda: calls["config"])
    return calls


def _run(ctx=None):
    ctx = ctx or _ctx()
    watch_signals.emit_watch_signals(ctx=ctx)
    return asyncio.run(wr.read_stories(ctx))


def _story():
    from backend.db.intel_stories_store import IntelStoriesStore

    return next(s for s in IntelStoriesStore().all() if s.get("synthetic_key") == "watch:AMD")


def test_a_live_watch_story_gets_a_read_without_a_timeline_entry_or_unread_flip(db_isolated, stubs):
    from backend.db.intel_stories_store import IntelStoriesStore
    from backend.services.intel_stories import apply_story_change

    watch_signals.emit_watch_signals(ctx=_ctx())
    apply_story_change({**_story(), "read": True})
    before = len(IntelStoriesStore().entries_for_signal(_story()["id"], limit=None))

    assert asyncio.run(wr.read_stories(_ctx())) == 1

    story = _story()
    assert story["lucius_read"]["text"] == READ["read"]
    assert story["lucius_read"]["watch_for"] == READ["watch_for"]
    assert "lean" not in story["lucius_read"]  # information level: no opinion fields
    assert story["read"] is True
    assert len(IntelStoriesStore().entries_for_signal(story["id"], limit=None)) == before
    # the model saw the earlier research
    assert "Can MI400 ship on time?" in stubs["prompts"][0]


def test_the_same_story_is_not_read_twice_but_a_new_level_or_session_is(db_isolated, stubs):
    assert _run() == 1
    assert _run() == 0 and stubs["llm"] == 1
    stubs["config"] = {"ai_opinion_level": "lean"}
    stubs["reply"] = {**READ, "lean": "bearish", "lean_reason": "It sits near the 52-week low."}
    assert _run() == 1
    assert _story()["lucius_read"]["lean"] == "bearish"
    assert _run(_ctx(NOW + timedelta(days=1))) == 1


def test_a_story_explained_this_session_is_left_to_the_explained_alert(db_isolated, stubs):
    from backend.services.intel_stories import apply_story_change

    watch_signals.emit_watch_signals(ctx=_ctx())
    apply_story_change({**_story(), "explained": {"trigger_key": "AMD:company_move:2026-09-24"}})
    assert asyncio.run(wr.read_stories(_ctx())) == 0 and stubs["llm"] == 0


@pytest.mark.parametrize("flag", ["dismissed", "resolved"])
def test_dismissed_or_resolved_stories_are_skipped(db_isolated, stubs, flag):
    from backend.services.intel_stories import apply_story_change

    watch_signals.emit_watch_signals(ctx=_ctx())
    apply_story_change({**_story(), flag: True})
    assert asyncio.run(wr.read_stories(_ctx())) == 0


@pytest.mark.parametrize("config", [{"watch_reads_enabled": False}, {"watch_reads_per_day": 0}])
def test_settings_turn_reads_off(db_isolated, stubs, config):
    stubs["config"] = config
    assert _run() == 0 and stubs["llm"] == 0


def test_the_daily_cap_counts_todays_reads(db_isolated, stubs):
    stubs["config"] = {"watch_reads_per_day": 1}
    assert _run() == 1
    stubs["config"] = {"watch_reads_per_day": 1, "ai_opinion_level": "lean"}
    stubs["reply"] = {**READ, "lean": "neutral", "lean_reason": "Mixed signals."}
    assert _run() == 0 and stubs["llm"] == 1


def test_no_llm_provider_is_a_silent_skip(db_isolated, stubs, monkeypatch):
    import backend.core.llm_seam as seam

    async def unavailable(*a, **kw):
        raise seam.LLMUnavailable("none")

    monkeypatch.setattr(seam, "complete_json", unavailable)
    assert _run() == 0


def test_a_revived_story_drops_its_read(db_isolated, stubs):
    from backend.services.intel_stories import apply_story_change

    assert _run() == 1
    apply_story_change({**_story(), "resolved": True})
    watch_signals.emit_watch_signals(ctx=_ctx())
    assert "lucius_read" not in _story()


# ── the checks, directly ─────────────────────────────────────────────────────────

FACTS = {"day_pct": -9.0, "times_usual_move": 11.2}


def test_check_read_rules():
    grounded = rp.grounded_text(None, ["Demand of 12.5 GW"])
    assert rp.check_read(READ, FACTS, 0, grounded, "information") is None
    assert "not in FACTS" in rp.check_read(
        {**READ, "read": "It could rally 47.3%."}, FACTS, 0, grounded, "information"
    )
    assert (
        rp.check_read({**READ, "read": "Demand of 12.5 GW."}, FACTS, 0, grounded, "information")
        is None
    )
    assert "Don't tell" in rp.check_read(
        {**READ, "read": "You should buy the dip."}, FACTS, 0, grounded, "information"
    )
    assert "source_ids" in rp.check_read(
        {**READ, "source_ids": [2]}, FACTS, 1, grounded, "information"
    )


def test_opinion_levels():
    lean = {**READ, "lean": "bullish", "lean_reason": "Rare moves like this have reversed."}
    assert rp.check_read(lean, FACTS, 0, "", "lean") is None
    assert "'lean'" in rp.check_read({**lean, "lean": "moon"}, FACTS, 0, "", "lean")
    assert "Don't tell" in rp.check_read(
        {**lean, "lean_reason": "You should add here."}, FACTS, 0, "", "lean"
    )
    suggest = {**lean, "suggestion": "Consider adding only inside your buy zone."}
    assert rp.check_read(suggest, FACTS, 0, "", "suggest") is None
    assert "urge" in rp.check_read({**suggest, "suggestion": "Buy now."}, FACTS, 0, "", "suggest")
    assert "not in FACTS" in rp.check_read(
        {**suggest, "suggestion": "Consider adding below 71.4."}, FACTS, 0, "", "suggest"
    )
