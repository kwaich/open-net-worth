"""Dashboard-layout business logic — the Overview widget grid's server-side persistence.

Deliberately thin and dumb: this service validates SHAPE only (a view has a name and
a widget array; a widget carries either a ``defId`` or ``kind: "section"``, plus the
grid geometry). It knows nothing about ``WIDGET_DEFINITIONS``, chart types, or any
per-widget data — that is 100% client-side per the handoff's "position/size/config
only, never a derived figure" guidance (see ADR 2026-07-22-overview-tab-redesign-
faithful-port.md). Rejecting an unknown ``defId`` would require this layer to know
the client's registry, which would immediately drift; an unrecognized widget is the
client's problem to render as a "no longer available" placeholder, not this layer's
to refuse.
"""

from __future__ import annotations

import math

from backend.core.errors import ValidationError
from backend.db.dashboard_layout_store import DashboardLayoutStore

_store = DashboardLayoutStore()

_MAX_NAME_LEN = 60
_MAX_TITLE_LEN = 120
_MAX_GRID_COLS = 12
_MAX_Y_OR_H = 100_000


def _validate_geometry(w: dict, idx: int, key: str) -> None:
    value = w.get(key)
    # bool is an int subclass in Python — isinstance(True, int) is True — so it must be
    # excluded explicitly, or True/False would silently pass as 1/0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"widgets[{idx}].{key} must be a number")
    if not math.isfinite(value):
        raise ValidationError(f"widgets[{idx}].{key} must be a finite number")
    if key in ("x", "y") and value < 0:
        raise ValidationError(f"widgets[{idx}].{key} must be >= 0")
    if key in ("w", "h") and value <= 0:
        raise ValidationError(f"widgets[{idx}].{key} must be > 0")
    if key == "w" and value > _MAX_GRID_COLS:
        raise ValidationError(f"widgets[{idx}].w must be <= {_MAX_GRID_COLS}")
    if key == "x" and value >= _MAX_GRID_COLS:
        raise ValidationError(f"widgets[{idx}].x must be < {_MAX_GRID_COLS}")
    if key in ("y", "h") and value > _MAX_Y_OR_H:
        raise ValidationError(f"widgets[{idx}].{key} must be <= {_MAX_Y_OR_H}")


def _validate_widget(w: dict, idx: int) -> None:
    if not isinstance(w, dict):
        raise ValidationError(f"widgets[{idx}] must be an object")
    is_section = w.get("kind") == "section"
    if not is_section:
        if not isinstance(w.get("defId"), str) or not w["defId"].strip():
            raise ValidationError(f"widgets[{idx}].defId is required (or kind: 'section')")
    # Geometry is checked for every row, section or widget — the client's
    # normalize()/compact() trusts x/y/w/h are numbers on every GridItem,
    # section dividers included.
    for key in ("x", "y", "w", "h"):
        _validate_geometry(w, idx, key)
    if is_section:
        return
    title = w.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > _MAX_TITLE_LEN):
        raise ValidationError(f"widgets[{idx}].title must be a string ≤ {_MAX_TITLE_LEN} chars")


def _validate_view(v: dict, idx: int) -> None:
    if not isinstance(v, dict):
        raise ValidationError(f"views[{idx}] must be an object")
    name = v.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"views[{idx}].name is required")
    if len(name) > _MAX_NAME_LEN:
        raise ValidationError(f"views[{idx}].name must be ≤ {_MAX_NAME_LEN} chars")
    widgets = v.get("widgets")
    if not isinstance(widgets, list):
        raise ValidationError(f"views[{idx}].widgets must be an array")
    seen_instance_ids: set = set()
    for i, w in enumerate(widgets):
        _validate_widget(w, i)
        instance_id = w.get("instanceId")
        if instance_id is not None:
            if instance_id in seen_instance_ids:
                raise ValidationError(
                    f"widgets[{i}].instanceId duplicates another widget in this view: "
                    f"{instance_id!r}"
                )
            seen_instance_ids.add(instance_id)


def get_layout() -> dict:
    return _store.get_state()


def save_layout(active_view_id: str | None, views: list) -> dict:
    if not isinstance(views, list):
        raise ValidationError("views must be an array")
    for i, v in enumerate(views):
        _validate_view(v, i)
    return _store.save_state(active_view_id, views)
