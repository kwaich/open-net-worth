"""Portable-bundle normalization for owned assets — an older export's rows are upgraded to the
current stored shape before they re-enter storage (``db.portable_bridge``'s ``assets.json``
bridge). Kept apart from ``OwnedAssetsStore`` so the store holds only today's shape.

The value-history steps are the SAME seven as revision ``202609261500_owned_asset_purchase_
point_derived`` — a migration never imports live code, so this is the live copy, pinned equal
to the migration's frozen one by ``tests/test_owned_assets_portability.py``.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, timedelta, timezone

from backend.core.errors import ValidationError
from backend.db.attachment_store import is_attachment_id
from backend.db.owned_assets_store import _clean_facts

_FALSE_INSURED = {"no", "false", "not insured", "uninsured", "none"}
_PLAIN_INSURED = {"yes", "true", "insured"}
_PURCHASE_NOTE = "entered on add"
_NONE_NOTE = "— none"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _legacy_date(value: str) -> str | None:
    text = value.strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    for pattern in ("%Y-%m", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(text, pattern).date().replace(day=1).isoformat()
        except ValueError:
            continue
    return None


def _refuse_bad_money(payload: dict) -> None:
    row_id = payload.get("id")
    for field in ("cost", "share_pct", "disposal_proceeds"):
        value = payload.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(
                f"owned asset {row_id!r}: money field {field!r} is not numeric ({value!r})"
            )
        if not math.isfinite(value):
            raise ValidationError(
                f"owned asset {row_id!r}: money field {field!r} is not finite ({value!r})"
            )
    share = payload.get("share_pct")
    if share is not None and not 0 <= share <= 100:
        raise ValidationError(
            f"owned asset {row_id!r}: 'share_pct' must be between 0 and 100 ({share!r})"
        )
    for mark in payload.get("marks") or []:
        value = mark.get("value") if isinstance(mark, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(
                f"owned asset {row_id!r}: money field 'marks.value' is not numeric ({value!r})"
            )
        if not math.isfinite(value):
            raise ValidationError(
                f"owned asset {row_id!r}: money field 'marks.value' is not finite ({value!r})"
            )


def _refuse_bad_attachment_refs(payload: dict) -> None:
    """A photo or document must point at an id the attachment store mints: asset ops delete a
    replaced photo's or removed document's file by it (security review 2026-09-26, S1)."""
    row_id = payload.get("id")
    photo = payload.get("photo_ref")
    if photo is not None and not is_attachment_id(photo):
        raise ValidationError(
            f"owned asset {row_id!r}: 'photo_ref' {photo!r} is not an attachment id"
        )
    for doc in payload.get("docs") or []:
        ref = doc.get("ref_id") if isinstance(doc, dict) else None
        if not is_attachment_id(ref):
            raise ValidationError(
                f"owned asset {row_id!r}: 'docs.ref_id' {ref!r} is not an attachment id"
            )


def _type_legacy_facts(payload: dict) -> None:
    """Insurance and maturity became typed fields (revision 202609101600)."""
    insured = payload.get("insured") if isinstance(payload.get("insured"), bool) else False
    maturity = payload.get("maturity_date") or None
    facts: list[list[str]] = []
    for label, value in _clean_facts(payload.get("facts")):
        key = label.strip().lower().replace("’", "'")
        detail = value.strip()
        if key == "insured":
            normalized = detail.lower()
            insured = bool(detail) and normalized not in _FALSE_INSURED
            if insured and normalized not in _PLAIN_INSURED:
                facts.append(["Insurance details", detail])
        elif key == "matures":
            parsed = _legacy_date(detail)
            if parsed:
                maturity = parsed
            elif detail:
                facts.append(["Maturity (legacy text)", detail])
        elif key in {"where is it kept", "where it's kept"}:
            facts.append(["Where it’s kept", detail])
        else:
            facts.append([label, value])
    payload.update(insured=insured, maturity_date=maturity, facts=facts)


def _stamp(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _minted_id(asset_id, mark: dict, taken: set[str]) -> str:
    n = 0
    while True:
        seed = f"{asset_id}|{mark.get('date')}|{mark.get('created_at')}|{mark.get('value')!r}|{n}"
        candidate = f"mk_{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
        if candidate not in taken:
            return candidate
        n += 1


def _unique_ids(marks: list[dict], asset_id) -> list[dict]:
    """Step 4: correct and remove find a value by its id, and "purchase" names the derived
    point. The first holder of an id keeps it; a missing, duplicate or "purchase" id is
    re-minted from the mark's own content, so normalizing twice changes nothing."""
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


def _normalized_marks(marks: list[dict], asset_id) -> list[dict]:
    """Steps 1–5: notes (`source` → note, "— none" → ""), the create-time purchase copy dropped
    (the ONE "entered on add" mark 1µs older than a sibling; several → none), `edited_at`
    present, ids unique, sorted newest-first by (date, created_at, id)."""
    out = []
    for mark in marks:
        mark = dict(mark)
        note = mark.get("note") or mark.pop("source", None) or ""
        mark.pop("source", None)
        mark["note"] = "" if note == _NONE_NOTE else note
        mark.setdefault("edited_at", None)
        out.append(mark)
    stamps = [_stamp(m.get("created_at")) for m in out]
    known = {s for s in stamps if s is not None}
    copies = [
        i
        for i, mark in enumerate(out)
        if mark["note"] == _PURCHASE_NOTE
        and stamps[i] is not None
        and stamps[i] + timedelta(microseconds=1) in known
    ]
    if len(copies) == 1:
        out.pop(copies[0])
    return sorted(
        _unique_ids(out, asset_id),
        key=lambda m: (m.get("date") or "", m.get("created_at") or "", m.get("id") or ""),
        reverse=True,
    )


def _without_added_fact(payload: dict) -> None:
    """Step 6: the system "Added" fact — the creation month in English, for the UTC date ±1
    day (create formatted the server's LOCAL date)."""
    created = _stamp(payload.get("created_at")) if payload.get("created_at") else None
    if created is None:
        return
    day = created.astimezone(timezone.utc).date()
    months = {
        f"{_MONTHS[d.month - 1]} {d.year}" for d in (day - timedelta(1), day, day + timedelta(1))
    }
    payload["facts"] = [
        fact for fact in payload["facts"] if not (fact[0] == "Added" and fact[1] in months)
    ]


def normalize_imported_asset(data: dict) -> dict:
    """Upgrade an older portable asset row before it can re-enter current storage."""
    payload = dict(data)
    _refuse_bad_money(payload)
    _refuse_bad_attachment_refs(payload)
    for key in ("cadence_months", "automation_id", "months_old", "automation"):
        payload.pop(key, None)
    _type_legacy_facts(payload)
    payload["marks"] = _normalized_marks(payload.get("marks") or [], payload.get("id"))
    _without_added_fact(payload)
    if payload.get("archived") and payload.get("loan_account_id") is not None:
        payload["loan_account_id"] = None  # step 7: a sold asset secures nothing
    return payload
