"""
Regression tests for memory hygiene — MEM-2 (volatile snapshots stored as durable)
and MEM-3 (duplicate/contradictory facts accumulate).

MEM-2: extract_and_merge must (a) instruct the extractor to prefer durable, low-churn
facts and avoid memorizing live-readable figures (share counts / prices / balances), and
mark essential figures time_bound; and (b) a deterministic post-extraction guard demotes a
durable fact that still carries a perishable figure to time_bound with a near-term expiry,
so it ages out of the always-injected prompt instead of misleading forever.

MEM-3: consolidate() must collapse several overlapping/contradictory variants of one event
into a single most-recent fact. The LLM call is mocked to return a deterministic
consolidation; the pure _dedup_exact backstop is unit-tested directly.

Prompt-content assertions mirror test_grounding_prompt.py. Fully isolated — no network/LLM.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from backend.ai import memory
from backend.db import memory_store


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


# ── MEM-2: extractor prompt guidance ────────────────────────────────────────────


def test_extractor_prompt_prefers_durable_and_avoids_live_figures(isolated, monkeypatch):
    captured: dict[str, str] = {}

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())

    history = [
        {"role": "user", "content": "I hold META and care about my long-term allocation."},
        {"role": "assistant", "content": "Noted."},
    ]
    asyncio.run(memory.extract_and_merge(history))

    assert "prompt" in captured, "extractor never called the provider"
    n = _norm(captured["prompt"])

    # Prefer durable, low-churn facts.
    assert "prefer durable, low-churn facts" in n
    assert "preferences, goals, risk posture, account structure" in n
    # Avoid memorizing live-readable figures.
    assert "avoid memorizing quantities, share counts, live prices, or account balances" in n
    # Essential figures go time_bound with an as-of, never durable.
    assert "mark that fact time_bound with an as-of date" in n
    assert "never durable" in n


# ── MEM-2: deterministic perishable-figure guard ────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        "User holds META (12.5 shares), trading at ~$598 (underwater).",
        "AMD 16 shares ~$522.",
        "MSFT 38 shares trading at $441.",
        "Meridian Bank Multiplier balance of $12,000.",
        "Holds 890.4 units of ONWAI.",
    ],
)
def test_guard_demotes_perishable_durable_fact(content):
    today = "2026-06-05"
    fact = {"content": content, "tier": "durable", "expires_at": None}
    out = memory._guard_perishable_durable(fact, today)

    assert out["tier"] == "time_bound", f"should demote: {content!r}"
    assert out["expires_at"], "demoted fact must carry an expiry so it ages out"
    expected = (
        date.fromisoformat(today) + timedelta(days=memory.PERISHABLE_FACT_TTL_DAYS)
    ).isoformat()
    assert out["expires_at"] == expected
    # The expiry is in the future relative to today (will be active now, stale later).
    assert out["expires_at"] > today


@pytest.mark.parametrize(
    "content",
    [
        "Risk tolerance is moderate.",
        "Targeting retirement at 45.",
        "Does not want crypto above 10% of total portfolio.",
        "Holds META as a long-term equity position.",
        "Banks primarily with Meridian Bank in Singapore.",
    ],
)
def test_guard_leaves_genuine_durable_preferences_untouched(content):
    fact = {"content": content, "tier": "durable", "expires_at": None}
    out = memory._guard_perishable_durable(fact, "2026-06-05")
    assert out["tier"] == "durable", f"should NOT demote a preference: {content!r}"
    assert out is fact  # unchanged object


def test_guard_leaves_time_bound_facts_alone():
    fact = {
        "content": "Sold 500 ONWAI at €30.00/share",
        "tier": "time_bound",
        "expires_at": "2026-07-01",
    }
    out = memory._guard_perishable_durable(fact, "2026-06-05")
    assert out is fact  # already time_bound — guard is a no-op


def test_guard_is_idempotent():
    today = "2026-06-05"
    fact = {"content": "META 12.5 shares at $598", "tier": "durable", "expires_at": None}
    once = memory._guard_perishable_durable(fact, today)
    twice = memory._guard_perishable_durable(once, today)
    assert twice["tier"] == "time_bound"
    assert twice["expires_at"] == once["expires_at"]


def test_extract_and_merge_demotes_perishable_new_durable_fact(isolated, monkeypatch):
    """End-to-end: a durable figure-bearing fact the model emits is stored time_bound."""

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            return json.dumps(
                {
                    "new": [
                        {
                            "content": "User holds META (12.5 shares) trading at ~$598.",
                            "tier": "durable",
                            "expires_at": None,
                        },
                        {
                            "content": "Risk tolerance is moderate.",
                            "tier": "durable",
                            "expires_at": None,
                        },
                    ],
                    "updates": [],
                    "remove": [],
                }
            )

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())

    history = [
        {"role": "user", "content": "Talked through META and my risk posture."},
        {"role": "assistant", "content": "Noted."},
    ]
    asyncio.run(memory.extract_and_merge(history))

    store = memory_store.MemoryStore()
    facts = {f["content"]: f for f in store.all_facts()}

    meta = facts["User holds META (12.5 shares) trading at ~$598."]
    assert meta["tier"] == "time_bound", "perishable figure must be demoted to time_bound"
    assert meta["expires_at"], "demoted fact must carry an expiry"

    risk = facts["Risk tolerance is moderate."]
    assert risk["tier"] == "durable", "a plain preference must stay durable"
    assert risk["expires_at"] is None


# ── MEM-3: consolidation collapses duplicate/contradictory event variants ─────────


def test_dedup_exact_collapses_identical_content_keeping_newest():
    facts = [
        {"id": "a", "content": "Sold 500 ONWAI", "updated_at": "2026-06-01T00:00:00"},
        {"id": "b", "content": "sold 500 onwai", "updated_at": "2026-06-04T00:00:00"},  # dup
        {
            "id": "c",
            "content": "  Sold   500   ONWAI  ",
            "updated_at": "2026-06-02T00:00:00",
        },  # dup
        {"id": "d", "content": "Banks with Meridian Bank", "updated_at": "2026-06-03T00:00:00"},
    ]
    out = memory._dedup_exact(facts)
    contents = [f["content"] for f in out]
    assert len(out) == 2
    # The ONWAI trio collapses to the newest (id "b").
    onwai = next(f for f in out if "onwai" in f["content"].lower())
    assert onwai["id"] == "b"
    assert "Banks with Meridian Bank" in contents


def test_dedup_exact_keeps_distinct_facts():
    facts = [
        {"id": "a", "content": "Risk tolerance moderate", "updated_at": "2026-06-01T00:00:00"},
        {"id": "b", "content": "Retiring at 45", "updated_at": "2026-06-02T00:00:00"},
    ]
    out = memory._dedup_exact(facts)
    assert len(out) == 2


def test_consolidate_reconciles_overlapping_event_variants(isolated, monkeypatch):
    """Several overlapping/contradictory ONWAI-sale variants → one most-recent fact.

    The memory model is mocked to return the deterministic consolidation a good
    consolidator should produce (keep the latest, drop the rest); the _dedup_exact
    backstop further collapses any exact dup the model leaves behind.
    """
    store = memory_store.MemoryStore()
    variants = [
        {
            "id": "v1",
            "content": "User sold ONWAI; proceeds €21,480.35 into Meridian Bank Multiplier EUR",
            "tier": "time_bound",
            "source": "implicit",
            "expires_at": None,
            "created_at": "2026-06-01T00:00:00",
            "updated_at": "2026-06-01T00:00:00",
        },
        {
            "id": "v2",
            "content": "User sold ONWAI; proceeds ~€21,300; reduced from 1,310 to ~8xx",
            "tier": "time_bound",
            "source": "implicit",
            "expires_at": None,
            "created_at": "2026-06-02T00:00:00",
            "updated_at": "2026-06-02T00:00:00",
        },
        {
            "id": "v3",
            "content": "User sold ONWAI (fourth tranche); reduced to 890.4 shares",
            "tier": "time_bound",
            "source": "implicit",
            "expires_at": None,
            "created_at": "2026-06-03T00:00:00",
            "updated_at": "2026-06-03T00:00:00",
        },
        {
            "id": "v4",
            "content": "User sold ONWAI via lot disposal; realized gain ~€5,120",
            "tier": "time_bound",
            "source": "implicit",
            "expires_at": None,
            "created_at": "2026-06-04T00:00:00",
            "updated_at": "2026-06-04T00:00:00",
        },
        {
            "id": "keep",
            "content": "Risk tolerance is moderate",
            "tier": "durable",
            "source": "explicit",
            "expires_at": None,
            "created_at": "2026-05-01T00:00:00",
            "updated_at": "2026-05-01T00:00:00",
        },
    ]
    store.replace_all(variants)

    captured: dict[str, str] = {}

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            captured["prompt"] = prompt
            # A correct consolidator collapses the 4 ONWAI variants to the newest (v4)
            # and keeps the unrelated durable preference.
            return json.dumps(
                {
                    "merge": [
                        {
                            "keep_id": "v4",
                            "absorb_ids": ["v1", "v2", "v3"],
                            "content": "User sold ONWAI via lot disposal; realized gain ~€5,120",
                            "tier": "time_bound",
                            "expires_at": None,
                        }
                    ],
                    "remove": [],
                }
            )

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())

    asyncio.run(memory.consolidate())

    facts = memory_store.MemoryStore().all_facts()
    onwai_facts = [f for f in facts if "onwai" in f["content"].lower()]
    assert len(onwai_facts) == 1, f"4 ONWAI variants must collapse to 1, got {len(onwai_facts)}"
    assert onwai_facts[0]["id"] == "v4", "the most-recent variant survives"
    assert any(f["content"] == "Risk tolerance is moderate" for f in facts)
    assert len(facts) == 2


def test_consolidate_prompt_instructs_entity_merge_and_cap(isolated, monkeypatch):
    store = memory_store.MemoryStore()
    store.replace_all(
        [
            {
                "id": "a",
                "content": "Fact one",
                "tier": "durable",
                "source": "implicit",
                "expires_at": None,
                "created_at": "2026-06-01T00:00:00",
                "updated_at": "2026-06-01T00:00:00",
            },
            {
                "id": "b",
                "content": "Fact two",
                "tier": "durable",
                "source": "implicit",
                "expires_at": None,
                "created_at": "2026-06-02T00:00:00",
                "updated_at": "2026-06-02T00:00:00",
            },
        ]
    )

    captured: dict[str, str] = {}

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return "[]"  # empty → leaves memory untouched (guarded)

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())
    asyncio.run(memory.consolidate())

    n = _norm(captured["prompt"])
    assert "group facts by the same entity or event" in n
    assert "collapse each group into one fact" in n
    assert "keep only the version with the latest updated_at" in n
    assert f"keep at most {memory.MAX_FACTS_PER_ENTITY} facts" in n
