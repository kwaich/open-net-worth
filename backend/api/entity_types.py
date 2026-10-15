"""GET /api/entity-types — entity descriptor registry endpoint.

Returns one entry per registered mention type with the metadata the client
needs to render chips, popovers, and the autocomplete picker without
per-type branching. The same payload is embedded in the SPA's index.html;
``services.mentions.entity_types`` owns the shape.

RFC 2026-06-25-lucius-generic-entity-registry §1b (backend half).
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.services.mentions.entity_types import entity_types

router = APIRouter()


@router.get("/api/entity-types")
def get_entity_types() -> list[dict]:
    """``[{type, label, icon, tone, field_schema: [...]}]`` — schema/metadata only."""
    return entity_types()
