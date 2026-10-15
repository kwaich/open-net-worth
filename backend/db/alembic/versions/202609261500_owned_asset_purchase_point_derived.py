"""Owned assets: the purchase point is derived, never stored.

Until 2026-09-26 ``create`` stored the purchase price as a second mark ("entered on add") next
to the first value — stamped exactly 1µs older than it — and no later purchase edit ever moved
that copy. It also stored a system ``["Added", "<Mon YYYY>"]`` fact and a literal "— none" first
note. The purchase point is now derived on read from cost/cost_date
(``services.owned_assets_valuation``), so each row is normalized, in this order:

1. Notes: ``note = note or source or ""``; ``source`` dropped; "— none" becomes "".
2. The create-time purchase mark: the ONE mark noted "entered on add" whose created_at + 1µs is
   another mark's created_at is dropped. No match: nothing. Several: none, and a warning.
3. ``edited_at`` is set to null wherever it is missing.
4. Mark ids are made addressable: correct and remove find a value by its id, and "purchase"
   names the derived point. The first holder of an id keeps it; a missing, duplicate or
   "purchase" id is re-minted from the asset id and the mark's own date, created_at and value
   (``mk_<12 hex>`` of a sha256), so a checkpoint image re-mints exactly as its row does.
5. Marks are sorted newest-first by (date, created_at, id).
6. The system "Added" fact is removed: ``["Added", M]`` where M is the English month-year of
   created_at's UTC date minus one day, that day, or plus one day (create formatted the
   server's LOCAL date). A row with no created_at is left alone.
7. A sold (archived) asset releases its loan link.

The same rewrite runs over every checkpoint's owned-asset before-image (``write_log``): a
restore replays those raw, so a checkpoint taken before this revision would otherwise bring
the old shape back. Every row and image is read and rewritten BEFORE anything is written, so a
malformed one refuses the revision with nothing changed; an INFO line summarizes what changed.

Today's value — the newest stored valuation — is unchanged for every row the app wrote: the
dropped copy was never the newest mark. Only a hand-edited, unsorted import can differ; it is
logged (asset id, before, after), never refused. Live assets that already share a loan are
logged and left alone. Idempotent: every step is a projection, so a second run writes nothing.

Downgrade (rows and checkpoint images alike) strips ``edited_at``, writes the purchase point
the projection shows back as an old-shape "entered on add" mark stamped 1µs before the oldest
valuation (so upgrading again finds it) and re-sorts by (date, created_at). Not restored:
"Added" facts, "— none" notes, loan links of sold assets, re-minted mark ids, and fabricated or
stale purchase marks.

The helpers are frozen copies — a migration never imports live code (``202609101600``).

Revision ID: 202609261500_owned_asset_purchase_point_derived
Revises: 202609231130_pending_position_fmv_basis
Create Date: 2026-09-26
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "202609261500_owned_asset_purchase_point_derived"
down_revision = "202609231130_pending_position_fmv_basis"
branch_labels = None
depends_on = None

_log = logging.getLogger(__name__)

_SELECT = sa.text("SELECT id, payload FROM owned_assets")
_UPDATE = sa.text("UPDATE owned_assets SET payload = :payload WHERE id = :id")
# Checkpoint before-images of owned assets: a restore replays them raw, so a checkpoint taken
# before this revision would bring the old shape back (code review R1). A "row did not exist"
# image is stored as JSON null — left alone.
_SELECT_SNAPSHOTS = sa.text(
    "SELECT checkpoint_id, seq, before_payload FROM write_log WHERE table_name = 'owned_assets'"
)
_UPDATE_SNAPSHOT = sa.text(
    "UPDATE write_log SET before_payload = :image WHERE checkpoint_id = :checkpoint_id "
    "AND seq = :seq"
)
_PURCHASE_NOTE = "entered on add"
_NONE_NOTE = "— none"
_MICROSECOND = timedelta(microseconds=1)
_DAY = timedelta(days=1)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _dict(raw, row_id: str) -> dict:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"payload {row_id!r} is unreadable; migration refused") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"payload {row_id!r} must be a JSON object; migration refused")
    if not isinstance(payload.get("marks") or [], list):
        raise RuntimeError(f"payload {row_id!r} has marks that are not a list; migration refused")
    for mark in payload.get("marks") or []:
        if not isinstance(mark, dict):
            raise RuntimeError(f"payload {row_id!r} has a mark that is not an object")
    return payload


def _stamp(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _notes(marks: list[dict]) -> list[dict]:
    out = []
    for mark in marks:
        mark = dict(mark)
        note = mark.get("note") or mark.pop("source", None) or ""
        mark.pop("source", None)
        mark["note"] = "" if note == _NONE_NOTE else note
        out.append(mark)
    return out


def _drop_create_time_purchase(marks: list[dict], asset_id) -> list[dict]:
    stamps = [_stamp(m.get("created_at")) for m in marks]
    known = {s for s in stamps if s is not None}
    matches = [
        i
        for i, mark in enumerate(marks)
        if mark.get("note") == _PURCHASE_NOTE
        and stamps[i] is not None
        and stamps[i] + _MICROSECOND in known
    ]
    if len(matches) > 1:
        _log.warning(
            "owned asset %s: %d create-time purchase marks — none dropped", asset_id, len(matches)
        )
    if len(matches) != 1:
        return marks
    return [m for i, m in enumerate(marks) if i != matches[0]]


def _minted_id(asset_id, mark: dict, taken: set[str]) -> str:
    n = 0
    while True:
        seed = f"{asset_id}|{mark.get('date')}|{mark.get('created_at')}|{mark.get('value')!r}|{n}"
        candidate = f"mk_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
        if candidate not in taken:
            return candidate
        n += 1


def _unique_ids(marks: list[dict], asset_id) -> list[dict]:
    kept: set[str] = set()
    keeps = []
    for mark in marks:
        mark_id = mark.get("id")
        keep = isinstance(mark_id, str) and mark_id not in ("", "purchase") and mark_id not in kept
        keeps.append(keep)
        if keep:
            kept.add(mark_id)
    out = []
    for mark, keep in zip(marks, keeps):
        if not keep:
            mark = {**mark, "id": _minted_id(asset_id, mark, kept)}
            kept.add(mark["id"])
        out.append(mark)
    return out


def _sorted(marks: list[dict]) -> list[dict]:
    return sorted(
        marks,
        key=lambda m: (m.get("date") or "", m.get("created_at") or "", m.get("id") or ""),
        reverse=True,
    )


def _without_added_fact(asset: dict) -> None:
    created = _stamp(asset.get("created_at")) if asset.get("created_at") else None
    facts = asset.get("facts")
    if created is None or not isinstance(facts, list):
        return
    day = created.astimezone(timezone.utc).date()
    months = {f"{_MONTHS[d.month - 1]} {d.year}" for d in (day - _DAY, day, day + _DAY)}
    asset["facts"] = [
        fact
        for fact in facts
        if not (
            isinstance(fact, (list, tuple))
            and len(fact) == 2
            and fact[0] == "Added"
            and fact[1] in months
        )
    ]


def _migrate_asset(payload: dict) -> dict:
    """Steps 1–7 on one stored asset; returns a new dict and never mutates `payload`."""
    asset = copy.deepcopy(payload)
    marks = _drop_create_time_purchase(_notes(asset.get("marks") or []), asset.get("id"))
    for mark in marks:
        mark.setdefault("edited_at", None)
    asset["marks"] = _sorted(_unique_ids(marks, asset.get("id")))
    _without_added_fact(asset)
    if asset.get("archived") and asset.get("loan_account_id") is not None:
        asset["loan_account_id"] = None
    return asset


def _snapshot_image(raw, key: str) -> dict | None:
    """A checkpoint's owned-asset row image ``{book_id, seq, id, payload}``, or None for a
    "row did not exist" image. Anything else refuses the revision (fail-closed)."""
    try:
        image = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{key} is unreadable; migration refused") from exc
    if image is None:
        return None
    if not isinstance(image, dict) or not isinstance(image.get("payload"), dict):
        raise RuntimeError(f"{key} is not an owned-asset row image; migration refused")
    _dict(image["payload"], key)
    return image


def _plan_assets(conn, rewrite) -> list[tuple[str, dict, dict]]:
    """``(id, before, after)`` for every stored asset. Every payload is read and rewritten
    BEFORE anything is written, so one malformed row refuses the revision with nothing changed
    (security review S7)."""
    return [
        (row_id, payload, rewrite(payload))
        for row_id, payload in (
            (row_id, _dict(raw, row_id)) for row_id, raw in conn.execute(_SELECT).fetchall()
        )
    ]


def _plan_snapshots(conn, rewrite) -> list[tuple[str, int, dict]]:
    """``(checkpoint_id, seq, image)`` for every owned-asset before-image the rewrite changes."""
    planned = []
    for checkpoint_id, seq, raw in conn.execute(_SELECT_SNAPSHOTS).fetchall():
        image = _snapshot_image(raw, f"checkpoint snapshot {checkpoint_id}/{seq}")
        if image is None:
            continue
        rewritten = {**image, "payload": rewrite(image["payload"])}
        if rewritten != image:
            planned.append((checkpoint_id, seq, rewritten))
    return planned


def _write(conn, assets: list[tuple[str, dict, dict]], snapshots: list[tuple]) -> None:
    for row_id, before, after in assets:
        if after != before:
            conn.execute(_UPDATE, {"id": row_id, "payload": json.dumps(after)})
    for checkpoint_id, seq, image in snapshots:
        conn.execute(
            _UPDATE_SNAPSHOT,
            {"checkpoint_id": checkpoint_id, "seq": seq, "image": json.dumps(image)},
        )


def _log_value_drift(before: dict, after: dict) -> None:
    """The old reader took stored marks[0]; the new one takes the newest valuation. They differ
    only for a hand-edited, unsorted import — logged, never refused."""
    old = (before.get("marks") or [{}])[0].get("value")
    new = (after.get("marks") or [{}])[0].get("value")
    if old != new:
        _log.warning("owned asset %s: current value %r -> %r", after.get("id"), old, new)


def _log_shared_loans(assets: list[dict]) -> None:
    holders: dict[str, list[str]] = {}
    for asset in assets:
        if not asset.get("archived") and asset.get("loan_account_id"):
            holders.setdefault(asset["loan_account_id"], []).append(asset.get("id"))
    for loan_id, asset_ids in holders.items():
        if len(asset_ids) > 1:
            _log.warning("loan %s secures %d live assets: %s", loan_id, len(asset_ids), asset_ids)


def _count(value) -> int:
    return len(value) if isinstance(value, list) else 0


def _changes(before: dict, after: dict) -> dict[str, int]:
    """What one asset's rewrite did, for the summary line: only step 2 removes marks and only
    step 6 removes facts, so the length differences count them; a re-minted id is one the row
    did not hold before."""
    marks = before.get("marks") or []
    held = {m.get("id") for m in marks if isinstance(m.get("id"), str)}
    return {
        "rewritten": int(after != before),
        "purchase_marks": _count(marks) - _count(after.get("marks")),
        "notes": sum(1 for m in marks if "source" in m or m.get("note") == _NONE_NOTE),
        "mark_ids": sum(1 for m in after.get("marks") or [] if m.get("id") not in held),
        "added_facts": _count(before.get("facts")) - _count(after.get("facts")),
        "loans": int(
            before.get("loan_account_id") is not None and after.get("loan_account_id") is None
        ),
    }


def _log_summary(assets: list[tuple[str, dict, dict]], snapshots: int) -> None:
    totals: dict[str, int] = {}
    for _row_id, before, after in assets:
        for key, count in _changes(before, after).items():
            totals[key] = totals.get(key, 0) + count
    _log.info(
        "owned assets: %d of %d rewritten (%d create-time purchase marks dropped, %d notes "
        'normalized, %d mark ids re-minted, %d "Added" facts removed, %d loans released on '
        "sold assets); %d checkpoint snapshots migrated",
        totals.get("rewritten", 0),
        len(assets),
        totals.get("purchase_marks", 0),
        totals.get("notes", 0),
        totals.get("mark_ids", 0),
        totals.get("added_facts", 0),
        totals.get("loans", 0),
        snapshots,
    )


def _migrate_all(conn) -> int:
    """Rewrite every asset and owned-asset checkpoint snapshot that changes; returns how many
    rows were written (0 on a second run)."""
    assets = _plan_assets(conn, _migrate_asset)
    snapshots = _plan_snapshots(conn, _migrate_asset)
    for _row_id, before, after in assets:
        if after != before:
            _log_value_drift(before, after)
    _write(conn, assets, snapshots)
    _log_shared_loans([after for _row_id, _before, after in assets])
    _log_summary(assets, len(snapshots))
    return sum(after != before for _row_id, before, after in assets) + len(snapshots)


def upgrade() -> None:
    _migrate_all(op.get_bind())


def _shown_purchase_point(asset: dict, marks: list[dict]) -> dict | None:
    """The purchase point the projection shows (frozen copy of the value rule): not for a
    policy, a finite cost > 0 with a date, dated on or before the newest valuation."""
    cost, cost_date = asset.get("cost"), asset.get("cost_date")
    if asset.get("category") == "policy" or not cost_date or not marks:
        return None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost):
        return None
    if cost <= 0 or cost_date > max(m.get("date") or "" for m in marks):
        return None
    oldest = min((s for s in (_stamp(m.get("created_at")) for m in marks) if s), default=None)
    if oldest is None:
        return None
    digest = hashlib.sha256(f"{asset.get('id')}:purchase".encode()).hexdigest()[:12]
    return {
        "id": f"mk_{digest}",
        "date": cost_date,
        "value": float(cost),
        "note": _PURCHASE_NOTE,
        "by": "you",
        "created_at": (oldest - _MICROSECOND).isoformat(),
    }


def _unmigrate_asset(payload: dict) -> dict:
    asset = copy.deepcopy(payload)
    marks = [{k: v for k, v in m.items() if k != "edited_at"} for m in asset.get("marks") or []]
    purchase = _shown_purchase_point(asset, marks)
    if purchase is not None:
        marks.append(purchase)
    asset["marks"] = sorted(
        marks, key=lambda m: (m.get("date") or "", m.get("created_at") or ""), reverse=True
    )
    return asset


def downgrade() -> None:
    conn = op.get_bind()
    _write(
        conn,
        _plan_assets(conn, _unmigrate_asset),
        _plan_snapshots(conn, _unmigrate_asset),
    )
