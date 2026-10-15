"""``get_profile`` is the PERSON — not every domain that happens to be stored on the profile row
(owner, 2026-09-26). It shipped the raw row: Zero-Based targets keyed by envelope id with no
names (under the stale key ``category_targets``), budget config, the investment target mix, the
Risk card's limits and chat/import UI toggles — and the model paired target amounts with the
wrong envelopes. ``services.profile.profile_for_model`` is an allowlist; each other domain is
read through its own tool, ids with their labels (ADR 2026-07-20)."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from backend.ai.tools import execute_tool
from backend.ai.tools.profile import UpdateProfileChanges
from backend.db.profile_store import ProfileStore
from backend.services.profile import _MODEL_FIELDS, _MODEL_SETTINGS, profile_for_model

STORED = {
    "name": "Test Person",
    "date_of_birth": "1990-10-15",
    "base_currency": "SGD",
    "monthly_net_income": 8000,
    "risk_tolerance": "moderate",
    "notes": "Saving for a flat.",
    # Other domains' state that lives on the same row — none of it may reach the model raw.
    "category_targets": {"env_aaaaaaaaaaaa": {"type": "monthly", "amount": 650}},
    "category_notes": {"env_aaaaaaaaaaaa": "a note"},
    "budget_config": {"rollover_start": "2026-01"},
    "credit_card_mode": "payment_envelope",
    "target_allocation": {"equity": 0.6, "crypto": 0.4},
    "risk_concentration_guideline": 20.0,
    "risk_stress_drop_pct": 30.0,
    "chat_cost_visible": True,
    "chat_multi_turn_enabled": True,
    "dedup_policy": "strict",
    "a_key_added_next_year": {"anything": 1},
    # The preferences update_profile can set, grouped under `settings`.
    "briefing_preferences": {"fx": False},
    "import_review_default": "skip",
    "cashflow_low_balance_threshold": 500,
}

LEAKS = (
    "category_targets",
    "category_notes",
    "budget_config",
    "credit_card_mode",
    "target_allocation",
    "risk_concentration_guideline",
    "risk_stress_drop_pct",
    "chat_cost_visible",
    "chat_multi_turn_enabled",
    "dedup_policy",
    "a_key_added_next_year",
)


def _get_profile() -> dict:
    text, proposed = asyncio.run(execute_tool("get_profile", {}))
    assert proposed is None
    return json.loads(text)


def test_get_profile_is_the_person_and_nothing_raw_from_other_domains():
    ProfileStore().set_raw(STORED)
    body = _get_profile()
    leaked = [k for k in LEAKS if k in body or k in body["settings"]]
    assert not leaked, f"get_profile still ships other domains' state: {leaked}"
    assert "env_aaaaaaaaaaaa" not in json.dumps(body), "an opaque envelope id reached the model"
    assert body["name"] == "Test Person"
    assert body["monthly_net_income"] == 8000
    assert body["notes"] == "Saving for a flat."
    assert body["settings"]["import_review_default"] == "skip"
    assert body["settings"]["briefing_preferences"]["fx"] is False
    assert body["settings"]["cashflow_low_balance_threshold"] == 500


def test_every_field_update_profile_writes_reads_back_somewhere():
    """Read/write symmetry: the model can confirm anything it can change. The one field it reads
    through another tool is named here, so dropping a read path can't happen silently."""
    read_elsewhere = {"target_allocation": "get_rebalance_plan"}
    readable = set(_MODEL_FIELDS) | set(_MODEL_SETTINGS) | set(read_elsewhere)
    unreadable = set(UpdateProfileChanges.model_fields) - readable
    assert not unreadable, f"update_profile writes fields get_profile can't show: {unreadable}"


def test_age_is_worked_out_server_side_not_by_the_model():
    today = date(2026, 9, 26)
    assert profile_for_model({"date_of_birth": "1990-10-15"}, today)["age"] == 35
    assert profile_for_model({"date_of_birth": "1990-09-26"}, today)["age"] == 36
    assert "age" not in profile_for_model({"date_of_birth": None}, today)
    assert "age" not in profile_for_model({"date_of_birth": "not a date"}, today)


def test_the_system_prompt_names_the_books_budget_method_and_its_tool(monkeypatch):
    """get_budget_month is Zero-Based's table whatever the method — a 50/30/20 book has to be
    told to read get_budget_view, or "how's my budget?" comes back in Ready to Assign terms."""
    from backend.ai import prompts
    from backend.services.budget_engines import base as budget_engines

    monkeypatch.setattr(budget_engines, "home_engine_id", lambda: "proportional")
    line = prompts._budget_method_line()
    assert line.startswith("50/30/20") and "get_budget_view" in line
    monkeypatch.setattr(budget_engines, "home_engine_id", lambda: "zero_based")
    assert prompts._budget_method_line().startswith("Zero-Based")
