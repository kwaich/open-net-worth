"""The two enforcement points for a tool's ``connector=`` tag: DISCOVERY and OWNERSHIP.

Shared by ``ai.tools.catalog`` (the flat system-prompt catalog) and ``ai.tools._groups``
(``find_tools``' query-search and exact-name load) so the two surfaces stay in lockstep
and the live-enablement lookup runs ONCE per catalog build / ``find_tools`` call, never
once per tag. See ``registry.tool``'s ``connector=`` docstring and RFC ``.claude/rfcs/
2026-08-10-connectors-architecture.md`` §B2 for the design this implements.

``hidden_connector_tools()`` is DISCOVERY only for an ordinarily-tagged connector — this
hides a name from the offer/search surface, never from dispatch. An internal (untagged)
tool, or a connector-tagged tool whose connector is enabled, is never touched here.

**One exception: ``DISABLED_CONNECTORS`` below.** A connector withdrawn from the product
by POLICY (not merely "not yet configured") is hidden from discovery UNCONDITIONALLY —
even if its store somehow reports ``enabled: true`` — AND its tools are REFUSED outright
at dispatch (``registry.dispatch`` consults the same frozenset), unlike every other
connector-tagged tool, which stays reachable by exact name per the paragraph above. See
ADR ``2026-08-14-hide-yodlee-enterprise-only-pricing``.

``permission_owner()`` is a separate concern, added 2026-08-14: the tag is also the JOIN
onto the permission table (``services.tool_permissions``), which is what makes a level set
in the Connectors tab actually gate the tool. Untagged tools are not exempt — they are
owned by the reserved ``core`` bucket, which is where the absorbed ``tool_grants`` rows
landed. Whether a given call then fires is ``_confirm_gate.effective_level``'s answer, not
this module's; this one only says WHOSE decision applies — a DISABLED connector's tools
still resolve to a real owner here (permission-table bookkeeping is unaffected), they are
simply never reached, since ``DISABLED_CONNECTORS`` refuses them in ``registry.dispatch``
before ``effective_level`` is ever consulted.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.services.connector_catalog import CATALOG

logger = logging.getLogger(__name__)

#: Connectors withdrawn from the product by policy — hidden from discovery
#: unconditionally and refused at dispatch (``registry.dispatch``), not merely absent from
#: the "not yet enabled" set below. Yodlee: enterprise-only pricing with no public
#: self-serve tier makes it impractical for this app's actual self-hosted users. The
#: underlying code (store/service/API/UI section) is untouched — ``disabled=True`` on the
#: kind in ``services.connector_catalog`` is the one place to flip it back on. ADR
#: ``2026-08-14-hide-yodlee-enterprise-only-pricing``.
DISABLED_CONNECTORS: frozenset[str] = frozenset(k for k, v in CATALOG.items() if v.disabled)


def permission_owner(tool_name: str) -> str:
    """The permission-table owner for ``tool_name`` — its ``connector=`` id, or ``core``.

    The composite key is what stops a decision leaking sideways between providers.
    ``ai/tools/connectors.py`` records the live incident behind that: a single shared
    ``connector_sync`` name meant one name-keyed grant, so "always allow Plaid syncs"
    would silently have allowed IBKR too. Per-provider tools plus a per-owner key make
    that structurally impossible — ``plaid_sync_now`` is ``plaid:plaid_sync_now`` and
    ``ibkr_sync_now`` is ``ibkr:ibkr_sync_now``, two rows that cannot be one decision.

    Aliases resolve first, so a decision is keyed on the CANONICAL name exactly like the
    grants this replaced (a permission deliberately does not survive a rename — RFC
    ``2026-07-20`` §15.1 — because a rename here is frequently a semantic change).

    External-MCP tools never reach this: they are dispatched outside the registry and
    carry their own ``mcp:<server_id>`` owner (``mcp.client_manager``).
    """
    from backend.ai.tools.registry import tool_connector
    from backend.services.tool_permissions import CORE_CONNECTOR_ID

    canonical = tool_name
    owner: Optional[str] = tool_connector(canonical)
    return owner or CORE_CONNECTOR_ID


def hidden_connector_tools() -> frozenset[str]:
    """Every tool name currently hidden from discovery: it carries a ``connector=`` tag
    AND (that connector is not (yet) enabled OR that connector is in
    ``DISABLED_CONNECTORS``).

    Fails CLOSED on any lookup error — hides every connector-tagged tool rather than
    guessing — matching this module's sibling gates (``catalog._external_sections``
    returns nothing on a broken external-MCP manager; ``_groups._is_interactive_scope``
    returns False on any uncertainty). An enablement lookup failing must never WIDEN what
    gets offered; it may only narrow it further than intended, which is the safe
    direction — every connector-tagged tool is still reachable by exact name once the
    underlying issue is fixed, same as any other discovery-gated tool today (except a
    ``DISABLED_CONNECTORS`` member, which is never reachable by name — see
    ``registry.dispatch``).
    """
    from backend.ai.tools.registry import connector_tagged_tools

    tagged = connector_tagged_tools()
    if not tagged:
        return frozenset()
    try:
        from backend.services.connectors import enabled_connector_ids

        enabled = enabled_connector_ids() - DISABLED_CONNECTORS
    except Exception:
        logger.warning(
            "connector discovery gate: enablement lookup failed; hiding every "
            "connector-tagged tool this pass",
            exc_info=True,
        )
        enabled = frozenset()
    return frozenset(name for name, connector_id in tagged.items() if connector_id not in enabled)
