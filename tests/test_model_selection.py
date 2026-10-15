"""Canonical conversation-model identity is provider pinned."""

from __future__ import annotations

import pytest

from backend.ai import assistant, conversation
from backend.core.errors import ConfigurationError, ProviderDisabledError
from backend.db.model_config_store import ModelConfigStore
from backend.services.model_selection import (
    canonical_model_ref,
    configured_model_choices,
    describe_model_ref,
)


class _Store:
    def public(self):
        return {"default_model": "claude_cli:sonnet"}

    def resolve_override(self, ref):
        if ":" in ref:
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}
        return {"provider_key": None, "model": ref}


def test_legacy_bare_model_is_pinned_to_the_current_default_provider():
    store = _Store()
    store.resolve_override = lambda _ref: (_ for _ in ()).throw(
        AssertionError("canonical identity must not resolve provider credentials")
    )

    assert canonical_model_ref("sonnet", store=store) == "claude_cli:sonnet"


def test_legacy_colon_bearing_model_is_pinned_without_becoming_a_provider():
    class _OllamaStore(_Store):
        def public(self):
            return {"default_model": "ollama:qwen2.5"}

    assert (
        canonical_model_ref("llama3.1:8b", store=_OllamaStore(), legacy_bare=True)
        == "ollama:llama3.1:8b"
    )


def test_explicit_unknown_provider_is_not_reinterpreted_as_a_model_name():
    with pytest.raises(ConfigurationError, match="Unknown AI provider"):
        canonical_model_ref("not_a_provider:sonnet", store=_Store())


def test_selecting_a_disabled_provider_does_not_persist(monkeypatch):
    class _DisabledStore(_Store):
        def resolve_override(self, _ref):
            raise ProviderDisabledError("disabled", provider_key="xai", label="xAI")

    persisted = []
    monkeypatch.setattr(
        assistant,
        "upsert_session_meta",
        lambda *_args, **kwargs: persisted.append(kwargs),
    )

    with pytest.raises(ProviderDisabledError):
        conversation.select_session_model("session-1", "xai:grok-4", store=_DisabledStore())

    assert persisted == []


def test_automation_fallback_skips_a_disabled_provider_for_the_instance_default(monkeypatch):
    class _DisabledFallbackStore(_Store):
        def resolve_override(self, ref):
            if ref.startswith("xai:"):
                raise ProviderDisabledError("disabled", provider_key="xai", label="xAI")
            return super().resolve_override(ref)

    monkeypatch.setattr(conversation, "session_model_ref", lambda *_args, **_kwargs: None)

    assert (
        conversation.turn_model_ref(
            "automation-session",
            fallback_refs=["xai:grok-4", "openai:historical-run-model"],
            store=_DisabledFallbackStore(),
        )
        == "claude_cli:sonnet"
    )


def test_explicit_model_selection_is_a_loud_durable_preference_write(monkeypatch):
    calls = []

    def fail_write(*_args, **kwargs):
        calls.append(kwargs)
        raise OSError("database is read-only")

    monkeypatch.setattr(assistant, "upsert_session_meta", fail_write)

    with pytest.raises(OSError, match="read-only"):
        conversation.select_session_model("session-1", "claude_cli:sonnet", store=_Store())

    assert calls == [{"model": "claude_cli:sonnet", "fail_loud": True}]


def test_two_providers_exposing_the_same_friendly_model_id_stay_distinct_choices():
    """Phase-0 pin (RFC cross-channel-attachments item 2): the picker's dedup key is the
    full canonical ``provider:model`` ref, never the bare model id/display label — so
    "openai:gpt-4o" and "openrouter:gpt-4o" (two DIFFERENT providers that both happen to
    call a model "gpt-4o") must both survive, not collapse into one."""

    class _DualStore(_Store):
        def provider_enabled(self, _provider_key):
            return True

        def public(self):
            return {"default_model": "claude_cli:sonnet", "task_models": {}}

    choices = configured_model_choices(
        store=_DualStore(),
        preferred_refs=["openai:gpt-4o", "openrouter:gpt-4o"],
    )

    assert [c.ref for c in choices] == [
        "claude_cli:sonnet",
        "openai:gpt-4o",
        "openrouter:gpt-4o",
    ]
    same_named = [c for c in choices if c.model_id == "gpt-4o"]
    assert len(same_named) == 2
    assert {c.provider_key for c in same_named} == {"openai", "openrouter"}
    assert {c.provider_label for c in same_named} == {"OpenAI", "OpenRouter"}


def test_describe_model_ref_never_conflates_same_named_models_across_providers():
    a = describe_model_ref("openai:gpt-4o")
    b = describe_model_ref("openrouter:gpt-4o")
    assert a.model_id == b.model_id == "gpt-4o"
    assert a.ref != b.ref
    assert a.provider_key != b.provider_key
    assert a.display != b.display


def test_explicit_selection_between_two_same_named_models_persists_the_full_ref(monkeypatch):
    """Picking the SECOND same-named model must persist a genuinely different value, not
    something that reads back as "no change" because only the display label differed."""
    persisted = []
    monkeypatch.setattr(
        assistant,
        "upsert_session_meta",
        lambda *_args, **kwargs: persisted.append(kwargs),
    )

    conversation.select_session_model("session-dup", "openai:gpt-4o", store=_Store())
    conversation.select_session_model("session-dup", "openrouter:gpt-4o", store=_Store())

    assert [p["model"] for p in persisted] == ["openai:gpt-4o", "openrouter:gpt-4o"]


def test_canonical_model_ref_round_trips_codex_cli_with_a_blank_model_tail():
    """codex_cli is the one provider where an explicit blank model tail is VALID — it means
    "whatever the CLI is configured to use", not "no model chosen". canonical_model_ref must
    return it unchanged rather than raising or silently filling in a model id."""
    assert canonical_model_ref("codex_cli:", store=_Store()) == "codex_cli:"


def test_describe_model_ref_resolves_a_blank_codex_cli_tail_without_raising():
    choice = describe_model_ref("codex_cli:", store=_Store())
    assert choice.ref == "codex_cli:"
    assert choice.provider_key == "codex_cli"
    assert choice.model_id == ""
    assert choice.provider_label  # still names the provider, even with no model id


def test_session_model_ref_round_trips_a_blank_codex_cli_tail_without_mutating_it(monkeypatch):
    """The read-time legacy-migration rewrite in ``session_model_ref`` must not treat an
    intentionally blank codex_cli tail as something to "fix" — canonical_model_ref returns
    it unchanged, so no rewrite should even be attempted."""
    ModelConfigStore().save({"default_model": "claude_cli:sonnet"})
    assistant.get_or_create_session("sess-codex-blank")
    assistant.upsert_session_meta("sess-codex-blank", model="codex_cli:")

    rewrites = []
    original = assistant.upsert_session_meta

    def _spy(*args, **kwargs):
        rewrites.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(assistant, "upsert_session_meta", _spy)

    assert conversation.session_model_ref("sess-codex-blank") == "codex_cli:"
    assert rewrites == []  # unchanged on read → no durable rewrite triggered
    assert conversation.turn_model_ref("sess-codex-blank") == "codex_cli:"


def test_compact_picker_skips_stale_or_disabled_refs_without_losing_valid_choices():
    class _PickerStore(_Store):
        def provider_enabled(self, provider_key):
            return provider_key != "xai"

        def public(self):
            return {"default_model": "claude_cli:sonnet", "task_models": {}}

    choices = configured_model_choices(
        store=_PickerStore(),
        preferred_refs=[
            "provider_removed:model",
            "xai:grok-4",
            "openrouter:anthropic/sonnet",
        ],
    )

    assert [choice.ref for choice in choices] == [
        "claude_cli:sonnet",
        "openrouter:anthropic/sonnet",
    ]
