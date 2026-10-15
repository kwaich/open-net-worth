"""Descriptor definitions package — auto-collect seam for all entity types.

Each submodule calls ``_collect.register(EntityDescriptor(...))`` at import time.
Importing this package fires all registrations; ``_collect.build_registry()``
then wraps them into ``MentionProvider`` objects sorted by ``descriptor.order``.

Submodule grouping follows RFC §4.2's 4-class type partition:
  singletons    — (d) synthesized / no store: net_worth, category, budget_month,
                  allocation_slice, returns, envelope (order 25)
  financials    — (b) service-hydrated: holding, instrument, account, transaction
  store_backed  — (a) clean store-backed: payee, watchlist, rule, scheduled_task,
                  target
  multi_surface — (c) multi-surface: agent, skill
  new_types     — (a) Phase 2a generic-provider types: commitment, intel_signal,
                  alert (orders 16–18)
  equity_lots   — (a) Phase 2b generic-provider types: income, lot, grant, vest
                  (orders 19–22)
  connector     — (a) generic-provider type over the connectors SERVICE (order 23)
  report        — (a) generic-provider TYPE CATALOG over services/reports (order 24)
  vault         — (a) generic-provider type over the Vault read model (order 36)
"""

from backend.services.mentions.descriptors import (  # noqa: F401
    commitment_parts,
    connector,
    equity_lots,
    financials,
    holdings_extra,
    multi_surface,
    new_types,
    report,
    singletons,
    store_backed,
    vault,
)
