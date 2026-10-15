"""Image-input (vision) capability — the ladder, the chat fallback, the statement refusal.

ADR 2026-07-26. The behaviour under test, in one line: a model that cannot see images must
never be handed one silently, and a model we are merely UNSURE about must not be refused.
"""

from __future__ import annotations

import pytest

from backend.core.vision_names import infer_vision_from_name, vision_from_modalities
from backend.db.model_config_store import ModelConfigStore
from backend.services.vision_capability import (
    record_vision_capability,
    resolve_extractor_ref,
    supports_vision,
    vision_key,
)


@pytest.fixture()
def store(db_isolated, monkeypatch):
    """A ModelConfigStore on the isolated test DB, seeded with a GLM instance default."""
    monkeypatch.setenv("ONW_QA_MODE", "0")
    s = ModelConfigStore()
    s.update(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.z.ai/api/paas/v4/",
            "model": "glm-4.6",
        }
    )
    return s


# ── The pure name heuristic (core leaf) ──────────────────────────────────────


class TestNameInference:
    def test_known_vision_families_are_true(self):
        for mid in (
            "gpt-4o",
            "claude-opus-4-8",
            "gemini-2.5-flash",
            "qwen2.5-vl-7b",
            "pixtral-12b",
        ):
            assert infer_vision_from_name(mid) is True, mid

    def test_known_text_only_families_are_false(self):
        for mid in ("glm-4.6", "glm-5.2", "glm-4.5-air", "deepseek-chat", "kimi-k2"):
            assert infer_vision_from_name(mid) is False, mid

    def test_glm_vision_line_is_not_swallowed_by_the_glm_text_pattern(self):
        """The whole reason vision patterns are checked FIRST: glm-4v is a vision model whose
        name is a prefix-sibling of the text-only glm-4.x family."""
        assert infer_vision_from_name("glm-4v") is True
        assert infer_vision_from_name("glm-4.1v-thinking") is True
        assert infer_vision_from_name("glm-4.6") is False

    def test_an_unrecognized_model_is_UNKNOWN_not_false(self):
        """The load-bearing case. A brand-new model must not be declared blind by omission —
        that would silently strip image input from something that may well accept it."""
        assert infer_vision_from_name("some-new-model-v9") is None
        assert infer_vision_from_name("") is None

    def test_declared_modalities_are_read_exactly(self):
        assert vision_from_modalities(["text", "image"]) is True
        assert vision_from_modalities(["text"]) is False
        # Absent metadata is NOT "no vision" — the provider simply didn't say.
        assert vision_from_modalities(None) is None


# ── The stored ladder ────────────────────────────────────────────────────────


class TestLadder:
    def test_name_inference_answers_when_nothing_is_stored(self, store):
        assert supports_vision("glm:glm-4.6", store=store) is False
        assert supports_vision("openai:gpt-4o", store=store) is True

    def test_a_bare_model_id_resolves_against_the_instance_default_provider(self, store):
        assert supports_vision("glm-4.6", store=store) is False

    def test_an_empty_ref_resolves_the_instance_default_model(self, store):
        """The chat path passes "" for "no per-conversation override" — it must resolve to the
        default model, not to "unknown"."""
        assert supports_vision("", store=store) is False

    def test_a_stored_answer_beats_the_name_heuristic(self, store):
        """The escape hatch for a wrong guess: a GLM-named model that actually does see
        images (a self-hosted alias, say) is fixed by one recorded override."""
        record_vision_capability("glm:glm-4.6", True, "user", store=store)
        assert supports_vision("glm:glm-4.6", store=store) is True

    def test_a_weaker_source_never_overwrites_a_stronger_one(self, store):
        record_vision_capability("glm:glm-4.6", True, "user", store=store)
        record_vision_capability("glm:glm-4.6", False, "learned", store=store)
        record_vision_capability("glm:glm-4.6", False, "declared", store=store)
        assert supports_vision("glm:glm-4.6", store=store) is True

    def test_a_stronger_source_does_overwrite(self, store):
        record_vision_capability("glm:glm-4.6", True, "declared", store=store)
        record_vision_capability("glm:glm-4.6", False, "learned", store=store)
        assert supports_vision("glm:glm-4.6", store=store) is False

    def test_an_unknown_source_name_is_refused(self, store):
        with pytest.raises(ValueError):
            record_vision_capability("glm:glm-4.6", False, "vibes", store=store)

    def test_a_corrupt_vision_map_degrades_to_the_heuristic_rather_than_raising(self, store):
        """No more on-disk file to hand-corrupt (model_config is a secret_config row now) —
        write the same malformed shape straight into the persisted raw dict via the store's
        own _write_raw, the DB-native equivalent of the old direct file write."""
        raw = store._read_raw()
        raw["vision"] = {vision_key("openai", "gpt-4o"): "not-a-dict"}
        store._write_raw(raw)
        assert supports_vision("openai:gpt-4o", store=store) is True


# ── The transport can veto the model ─────────────────────────────────────────


class TestCliImageTransports:
    """Both CLI providers carry images through `complete` and `stream_chat`.

    The model capability ladder still decides whether the model itself is a reader; the
    transport no longer vetoes a model whose native CLI path can deliver the picture.
    """

    def test_a_cli_transport_allows_stream_chat_for_a_vision_capable_model_name(self, store):
        assert supports_vision("claude_cli:claude-opus-4-8", store=store) is True
        assert supports_vision("codex_cli:gpt-5.1-codex", store=store) is True

    def test_the_model_name_alone_still_reads_as_vision_capable(self):
        """The provider change does not alter model-name capability inference."""
        assert infer_vision_from_name("claude-opus-4-8") is True
        assert infer_vision_from_name("gpt-5.1-codex") is True

    def test_an_unknown_cli_model_is_not_vetoed_by_the_transport(self, store):
        assert supports_vision("claude_cli:sonnet", store=store) is not False

    def test_a_user_override_applies_to_cli_stream_chat(self, store):
        record_vision_capability("claude_cli:claude-opus-4-8", True, "user", store=store)
        assert supports_vision("claude_cli:claude-opus-4-8", store=store) is True

    def test_a_cli_transport_is_not_refused_for_complete(self, store):
        """A CLI-pinned statement_vision/chat_vision task remains a real reader."""
        assert supports_vision("claude_cli:sonnet", store=store, via="complete") is not False
        assert supports_vision("codex_cli:gpt-5.1-codex", store=store, via="complete") is not False

    def test_a_cli_instance_default_IS_offered_as_the_extractor(self, store):
        """`resolve_extractor_ref` drives `complete()`, where a CLI default is valid."""
        store.update({"provider": "claude_cli", "base_url": "", "model": "claude-opus-4-8"})
        assert resolve_extractor_ref(store=store) == "claude_cli:claude-opus-4-8"

    def test_the_transport_lists_match_what_the_providers_actually_do(self):
        """Sync check against the source of truth: both entry points accept images.

        Only a test may assert this: `services` cannot import `ai` under the dependency
        rule, which is why the lists are duplicated in the first place.
        """
        import asyncio

        from backend.ai.models.claude_cli_provider import ClaudeCliProvider
        from backend.ai.models.codex_cli_provider import CodexCliProvider
        from backend.services.vision_capability import (
            _COMPLETE_INCAPABLE_TRANSPORTS,
            _STREAM_CHAT_INCAPABLE_TRANSPORTS,
        )

        assert _STREAM_CHAT_INCAPABLE_TRANSPORTS == frozenset()
        assert _COMPLETE_INCAPABLE_TRANSPORTS == frozenset()
        img = [{"media_type": "image/png", "data": "aGk="}]
        for provider in (ClaudeCliProvider(), CodexCliProvider()):
            with pytest.raises(Exception) as exc:
                # No real CLI on PATH in CI, or a real one with no login — either way this
                # must fail on SPAWN/AUTH, never on an "images unsupported" refusal.
                asyncio.run(provider.complete("read this", images=img))
            assert "cannot receive images" not in str(exc.value).lower()


# ── Which model reads the image ──────────────────────────────────────────────


class TestExtractorResolution:
    def test_the_chat_vision_pin_wins(self, store):
        store.update_provider("anthropic", {"api_key": "sk-ant-test"})
        store.set_task_model("chat_vision", "anthropic:claude-haiku-4-5")
        assert resolve_extractor_ref(store=store) == "anthropic:claude-haiku-4-5"

    def test_a_blind_instance_default_is_NEVER_offered_as_the_reader(self, store):
        """Routing the extraction to the model that can't see is money spent to ask a blind
        model to describe a picture."""
        assert resolve_extractor_ref(store=store) == ""

    def test_a_vision_capable_instance_default_is_used_with_no_pin(self, store):
        """The common real case: the default is a vision model and only THIS conversation was
        switched to a cheap text-only one."""
        store.update({"provider": "anthropic", "base_url": "", "model": "claude-opus-4-8"})
        assert resolve_extractor_ref(store=store) == "anthropic:claude-opus-4-8"
