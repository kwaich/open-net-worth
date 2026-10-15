"""EntityDescriptor — the single declarative description of a mentionable type.

RFC 2026-06-25-lucius-generic-entity-registry §4.1.

This module is a LEAF: it imports only from backend.services.mentions.types and
stdlib.  No service / db / api imports here (those belong in the descriptor
definitions under descriptors/).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from backend.services.mentions.types import SearchQuery

FieldType = Literal["text", "money", "pct", "date", "qty", "enum", "id"]


@dataclass(frozen=True)
class FieldSpec:
    """One LOGICAL field — the single source for key name + label + sensitivity.

    ``sensitive=True`` means the field is omitted at source from all model-bound
    output (resolve payloads, format_mention_context, data-ctx-fields).  It is
    NEVER masked-after-read — the field must not appear in the projected dict at all.

    ``show_in_popover`` mirrors objectFields.showInPopover.
    ``in_sublabel`` marks fields that contribute to the search-row second line when
    the generic engine builds the sublabel.
    """

    label: str
    type: FieldType = "text"
    sensitive: bool = False
    show_in_popover: bool = True
    in_sublabel: bool = False


@dataclass(frozen=True)
class EntityDescriptor:
    """Declarative description of a mentionable entity type.

    ``order`` is the registry sort key that preserves today's exact REGISTRY dict
    sequence so tie-break picker order is stable after auto-collect (§3.2).

    ``rows`` is QUERY-AWARE (``Callable[[SearchQuery], list[dict]]``) so a
    transaction descriptor keeps its ``store.query_txns(q=...)`` pushdown instead of
    regressing to a full ``.all()`` scan on every keystroke.

    ``fields`` is the security allowlist: only declared keys are projected into
    model-bound output.  An empty ``fields`` dict produces an empty resolve payload,
    never "all keys."  Keys matching ``_SECRET_KEY_RE`` are refused at registration.

    Override hooks let singleton / service-hydrated / multi-surface types bypass the
    generic loop while keeping the descriptor wrapper for metadata (label, icon, tone,
    order, surfaces).
    """

    type: str
    label: str
    icon: str
    order: int
    tone: str = "neutral"
    surfaces: frozenset[str] = frozenset({"entity"})
    id_field: str = "id"
    rows: Callable[[SearchQuery], list[dict]] | None = None
    label_field: str = "name"
    search_fields: tuple[str, ...] = ()
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    search_override: Callable[[SearchQuery], list[dict]] | None = None
    resolve_override: Callable[[str], dict | None] | None = None
    sublabel_fn: Callable[[dict], str] | None = None
    project_fn: Callable[[dict], dict] | None = None
    find_one: Callable[[str], dict | None] | None = None
