"""Seed the Overview tab's saved views.

The Overview is the first screen a demo visitor lands on, and it renders whatever the saved
layout holds. An empty layout gets an "add a widget" prompt — the worst possible first
impression for a book with two and a half years of data behind it.

**Geometry is stored, not left to the client.** The frontend's grid places every tile with
``left: calc(x/12*100%)`` / ``width: calc(w/12*100%)``, so a widget saved without ``x/y/w/h``
computes ``calc(NaN%)`` and collapses to content width, with every tile stacked at one point.
Seeding ``{defId}`` alone did exactly that (2026-08-23). The API's own PUT validator refuses
that shape too, so a geometry-less layout is not merely fragile — it is not a layout the app
considers valid.

The numbers in ``dashboard_layout.json`` are not hand-typed: they were produced by running the
frontend's own ``grid.normalize()`` over these widget lists, so they are exactly the placement
the app would compute. ``useDashboardLayout.partialLayout.test.ts`` re-derives them from the
live widget definitions and fails if a definition's default size drifts away from what is
stored here — which is what keeps a baked snapshot honest.
"""

from __future__ import annotations

import json
from pathlib import Path

LAYOUT_FILE = Path(__file__).with_name("dashboard_layout.json")


def views() -> list[dict]:
    """The saved views, with full geometry on every widget."""
    raw = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
    return [
        {
            "name": view["name"],
            "widgets": [
                {
                    "defId": w["defId"],
                    "x": w["x"],
                    "y": w["y"],
                    "w": w["w"],
                    "h": w["h"],
                    "config": {},
                }
                for w in view["widgets"]
            ],
        }
        for view in raw
    ]


def seed_views() -> int:
    """Write the saved views. Returns how many were written."""
    from backend.db.dashboard_layout_store import DashboardLayoutStore

    saved = DashboardLayoutStore().save_state(None, views())
    return len(saved.get("views") or [])
