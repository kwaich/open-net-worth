"""Screen-aware context schemas — the structured snapshot + handle manifest a frontend screen
attaches to a chat turn so Lucius knows what the user is looking at.

Design: ``docs/superpowers/specs/2026-06-24-screen-aware-context-design.md``. A leaf beside the
other ``models/`` schemas (pydantic + stdlib only — no internal imports).

Two layers, by cost:
- ``ScreenSnapshot`` is CHEAP and rides every turn (title, view-state, derived facts, a bounded
  visible-text fallback) — the "what am I looking at" context.
- ``ScreenHandle`` is a POINTER to deeper content shown only as a summary/visual (a news article,
  a chart's series, off-screen rows). Its body is NOT sent; the model fetches it on demand via the
  ``get_screen_detail`` tool, which decodes ``source`` server-side.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ScreenHandle(BaseModel):
    """A lazily-fetchable pointer to deeper content visible on the current screen."""

    id: str  # unique within the snapshot, e.g. "news-0" / "price-1y"
    type: str = ""  # news_story | price_series | table | ... (hint for the hydrator + UI)
    label: str = ""  # human label, e.g. "Reuters — Oracle Q3 beats estimates"
    descriptor: str = ""  # one-line hint, e.g. "full article" / "252 daily closes"
    # Server-resolvable source the hydrator decodes: an http(s) URL (news article, SSRF-guarded)
    # or an opaque selector like "price_series:<instrument_id>:<period>". Empty → not fetchable
    # (the hydrator echoes the descriptor so the model still gets the gist).
    source: Optional[str] = None
    # The user tapped "+ attach" in the transparency panel → fetch this now (vs leaving it for the
    # model to fetch on demand only if it decides it needs it). Surfaced in the context block.
    pinned: bool = False


class ScreenCommandParam(BaseModel):
    """One arg in a screen command's compact param spec (not full JSON Schema)."""

    name: str
    type: str = "string"  # string | number | boolean | array | object
    required: bool = False
    enum: Optional[list[str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    # Compact item-shape hint for arrays, rendered as `name:array<hint>` in the prose
    # manifest (e.g. "{label, amount, age}"). Display-only — the command DESCRIPTION
    # truncates at 160 chars, so item shapes MUST ride here to reach the model (a live
    # repro had the model inventing `note`/`type` fields for add_events without it).
    items: Optional[str] = None


class ScreenCommand(BaseModel):
    """A drivable action a screen registered — ephemeral UI state only, never a store write.

    ``route`` is a SYMBOLIC key the frontend maps to a real path for the parked-command
    navigation chip (never a URL — the model cannot steer navigation anywhere else).
    ``live`` is True when the owning screen is visible this turn; False = catalog entry
    (invoking parks the command and the reply carries the chip).
    """

    name: str
    description: str = ""
    params: list[ScreenCommandParam] = Field(default_factory=list)
    route: str = ""
    live: bool = False


class ScreenSnapshot(BaseModel):
    """What the user is currently looking at — cheap structured context, attached every turn."""

    id: str = ""  # screen/route id, e.g. "asset-detail" / "cash"
    title: str = ""  # e.g. "ORCL — Oracle Corporation"
    view_state: dict = Field(default_factory=dict)  # active tab / range / selection / filters
    facts: dict = Field(default_factory=dict)  # key derived figures (bounded by the formatter)
    visible_text: str = ""  # bounded free-text fallback for the long tail
    handles: list[ScreenHandle] = Field(default_factory=list)  # deep-content pointers (metadata)
    commands: list[ScreenCommand] = Field(default_factory=list)  # drivable-action manifest
    as_of: Optional[str] = None  # ISO capture time (freshness label)
