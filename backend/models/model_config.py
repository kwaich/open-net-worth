"""Pydantic schemas for the runtime AI model/provider config.

The generic AI-provider seam made first-class: a self-hoster can switch provider
+ model (including a local OpenAI-compatible endpoint — Ollama / LM Studio /
OpenRouter / vLLM) from the running instance, without an env edit + restart.

``models/`` is a leaf beside ``core/`` (RFC P1-A3 §3): stdlib + pydantic +
``backend.core`` only. These types describe the request/response shapes; the
storage + resolution logic lives in ``db/model_config_store.py`` and
``ai/assistant.get_provider``.

The ``api_key`` is WRITE-ONLY — accepted on ``PUT`` but NEVER returned. The
public view exposes only ``has_api_key``, mirroring ``EmailConfigStore.public()``.
"""

from __future__ import annotations

from typing import Optional

from backend.models.base import ONBaseModel

# The provider kinds the seam supports. ``anthropic`` = the Claude SDK path;
# ``openai_compatible`` = the OpenAI Python SDK pointed at ANY base_url (the local
# / OpenRouter / self-hosted enabler); ``claude_cli`` = the locally-installed Claude
# Code headless CLI (``claude -p``), which spends the user's existing Claude
# subscription instead of API credits (RFC 2026-07-22-claude-code-cli-provider);
# ``codex_cli`` = the same idea for OpenAI's Codex CLI (``codex exec``), spending a
# ChatGPT plan (RFC 2026-07-22-codex-cli-provider); ``antigravity_cli`` = the locally-installed
# Google Antigravity CLI (``agy -p``).
VALID_PROVIDERS = ("anthropic", "openai_compatible", "claude_cli", "codex_cli", "antigravity_cli")


class ModelConfigUpdate(ONBaseModel):
    """A partial patch from ``PUT /api/model-config``.

    Every field is optional — only the supplied ones are persisted. A blank/omitted
    ``api_key`` leaves the saved key unchanged (same semantics as the IMAP password).
    """

    # The canonical form: ONE resolvable "<provider_key>:<model_id>" ref. The
    # provider/model/base_url triple below is still accepted (env seeding, Telegram, older
    # clients) and COMPOSED into it by the store — never stored as three fields.
    default_model: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ModelConfigPublic(ONBaseModel):
    """The non-secret config view returned by ``GET``/``PUT /api/model-config``.

    Note: NO ``api_key`` field — only ``has_api_key``. The key is write-only.
    """

    provider: str
    model: str
    base_url: str
    has_api_key: bool
