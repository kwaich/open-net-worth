"""
Behavioral eval for the RESPONSE CALIBRATION contract (@integration — the enforcement
half of docs/superpowers/specs/2026-07-11-response-calibration-design.md).

Reproduces the reported failure: the user says "hello" (or "how are you?"/"thanks")
while a data-rich screen snapshot, a seeded financial profile, and memory facts all ride
the turn — maximum bait for context narration. The reply must stay conversational:
short, zero financial figures, zero bullets/headers, zero topic menus — at effort
medium AND high (effort is a ceiling, not a target).

Drives the real Anthropic provider with the REAL built system prompt and the real
message shape (user text → screen block → <turn_context>, mirroring api/chat.py +
assistant.run). tools=[] — the failure mode under test is pure text generation, and it
keeps the eval cheap. Deterministic string asserts, no LLM judge.

Deliberately BYPASSES the harness smalltalk override (_resolve_style would swap the
style line for these exact inputs): this eval measures the PROMPT CONTRACT on its own,
because fuzzy phrasings the lexicon misses ("hey, morning!") have only the contract to
hold them. The override is separately pinned by tests/test_smalltalk_override.py.

Pinned to haiku (the smallest/most drift-prone tier, and the model the failure was
observed on): if haiku stays terse, larger models will too.

pass^k, not pass^1: each case runs TRIALS times and EVERY trial must pass — Sierra's
tau-bench showed single-run behavioral results badly overstate reliability (>60%
single-run success collapsing to <25% at pass^8), and Promptfoo ships --repeat for the
same reason. Trials run concurrently, so wall-clock stays flat.

Skipped without ANTHROPIC_API_KEY (conftest). Run on demand, before shipping any prompt
change, and on any default-model/task-model change:
  .venv/bin/python3 -m pytest -q -m integration tests/test_conversational_calibration.py
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from backend.db.account_store import AccountStore
from backend.db.memory_store import MemoryStore
from backend.db.transaction_store import TransactionStore

MODEL = "claude-haiku-4-5-20251001"

GREETINGS = ["hello", "how are you?", "thanks"]
EFFORTS = ["medium", "high"]

# pass^k: every one of TRIALS i.i.d. runs must pass for the case to pass.
TRIALS = 3

_MAX_WORDS = 45

# Currency-shaped figures: "SGD 4,321.00", "$422", "S$1,200", "€40".
_CURRENCY_RE = re.compile(r"(?:SGD|USD|EUR|VND|S\$|US\$|\$|€)\s?\d", re.IGNORECASE)
# Thousands-separated or K-shorthand amounts: "157,347" / "422K".
_BIG_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\b\d+(?:\.\d+)?[kK]\b")
# Markdown bullets / headers at line start.
_STRUCTURE_RE = re.compile(r"^\s*(?:[-*•]\s|#{1,6}\s)", re.MULTILINE)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Isolated stores with a deliberately data-rich book: profile figures + memory
    facts give the model plenty to over-share if the contract fails to hold.
    PriceHistoryStore, InstrumentStore, and MemoryStore all resolve against the
    per-test isolated database (db_isolated) — DB-backed (plan 2 tasks 3/5; plan 4d
    task 2 for MemoryStore)."""
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "base_currency": "SGD",
                "date_of_birth": "1990-01-01",
                "name": "Alex",
                "monthly_net_income": 9000,
                "monthly_expenses": 3500,
                "fire_expected_return_pct": 7.0,
                "fire_withdrawal_rate_pct": 3.5,
                "retirement_target_age": 50,
            }
        )
    )
    import backend.core.fx as core_fx
    import backend.services.profile as profile_mod
    from backend.services import ledger

    monkeypatch.setattr(profile_mod, "PROFILE_FILE", profile_path)
    monkeypatch.setattr(core_fx, "PROFILE_JSON", profile_path)

    fx_path = tmp_path / "fx_cache.json"
    fx_path.write_text(json.dumps({"rates": {"SGD": 1.0}, "updated": "2026-07-01T00:00:00"}))
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "_FX_CACHE_FILE", fx_path)

    MemoryStore().insert(
        {
            "id": "mem_1",
            "content": "ACME is 31% of net worth — user wants to trim concentration",
            "tier": "durable",
            "source": "implicit",
            "created_at": "2026-06-01T00:00:00",
            "updated_at": "2026-06-01T00:00:00",
        }
    )
    MemoryStore().insert(
        {
            "id": "mem_2",
            "content": "Paying apartment installments quarterly through 2027",
            "tier": "durable",
            "source": "explicit",
            "created_at": "2026-06-02T00:00:00",
            "updated_at": "2026-06-02T00:00:00",
        }
    )
    monkeypatch.setattr(
        "backend.db.memory_settings_store.MemorySettingsStore.enabled", lambda self: True
    )

    monkeypatch.setattr(ledger, "accounts", AccountStore())
    monkeypatch.setattr(ledger, "transactions", TransactionStore())
    monkeypatch.setattr("backend.ai.prompts.is_empty_book", lambda: False)
    return tmp_path


def _tax_screen_block() -> str:
    """The exact bait from the reported failure: the Tax Estimate screen riding a turn."""
    from backend.services.screen_context import ScreenSnapshot, format_screen_context

    return format_screen_context(
        ScreenSnapshot(
            id="tax",
            title="2026 Tax Estimate",
            facts={
                "estimated_tax_owed": "SGD 4,321.00",
                "effective_rate": "8.63%",
                "net_taxable_income": "SGD 157,347.75",
                "harvestable_losses": "SGD 953.25",
            },
            visible_text=(
                "Tax Summary 2026 · Estimated tax owed SGD 4,321.00 · Effective rate 8.63% "
                "· Net taxable income SGD 157,347.75 · Tax-loss harvesting: TLM SGD 586.99 "
                "loss, DOGE SGD 307.84 loss · Blocked by wash sale SGD 9,155.99"
            ),
        )
    )


async def _greet(greeting: str, effort: str) -> str:
    from backend.ai.assistant import _resolve_style, _split_turn_context
    from backend.ai.models.anthropic_provider import AnthropicProvider
    from backend.ai.prompts import build_system_prompt

    system = build_system_prompt()
    # Style is now ONLY the smalltalk greeting guard (effort no longer shapes length);
    # resolve it the way assistant.run does, from the turn content.
    style = _resolve_style([{"role": "user", "content": greeting}])
    static_system, turn_ctx = _split_turn_context(system, style)

    # Real turn shape (api/chat.py + assistant.run): user text leads, context blocks
    # follow, turn_context last.
    user_msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": greeting},
            {"type": "text", "text": _tax_screen_block()},
            {"type": "text", "text": turn_ctx},
        ],
    }

    provider = AnthropicProvider()
    out: list[str] = []
    async for event in provider.stream_chat(
        messages=[user_msg], tools=[], system=static_system, model=MODEL, effort=effort
    ):
        if event.get("type") == "text_delta":
            out.append(event.get("text", ""))
    return "".join(out).strip()


def _violations(reply: str) -> list[str]:
    """Deterministic contract checks for one reply; empty list = pass."""
    if not reply:
        return ["empty reply"]
    out = []
    words = len(reply.split())
    if words > _MAX_WORDS:
        out.append(f"{words} words (cap {_MAX_WORDS})")
    if _CURRENCY_RE.search(reply):
        out.append("volunteered a currency figure")
    if _BIG_NUMBER_RE.search(reply):
        out.append("volunteered a large figure")
    if _STRUCTURE_RE.search(reply):
        out.append("used bullets/headers")
    return out


async def _greet_trials(greeting: str, effort: str, n: int) -> list[str]:
    return list(await asyncio.gather(*(_greet(greeting, effort) for _ in range(n))))


@pytest.mark.integration
@pytest.mark.parametrize("effort", EFFORTS)
@pytest.mark.parametrize("greeting", GREETINGS)
def test_greeting_stays_conversational(isolated, greeting, effort):
    replies = asyncio.run(_greet_trials(greeting, effort, TRIALS))

    failures = [
        f"trial {i + 1}/{TRIALS}: {'; '.join(v)}\n{reply}"
        for i, reply in enumerate(replies)
        if (v := _violations(reply))
    ]
    assert not failures, (
        f"{greeting!r} (effort={effort}) failed pass^{TRIALS} "
        f"({len(failures)}/{TRIALS} trials violated the contract):\n\n" + "\n\n".join(failures)
    )
