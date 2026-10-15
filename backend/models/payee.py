"""Payee schema — a first-class merchant entity (P4-PAYEE phase 1).

A **Payee** is the stable identity behind a merchant that today is only a free-text
``description``. A transaction stores a ``payee_id`` (not the name); the display name
is resolved on read, mirroring how a leg stores ``from_account_id`` and the frontend
resolves the account name. This makes Phase-2 rename/merge a one-entity update with
zero transaction-row rewrites.

``models/`` is a leaf beside ``core/`` (imports only stdlib + pydantic + ``.base``);
the entity logic (resolve/learn/usage) lives in ``services/payee_registry.py`` and the
storage-format CRUD in ``db/payee_store.py``. This module is the shape only.

Design: ``docs/prd/payee-registry.md``, ``.claude/rfcs/phase4/payee-registry.md``.

Phase-1 note: ``autocategorize`` and ``hidden`` are PERSISTED but have NO UI yet — the
register always treats ``autocategorize`` as on and never hides a payee. They exist now
so the data model is stable when Phase 2 wires their toggles (the "Manage Payees" view).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field

from .base import ONBaseModel


class Payee(ONBaseModel):
    """One merchant entity. ``id`` is the stable anchor stored on transactions."""

    id: str
    name: str = ""  # display name ("Anthropic"); the most-recent raw description at mint
    # Per-kind auto-applied default category (owner decision 2026-07-10): a merchant's
    # default is direction-specific, so a payee holds one default PER transaction kind
    # ({expense|income|transfer|investment} → category id). The create choke applies the
    # default for the transaction's OWN kind — killing the cross-kind bleed where an
    # expense category leaked onto income/transfer/investment. Adjustment never appears.
    default_categories: Dict[str, str] = Field(default_factory=dict)
    autocategorize: bool = True  # apply defaults on new txns (Phase-1: always on)
    hidden: bool = False  # suppress from autocomplete (Phase-1: stored, unused)
    aliases: List[str] = Field(default_factory=list)  # normalized strings → this payee
    last_used: Optional[str] = None  # ISO date of most-recent use (ranking signal)
    created_at: Optional[str] = None
