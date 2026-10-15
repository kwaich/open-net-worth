"""The Codex model list comes from the CLI's own catalog, never from a list we maintain.

Scar: the hand-written ``_CODEX_CLI_MODELS`` in ``api/models.py`` listed ``gpt-5.1-codex``,
``gpt-5.1-codex-mini``, ``gpt-5.1``, ``gpt-5-codex`` and ``gpt-5``. By 2026-07-31 OpenAI had
retired every one of them, so the picker offered five models and the installed CLI could run
none — while the CLI itself had the current list sitting in ``$CODEX_HOME/models_cache.json``
the whole time, with each model's real reasoning ladder attached.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.models import codex_cli_catalog as cat

_CACHE = {
    "fetched_at": "2026-07-31T02:31:45Z",
    "models": [
        {
            "slug": "gpt-5.4",
            "display_name": "GPT-5.4",
            "visibility": "list",
            "priority": 16,
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "medium"},
                {"effort": "high"},
                {"effort": "xhigh"},
            ],
        },
        {
            "slug": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "visibility": "list",
            "priority": 1,
            "supported_reasoning_levels": [
                {"effort": "low"},
                {"effort": "medium"},
                {"effort": "high"},
                {"effort": "xhigh"},
                {"effort": "max"},
                {"effort": "ultra"},
            ],
        },
        {
            "slug": "codex-auto-review",
            "display_name": "Codex Auto Review",
            "visibility": "hide",
            "priority": 3,
            "supported_reasoning_levels": [{"effort": "low"}],
        },
        {
            # supported_in_api=False must NOT exclude it: we drive the CLI, not the API.
            "slug": "gpt-5.3-codex-spark",
            "display_name": "GPT-5.3-Codex-Spark",
            "visibility": "list",
            "supported_in_api": False,
            "priority": 26,
            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
        },
    ],
}


@pytest.fixture
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    return tmp_path


def _write(home, payload):
    (home / "models_cache.json").write_text(json.dumps(payload), encoding="utf-8")


def test_reads_the_clis_own_catalog_in_its_priority_order(_home):
    _write(_home, _CACHE)
    assert cat.model_ids() == ["gpt-5.6-sol", "gpt-5.4", "gpt-5.3-codex-spark"]


def test_hidden_models_are_not_offered(_home):
    """``codex-auto-review`` is machinery, not a model a user should pick for chat."""
    _write(_home, _CACHE)
    assert "codex-auto-review" not in cat.model_ids()


def test_api_unavailable_models_are_still_offered(_home):
    """We invoke the binary, so 'the API cannot serve this' says nothing about the CLI."""
    _write(_home, _CACHE)
    assert "gpt-5.3-codex-spark" in cat.model_ids()


def test_effort_ladder_comes_from_the_cli_and_is_per_model(_home):
    """The old code returned a flat low|medium|high for EVERY Codex model, understating
    every current one."""
    _write(_home, _CACHE)
    assert cat.effort_levels_for("gpt-5.6-sol") == ["low", "medium", "high", "xhigh", "max"]
    assert cat.effort_levels_for("gpt-5.4") == ["low", "medium", "high", "xhigh"]
    assert cat.effort_levels_for("gpt-5.3-codex-spark") == ["low", "high"]


def test_unknown_rungs_are_dropped_not_surfaced(_home):
    """The CLI already ships an ``ultra`` rung our effort dial has no option for."""
    _write(_home, _CACHE)
    assert "ultra" not in (cat.effort_levels_for("gpt-5.6-sol") or [])


def test_effort_for_an_unlisted_model_is_none(_home):
    _write(_home, _CACHE)
    assert cat.effort_levels_for("gpt-5.1-codex") is None


def test_capabilities_uses_the_cli_ladder(_home):
    from backend.ai.models.capabilities import effort_levels

    _write(_home, _CACHE)
    assert effort_levels("codex_cli", "", "gpt-5.6-sol") == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    # Unknown model → the conservative floor, not an empty ladder (which hides the dial).
    assert effort_levels("codex_cli", "", "who-knows") == ["low", "medium", "high"]


def test_missing_cache_falls_back_to_the_static_floor(_home):
    assert cat.available_models() == []
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_corrupt_cache_falls_back_rather_than_raising(_home):
    (_home / "models_cache.json").write_text("{not json", encoding="utf-8")
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_malformed_rows_are_skipped_not_fatal(_home):
    _write(
        _home, {"models": ["nonsense", {"slug": "", "visibility": "list"}, {"visibility": "list"}]}
    )
    assert cat.model_ids() == list(cat.FALLBACK_MODELS)


def test_the_picker_resolves_the_catalog_per_request(_home):
    """The picker's model list must be resolved when asked, not frozen at import — the CLI
    refreshes that cache behind our back, so an import-time snapshot goes stale silently."""
    from backend.api.models import _CLI_TRANSPORTS

    _write(_home, _CACHE)
    assert _CLI_TRANSPORTS["codex_cli"]["models"]() == [
        "gpt-5.6-sol",
        "gpt-5.4",
        "gpt-5.3-codex-spark",
    ]

    # Same process, new catalog on disk → new answer, with nothing re-imported.
    _write(_home, {"models": [{"slug": "gpt-9", "visibility": "list", "priority": 1}]})
    assert _CLI_TRANSPORTS["codex_cli"]["models"]() == ["gpt-9"]


def test_claude_cli_keeps_its_alias_list(_home):
    """The Claude CLI publishes no catalog, so its resolver stays the curated aliases —
    which self-update by design (`sonnet` always means the current Sonnet)."""
    from backend.api.models import _CLI_TRANSPORTS

    ids = _CLI_TRANSPORTS["claude_cli"]["models"]()
    assert ids[:3] == ["sonnet", "opus", "haiku"]
