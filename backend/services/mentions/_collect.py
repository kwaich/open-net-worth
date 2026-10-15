"""Auto-collect seam for EntityDescriptor registrations.

RFC 2026-06-25-lucius-generic-entity-registry §4.4.

Mirrors the @tool registry convention: descriptor definitions call ``register(d)``
at import time; ``build_registry()`` wraps each into a ``MentionProvider`` sorted
by ``d.order`` and returns the ordered dict.

REGISTRY in registry.py still uses the existing ``_entity(...)`` calls so Phase 0
is inert — this seam is wired but collects no descriptors yet.
"""

from __future__ import annotations

from backend.core.errors import MentionError
from backend.core.mention_guard import (
    MENTION_FORBIDDEN_STORES,
    check_descriptor_sources,
    check_fields_keys,
)
from backend.services.mentions.descriptor import EntityDescriptor
from backend.services.mentions.generic import make_resolve, make_search
from backend.services.mentions.types import MentionProvider

_DESCRIPTORS: list[EntityDescriptor] = []
_REGISTERED_TYPES: set[str] = set()


def register(
    d: EntityDescriptor,
    forbidden_sources: frozenset[str] = MENTION_FORBIDDEN_STORES,
) -> None:
    """Register a descriptor, running all fail-closed security checks first.

    Raises ``MentionError`` on:
    - Duplicate type (each type may be registered exactly once).
    - A ``fields`` key matching the secret key denylist.
    - A callable (``rows`` / ``find_one`` / ``project_fn``) referencing a forbidden store.
    """
    if d.type in _REGISTERED_TYPES:
        raise MentionError(f"Duplicate descriptor registration for type '{d.type}'")
    # RFC R12: the generic search loop is single-surface; a multi-surface type (e.g. agent on
    # {entity,agent}, skill on {entity,verb}) MUST supply its own search_override or its non-entity
    # surface silently vanishes. Fail-closed at registration so a future multi-surface type can't
    # regress this quietly.
    if len(d.surfaces) > 1 and d.search_override is None:
        raise MentionError(
            f"Multi-surface descriptor '{d.type}' (surfaces={sorted(d.surfaces)}) must set "
            "search_override — the generic search is single-surface"
        )
    check_fields_keys(d.type, d.fields)
    check_descriptor_sources(d.type, d.rows, d.find_one, d.project_fn, forbidden_sources)
    _DESCRIPTORS.append(d)
    _REGISTERED_TYPES.add(d.type)


def build_descriptor_map() -> dict[str, EntityDescriptor]:
    """type → the registered descriptor itself, in the same ``order`` sequence as
    ``build_registry``.

    ``MentionProvider`` deliberately exposes only resolve/search; the write-receipt chip
    needs the descriptor's own ``rows`` source and ``label_field`` to turn a bare
    ``(type, id)`` pair into a labelled chip, and re-deriving those at the call site is
    exactly the per-type guessing this registry exists to prevent."""
    return {d.type: d for d in sorted(_DESCRIPTORS, key=lambda d: d.order)}


def build_registry() -> dict[str, MentionProvider]:
    """Wrap all registered descriptors into ``MentionProvider`` objects, sorted by ``d.order``.

    Sorting by ``order`` reproduces today's exact REGISTRY dict sequence so
    tie-break picker order is stable regardless of import order (§3.2).
    """
    sorted_descriptors = sorted(_DESCRIPTORS, key=lambda d: d.order)
    registry: dict[str, MentionProvider] = {}
    for d in sorted_descriptors:
        registry[d.type] = MentionProvider(
            type=d.type,
            surfaces=d.surfaces,
            icon=d.icon,
            resolve=make_resolve(d),
            search=make_search(d),
        )
    return registry
