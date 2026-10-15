"""Synthetic owned-asset rows in the shapes the app wrote BEFORE 2026-09-26 — shared by the
purchase-point migration test and the portable-import normalization test, so both pin the
same legacy inputs.

The old write paths, reproduced exactly:

* ``create`` stored ``[value mark, purchase mark]`` — the purchase mark only when a cost was
  given, noted "entered on add", dated ``cost_date`` or (no date) the create day — and the
  store stamped the i-th mark ``now − i µs``, so the purchase mark is exactly 1µs older than
  its sibling. The value mark's default note was "— none". A system ``["Added", "<Mon
  YYYY>"]`` fact was appended, formatted from the server's LOCAL date.
* ``add_mark`` appended a mark stamped by the wall clock (always later than the create pair)
  and re-sorted newest-first by ``(date, created_at)``.
* ``update`` changed identity fields only — never the stored purchase mark.
"""

from __future__ import annotations

from datetime import datetime, timedelta

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_year(day: str) -> str:
    year, month = int(day[:4]), int(day[5:7])
    return f"{_MONTHS[month - 1]} {year}"


def _old_sort(marks: list[dict]) -> list[dict]:
    return sorted(marks, key=lambda m: (m["date"], m["created_at"]), reverse=True)


def old_create(
    asset_id: str,
    *,
    value: float,
    created: str,
    category: str = "watch",
    cost: float | None = None,
    cost_date: str | None = None,
    note: str = "— none",
    local_day: str | None = None,
    **fields,
) -> dict:
    """`created` is the UTC creation instant; `local_day` the server's local date that day
    (the "Added" fact's month) — the UTC day unless the test crosses a boundary."""
    now = datetime.fromisoformat(created)
    day = created[:10]
    marks = [
        {
            "id": f"mk_{asset_id}_v",
            "date": day,
            "value": value,
            "note": note,
            "by": "you",
            "created_at": now.isoformat(),
        }
    ]
    if cost is not None:
        marks.append(
            {
                "id": f"mk_{asset_id}_p",
                "date": cost_date or day,
                "value": cost,
                "note": "entered on add",
                "by": "you",
                "created_at": (now - timedelta(microseconds=1)).isoformat(),
            }
        )
    asset = {
        "id": asset_id,
        "name": asset_id.replace("_", " "),
        "category": category,
        "description": "",
        "photo_ref": None,
        "facts": [["Added", month_year(local_day or day)]],
        "notes": "",
        "cost": cost,
        "cost_date": cost_date,
        "share_pct": 100.0,
        "share_note": "",
        "loan_account_id": None,
        "insured": False,
        "maturity_date": None,
        "currency": "SGD",
        "marks": _old_sort(marks),
        "docs": [],
        "archived": False,
        "disposed_on": None,
        "disposal_proceeds": None,
        "disposal_txn_id": None,
        "disposal_account_id": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    asset.update(fields)
    return asset


def old_add_mark(asset: dict, *, day: str, value: float, stamp: str, note: str = "") -> dict:
    """A value recorded later — its stamp is the wall clock at the time, `day` may be back-dated."""
    mark = {
        "id": f"mk_{asset['id']}_{stamp[:19].replace(':', '').replace('-', '')}",
        "date": day,
        "value": value,
        "note": note,
        "by": "you",
        "created_at": stamp,
    }
    return {**asset, "marks": _old_sort([*asset["marks"], mark])}


def old_update(asset: dict, **changes) -> dict:
    """An identity edit: the stored purchase mark stays exactly where it was."""
    return {**asset, **changes}


def legacy_book() -> list[dict]:
    """One row per legacy shape the migration and the import normalization must handle."""
    purchased = old_create(
        "flat",
        category="property",
        value=1_150_000,
        cost=900_000,
        cost_date="2019-03-15",
        created="2026-09-06T02:10:00.000000+00:00",
        note="lark-refinance.pdf",
    )
    undated = old_create("watch", value=15_000, cost=10_000, created="2026-09-07T05:00:00+00:00")
    boundary = old_create(
        "bike",
        category="other",
        value=900,
        created="2026-09-30T23:30:00+00:00",
        local_day="2026-10-01",
    )
    edited = old_update(
        old_add_mark(
            old_create(
                "car",
                category="vehicle",
                value=40_000,
                cost=38_000,
                cost_date="2025-01-10",
                created="2026-09-08T08:00:00+00:00",
            ),
            day="2025-06-30",
            value=39_000,
            stamp="2026-09-09T08:00:00+00:00",
        ),
        cost_date="2024-11-01",
    )
    policy = old_update(
        old_create(
            "endowment",
            category="other",
            value=42_000,
            cost=18_000,
            cost_date="2019-06-01",
            created="2026-09-10T08:00:00+00:00",
        ),
        category="policy",
    )
    renamed = {
        **old_create("art", category="art", value=5_000, created="2026-09-11T08:00:00+00:00"),
        "facts": [["Added", "Sep 2026"], ["Added", "2019"], ["Artist", "Unknown"]],
    }
    sourced = old_create(
        "gold", category="metal", value=34_300, created="2026-09-05T08:00:00+00:00"
    )
    sourced["marks"] = [
        {k: v for k, v in sourced["marks"][0].items() if k != "note"} | {"source": "dealer"}
    ]
    sold = old_create(
        "scooter",
        category="vehicle",
        value=2_000,
        created="2026-09-12T08:00:00+00:00",
        loan_account_id="acc_scooter_loan",
        archived=True,
        disposed_on="2026-09-20",
    )
    return [purchased, undated, boundary, edited, policy, renamed, sourced, sold]


def legacy_id_clashes() -> list[dict]:
    """Mark ids an import could carry that correct/remove cannot address: a duplicate, a
    missing id, and the id "purchase" (reserved for the derived purchase point)."""
    asset = old_create("clash", value=10, created="2026-09-13T08:00:00+00:00")
    first = asset["marks"][0]
    asset["marks"] = [
        {**first, "id": "mk_same", "date": "2026-01-01", "created_at": "2026-09-13T09:00:00+00:00"},
        {**first, "id": "mk_same", "date": "2026-02-01", "created_at": "2026-09-13T10:00:00+00:00"},
        {k: v for k, v in first.items() if k != "id"} | {"date": "2026-03-01"},
        {
            **first,
            "id": "purchase",
            "date": "2026-04-01",
            "created_at": "2026-09-13T11:00:00+00:00",
        },
    ]
    return [asset]
