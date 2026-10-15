"""The write-then-verify chip — turn a turn's writes into ONE differential summary.

## Why this replaces the confirm card

The confirm gate asked permission BEFORE every ordinary ledger write. Owner verdict:
"do we really need to show these confirm cards at all though? ... i would rather see the
chip of newly created/updated ones in the ai response and then tap on it to see the
details" — and, on which ones were actually worth a card, "bulks, i can understand / the
rest, bullshit". The remaining requirement is not permission, it is verifiability:
"it's important for users to know what changes are going to be made and be able to verify
right after that".

So the flow inverts. The write happens, and the reply carries a chip that says what
landed. Permission survives only where the MODEL chose the SET of rows rather than the
user naming them (``delete_transactions_where``, the global ``apply_payee_rules``) —
there the blast radius is a selection nobody can verify after the fact, which is a
different problem from "did you record what I said".

## Every row is a MENTION CHIP, of any type

The chip started ledger-only and its rows were bespoke transaction markup. It is now
multi-type: a turn that books a transaction, assigns an envelope and records a vest gets
ONE chip with three rows of three types. That is possible because every row is a real
``MentionRow`` — the same shape the ``@``-picker emits — hydrated ONCE at turn end by
``services.mentions.labels.receipt_rows``. The collection seam (``core.write_receipt``)
still records only ``(type, op, id)`` on the write path; no store is read there.

Which chokes report is a deliberate list, not a sweep: a choke that already exists gets
instrumented, a domain without one is DEFERRED rather than instrumented at its call
sites. That is what keeps this from drifting into the 167-handler failure mode.

## Entity-only, deliberately

Collapsed, the chip names counts and entity types. Expanded, it lists only the affected
entity chips and their create/update/delete glyphs. It does not repeat amounts, dates,
categories, account names, budget months, or anomaly judgments; those belong in the real
entity drawer the chip opens where supported.

## Undo is the turn checkpoint, deliberately

The chip does not carry its own reversal mechanism. ``ai/checkpoint.py`` already captures
the pre-write bytes of every file a turn touches, is anchored to that turn, and survives
a reload (``turn_anchors``), so the receipt rides that record rather than growing a second
one with its own expiry.
"""

from __future__ import annotations

from typing import Optional

# The type every pre-generalization record carried.
TXN = "transaction"


# Rows carried on the chip's expandable detail. Past this the chip reports the true total
# and shows a sample — the 12-row convention `services.ledger_preview.preview_bulk`
# already established for a bulk card, kept identical so both surfaces read the same.
MAX_ITEMS = 12


def _dedup(writes: list[dict]) -> list[dict]:
    """Collapse repeat records of the same ``(type, op, id)``, merging their meta.

    Two things need this. A user action can legitimately reach one entity twice in a turn
    (an auto-assign then a correction), and "40 envelopes edited" should not become 80.
    And a choke may deliberately record an entity a SECOND time purely to attach
    provenance the first record could not know — ``match_review`` does exactly that,
    adding ``verb=merge`` to a row the audit choke already reported. Later meta wins on a
    key collision, so the more specific record refines the earlier one."""
    order: list[tuple] = []
    merged: dict[tuple, dict] = {}
    for w in writes:
        key = (w.get("type") or TXN, w.get("op") or "", w.get("id") or "")
        if key not in merged:
            order.append(key)
            merged[key] = {**w, "meta": dict(w.get("meta") or {})}
            continue
        merged[key]["meta"].update(w.get("meta") or {})
    return [merged[k] for k in order]


def _fold_updates_into_creates(writes: list[dict]) -> list[dict]:
    """An entity created in this turn and then edited in it is ONE thing the user did: it was
    added. Importing 23 statement rows and labelling them in the same pass read "37
    transactions changed", a figure nobody would count; the edits of a row the turn itself
    created fold into its create record (their meta refines it) instead of adding to the
    totals."""
    created = {(w.get("type") or TXN, w.get("id")): w for w in writes if w.get("op") == "create"}
    kept: list[dict] = []
    for w in writes:
        target = created.get((w.get("type") or TXN, w.get("id")))
        if w.get("op") == "update" and target is not None:
            target["meta"].update(w.get("meta") or {})
            continue
        kept.append(w)
    return kept


def _sample(writes: list[dict]) -> list[dict]:
    """Up to ``MAX_ITEMS`` records for the expandable detail, guaranteeing AT LEAST ONE row
    per type before filling by order.

    Without the guarantee a mixed turn's rare row — the one envelope assignment beside a
    12-row import — is exactly what gets crowded out, and it is the row most worth seeing."""
    if len(writes) <= MAX_ITEMS:
        return list(writes)
    first_of_type: list[dict] = []
    seen: set = set()
    for w in writes:
        t = w.get("type") or TXN
        if t not in seen:
            seen.add(t)
            first_of_type.append(w)
    out = first_of_type[:MAX_ITEMS]
    picked = {id(w) for w in out}
    for w in writes:
        if len(out) >= MAX_ITEMS:
            break
        if id(w) not in picked:
            out.append(w)
    return out


def _groups(writes: list[dict], order: dict[str, int]) -> list[dict]:
    """Per-type head groups: ``[{type, count, ops}]``, most rows first.

    The head used to read "1 edited", which never named WHAT was edited — harmless while
    every row was a transaction, actively misleading the moment they are not. Sorted by
    count then the registry's own ``descriptor.order`` so ties break deterministically
    rather than by dict insertion."""
    counts: dict[str, int] = {}
    ops: dict[str, set] = {}
    for w in writes:
        t = w.get("type") or TXN
        counts[t] = counts.get(t, 0) + 1
        ops.setdefault(t, set()).add(w.get("op") or "")
    return [
        {"type": t, "count": counts[t], "ops": sorted(o for o in ops[t] if o)}
        for t in sorted(counts, key=lambda t: (-counts[t], order.get(t, 999), t))
    ]


def build_turn_receipt(writes: list[dict], *, since: str = "") -> Optional[dict]:
    """Build the chip payload for a turn's collected writes, or ``None`` when nothing was
    written (and therefore nothing to verify).

    ``since`` remains accepted for callers deployed with the older receipt contract; entity-
    only receipts do not use timestamps.

    Best-effort by contract: this runs at turn end, AFTER every write has already
    committed, so a failure here must degrade the chip and never the turn. The caller
    treats ``None`` as "no chip" — never as "no writes happened".
    """
    if not writes:
        return None

    from backend.services.mentions.labels import receipt_rows
    from backend.services.mentions.registry import DESCRIPTORS

    # An unregistered type has no chip renderer, and a row that cannot render is worse than
    # no row — drop it here rather than shipping a blank to the client. A test asserts every
    # type any choke actually records IS registered, so this never silently eats a domain.
    known = set(DESCRIPTORS)
    # A write can absorb another that is part of it (an account's own Starting Balance row):
    # the user did ONE thing, so the receipt shows one.
    absorbed = {(w.get("meta") or {}).get("absorbs") for w in writes} - {None, ""}
    writes = [w for w in writes if w.get("id") not in absorbed]
    renderable = [w for w in writes if (w.get("type") or TXN) in known and w.get("id")]
    # Counted BEFORE the de-dup, deliberately: `dropped` is "records that carry no
    # renderable entity" (an over-cap row, an unregistered type), which must still show up
    # in the total. Measuring it after the fold would re-add every collapsed duplicate as a
    # phantom write and undo the fold's whole point.
    dropped = len(writes) - len(renderable)
    kept = _fold_updates_into_creates(_dedup(renderable))

    counts = {"create": 0, "update": 0, "delete": 0}
    for w in kept:
        if w.get("op") in counts:
            counts[w["op"]] += 1
    # Rows past MAX_COLLECTED carry a type but no id, so they never reach `kept` — count
    # them here so the head total stays honest even when the detail is gone.
    total = counts["create"] + counts["update"] + counts["delete"] + max(0, dropped)
    if total == 0:
        return None

    order = {t: i for i, t in enumerate(DESCRIPTORS)}
    sampled = _sample(kept)
    rows = receipt_rows([(w.get("type") or TXN, w["id"]) for w in sampled])

    items: list[dict] = []
    for w, mention in zip(sampled, rows):
        # Receipts identify entities only. Search/picker sublabels describe domain details
        # and do not belong on this surface; the entity drawer remains their source.
        mention["sublabel"] = ""
        items.append({"mention": mention, "op": w.get("op") or ""})

    return {
        "created": counts["create"],
        "updated": counts["update"],
        "deleted": counts["delete"],
        "total": total,
        "showing": len(items),
        # Per-type head groups — what makes "1 edited" say WHICH one thing.
        "groups": _groups(kept, order),
        "items": items,
    }
