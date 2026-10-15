"""Which STATEMENT(S) a ledger row is on, resolved from ``external_refs``.

The "In your ledger, not on this statement" panel lists entries the statement being imported
does not account for. The owner's question about each is *"which statement IS it on, then?"* —
because an entry that is on the adjacent cycle is fine, and one that is on nothing might not be.

**Two different questions, and only one of them is this one** (owner, 2026-07-31: *"why the
fuck are we using import batch id? why don't we use external_refs?"*):

| field | question | the owner's Acme Software row |
|---|---|---|
| ``batch_id`` | where did this row COME FROM? | the "Acme Software USD invoices" batch |
| ``external_refs``   | which statement is it ON?     | ``Invoice_Jun2026 2.pdf`` |

An earlier cut of this module answered via ``batch_id → batch → statement`` and rendered
the result as "Already on …" — a truthful answer to the wrong question. **A merge never stamps
``batch_id``**: a row the statement LINKED keeps whatever batch created it, and the
statement's ref is APPENDED to ``external_refs``. So batch-derived provenance is structurally
blind to every linked row. Measured on the owner's book: 355 rows are on a statement by refs,
of which **116 are invisible to the batch link** (statement `Invoice_Jun2026 2.pdf` alone
created 0 transactions and linked 46). Where both resolve they never disagree (239/239).

**A row can be on MORE than one statement** — 24 rows on this book are on two (a charge landing
in two cycles, or a re-import). All links ride the payload; the primary is the statement whose
printed period CONTAINS the row's date, then the most recent, and ``label`` names the rest as
"+N more" rather than silently dropping them.

The reverse index is built ONCE per panel computation, never per row.

Layer: services → db → core. Read-only; no money math.
"""

from __future__ import annotations

import re
from datetime import date


def _human_day(value: str | None) -> str | None:
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
    return f"{d.day} {d:%b %Y}"


def _human_range(start: str | None, end: str | None) -> str | None:
    """A printed period as one compact phrase — "3–29 Jun 2026", "3 Jun – 1 Jul 2026",
    "28 Dec 2025 – 27 Jan 2026". Shared units are written once because this lands on a 190px
    rail: "3 Jun 2026 – 1 Jul 2026" ellipsizes down to nothing that identifies anything."""
    try:
        lo = date.fromisoformat(str(start)[:10])
        hi = date.fromisoformat(str(end)[:10])
    except (TypeError, ValueError):
        return None
    if lo.year != hi.year:
        return f"{lo.day} {lo:%b %Y} – {hi.day} {hi:%b %Y}"
    if lo.month == hi.month:
        return f"{lo.day}–{hi.day} {hi:%b %Y}"
    return f"{lo.day} {lo:%b} – {hi.day} {hi:%b %Y}"


def statement_label(record: dict) -> str:
    """The statement's human name. The PRINTED PERIOD leads — it is what the owner reconciles
    against ("is this on the June statement?"). The filename is the last resort: it is often a
    bank reference number that names nothing."""
    meta = record.get("import_meta") or {}
    period = _human_range(meta.get("period_start"), meta.get("period_end"))
    if period:
        return f"{period} statement"
    on = _human_day(meta.get("statement_date"))
    if on:
        return f"{on} statement"
    return (record.get("filename") or "").strip() or "An earlier statement"


_DATE_TAIL = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT_TAIL = re.compile(r"-?\d+\.\d{2}")


def bank_reference(ref: str | None) -> str | None:
    """The BANK's own reference inside one ``external_refs`` ref string, or None.

    The bank-reference shape is ``ref:<account_id>:<reference>[:<date>:<amount>]``; anything
    else (a bare hash — OUR dedup key, worse than printing nothing — a legacy ``<fmt>:<ref>``
    row, a format-native FITID) returns None.

    The account id is deliberately stripped: it is an internal opaque id, and ``external_refs``
    is classed as internal bookkeeping everywhere else in the UI
    (``TxnDetail.CHANGE_FIELD_HIDDEN``). What survives is the part the owner can look up on the
    printed statement."""
    if not ref:
        return None
    parts = str(ref).split(":")
    if len(parts) < 3 or parts[0] != "ref":
        return None
    tail = parts[2:]
    if (
        len(tail) > 1
        and _DATE_TAIL.fullmatch(tail[-2] or "")
        and _AMOUNT_TAIL.fullmatch(tail[-1] or "")
    ):
        tail = tail[:-2]
    return ":".join(tail).strip() or None


class StatementRefIndex:
    """``ref string -> [statement records]``, built ONCE per panel computation.

    A statement record keeps its parse as ``import_rows`` (``CanonicalRow`` dicts), so the refs
    it owns are re-derived through the SAME ``statement_row_refs`` + ``disambiguate_identical_rows``
    pipeline the draft path runs (see ``_record_refs``), rather than restating either step, so
    the index cannot drift from what a commit actually wrote — including a repeat row's
    occurrence-index-suffixed ref. A structured format (OFX/CAMT/MT940) carries a format-native
    id on the row instead, which replaces the fabricated refs exactly as it does on the draft.

    Records with no cached parse contribute nothing — they predate the decision pipeline and
    have no canonical rows to index (74 of 81 on the owner's book).
    """

    __slots__ = ("by_ref", "reference_of")

    def __init__(self) -> None:
        from backend.db.statement_store import StatementStore

        self.by_ref: dict[str, list[dict]] = {}
        # (statement id, ref) -> the bank's own reference printed on that row. The fabricated
        # ref is a content hash and no longer embeds it, so it rides beside the ref instead.
        self.reference_of: dict[tuple[str, str], str | None] = {}
        for record in StatementStore().all():
            for ref, reference in _record_refs(record).items():
                self.by_ref.setdefault(ref, []).append(record)
                if reference:
                    self.reference_of[(str(record.get("id")), ref)] = reference


def _record_accounts(record: dict) -> list[str]:
    """Every account the statement's rows could have been imported into — the ref embeds the
    account id, and a multi-account statement routes different sections to different ones."""
    accounts = [record.get("account_id")]
    accounts += list((record.get("account_map") or {}).values())
    accounts += list(record.get("account_ids") or [])
    return [a for a in dict.fromkeys(accounts) if a]


def _record_refs(record: dict) -> dict[str, str | None]:
    """Re-derives each cached row's ref set through the SAME ``statement_row_refs`` +
    ``disambiguate_identical_rows`` pipeline the draft path runs (one source of truth), rather
    than recomputing a bare hash independently — a repeat row's ledger ref carries an
    occurrence-index suffix, which a bare re-derivation can never match.

    A multi-account statement doesn't record which candidate account each row actually routed
    to, so every account is tried for every row (over-approximation, as before) — each pass
    disambiguated on its own, since the fabricated refs already embed the account id."""
    from dataclasses import fields

    from backend.services.import_common import RowResult, disambiguate_identical_rows
    from backend.services.statements.canonical import CanonicalRow
    from backend.services.statements.importer import statement_row_refs

    rows = record.get("import_rows") or []
    accounts = _record_accounts(record)
    if not rows or not accounts:
        return {}
    # A cached parse can predate a CanonicalRow field change; one stale key must not 500 the
    # statements panel for every transaction.
    known = {f.name for f in fields(CanonicalRow)}
    canonical_rows = [CanonicalRow(**{k: v for k, v in row.items() if k in known}) for row in rows]
    refs: dict[str, str | None] = {}
    for account_id in accounts:
        results = [
            RowResult(line=i, draft={"external_refs": statement_row_refs(row, account_id)})
            for i, row in enumerate(canonical_rows, start=1)
        ]
        disambiguate_identical_rows(results)
        for row, r in zip(canonical_rows, results):
            for entry in r.draft["external_refs"]:
                if entry.get("ref"):
                    refs.setdefault(str(entry["ref"]), row.reference or None)
    return refs


def _link(record: dict, reference: str | None) -> dict:
    meta = record.get("import_meta") or {}
    return {
        "statement_id": record.get("id"),
        "label": statement_label(record),
        "filename": (record.get("filename") or None),
        "reference": reference,
        "period_start": meta.get("period_start"),
        "period_end": meta.get("period_end"),
    }


def _covers(link: dict, day: str) -> bool:
    lo, hi = link.get("period_start"), link.get("period_end")
    return bool(lo and hi and str(lo) <= day <= str(hi))


def on_statements(txn: dict, index: StatementRefIndex) -> dict | None:
    """The statement(s) this ledger row is already on — or None when it is on none we hold.

    None is the honest answer for a hand-entered row, an agent booking, a bank-feed sync, and
    an import whose statement record was deleted. The panel renders nothing rather than
    substituting where the row came FROM, which is a different question (see the module
    docstring) and was the 2026-07-31 bug.
    """
    day = str(txn.get("date") or "")[:10]
    best: dict[str, tuple[dict, str | None]] = {}
    for entry in txn.get("external_refs") or []:
        ref = entry.get("ref") if isinstance(entry, dict) else None
        if not ref:
            continue
        for record in index.by_ref.get(str(ref), []):
            sid = record.get("id")
            if not sid:
                continue
            # The statement row's own printed reference; a ledger ref of the older documented
            # `ref:<account>:<reference>` shape (a feed row) still carries it in the string.
            reference = index.reference_of.get((str(sid), str(ref))) or bank_reference(str(ref))
            current = best.get(sid)
            if current is None or (reference and not current[1]):
                best[sid] = (record, reference)
    links = [_link(record, reference) for record, reference in best.values()]
    if not links:
        return None
    # Primary = the statement whose printed period CONTAINS this row (the cycle it genuinely
    # belongs to); then the latest-closing; then a stable tie-break on id. Two passes because
    # the period sorts DESCENDING while the covers test sorts ascending, and sort is stable.
    links.sort(key=lambda x: (str(x.get("period_end") or ""), str(x["statement_id"])), reverse=True)
    links.sort(key=lambda x: not _covers(x, day))
    primary = links[0]
    extra = len(links) - 1
    return {
        "label": primary["label"] + (f" +{extra} more" if extra else ""),
        "reference": primary["reference"],
        "statements": links,
    }
