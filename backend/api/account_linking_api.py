"""Account-linking front-door REST (P3-LINK §3.3) — the unified "Add account → search your
institution" router.

Thin over ``services.account_linking`` (compute-only — it federates the providers' bank lists
+ a region→provider map and routes the user to the right provider's EXISTING link flow; it
opens NO write path to domain data). The actual link/exchange/sync stay on each provider's
own router (``/api/{plaid,simplefin,enable-banking}/…``).

  * ``GET /api/account-linking/institutions?q=&country=`` — federated institution search +
    the provider-level options for the country + the manual-import fallback flag.
  * ``GET /api/account-linking/providers`` — the region→provider priority map (for the UI).

Auth is the app-wide ``require_auth`` dependency. NEVER 500s — the service degrades a failing
provider to no rows.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from backend.services import account_linking

router = APIRouter()


@router.get("/api/account-linking/institutions")
async def account_linking_institutions(q: str = "", country: str = ""):
    """Search institutions across the configured providers + the provider-level catalog.
    Returns ``{institutions, providers, manual_import}``. Blocking provider httpx (per-bank
    lists) runs off the loop. Never raises."""
    return await asyncio.to_thread(account_linking.search_institutions, q, country)


@router.get("/api/account-linking/providers")
def account_linking_providers(country: str = ""):
    """The region→provider priority map (full table) + — when ``country`` is given — the
    ordered providers that can link a bank there."""
    out: dict = {"map": account_linking.provider_map()}
    if (country or "").strip():
        out["providers"] = account_linking.providers_for_country(country)
    return out
