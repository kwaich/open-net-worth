"""The provider catalog's ``model_hints``: what Add-provider offers before a key exists, and —
for the FIRST entry — what first run prefills as a new user's default model.

They are hand-typed and rotted twice in six weeks (2026-08-24 refreshed a list two generations
behind; by 2026-10-04 it was a family behind again — and one hint, `gpt-5-pro`, is
Responses-API-only, so choosing it would fail on the /v1/chat/completions call this provider
makes). These pin what can be pinned without a network: every hint is a model the vendored price
map knows, and the first one is a sensible default.
"""

from __future__ import annotations

import re

import pytest

from backend.ai.models.price_map import vendored_capabilities
from backend.db.model_config_store import PROVIDER_CATALOG

# Providers whose model ids live in the vendored price map. The CLIs use aliases ("sonnet"),
# Ollama / LiteLLM / custom list local or user-defined names, and OpenRouter ids are
# "vendor/model" routed ids the map keys differently.
PRICED = ("anthropic", "openai", "gemini", "glm", "xai")


def _entries(keys):
    return [e for e in PROVIDER_CATALOG if e["key"] in keys]


@pytest.mark.parametrize("entry", _entries(PRICED), ids=lambda e: e["key"])
def test_every_hint_is_a_model_the_price_map_knows(entry):
    unknown = [
        h for h in entry["model_hints"] if vendored_capabilities(h, entry["base_url"]) is None
    ]

    assert not unknown, (
        f"{entry['key']}: not in ai/models/model_prices.json (retired, mistyped, or the map is "
        f"behind — `make refresh-prices`): {unknown}"
    )


@pytest.mark.parametrize("entry", PROVIDER_CATALOG, ids=lambda e: e["key"])
def test_the_first_hint_is_never_a_preview_model(entry):
    # First run prefills hints[0] as the new user's default model.
    hints = entry["model_hints"]
    if hints:
        assert "preview" not in hints[0], f"{entry['key']}: first run would default to a preview"


def test_openai_hints_are_all_chat_completions_models():
    # This provider only calls /v1/chat/completions. OpenAI's "-pro" models are served by the
    # Responses API alone, so offering one hands the user a model that cannot answer.
    hints = next(e for e in PROVIDER_CATALOG if e["key"] == "openai")["model_hints"]

    assert not [h for h in hints if re.search(r"-pro($|-)", h)]
