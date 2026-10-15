"""watch_analyst (Tier 3) — the explained watch alert. The model, the price-movement note and the
push are stubbed; stories live in an isolated DB. Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.monitoring import watch_analyst as wa
from backend.monitoring import watch_analyst_prompt as wp
from backend.services import watch_signals

NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
SOURCES = [{"title": "AMD delays MI400", "url": "https://example.com/a", "domain": "example.com"}]
GOOD = {
    "what_happened": "AMD fell 9.0% today, about 11.2× its usual daily move.",
    "why": "Reports say the MI400 accelerator slipped a quarter.",
    "source_ids": [0],
    "what_it_means": "That puts it inside your buy zone, with your thesis resting on AI accelerators.",
    "worth_weighing": ["Whether the delay changes the 2027 roadmap", "How peers react"],
}


def _item(**kw):
    return {
        "id": "wli_AMD",
        "type": "ticker",
        "value": "AMD",
        "label": "AMD",
        "asset_class": "equity",
        "monitor_news": True,
        "added_by": "user",
        "watchlist_ids": ["wl"],
        "instrument_id": "ins_amd",
        **kw,
    }


def _ctx(price=91.0, prev=100.0, *, intent=None, calm=True):
    from datetime import date, timedelta

    hist = [
        (
            (date(2026, 9, 23) - timedelta(days=120 - i)).isoformat(),
            100 + (i % 2) * (0.8 if calm else 6),
        )
        for i in range(120)
    ]
    return {
        "now": NOW,
        "items": [_item(intent=intent)],
        "snaps": {
            "AMD": {
                "price": price,
                "prev_close": prev,
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
    calls: dict = {
        "llm": 0,
        "push": [],
        "reply": GOOD,
        "note": {"summary": "MI400 slipped.", "sources": SOURCES},
    }

    async def fake_complete_json(prompt, **kw):
        calls["llm"] += 1
        calls["prompt"] = prompt
        calls["system"] = kw.get("system", "")
        return calls["reply"]

    import backend.core.llm_seam as seam
    import backend.db.price_movement_store as pms

    monkeypatch.setattr(seam, "complete_json", fake_complete_json)

    class _Notes:
        def get_note(self, iid, session):
            return calls["note"]

    monkeypatch.setattr(pms, "PriceMovementStore", _Notes)
    monkeypatch.setattr(
        wa, "_analyst", lambda symbol: {"rating": "Buy", "target_mean": 130.0, "analyst_count": 40}
    )

    async def fake_push(ticker, explained):
        calls["push"].append((ticker, explained["what_happened"]))

    monkeypatch.setattr(wa, "_push", fake_push)
    return calls


def _run(ctx):
    watch_signals.emit_watch_signals(ctx=ctx)
    return asyncio.run(wa.explain_material(ctx))


def _story():
    from backend.db.intel_stories_store import IntelStoriesStore

    return next(s for s in IntelStoriesStore().all() if s.get("synthetic_key") == "watch:AMD")


def test_a_material_company_move_gets_one_explained_sourced_pushed_alert(db_isolated, stubs):
    assert _run(_ctx()) == 1
    ex = _story()["explained"]
    assert ex["trigger"] == "company_move"
    assert ex["sources"] == SOURCES
    assert ex["worth_weighing"]
    assert stubs["push"] == [("AMD", GOOD["what_happened"])]
    # the model saw the stock's own history and the user's context, not just the % move
    assert "usual_daily_move_pct" in stubs["prompt"] and "your_sector_share_pct" in stubs["prompt"]


def test_same_trigger_same_session_is_explained_once(db_isolated, stubs):
    _run(_ctx())
    assert _run(_ctx()) == 0
    assert stubs["llm"] == 1


def test_no_sourced_why_means_no_alert_and_no_push(db_isolated, stubs):
    stubs["note"] = {"summary": "Something happened.", "sources": []}
    assert _run(_ctx()) == 0
    assert stubs["llm"] == 0 and stubs["push"] == []


def test_not_material_means_no_model_call(db_isolated, stubs):
    # the same -9% on a stock that swings ±6% a day is ordinary for it
    assert _run(_ctx(calm=False)) == 0
    assert stubs["llm"] == 0


def test_invented_figures_or_advice_are_rejected(db_isolated, stubs):
    stubs["reply"] = {**GOOD, "what_it_means": "You should buy — it could rally 47.3%."}
    assert _run(_ctx()) == 0
    assert "explained" not in _story() and stubs["push"] == []


def test_entering_your_buy_zone_is_material_even_on_a_quiet_day(db_isolated, stubs):
    ctx = _ctx(price=89.5, prev=90.5, intent={"buy_below": 90.0})
    assert _run(ctx) == 1
    assert _story()["explained"]["trigger"] == "buy_zone"


def test_daily_cap(db_isolated, stubs, monkeypatch):
    monkeypatch.setattr(wa, "MAX_EXPLAINED_PER_DAY", 0)
    assert _run(_ctx()) == 0 and stubs["llm"] == 0


def test_the_daily_cap_survives_a_revived_story_dropping_its_explained_alert(
    db_isolated, stubs, monkeypatch
):
    """A revived story drops `explained` (a new episode), so counting `explained.at` let a
    second episode on the same day slip past the cap. The per-story log still counts it."""
    from backend.services.intel_stories import apply_story_change

    monkeypatch.setattr(wa, "MAX_EXPLAINED_PER_DAY", 1)
    assert _run(_ctx()) == 1
    story = _story()
    story.pop("explained")
    apply_story_change(story)
    assert _run(_ctx()) == 0 and stubs["llm"] == 1


def test_no_llm_provider_is_a_silent_skip(db_isolated, stubs, monkeypatch):
    import backend.core.llm_seam as seam

    async def unavailable(*a, **kw):
        raise seam.LLMUnavailable("none")

    monkeypatch.setattr(seam, "complete_json", unavailable)
    assert _run(_ctx()) == 0


# ── the checks, directly ─────────────────────────────────────────────────────────


def test_check_result_rules():
    facts = {"day_pct": -9.0, "times_usual_move": 11.2, "your_intent": {"buy_below": 90.0}}
    assert wp.check_result(GOOD, facts, 1) is None
    assert "not in FACTS" in wp.check_result({**GOOD, "why": "Shares may fall to 71.4."}, facts, 1)
    # a figure from the sourced paragraph is grounded; a product name is not a figure
    assert (
        wp.check_result(
            {**GOOD, "why": "Guidance was cut to 71.4 billion."},
            facts,
            1,
            "Guidance was cut to 71.4 billion",
        )
        is None
    )
    assert wp.invents_figures("the MI400 and H100 in Q3", facts) is None
    assert "Don't tell" in wp.check_result({**GOOD, "why": "Time to buy."}, facts, 1)
    assert "source_ids" in wp.check_result({**GOOD, "source_ids": [3]}, facts, 1)
    assert wp.check_result({**GOOD, "worth_weighing": []}, facts, 1)


def test_push_message_carries_what_why_and_meaning():
    msg = wa.push_message("AMD", {**GOOD, "sources": SOURCES})
    assert "AMD" in msg and GOOD["why"] in msg and GOOD["what_it_means"] in msg


def test_a_revived_story_drops_the_last_episodes_explanation(db_isolated, stubs):
    _run(_ctx())
    assert "explained" in _story()
    watch_signals.emit_watch_signals(ctx=_ctx(price=100.05, prev=100.0))  # cools → resolved
    assert _story()["resolved"] is True
    stubs["note"] = {"summary": "", "sources": []}  # no sourced why this time → stays deterministic
    _run(_ctx(price=108.0, prev=100.0))
    revived = _story()
    assert revived["resolved"] is False and "explained" not in revived


# ── settings: the explained-alert toggle and the opinion level ──────────────────


def test_turning_explained_alerts_off_means_no_alert_and_no_push(db_isolated, stubs, monkeypatch):
    monkeypatch.setattr(wa, "settings", lambda: {"explained_alerts_enabled": False})
    assert _run(_ctx()) == 0
    assert stubs["llm"] == 0 and stubs["push"] == []


def test_at_the_lean_level_the_lean_is_kept_and_pushed(db_isolated, stubs, monkeypatch):
    monkeypatch.setattr(wa, "settings", lambda: {"ai_opinion_level": "lean"})
    stubs["reply"] = {**GOOD, "lean": "bearish", "lean_reason": "The delay hits the AI thesis."}
    assert _run(_ctx()) == 1
    ex = _story()["explained"]
    assert (ex["lean"], ex["lean_reason"]) == ("bearish", "The delay hits the AI thesis.")
    assert "leans bearish" in wa.push_message("AMD", ex)
    assert "lean" in stubs["system"] or "lean" in stubs["prompt"]


def test_at_the_information_level_opinion_fields_are_dropped(db_isolated, stubs):
    stubs["reply"] = {**GOOD, "lean": "bullish", "lean_reason": "Looks cheap."}
    assert _run(_ctx()) == 1
    assert "lean" not in _story()["explained"]


def test_opinion_level_checks():
    facts = {"day_pct": -9.0, "times_usual_move": 11.2, "your_intent": {"buy_below": 90.0}}
    lean = {**GOOD, "lean": "bullish", "lean_reason": "The pullback reached your zone."}
    assert wp.check_result(lean, facts, 1, level="lean") is None
    assert "'lean'" in wp.check_result({**lean, "lean": "up"}, facts, 1, level="lean")
    assert "Don't tell" in wp.check_result(
        {**lean, "lean_reason": "You should add."}, facts, 1, level="lean"
    )
    suggest = {**lean, "suggestion": "Consider adding within your buy zone below 90.0."}
    assert wp.check_result(suggest, facts, 1, level="suggest") is None
    assert "urge" in wp.check_result(
        {**suggest, "suggestion": "Buy now."}, facts, 1, level="suggest"
    )
    assert "not in FACTS" in wp.check_result(
        {**suggest, "suggestion": "Consider adding below 71.4."}, facts, 1, level="suggest"
    )


def test_a_period_label_is_not_a_figure():
    assert wp.invents_figures("near its 52-week low and 200-day average", {}) is None
    assert wp.invents_figures("down 52 points", {}) == "52"
