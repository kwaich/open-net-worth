"""
OwnedAssetsStore / OwnedAssetCategoriesStore — the Assets tab domain (things you own,
valued by a dated manual mark: property, vehicles, watches & jewellery, art, precious
metals, cash-value policies, and user-created categories). See
``docs/adr/2026-09-06-assets-tab-faithful-port.md``.

Asset schema (the ADR's ``Asset``):
    id                 : str    "oa_<12hex>" (core.ids.new_id)
    name               : str
    category           : str    a builtin category key (services.owned_assets.CATEGORIES) or a
                                 custom category's `key` — NOT validated here; the service knows
                                 the full category set (builtins + this store's own custom rows),
                                 this store does not.
    description        : str
    photo_ref          : str|None   an AttachmentStore ref (services.attachment_ingress)
    facts              : [[str, str]]   free-form label/value pairs, order preserved
    notes              : str
    cost               : float|None   purchase price, native `currency`
    cost_date          : str|None   ISO date
    share_pct          : float   0..100, default 100 — the owner's share when jointly held;
                                 refused outside the range, never clamped
    share_note         : str
    loan_account_id    : str|None   FK-by-convention to a liability account (opaque; resolved
                                    by the service — no DB FK, matching every Tier-1 store).
                                    A loan secures at most one live asset; None once archived
    insured            : bool   whether the asset is insured
    maturity_date      : str|None   ISO date, primarily for cash-value policies
    currency           : str    the book's base currency at create time
    marks              : [Mark]   newest-first (see _sort_marks) — VALUATIONS only. The
                                  purchase point is derived on read from cost/cost_date
                                  (services.owned_assets_valuation), never stored; identity
                                  edits (update_asset) never touch this list.
    docs               : [Doc]   attachment references (see _make_doc)
    archived           : bool
    disposed_on        : str|None   ISO date
    disposal_proceeds  : float|None
    disposal_txn_id    : str|None
    disposal_account_id: str|None
    created_at         : str    ISO datetime (UTC)
    updated_at         : str    ISO datetime (UTC)

Mark (a valuation):
    id         : str    "mk_<12hex>"
    date       : str    ISO date
    value      : float
    note       : str    e.g. "lark-refinance-2026.pdf", or ""
    by         : "you" | "automation"
    created_at : str    ISO datetime — the tiebreaker when two marks share a `date`
    edited_at  : str|None   ISO datetime of the last in-place correction, None if never

Doc:
    ref_id          : str    an AttachmentStore ref
    name            : str
    added_on        : str    ISO date
    lucius_readable : bool
    note            : str    optional — the user's note on the file, written in Documents
    tags            : [str]  optional — the user's tags on the file, written in Documents

OwnedAssetCategory schema (user-created categories only — builtins are a service-level
module constant, never stored):
    key          : str    "c_<slug>_<4 base36>"
    label        : str
    plural       : str
    color        : str    a `var(--…)` token from the CUSTOM_COLORS ring
    custom       : bool   (True)
    created_at   : str    ISO date

Both are ``BookPayloadTableStore`` subclasses (Tier-1, table ``owned_assets`` /
``owned_asset_categories`` — backend/db/tables.py): the whole nested dict (marks/docs/facts
included) is stored as ONE verbatim ``payload`` column, mirroring CommitmentsStore.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import Connection

from backend.core.errors import ConflictError, ValidationError
from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import owned_asset_categories, owned_assets

MARK_BY = {"you", "automation"}
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _today() -> str:
    return user_today().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _share_pct(value) -> float:
    """0..100, default 100 — REFUSED outside that range, never clamped: a silent clamp stored
    a typed 150 as 100%. The service refuses first; this is the defence in depth under it."""
    if value is None:
        return 100.0
    pct = None if isinstance(value, bool) else _to_float(value, None)
    if pct is None or not math.isfinite(pct) or not 0 <= pct <= 100:
        raise ValidationError("Your share must be between 0 and 100%.", field="share_pct")
    return pct


def _clean_facts(facts) -> list[list[str]]:
    out: list[list[str]] = []
    for f in facts or []:
        if isinstance(f, (list, tuple)) and len(f) == 2:
            label, value = f
            out.append([str(label), str(value)])
    return out


def _make_mark(data: dict, *, created_at: str | None = None) -> dict:
    by = data.get("by")
    if by not in MARK_BY:
        by = "you"
    return {
        "id": data.get("id") or new_id("mk"),
        "date": data.get("date") or _today(),
        "value": _to_float(data.get("value"), 0.0),
        "note": data.get("note") or "",
        "by": by,
        "created_at": data.get("created_at") or created_at or _now(),
        "edited_at": data.get("edited_at") or None,
    }


def _sort_marks(marks: list[dict]) -> list[dict]:
    """Newest-first by (date, created_at, id) — the store's one ordering rule for marks."""
    return sorted(
        marks,
        key=lambda m: (m.get("date") or "", m.get("created_at") or "", m.get("id") or ""),
        reverse=True,
    )


def _mark_index(marks: list[dict], mark_id: str) -> int | None:
    """The FIRST mark with `mark_id` — correct and remove touch that one mark only, so an id an
    older import duplicated can neither merge two values nor remove every value at once."""
    return next((i for i, m in enumerate(marks) if m.get("id") == mark_id), None)


def _make_doc(data: dict) -> dict:
    doc = {
        "ref_id": data.get("ref_id"),
        "name": data.get("name") or "",
        "added_on": data.get("added_on") or _today(),
        "lucius_readable": bool(data.get("lucius_readable", True)),
    }
    if data.get("note"):
        doc["note"] = str(data["note"])
    if data.get("tags"):
        doc["tags"] = [str(t) for t in data["tags"]]
    return doc


class OwnedAssetsStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(owned_assets, id_field="id", engine=engine)

    # ── Create ───────────────────────────────────────────────────────────────

    def create(self, data: dict, *, conn: Connection | None = None) -> dict:
        """Build and persist an asset with defaults, a fresh id, and normalized nested
        marks/docs/facts. Callers (services.owned_assets) supply an already-validated
        `category` — this store does not know the category set."""
        now = _now()
        # Stamp a strictly DECREASING created_at per list position (not wall-clock order of
        # the _make_mark() calls below) so the FIRST mark the caller supplies always sorts
        # newest among marks sharing its `date`, instead of racing real wall-clock timing in
        # `_sort_marks`'s (date, created_at, id) order.
        now_dt = datetime.now(timezone.utc)
        marks = _sort_marks(
            [
                _make_mark(m, created_at=(now_dt - timedelta(microseconds=i)).isoformat())
                for i, m in enumerate(data.get("marks") or [])
            ]
        )
        asset = {
            "id": data.get("id") or new_id("oa"),
            "name": (data.get("name") or "").strip(),
            "category": data.get("category"),
            "description": data.get("description") or "",
            "photo_ref": data.get("photo_ref") or None,
            "facts": _clean_facts(data.get("facts")),
            "notes": data.get("notes") or "",
            "cost": _to_float(data.get("cost"), None),
            "cost_date": data.get("cost_date") or None,
            "share_pct": _share_pct(data.get("share_pct")),
            "share_note": data.get("share_note") or "",
            "loan_account_id": data.get("loan_account_id") or None,
            "insured": bool(data.get("insured", False)),
            "maturity_date": data.get("maturity_date") or None,
            "currency": (data.get("currency") or "").upper(),
            "marks": marks,
            "docs": [_make_doc(d) for d in (data.get("docs") or [])],
            "archived": False,
            "disposed_on": None,
            "disposal_proceeds": None,
            "disposal_txn_id": None,
            "disposal_account_id": None,
            "created_at": data.get("created_at") or now,
            "updated_at": now,
        }
        return self.insert(asset, conn=conn)

    # ── Identity edit (whitelisted — never marks/value/disposal) ───────────────

    _EDITABLE = {
        "name",
        "category",
        "description",
        "notes",
        "cost",
        "cost_date",
        "share_pct",
        "share_note",
        "loan_account_id",
        "insured",
        "maturity_date",
        "facts",
    }

    def update_asset(self, id_val: str, changes: dict, *, conn=None) -> dict | None:
        """Patch identity fields only. `marks` and every disposal field are dropped even if
        present in `changes` — they have dedicated writers."""
        clean = {k: v for k, v in changes.items() if k in self._EDITABLE}
        if "cost" in clean:
            clean["cost"] = _to_float(clean["cost"], None)
        if "share_pct" in clean:
            clean["share_pct"] = _share_pct(clean["share_pct"])
        if "insured" in clean:
            clean["insured"] = bool(clean["insured"])
        if "facts" in clean:
            clean["facts"] = _clean_facts(clean["facts"])
        if not clean:
            return self.find_by_id(id_val, conn=conn)
        clean["updated_at"] = _now()
        return self.update_by_id(id_val, clean, conn=conn)

    # ── Read-modify-write ────────────────────────────────────────────────────────
    # Every write below derives its new list from the stored one, so it reads THROUGH the
    # connection it writes with — one transaction under the table lock (``_use_conn``). A
    # read taken outside it let a concurrent writer's mark or document be overwritten.

    @contextmanager
    def _read_for_write(
        self, id_val: str, conn: Connection | None
    ) -> Iterator[tuple[Connection, dict | None]]:
        with self._use_conn(conn) as owned:
            yield owned, self.find_by_id(id_val, conn=owned)

    def _patch(self, conn: Connection, id_val: str, **fields) -> dict | None:
        return self.update_by_id(id_val, {**fields, "updated_at": _now()}, conn=conn)

    # ── Marks (valuations, newest-first) ────────────────────────────────────────

    def add_mark(self, id_val: str, mark: dict, *, conn: Connection | None = None) -> dict | None:
        """Add a new valuation — never overwrites an existing one. The full list is re-sorted
        newest-first, so a backdated valuation still lands in the right place."""
        with self._read_for_write(id_val, conn) as (owned, asset):
            if asset is None:
                return None
            marks = _sort_marks([*asset.get("marks", []), _make_mark(mark)])
            return self._patch(owned, id_val, marks=marks)

    def correct_mark(
        self, id_val: str, mark_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Correct ONE valuation in place — its value, date and/or note. It keeps its id and
        created_at (the ordering tiebreak) and is stamped edited_at; no before-image is kept.
        A body identical to what is stored returns the asset untouched and unstamped. None
        when the asset or the valuation is missing."""
        with self._read_for_write(id_val, conn) as (owned, asset):
            marks = (asset or {}).get("marks") or []
            index = _mark_index(marks, mark_id)
            if asset is None or index is None:
                return None
            target = marks[index]
            patch = {key: changes[key] for key in ("value", "date", "note") if key in changes}
            if "value" in patch:
                patch["value"] = _to_float(patch["value"], 0.0)
            if all(target.get(key) == val for key, val in patch.items()):
                return asset
            corrected = {**target, **patch, "edited_at": _now()}
            others = [m for i, m in enumerate(marks) if i != index]
            return self._patch(owned, id_val, marks=_sort_marks([*others, corrected]))

    def remove_mark(
        self, id_val: str, mark_id: str, *, conn: Connection | None = None
    ) -> dict | None:
        """Delete ONE valuation. The last one is refused — an asset always has a value. None
        when the asset or the valuation is missing."""
        with self._read_for_write(id_val, conn) as (owned, asset):
            marks = (asset or {}).get("marks") or []
            index = _mark_index(marks, mark_id)
            if asset is None or index is None:
                return None
            kept = [m for i, m in enumerate(marks) if i != index]
            if not kept:
                raise ConflictError(
                    "An asset needs at least one value. Record a new value first, or delete "
                    "the asset.",
                    reason="last_value",
                )
            return self._patch(owned, id_val, marks=kept)

    # ── Photo / docs ─────────────────────────────────────────────────────────────

    def set_photo(
        self, id_val: str, ref: str | None, *, conn: Connection | None = None
    ) -> dict | None:
        return self.update_by_id(id_val, {"photo_ref": ref, "updated_at": _now()}, conn=conn)

    def add_doc(self, id_val: str, doc: dict, *, conn: Connection | None = None) -> dict | None:
        with self._read_for_write(id_val, conn) as (owned, asset):
            if asset is None:
                return None
            return self._patch(owned, id_val, docs=[*asset.get("docs", []), _make_doc(doc)])

    def update_doc(
        self, id_val: str, ref_id: str, changes: dict, *, conn: Connection | None = None
    ) -> dict | None:
        """Patch ONE doc row (`lucius_readable`, `name`, `note` or `tags`). Returns the
        updated asset, or None if the asset/doc is not found."""
        with self._read_for_write(id_val, conn) as (owned, asset):
            docs = [dict(d) for d in (asset or {}).get("docs", [])]
            target = next((d for d in docs if d.get("ref_id") == ref_id), None)
            if asset is None or target is None:
                return None
            if "name" in changes:
                target["name"] = str(changes["name"])
            if "lucius_readable" in changes:
                target["lucius_readable"] = bool(changes["lucius_readable"])
            for key in ("note", "tags"):
                if key not in changes:
                    continue
                if changes[key]:
                    target[key] = changes[key]
                else:
                    target.pop(key, None)
            return self._patch(owned, id_val, docs=docs)

    def remove_doc(
        self, id_val: str, ref_id: str, *, conn: Connection | None = None
    ) -> dict | None:
        with self._read_for_write(id_val, conn) as (owned, asset):
            docs = (asset or {}).get("docs", [])
            kept = [d for d in docs if d.get("ref_id") != ref_id]
            if asset is None or len(kept) == len(docs):
                return None
            return self._patch(owned, id_val, docs=kept)

    # ── Archive / dispose ────────────────────────────────────────────────────────

    def archive(self, id_val: str, disposal: dict, *, conn=None) -> dict | None:
        """Mark an asset archived with its disposal facts, in the SAME transaction as
        the disposal's ledger write when `conn` is given (services.owned_assets.dispose).
        History is kept — this is never a delete. A sold asset secures nothing, so its loan
        link is cleared: the loan is free for another asset, and deleting the loan account is
        no longer blocked by a row the owner can't edit."""
        patch = {
            "archived": True,
            "loan_account_id": None,
            "disposed_on": disposal.get("disposed_on") or _today(),
            "disposal_proceeds": _to_float(disposal.get("disposal_proceeds"), None),
            "disposal_txn_id": disposal.get("disposal_txn_id") or None,
            "disposal_account_id": disposal.get("disposal_account_id") or None,
            "updated_at": _now(),
        }
        return self.update_by_id(id_val, patch, conn=conn)

    # ── Queries ───────────────────────────────────────────────────────────────────

    def query_assets(
        self, category: str | None = None, include_archived: bool = False
    ) -> list[dict]:
        items = self.all()
        if not include_archived:
            items = [a for a in items if not a.get("archived")]
        if category is not None:
            items = [a for a in items if a.get("category") == category]
        return items


class OwnedAssetCategoriesStore(BookPayloadTableStore):
    """User-created categories only. Builtins live in services.owned_assets.CATEGORIES —
    never stored here."""

    def __init__(self, engine=None):
        super().__init__(owned_asset_categories, id_field="key", engine=engine)

    def _slug(self, label: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")

    def _mint_key(self, label: str) -> str:
        slug = self._slug(label) or "category"
        suffix = "".join(random.choices(_BASE36, k=4))
        return f"c_{slug}_{suffix}"

    def create(self, label: str, *, color: str | None = None) -> dict:
        """Idempotent on a case-insensitive label match against this store's OWN rows
        (custom categories) — re-adding "Vinyl & hi-fi" returns the existing row rather
        than minting a duplicate. `color` defaults to the next slot in the CUSTOM_COLORS
        ring (services.owned_assets), cycling by how many custom categories exist today."""
        label = (label or "").strip()
        for row in self.all():
            if (row.get("label") or "").strip().lower() == label.lower():
                return row
        row = {
            "key": self._mint_key(label),
            "label": label,
            "plural": label,
            "color": color or "var(--recv)",
            "custom": True,
            "created_at": _today(),
        }
        return self.insert(row)
