"""The instance default: ONE resolvable ref, and it must never be blank.

`model_config.json` stores `default_model: "<provider_key>:<model_id>"` and nothing else
about the default. It replaced a {provider, base_url, model} trio whose `model` half was
BARE — carrying no provider, so the same string resolved differently as the default
provider changed (ADR 2026-08-06-one-default-model).

Defects this pins (owner report 2026-08-06, "how is this default get picked?"):

1. **Drift.** The instance default is a SNAPSHOT of some provider's model — "Set as default"
   copies `providers[key].model` into the top-level `model` once. Editing that same
   provider's model afterwards changed only the per-provider entry, so Settings displayed
   one model while every turn ran another.
2. **A blank default was writable.** "Set as default" on a provider with no remembered model
   sent `model: ""`. `public()` does not validate, so Settings looked fine — and
   `resolved()` then raised on the NEXT chat turn, far from the action that caused it.

The guard lives in the store, not the router: REST, the `update_model_config` tool and
Telegram `/model` all write through `ModelConfigStore.update`.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConfigurationError
from backend.db.model_config_store import ModelConfigStore

XAI = "https://api.x.ai/v1"
OPENAI = "https://api.openai.com/v1"


@pytest.fixture
def store(tmp_path):
    # model_config is DB-backed now (Wave 5, JSON->DB) — seed the legacy trio directly
    # via the store's raw save() (bypasses update()'s own migration-aware merge, since the
    # whole point of most of these tests is an UN-migrated row for the read path to fold).
    ModelConfigStore().save(
        {
            "provider": "openai_compatible",
            "base_url": XAI,
            "model": "grok-4.5",
            "providers": {
                "xai": {"api_key": "k", "model": "grok-4.5"},
                "openai": {"api_key": "k"},
            },
        }
    )
    return ModelConfigStore()


# ── 1. the default tracks its provider ────────────────────────────────────────


def test_editing_the_default_providers_model_moves_the_instance_default(store):
    store.update_provider("xai", {"model": "grok-4.3"})
    assert store.public()["model"] == "grok-4.3"
    assert store.resolved()["model"] == "grok-4.3"


def test_editing_a_NON_default_provider_leaves_the_instance_default_alone(store):
    store.update_provider("openai", {"model": "gpt-4o-mini"})
    assert store.public()["model"] == "grok-4.5"
    assert store.providers_public()["openai"]["model"] == "gpt-4o-mini"


def test_clearing_the_default_providers_model_does_not_blank_the_instance_default(store):
    """Only a NON-blank model mirrors — else this becomes defect 2 by another route."""
    store.update_provider("xai", {"model": ""})
    assert store.public()["model"] == "grok-4.5"


def test_a_changed_base_url_follows_too(tmp_path):
    """An editable-base-url default must keep resolving to the same catalogue entry: the
    top-level base_url is what _infer_provider_key matches on."""
    ModelConfigStore().save(
        {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "model": "llama3.1",
            "providers": {"ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1"}},
        }
    )
    store = ModelConfigStore()
    store.update_provider("ollama", {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5"})

    assert store.public()["base_url"] == "http://127.0.0.1:11434/v1"
    assert store.public()["model"] == "qwen2.5"


def test_editing_only_a_key_touches_neither(store):
    store.update_provider("xai", {"api_key": "new-key"})
    assert store.public()["model"] == "grok-4.5"
    assert store.public()["base_url"] == XAI


# ── 2. a blank instance default is refused at the choke ───────────────────────


def test_blanking_the_model_is_refused(store):
    with pytest.raises(ConfigurationError):
        store.update({"provider": "openai_compatible", "base_url": OPENAI, "model": ""})


def test_the_refusal_leaves_the_previous_default_intact(store):
    with pytest.raises(ConfigurationError):
        store.update({"model": ""})
    assert store.resolved()["model"] == "grok-4.5"


def test_a_real_model_still_saves(store):
    store.update({"provider": "openai_compatible", "base_url": OPENAI, "model": "gpt-4o-mini"})
    assert store.resolved()["model"] == "gpt-4o-mini"


def test_codex_cli_may_have_a_blank_model(tmp_path):
    """Documented exception: blank means 'use the CLI's own account-compatible default'."""
    ModelConfigStore().save({"provider": "anthropic", "model": "claude-sonnet-4-6"})
    store = ModelConfigStore()

    store.update({"provider": "codex_cli", "base_url": "", "model": ""})
    assert store.resolved()["model"] == ""


def test_an_update_that_does_not_mention_model_is_unaffected(store):
    """The guard fires on an explicit blank, not on every unrelated patch."""
    store.update({"api_key": "sk-new"})
    assert store.resolved()["model"] == "grok-4.5"


# ── 3. the stored shape is ONE resolvable ref ─────────────────────────────────


def test_the_legacy_trio_is_migrated_and_deleted(store, tmp_path):
    store.public()  # first read migrates
    # model_config is DB-backed now (Wave 5, JSON->DB) — read the raw stored row back
    # through the store's own (unmigrating) get(), the DB-era "what's on disk" check.
    stored = ModelConfigStore().get()

    assert stored["default_model"] == "xai:grok-4.5"
    # Not kept "for compat": three fields that can disagree IS the defect.
    assert not any(k in stored for k in ("provider", "base_url", "model"))


def test_the_migration_persists_rather_than_recomposing_every_read(store, tmp_path):
    store.public()
    before = ModelConfigStore().get()
    ModelConfigStore().public()
    assert ModelConfigStore().get() == before


def test_a_pinned_ref_survives_a_change_of_default_provider(store):
    """The point of pinning: the ref names its own provider, so nothing re-aims it."""
    store.update({"default_model": "openrouter:z-ai/glm-5.2"})
    r = store.resolved()
    assert r["model"] == "z-ai/glm-5.2"
    assert r["base_url"] == "https://openrouter.ai/api/v1"


def test_an_unresolvable_provider_is_named_not_swallowed(tmp_path):
    ModelConfigStore().save({"provider": "bogus", "model": "x"})
    with pytest.raises(ConfigurationError, match="Unknown AI provider"):
        ModelConfigStore().resolved()


def test_a_custom_base_url_survives_composition(tmp_path):
    """An endpoint matching no preset lands on `custom`, whose catalogue base_url is empty
    — so the URL has to be persisted on the provider entry or it is silently lost."""
    store = ModelConfigStore()
    store.update(
        {"provider": "openai_compatible", "base_url": "http://my-gateway/v1", "model": "m"}
    )
    assert store.resolved()["base_url"] == "http://my-gateway/v1"


def test_the_default_still_resolves_when_its_provider_is_disabled(store):
    """Disabling the ACTIVE default is refused by the PUT guard, so this state is only
    reachable by writing the store directly — resolved() must not start raising for it."""
    store.update_provider("xai", {"enabled": False})
    assert store.resolved()["model"] == "grok-4.5"
