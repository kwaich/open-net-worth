"""Owned-assets value history — the ops that write an asset's valuations. Dispatched ONLY by
the write choke (``services.owned_assets.apply_owned_asset_change``); each returns the stored
row (``None`` if the asset vanished) and the choke projects it.

Recording a new value always appends a dated valuation; correcting a wrong one replaces it in
place and stamps it edited; removing one deletes it. Each op validates its input first, then
reads, checks and writes in ONE locked unit of work, so a check never passes against a row a
concurrent writer has already changed.
"""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.db.engine import locked_unit_of_work
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.db.tables import owned_assets as owned_assets_table
from backend.services import owned_assets_rules as rules


def add_mark(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    mark = {
        "value": rules.positive_value(change.get("value")),
        "note": rules.bounded_text(change.get("note") or "", rules.MAX_MARK_NOTE, "note"),
        "date": rules.parse_day(change.get("date"), field="date", future=rules.FUTURE_VALUE)
        or user_today().isoformat(),
        "by": change.get("by") or "you",
    }
    with locked_unit_of_work(owned_assets_table) as conn:
        asset = rules.require_asset(store, asset_id, conn=conn)
        rules.refuse_value_before_purchase(mark["date"], asset)
        return store.add_mark(asset_id, mark, conn=conn)


def _correction(change: dict) -> dict:
    """The fields a correction changes, parsed like a new value's. A missing (None) field is
    left as it is."""
    fields: dict = {}
    if change.get("value") is not None:
        fields["value"] = rules.positive_value(change["value"])
    day = rules.parse_day(change.get("date"), field="date", future=rules.FUTURE_VALUE)
    if day is not None:
        fields["date"] = day
    if change.get("note") is not None:
        fields["note"] = rules.bounded_text(change["note"], rules.MAX_MARK_NOTE, "note")
    return fields


def correct_mark(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    fields = _correction(change)
    mark_id = change.get("mark_id")
    with locked_unit_of_work(owned_assets_table) as conn:
        asset = rules.require_asset(store, asset_id, conn=conn)
        mark = rules.require_valuation(asset, mark_id)
        if "date" in fields and fields["date"] != mark.get("date"):
            rules.refuse_value_before_purchase(fields["date"], asset)
        return store.correct_mark(asset_id, mark_id, fields, conn=conn)


def remove_mark(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    """The store refuses the last valuation (409 last_value) inside this same unit."""
    mark_id = change.get("mark_id")
    with locked_unit_of_work(owned_assets_table) as conn:
        asset = rules.require_asset(store, asset_id, conn=conn)
        rules.require_valuation(asset, mark_id)
        return store.remove_mark(asset_id, mark_id, conn=conn)
