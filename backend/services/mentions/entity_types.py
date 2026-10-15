"""The entity-type registry as the client renders it — the ONE wire shape.

Served two ways, both from this function: ``GET /api/entity-types`` and embedded in the
SPA's ``index.html`` (``backend/main.py``) so chips have their icon/label/tone from the
first frame, before any request — and before login. There is no frontend copy: a
build-time one drifted silently from these descriptors for months (ADR
``2026-10-05-entity-types-ship-with-the-page-not-the-bundle``).

Shape per entry: ``{type, label, icon, tone, field_schema: [{key, label, type, sensitive,
showInPopover}]}``. Schema and display metadata only — no values, no user data, which is
what makes embedding it in the unauthenticated page shell safe.
"""

from __future__ import annotations

from backend.services.mentions.registry import DESCRIPTORS


def entity_types() -> list[dict]:
    """Every registered mention type in REGISTRY order (``descriptor.order``)."""
    return [
        {
            "type": d.type,
            "label": d.label,
            "icon": d.icon,
            "tone": d.tone,
            "field_schema": [
                {
                    "key": key,
                    "label": spec.label,
                    "type": spec.type,
                    "sensitive": spec.sensitive,
                    "showInPopover": spec.show_in_popover,
                }
                for key, spec in d.fields.items()
            ],
        }
        for d in DESCRIPTORS.values()
    ]
