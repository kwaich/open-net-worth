"""Account-linking front-door (P3-LINK §3) — the unified "Add account → search your
institution → we route you to the right provider" router + lens.

This is a **compute-only router**, NOT a sync pipeline. It does three things:
  1. **Search** institutions (by name / country) — federating the providers that expose a
     per-bank list (Enable Banking) plus a small static catalog of provider-level options for
     token/URL-only providers (Plaid, SimpleFIN — the bank is chosen INSIDE the provider's own
     hosted flow).
  2. **Resolve** a chosen institution → a provider id (the row already carries one for the
     per-bank providers; otherwise a country → provider-priority table picks one).
  3. Expose the region→provider **priority map** so the UI can render the right "set up …"
     hand-off.

It opens NO new write path to domain data (the connector-model safety invariant): the actual
link/exchange/sync stay on each provider's existing router. It imports only ``db``/``core``/
sibling aggregation services — never ``api``/``ai`` (Contract 3 stays green). It NEVER raises
on a provider that isn't configured / is unreachable — a provider that can't list banks simply
contributes its provider-level option (or nothing), so the front-door never dead-ends.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Region → ordered provider priority (RFC §3.2). The FIRST entry is the default; where two
# apply the UI may offer both. A small declarative table so adding a provider/region is one
# edit, NOT a UI change. A country absent from this table (e.g. Brazil, uncovered since
# Pluggy was removed) is NOT given a fallback guess — see ``providers_for_country``.
_REGION_PROVIDERS: dict[str, list[str]] = {
    # US / Canada — Plaid (broad) then SimpleFIN (cheap).
    "US": ["plaid", "simplefin"],
    "CA": ["plaid", "simplefin"],
    # EU / UK — Enable Banking.
    "GB": ["enable_banking"],
    "IE": ["enable_banking"],
    "DE": ["enable_banking"],
    "FR": ["enable_banking"],
    "ES": ["enable_banking"],
    "IT": ["enable_banking"],
    "NL": ["enable_banking"],
    "PT": ["enable_banking"],
    "BE": ["enable_banking"],
    "AT": ["enable_banking"],
    "FI": ["enable_banking"],
    "SE": ["enable_banking"],
    "DK": ["enable_banking"],
    "NO": ["enable_banking"],
    "PL": ["enable_banking"],
}

# Provider presentation metadata — the one place a provider's display name + a one-line
# trade-off note live (the UI renders these; no hardcoded provider list in the frontend).
_PROVIDER_META: dict[str, dict] = {
    "plaid": {
        "name": "Plaid",
        "region": "US / Canada",
        "note": "Broad US/Canada coverage. Bank chosen in Plaid's hosted link.",
        "per_bank_list": False,
    },
    "simplefin": {
        "name": "SimpleFIN",
        "region": "US / Canada",
        "note": "Cheap (~$15/yr). Bank chosen via a one-time setup token.",
        "per_bank_list": False,
    },
    "enable_banking": {
        "name": "Enable Banking",
        "region": "EU / UK",
        "note": "EU/UK open banking (PSD2). Pick your bank, then authenticate.",
        "per_bank_list": True,
    },
}


# ── provider configured-state probes (NEVER raise) ───────────────────────────────────


def _enable_banking_has_creds() -> bool:
    try:
        from backend.db.enable_banking_config_store import EnableBankingConfigStore

        return EnableBankingConfigStore().has_credentials()
    except Exception:
        return False


_HAS_CREDS = {
    "enable_banking": _enable_banking_has_creds,
}


def _provider_configured(provider: str) -> bool:
    probe = _HAS_CREDS.get(provider)
    return probe() if probe else False  # token/URL providers configure inside their own flow


# ── region → provider map ────────────────────────────────────────────────────────────


def providers_for_country(country: str) -> list[dict]:
    """The ordered providers that can link a bank in ``country`` (ISO 3166 alpha-2).

    Returns ``[{provider, name, region, note, per_bank_list, configured}]`` in priority order.
    A country outside ``_REGION_PROVIDERS`` (e.g. Brazil, or any country genuinely not yet
    covered) returns an EMPTY list — every remaining provider is strictly regional (US/CA,
    EU/UK), so there is no honest global default to offer. The front-door still never
    dead-ends: the UI renders "no auto-sync provider covers this country yet" plus the
    always-present manual-import exit.
    """
    code = (country or "").strip().upper()
    ordered = _REGION_PROVIDERS.get(code) or []

    out: list[dict] = []
    for provider in ordered:
        meta = _PROVIDER_META.get(provider)
        if not meta:
            continue
        out.append(
            {
                "provider": provider,
                "name": meta["name"],
                "region": meta["region"],
                "note": meta["note"],
                "per_bank_list": meta["per_bank_list"],
                "configured": _provider_configured(provider),
            }
        )
    return out


def provider_map() -> dict[str, list[str]]:
    """The full region→provider priority table (for the UI to render country routing)."""
    return {country: list(providers) for country, providers in _REGION_PROVIDERS.items()}


# ── institution search (federated, never raises) ─────────────────────────────────────


def _search_per_bank(provider: str, country: str, query: str) -> list[dict]:
    """Call one per-bank provider's ``list_institutions`` (best-effort). Returns the matching
    ``{id, name, country, provider, logo}`` rows; an unconfigured / unreachable provider
    contributes nothing (logged, never raised)."""
    if not country:
        return []  # the per-bank providers need a country to list banks
    try:
        if provider == "enable_banking":
            if not _provider_configured("enable_banking"):
                return []
            from backend.services.aggregation import enable_banking as svc

            raw = svc.list_institutions(country)
        else:
            return []
    except Exception as exc:  # auth/transport/bad-country — best-effort, never dead-end
        logger.info("account_linking: %s institution search skipped: %s", provider, exc)
        return []

    q = (query or "").strip().lower()
    rows: list[dict] = []
    for r in raw or []:
        name = r.get("name") or ""
        if q and q not in name.lower():
            continue
        rows.append(
            {
                "id": r.get("id"),
                "name": name,
                "country": (r.get("country") or country).upper(),
                "provider": provider,
                "logo": r.get("logo"),
            }
        )
    return rows


def search_institutions(query: str = "", country: str = "") -> dict:
    """Federate an institution search across the configured providers + the provider-level
    catalog (RFC §3.2).

    Returns ``{institutions, providers, manual_import}``:
      * ``institutions`` — per-bank rows from Enable Banking (when creds + a country are
        present), filtered by ``query``.
      * ``providers`` — the provider-level options for ``country`` (Plaid, SimpleFIN, where
        the bank is chosen inside the hosted flow) — always offered so the user can self-route.
      * ``manual_import`` — always True: the honest "can't auto-sync? import a statement"
        fallback exit so the front-door never dead-ends.

    Compute-only, NEVER raises (a failing provider degrades to no rows).
    """
    code = (country or "").strip().upper()
    institutions: list[dict] = list(_search_per_bank("enable_banking", code, query))

    return {
        "institutions": institutions,
        "providers": providers_for_country(code),
        "manual_import": True,
    }
