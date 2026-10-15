"""Web Search connector REST — per-provider web-search control (Tools family).

Thin router over ``db.SearchConfigStore``. Web search is a set of per-provider connectors —
each (tavily | perplexity | serper | google_pse) has its OWN enable toggle + write-only API
key. Multiple may be enabled; the tools use ONE at a time (the
``default_provider`` selection).

  * ``GET /api/search/config`` — the public config: every provider with ``enabled`` +
    ``has_key`` + the (non-secret) extra fields + which is active. NEVER returns a key.
  * ``PUT /api/search/config/{provider_id}`` — patch ONE provider: enable/disable, set its
    API key (write-only; blank keeps the saved one), set its extra fields (e.g. Google PSE
    ``cx``). Optional ``set_default`` makes it the provider the tools use.
  * ``PUT /api/search/config`` — set the default provider only (``{default_provider}``).

The ``web_search`` / ``fetch_page`` tools resolve the active provider from this same store
(``ai.search.get_search_provider``), so a UI change takes effect with no restart.

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The API keys are
SECRETS — accepted (write-only) but NEVER returned (the status echoes only ``has_key``).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db.search_config_store import SearchConfigStore

router = APIRouter()


class ProviderConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    # The API key (write-only). Blank/omitted → keep the saved one.
    api_key: Optional[str] = None
    # Non-secret extra config fields (e.g. Google PSE's `cx`); set verbatim, blank clears.
    extra: Optional[dict] = None
    # Make this the default provider the web_search/fetch_page tools use.
    set_default: Optional[bool] = None


class DefaultProviderUpdate(BaseModel):
    default_provider: Optional[str] = None


@router.get("/api/search/config")
def search_config():
    """Public config: every provider (enabled + has_key + extra fields + is_active) + the
    default selection. Never the keys themselves."""
    return SearchConfigStore().public()


@router.put("/api/search/config/{provider_id}")
def update_provider_config(provider_id: str, body: ProviderConfigUpdate):
    """Patch ONE provider's config (enable/disable, write-only key, extra fields). When a
    non-blank key arrives without an explicit ``enabled``, enable it (mirrors the env-seed
    default). ``set_default`` makes it the provider the tools use. Returns the public status."""
    store = SearchConfigStore()
    changes: dict = {}
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)
    if body.api_key is not None:
        changes["api_key"] = body.api_key
        # A freshly-set key means the user wants this provider on (unless they said otherwise).
        if body.enabled is None and str(body.api_key).strip():
            changes["enabled"] = True
    if body.extra:
        changes.update({k: v for k, v in body.extra.items()})

    store.update_provider(provider_id, changes)
    if body.set_default:
        store.set_default(provider_id)
    return store.public()


@router.put("/api/search/config")
def set_default_provider(body: DefaultProviderUpdate):
    """Set which enabled provider the web_search/fetch_page tools use (``default_provider``).
    A blank/unknown value clears the explicit selection (falls back to first-enabled)."""
    store = SearchConfigStore()
    store.set_default(body.default_provider or None)
    return store.public()
