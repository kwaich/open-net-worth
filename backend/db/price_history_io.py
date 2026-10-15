"""price_history import/export between the JSON keyed-dict shape and ``price_points``.

Shape: ``{price_source_id: [{"date", "price", "source"}, ...]}``, each series sorted by
date ascending — the store's "one point per day" invariant (now the table's composite
PK). ``import_price_history`` VERIFIES each series arrives sorted and ABORTS (raises,
no silent normalization) on the first violation — the fail-closed migration wrapper
restores the pre-migrate backup on any exception, so raising here is what triggers that
safety net rather than silently accepting a corrupt or hand-edited source file.
``export_price_history`` is the round-trip verifier for the cutover revision AND the
portable-export seam (spec D6 step 4)."""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from backend.db.tables import price_points

_BATCH = 1000


def import_price_history(conn: Connection, raw: dict) -> int:
    rows: list[dict] = []
    for pid, points in raw.items():
        prev_date: str | None = None
        for point in points:
            d = point.get("date", "")
            if prev_date is not None and d <= prev_date:
                raise ValueError(
                    f"price_history import: series {pid!r} is not sorted strictly ascending "
                    f"by date ({d!r} does not follow {prev_date!r})"
                )
            prev_date = d
            rows.append(
                {
                    "price_source_id": pid,
                    "date": d,
                    "price": point.get("price"),
                    "source": point.get("source") or "",
                }
            )
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(price_points), rows[i : i + _BATCH])
    return len(rows)


def export_price_history(conn: Connection) -> dict:
    out: dict[str, list[dict]] = {}
    q = select(price_points).order_by(price_points.c.price_source_id, price_points.c.date)
    for r in conn.execute(q):
        out.setdefault(r.price_source_id, []).append(
            {"date": r.date, "price": r.price, "source": r.source}
        )
    return out
