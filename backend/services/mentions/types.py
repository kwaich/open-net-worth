"""Shared types for the mention system — leaf module imported by registry, providers, and
the search engine.  No imports from anywhere else in backend.services.mentions (avoids cycles)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Single source of truth for type → icon mapping.  The "◆" fallback is applied by callers
# via ``_ICONS.get(type_, "◆")``.
ICONS: dict[str, str] = {
    "net_worth": "▲",
    "holding": "◈",
    "instrument": "◈",
    "account": "⬡",
    "transaction": "↕",
    "category": "▣",
    "envelope": "◫",  # budget envelope — the Plan row identity (envelope⊥category split)
    "budget_month": "▣",
    "target": "◎",
    "allocation_slice": "◭",
    "returns": "↗",
    "payee": "◉",
    "watchlist": "☆",
    "automation": "◷",
    "agent": "✦",  # Lucius/AI glyph — the Talk-to (responder) + agent-as-entity surface
    "skill": "⚡",  # invokable verb (/ trigger) + talk-about entity
    # Phase-2a generic descriptors (new_types.py) — distinct glyphs to avoid picker confusion
    "commitment": "⊙",
    "notification": "◮",
    "alert": "⚑",
    # Phase-2b generic descriptors (equity_lots.py) — investment money facts
    "income": "⥃",
    "lot": "▭",
    "disposal": "▱",
    "grant": "❖",
    "vest": "⟠",
    # connector descriptor (connector.py) — integrations via the connectors service
    "connector": "⇄",
    # report descriptor (report.py) — the fixed report-type catalog
    "report": "▦",
}


@dataclass(frozen=True)
class SearchQuery:
    q: str
    types: frozenset[str] | None
    surfaces: frozenset[str] | None
    limit: int
    cursor: str | None


@dataclass(frozen=True)
class MentionProvider:
    type: str
    surfaces: frozenset[str]
    icon: str
    resolve: Callable[[str], dict | None]
    search: Callable[[SearchQuery], list[dict]] | None = None
