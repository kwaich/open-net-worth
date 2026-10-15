from __future__ import annotations

from backend.ai.search.base import SearchProvider, build_provider


def get_search_provider() -> SearchProvider:
    """Resolve the ``SearchProvider`` the ``web_search`` / ``fetch_page`` tools should use.

    Web search is a set of first-class **per-provider connectors** (the Tools family). Each
    provider has its OWN enable toggle + (for keyed providers) a write-only API key in
    ``SearchConfigStore`` (the ``web_search:<id>`` connector rows).
    MULTIPLE may be enabled; the tools use exactly ONE at a time — the store's
    ``default_provider`` precedence/fallback chain (the single source of truth):

    1. the explicit ``default_provider`` selection, IF it's enabled + usable;
    2. else the first ENABLED **keyed** provider with a key, in catalog order (a real paid
       API the user configured beats the key-free default);
    3. else the **key-free fallback** (DuckDuckGo) — enabled-by-default, no key, so a fresh
       instance with zero configuration STILL searches (the "enabled-but-not-active provider
       silently never runs" config trap is gone, and search works out of the box);
    4. else (only if even the key-free fallback is disabled) raise ``ValueError``.

    A new provider = a new ``<id>_provider.py`` + one ``PROVIDER_REGISTRY`` entry (in
    ``ai/search/base.py``) + a matching ``SearchConfigStore.PROVIDER_CATALOG`` entry; no edit
    here.

    The ``web_search`` / ``fetch_page`` tools catch a raised ``ValueError`` → a clean "search
    not configured" message, never a 500.
    """
    from backend.db.search_config_store import SearchConfigStore

    store = SearchConfigStore()
    provider_id = store.default_provider()
    if not provider_id:
        raise ValueError("Web search is not set up. Connect a provider in Connectors → Web.")

    cfg = store.provider_runtime_config(provider_id)  # {api_key, <extra fields>}
    # A keyed provider must actually have a key (default_provider already filters for usable,
    # but be defensive). A key-free provider (DuckDuckGo) has no key by design.
    if store._needs_key(provider_id) and not cfg.get("api_key"):
        raise ValueError(f"Web search provider {provider_id!r} has no API key configured.")

    return build_provider(provider_id, cfg)
