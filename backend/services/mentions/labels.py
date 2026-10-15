"""``(type, id)`` → a real ``MentionRow``, resolved SERVER-SIDE.

## The gap this closes

Nothing else in this system can turn a bare ``(type, id)`` pair into a labelled chip:

  * ``resolve_mentions()`` ECHOES the caller's label back rather than deriving one, caps
    at 12 items, and on the chat path can splice network context — so it answers a
    different question than "what is this thing called".
  * ``search`` never matches on an id, only on display text.
  * the per-type resolve payload has no uniform name key, and for five types
    (``transaction``, ``net_worth``, ``budget_month``, ``allocation_slice``, ``returns``)
    has no name at all.

The write-receipt chip needs exactly that pair→row hydration: the collection seam records
ids at write time (thin by contract — no store reads on a write path) and the labels are
wanted once, at turn end. This module is that one place.

## One store read per TYPE, never per row

``receipt_rows`` groups the pairs by type and pulls each type's descriptor source exactly
once, then indexes by id. A per-row resolve across ten types is the failure mode this is
written to avoid — a 12-row mixed receipt would otherwise be twelve full store scans.

Rows come out in the SAME shape ``_common._row`` produces for the search path, through the
same helper and the same ``ICONS`` table, so a receipt chip and a picker chip are
byte-identically shaped and the client stays type-agnostic.

## A missing entity is STALE, never invented

A hard-deleted (or never-existed) id still emits a row — with ``label=""`` and
``stale: True`` — because dropping it would silently shrink a receipt whose counts say
otherwise. The client falls back to the type's own name and renders it as removed.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional

from backend.services.mentions._common import _row
from backend.services.mentions.descriptor import EntityDescriptor
from backend.services.mentions.types import SearchQuery

logger = logging.getLogger(__name__)

# Tried in order when a descriptor's own ``label_field`` yields nothing. This is the
# "resolve payload has no uniform label key" defect made explicit and fixed in ONE place
# rather than re-guessed at every call site.
_LABEL_FALLBACKS = ("name", "title", "label", "symbol", "ticker")

# Types whose rows carry no human name at all — the label has to be COMPOSED from the row.
# Keyed by type so a new nameless type declares its own rule rather than degrading to an id.
_COMPOSED_LABELS: dict[str, Callable[[dict], str]] = {
    "transaction": lambda r: (
        (r.get("description") or r.get("memo") or "").strip() or "(no description)"
    ),
}

# Colour is set ONLY where the entity genuinely owns a display tint. Everything else is
# left unset so the client's TYPE_COLOR applies and every lot/grant/vest row reads as its
# TYPE — inventing a per-row colour would make types indistinguishable at a glance.
_COLOR_FIELDS: dict[str, str] = {
    "account": "color",
    "envelope": "color",
    "category": "color",
}

# A source scan wide enough to cover any plausible one-turn write set without becoming an
# unbounded read on a huge book. Descriptor ``rows`` callables that honour ``limit`` (the
# query-aware transaction source does not, by design) get a real ceiling.
_SOURCE_LIMIT = 5000

# Past this many wanted ids of one type, one full scan beats N point lookups. A receipt is
# normally a handful of ids per type, so the point-lookup path is the common one.
_POINT_LOOKUP_MAX = 8


def _label_of(d: EntityDescriptor, row: dict) -> str:
    """The human name for ``row`` under descriptor ``d``."""
    composed = _COMPOSED_LABELS.get(d.type)
    if composed is not None:
        try:
            return composed(row)
        except Exception:  # noqa: BLE001 - a malformed row degrades to the fallbacks
            pass
    for key in (d.label_field, *_LABEL_FALLBACKS):
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if val is not None and not isinstance(val, (dict, list, bool)):
            text = str(val).strip()
            if text:
                return text
    return ""


def _sublabel_of(d: EntityDescriptor, row: dict) -> str:
    """The descriptor's OWN sublabel, when it declares one. Never raises — a sublabel is
    decoration, and a sublabel_fn that reaches a store can fail on a just-deleted row."""
    if d.sublabel_fn is None:
        return ""
    try:
        return d.sublabel_fn(row) or ""
    except Exception:  # noqa: BLE001
        return ""


def _index_for(d: EntityDescriptor, ids: set[str]) -> dict[str, dict]:
    """``id -> row`` for one type, via ONE read of the descriptor's own source.

    Uses ``find_one`` when the descriptor has one AND either the wanted set is small enough
    that N point-lookups beat a full scan, or there is no ``rows`` source to scan at all.
    ``find_one`` is then used EXCLUSIVELY — a ``None`` from it means the entity does not
    exist, not "look harder". Falling through to a scan on a partial result would make one
    unresolvable id silently trigger a full read, and would resolve rows ``find_one``
    deliberately excludes.
    """
    out: dict[str, dict] = {}
    if d.find_one is not None and (len(ids) <= _POINT_LOOKUP_MAX or d.rows is None):
        for i in ids:
            try:
                row = d.find_one(i)
            except Exception:  # noqa: BLE001 - one bad id must not sink the type
                row = None
            if row is not None:
                out[i] = row
        return out
    if d.rows is None:
        return out
    query = SearchQuery(q="", types=None, surfaces=None, limit=_SOURCE_LIMIT, cursor=None)
    for row in d.rows(query):
        rid = str(row.get(d.id_field) or "")
        if rid in ids and rid not in out:
            out[rid] = row
    return out


def _stale_row(type_: str, id_: str) -> dict:
    """A row for an entity that could not be read back. Label is deliberately EMPTY, not a
    guess and not the id: absent means "no label available" (the client falls back to the
    type's own name), whereas a fabricated one would be a wrong claim about a name."""
    row = _row(type_, id_, "", "", id_)
    row["stale"] = True
    return row


def receipt_rows(pairs: Iterable[tuple[str, str]]) -> list[dict]:
    """Hydrate ``[(type, id), …]`` into ``MentionRow`` dicts, preserving input order.

    One store read per DISTINCT type. A type whose read fails degrades to stale rows for
    that type alone and never sinks the rest — a receipt describes writes that already
    committed, so a labelling failure must never be able to hide them.
    """
    pair_list = [(t, i) for t, i in pairs if t and i]
    if not pair_list:
        return []

    from backend.services.mentions.registry import DESCRIPTORS

    wanted: dict[str, set[str]] = {}
    for type_, id_ in pair_list:
        wanted.setdefault(type_, set()).add(id_)

    indexes: dict[str, dict[str, dict]] = {}
    for type_, ids in wanted.items():
        d = DESCRIPTORS.get(type_)
        if d is None:
            indexes[type_] = {}
            continue
        try:
            indexes[type_] = _index_for(d, ids)
        except Exception:  # noqa: BLE001
            logger.exception("mention labels: could not read source for type %r", type_)
            indexes[type_] = {}

    # ONE ROW PER INPUT PAIR, duplicates included — the output is positionally joined with
    # the caller's own list, and a repeated pair is legitimate (an entity created and then
    # updated in the same turn is two rows of one entity). De-duplicating here would
    # silently shorten the result and misalign every row after the first repeat.
    out: list[dict] = []
    for type_, id_ in pair_list:
        d = DESCRIPTORS.get(type_)
        row = indexes.get(type_, {}).get(id_)
        if d is None or row is None:
            out.append(_stale_row(type_, id_))
            continue
        try:
            color = None
            color_key = _COLOR_FIELDS.get(type_)
            if color_key:
                raw = row.get(color_key)
                color = raw if isinstance(raw, str) and raw.strip() else None
            out.append(_row(type_, id_, _label_of(d, row), _sublabel_of(d, row), id_, color=color))
        except Exception:  # noqa: BLE001 - per-row isolation
            logger.exception("mention labels: could not build row for %s/%s", type_, id_)
            out.append(_stale_row(type_, id_))
    return out


def source_row(type_: str, id_: str) -> Optional[dict[str, Any]]:
    """The raw stored row behind one ``(type, id)`` — for a caller that needs a FIELD, not
    a chip (the receipt's per-type sublabel builder). ``None`` when unresolvable."""
    from backend.services.mentions.registry import DESCRIPTORS

    d = DESCRIPTORS.get(type_)
    if d is None or not id_:
        return None
    try:
        return _index_for(d, {id_}).get(id_)
    except Exception:  # noqa: BLE001
        return None
