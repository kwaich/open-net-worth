"""Bank/brokerage aggregation — institution search.

The per-provider status/sync tools that used to live here are gone: they collapsed into
``get_connector_status`` / ``connector_sync`` in ``connectors.py``, which carry their former
names as aliases. Setting an access token / credentials remains intentionally HUMAN-ONLY
(a secret, like Telegram's bot token), so there is still no config-writing tool here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of


class SearchInstitutionsInput(ToolInput):
    """Schema-of-record for ``search_institutions`` (see ``schema_from_model``)."""

    country: Optional[str] = Field(
        default=None,
        description=(
            "2-letter ISO country code (e.g. US, GB, DE, FR). Required to list specific "
            "banks for the EU providers."
        ),
    )
    query: Optional[str] = Field(
        default=None,
        description="Optional bank-name search filter (e.g. 'Meridian Bank', 'Beacon Bank').",
    )


@tool(
    "search_institutions",
    description=(
        "Search for a bank / financial institution to link, and find which provider can "
        "connect it. Pass a 2-letter country code (e.g. US, GB, DE) and optionally a name "
        "query. Returns matching banks (where the provider exposes a per-bank list), the "
        "provider-level options for that country (e.g. Plaid for US — the bank is chosen "
        "inside Plaid's hosted flow), and whether manual statement import is always an "
        "option. This is READ-ONLY — it tells the user how to link a bank; the actual "
        "linking (entering credentials, authenticating at the bank) is done by the user in "
        "the UI (Connectors → the provider), never by the assistant."
    ),
    input_schema=schema_of(SearchInstitutionsInput, tool_name="search_institutions"),
)
async def search_institutions(input_data: dict):
    from backend.services import account_linking

    country = (input_data.get("country") or "").strip()
    query = (input_data.get("query") or "").strip()
    result = await asyncio.to_thread(account_linking.search_institutions, query, country)
    return json.dumps(result, ensure_ascii=False), None
