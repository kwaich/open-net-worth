"""Vendored model price map — a maintained fallback for models not hand-pinned.

``pricing.py`` keeps small hand-typed tables that are PINNED and unit-tested (our
default/core models, plus the bleeding-edge ones upstream data lags on). This module
is the fallback consulted ONLY when those tables miss: it reads a filtered copy of
LiteLLM's ``model_prices_and_context_window.json`` (vendored as ``model_prices.json``)
so the long tail of models a user might pick still gets priced from a community-updated
source — WITHOUT a runtime dependency on ``litellm``. We borrow the DATA, not the code.

Refresh the data with ``make refresh-prices`` (``scripts/refresh_model_prices.py``).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PRICE_FILE = Path(__file__).with_name("model_prices.json")

# base_url substring → the provider prefix LiteLLM keys models under, used to build a
# "<prefix>/<model>" candidate key (e.g. api.x.ai + grok-4 → "xai/grok-4").
_BASE_URL_PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("api.x.ai", "xai"),
    ("z.ai", "zai"),
    ("bigmodel.cn", "zai"),
    ("deepseek.com", "deepseek"),
    ("generativelanguage.googleapis.com", "gemini"),
    ("api.mistral.ai", "mistral"),
    ("moonshot", "moonshot"),
    ("api.openai.com", "openai"),
)


@lru_cache(maxsize=1)
def _price_map() -> dict:
    """Load the vendored price map once. Missing/corrupt → empty (fallback disabled)."""
    try:
        with _PRICE_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "vendored price map unreadable (%s) — long-tail pricing disabled",
            type(exc).__name__,
        )
        return {}


def _provider_prefix_for(base_url: str) -> Optional[str]:
    u = (base_url or "").lower()
    for sub, prefix in _BASE_URL_PROVIDER_HINTS:
        if sub in u:
            return prefix
    return None


def _candidate_keys(model: str, base_url: str) -> list[str]:
    """Key forms to try against the map, most-specific first."""
    keys = [model]
    prefix = _provider_prefix_for(base_url)
    if prefix:
        keys.append(f"{prefix}/{model}")
    # A model that already carries a provider prefix → also try the bare tail.
    if "/" in model:
        keys.append(model.split("/", 1)[1])
    # De-dupe while preserving order (dict keys keep insertion order).
    return list(dict.fromkeys(keys))


def vendored_capabilities(model: str, base_url: str = "") -> Optional[dict]:
    """Capability fields for a model from the vendored map, or None if not found.

    Returns the entry subset {supports_function_calling?, max_input_tokens?} — an
    absent key means "unknown", which callers treat as permissive for curated models
    (the vendored data is an oracle, not a gatekeeper).
    """
    if not model:
        return None
    mapping = _price_map()
    if not mapping:
        return None
    for key in _candidate_keys(model, base_url):
        entry = mapping.get(key)
        if isinstance(entry, dict):
            return {
                k: entry[k] for k in ("supports_function_calling", "max_input_tokens") if k in entry
            }
    return None


def max_input_tokens(model: str, base_url: str = "", fallback: int = 200_000) -> int:
    """Return the vendored context window, falling back to Claude's 200k baseline."""
    capabilities = vendored_capabilities(model, base_url)
    value = capabilities.get("max_input_tokens") if capabilities else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value)
    return fallback


def vendored_rates(model: str, base_url: str = "") -> Optional[tuple[float, float]]:
    """(input_$/Mtok, output_$/Mtok) from the vendored map, or None if not found.

    Converts the upstream PER-TOKEN costs to per-million so the result plugs straight
    into pricing.py's per-Mtok formula. Rows with non-numeric costs are skipped.
    """
    if not model:
        return None
    mapping = _price_map()
    if not mapping:
        return None
    for key in _candidate_keys(model, base_url):
        entry = mapping.get(key)
        if not isinstance(entry, dict):
            continue
        inp = entry.get("input_cost_per_token")
        out = entry.get("output_cost_per_token")
        if isinstance(inp, (int, float)) and isinstance(out, (int, float)):
            return inp * 1_000_000, out * 1_000_000
    return None
