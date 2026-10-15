"""Effective API-key resolution for AI providers.

Settings-stored keys (model_config.json providers map) are the source of truth;
the provider env var is a legacy fallback for env-configured deployments. Kept
beside the store (db layer) so services/monitoring/api can all import it without
violating the dependency rule.
"""

from __future__ import annotations

import os
from typing import Optional

_ENV_VAR_BY_CATALOG = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",  # primary; GOOGLE_API_KEY accepted as secondary
}

# Secondary env-var fallbacks: tried only when the primary env var is absent.
_ENV_VAR_SECONDARY: dict[str, str] = {
    "gemini": "GOOGLE_API_KEY",
}

# Providers with no ENV-var key fallback: a stored key (if any) still wins above,
# but there is no environment variable to fall back to.  "ollama" is local compute;
# "custom" is the generic OpenAI-compatible catalog row whose endpoint the user
# supplies (a stored key is used when present; keyless local endpoints work too).
_NO_KEY_PROVIDERS = {"ollama", "custom"}


def effective_api_key(provider_key: str = "anthropic") -> Optional[str]:
    from backend.db.model_config_store import ModelConfigStore

    stored = ModelConfigStore().read_stored_api_key(provider_key)
    if stored:
        return stored
    # Local providers never have an env-var key — None is expected, not missing.
    if provider_key in _NO_KEY_PROVIDERS:
        return None
    env_var = _ENV_VAR_BY_CATALOG.get(provider_key)
    if env_var:
        val = (os.getenv(env_var) or "").strip()
        if val:
            return val
    # Secondary fallback (e.g. GOOGLE_API_KEY as backup for gemini).
    secondary = _ENV_VAR_SECONDARY.get(provider_key)
    if secondary:
        return (os.getenv(secondary) or "").strip() or None
    return None
