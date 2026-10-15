"""
Portability coverage for the owned-assets domain (ADR 2026-09-06-assets-tab-faithful-port,
decision 12) — assets.json / asset_categories.json join `core.portable.PORTABLE_FILES`,
`db.portable_bridge.BRIDGED`, and the `/api/reset` wipe map, bridged the SAME generic
`_planning_bridge` way as `commitments.json` (tests/db/test_planning_portable.py exercises
that sibling's own behavior in depth — this file stays scoped to the owned-assets pair).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from backend.core import portable
from backend.core.errors import ValidationError
from backend.db import config_io, portable_bridge
from backend.db.engine import get_engine
from backend.db.owned_assets_store import OwnedAssetCategoriesStore, OwnedAssetsStore
from backend.db.tables import automations, owned_asset_categories, owned_assets
from backend.services import owned_assets as owned_assets_service
from backend.services import portability as portability_service
from tests._legacy_owned_assets import legacy_book, legacy_id_clashes

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "backend/db/alembic/versions/202609261500_owned_asset_purchase_point_derived.py"
)


def test_owned_assets_files_are_portable_and_bridged():
    names = {p.name for p in portable.PORTABLE_FILES}
    assert {"assets.json", "asset_categories.json"} <= names
    assert {"assets.json", "asset_categories.json"} <= set(portable_bridge.BRIDGED)
    assert {"assets.json", "asset_categories.json"} <= set(portable_bridge._WIPE_FNS)


def test_legacy_portable_rows_cannot_restore_review_fields(db_isolated):
    legacy_asset = {
        "id": "oa_legacy",
        "name": "Legacy policy",
        "category": "policy",
        "cadence_months": 12,
        "automation_id": "auto_old",
        "facts": [["Insured", "Yes"], ["Matures", "Jun 2031"]],
    }
    legacy_category = {
        "key": "c_legacy",
        "label": "Legacy",
        "plural": "Legacy",
        "cadence_months": 12,
    }
    with get_engine().begin() as conn:
        portable_bridge.BRIDGED["assets.json"].import_fn(conn, [legacy_asset])
        portable_bridge.BRIDGED["asset_categories.json"].import_fn(conn, [legacy_category])

    restored = OwnedAssetsStore().find_by_id("oa_legacy")
    assert restored is not None
    assert restored["insured"] is True
    assert restored["maturity_date"] == "2031-06-01"
    assert {"cadence_months", "automation_id"}.isdisjoint(restored)
    assert "cadence_months" not in OwnedAssetCategoriesStore().find_by_id("c_legacy")


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("cost", float("nan")),
        ("share_pct", float("inf")),
        ("disposal_proceeds", "not-money"),
        ("cost", "100"),
        ("share_pct", True),
        ("share_pct", 150),
        ("share_pct", -1),
    ],
)
def test_portable_asset_import_rejects_invalid_money_fields(db_isolated, field, bad):
    row = {"id": "oa_bad", "name": "Bad asset", "category": "other", field: bad}

    with get_engine().begin() as conn:
        with pytest.raises(ValidationError, match=field):
            portable_bridge.BRIDGED["assets.json"].import_fn(conn, [row])

    assert OwnedAssetsStore().all() == []


@pytest.mark.parametrize("bad", [float("nan"), float("1e400"), "not-money", "100", True])
def test_portable_asset_import_rejects_invalid_nested_mark_values(db_isolated, bad):
    row = {
        "id": "oa_bad_mark",
        "name": "Bad asset",
        "category": "other",
        "marks": [{"id": "mk_bad", "date": "2026-01-01", "value": bad}],
    }

    with get_engine().begin() as conn:
        with pytest.raises(ValidationError, match="marks.value"):
            portable_bridge.BRIDGED["assets.json"].import_fn(conn, [row])

    assert OwnedAssetsStore().all() == []


def test_legacy_asset_reminder_automations_are_not_restored(db_isolated):
    retired = {
        "id": "auto_asset_review",
        "name": "Review asset",
        "provenance": {"system_key": "asset_remark:oa_legacy"},
    }
    unrelated = {
        "id": "auto_budget",
        "name": "Budget check",
        "provenance": {"system_key": "budget:monthly"},
    }

    with get_engine().begin() as conn:
        count = portable_bridge.BRIDGED["automations.json"].import_fn(conn, [retired, unrelated])

    assert count == 1
    with get_engine().connect() as conn:
        assert config_io.export_payload_list(conn, automations) == [unrelated]


def test_export_bundle_round_trips_an_asset_with_marks(db_isolated):
    asset = owned_assets_service.apply_owned_asset_change(
        {
            "op": "create",
            "name": "88 Marina Rise",
            "category": "property",
            "value": 1_150_000,
            "cost": 900_000,
            "cost_date": "2019-03-15",
        }
    )
    appraised = owned_assets_service.apply_owned_asset_change(
        {"op": "add_mark", "id": asset["id"], "value": 1_200_000, "note": "appraisal"}
    )
    corrected = owned_assets_service.apply_owned_asset_change(
        {
            "op": "correct_mark",
            "id": asset["id"],
            "mark_id": appraised["marks"][0]["id"],
            "note": "appraisal (bank)",
        }
    )
    edited_at = corrected["marks"][0]["edited_at"]
    assert edited_at
    category = owned_assets_service.create_category("Vinyl & hi-fi")

    bundle = portability_service.build_export_bundle()
    result = portability_service.apply_import(bundle)

    assert set(result["restored_files"]) >= {"assets.json", "asset_categories.json"}
    assert result["row_counts"]["assets.json"] == 1
    assert result["row_counts"]["asset_categories.json"] == 1

    restored = OwnedAssetsStore().find_by_id(asset["id"])
    assert restored is not None
    assert {m["value"] for m in restored["marks"]} == {1_150_000, 1_200_000}  # stored valuations
    assert restored["category"] == "property"
    assert [m["edited_at"] for m in restored["marks"]].count(edited_at) == 1
    projected = owned_assets_service.get_asset(asset["id"])["marks"]
    assert [m["kind"] for m in projected] == ["valuation", "valuation", "purchase"]
    assert projected[-1]["value"] == 900_000  # re-derived from the round-tripped cost/cost_date

    restored_cat = OwnedAssetCategoriesStore().find_by_id(category["key"])
    assert restored_cat is not None
    assert restored_cat["label"] == "Vinyl & hi-fi"


def test_export_then_wipe_then_import_restores_everything(db_isolated, tmp_path, monkeypatch):
    owned_assets_service.apply_owned_asset_change(
        {"op": "create", "name": "Gold bars", "category": "metal", "value": 5_000}
    )
    owned_assets_service.create_category("Vinyl & hi-fi")

    bundle = portability_service.build_export_bundle()

    with get_engine().begin() as conn:
        portable_bridge._WIPE_FNS["assets.json"](conn)
        portable_bridge._WIPE_FNS["asset_categories.json"](conn)

    assert OwnedAssetsStore().all() == []
    assert OwnedAssetCategoriesStore().all() == []

    portability_service.apply_import(bundle)

    assert len(OwnedAssetsStore().all()) == 1
    assert len(OwnedAssetCategoriesStore().all()) == 1


def test_reset_wipes_owned_assets_tables(db_isolated):
    owned_assets_service.apply_owned_asset_change(
        {"op": "create", "name": "Old Car", "category": "vehicle", "value": 8_000}
    )
    owned_assets_service.create_category("Vinyl & hi-fi")

    with get_engine().begin() as conn:
        portable_bridge._WIPE_FNS["assets.json"](conn)
        portable_bridge._WIPE_FNS["asset_categories.json"](conn)
        from sqlalchemy import select

        assert list(conn.execute(select(owned_assets))) == []
        assert list(conn.execute(select(owned_asset_categories))) == []


def test_legacy_bundle_import_is_normalized(db_isolated):
    """A backup taken before the purchase point became derived restores in today's shape: no
    stored purchase copy, no "— none"/`source` notes, no system "Added" fact, every value
    carrying edited_at, and a sold asset's loan released."""
    with get_engine().begin() as conn:
        portable_bridge.BRIDGED["assets.json"].import_fn(conn, legacy_book())

    rows = {a["id"]: a for a in OwnedAssetsStore().all()}
    assert [m["id"] for m in rows["flat"]["marks"]] == ["mk_flat_v"]
    assert rows["flat"]["marks"][0]["note"] == "lark-refinance.pdf"
    assert [m["note"] for m in rows["watch"]["marks"]] == [""]
    assert rows["gold"]["marks"][0]["note"] == "dealer"
    assert all("source" not in m and "edited_at" in m for a in rows.values() for m in a["marks"])
    assert rows["bike"]["facts"] == []
    assert rows["art"]["facts"] == [["Added", "2019"], ["Artist", "Unknown"]]
    assert rows["scooter"]["loan_account_id"] is None
    # The purchase point comes back derived — following the edited date, none for a policy.
    car = owned_assets_service.get_asset("car")["marks"]
    assert (car[-1]["kind"], car[-1]["date"]) == ("purchase", "2024-11-01")
    endowment = owned_assets_service.get_asset("endowment")["marks"]
    assert [m["kind"] for m in endowment] == ["valuation"]


def test_import_normalization_matches_the_migration():
    """The migration's helper is frozen (a migration never imports live code), so the live
    import path carries its own copy of the same steps — pinned equal on the shared fixtures."""
    spec = importlib.util.spec_from_file_location("_purchase_point_rev", _MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    from backend.db.owned_assets_legacy import normalize_imported_asset

    for asset in [*legacy_book(), *legacy_id_clashes()]:
        assert normalize_imported_asset(asset) == migration._migrate_asset(asset), asset["id"]


@pytest.mark.parametrize("crafted", ["../../*", "*", "att_x", None])
def test_portable_attachment_import_refuses_a_malformed_id(db_isolated, crafted):
    """An attachment's files are named after its id, so the id must be one the store mints."""
    rows = [{"id": "att_0123456789ab", "filename": "a.csv"}, {"id": crafted, "filename": "b"}]
    with get_engine().begin() as conn:
        with pytest.raises(ValidationError, match="attachment id"):
            portable_bridge.BRIDGED["attachments.json"].import_fn(conn, rows)
    with get_engine().connect() as conn:
        from sqlalchemy import select

        from backend.db.tables import attachments

        assert list(conn.execute(select(attachments))) == []


def test_inspect_refuses_malformed_attachment_ids_before_anything_is_applied():
    import io
    import json
    import zipfile

    from backend.services import portability as portability_service

    rows = json.dumps([{"id": "../../*", "filename": "x"}]).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("attachments.json", rows)
        manifest = {
            "schema_version": portability_service.SCHEMA_VERSION,
            "entries": {"attachments.json": {"sha256": portability_service._sha256(rows)}},
        }
        zf.writestr("manifest.json", json.dumps(manifest))
    report = portability_service.inspect_bundle(buf.getvalue())
    assert report["ok"] is False
    assert any("not an attachment id" in e for e in report["errors"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("photo_ref", "../../*"),
        ("docs", [{"ref_id": "*", "name": "everything"}]),
        ("docs", [{"name": "no ref"}]),
    ],
)
def test_portable_asset_import_refuses_malformed_attachment_refs(db_isolated, field, value):
    row = {"id": "oa_bad_ref", "name": "Bad asset", "category": "other", field: value}
    with get_engine().begin() as conn:
        with pytest.raises(ValidationError, match="attachment id"):
            portable_bridge.BRIDGED["assets.json"].import_fn(conn, [row])
    assert OwnedAssetsStore().all() == []


def test_inspect_refuses_malformed_asset_attachment_refs():
    import io
    import json
    import zipfile

    from backend.services import portability as portability_service

    rows = json.dumps([{"id": "oa_x", "photo_ref": "../../*", "docs": [{"ref_id": "*"}]}])
    raw = rows.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("assets.json", raw)
        manifest = {
            "schema_version": portability_service.SCHEMA_VERSION,
            "entries": {"assets.json": {"sha256": portability_service._sha256(raw)}},
        }
        zf.writestr("manifest.json", json.dumps(manifest))
    report = portability_service.inspect_bundle(buf.getvalue())
    assert report["ok"] is False
    assert sum("not an attachment id" in e for e in report["errors"]) == 2


def test_import_re_mints_duplicate_missing_and_reserved_mark_ids():
    """Correct and remove address a value by its id, so an imported asset's mark ids must be
    unique and never "purchase". The first holder keeps its id; re-minting is deterministic,
    so normalizing twice changes nothing."""
    from backend.db.owned_assets_legacy import normalize_imported_asset

    [asset] = legacy_id_clashes()
    normalized = normalize_imported_asset(asset)
    ids = [m["id"] for m in normalized["marks"]]
    assert len(set(ids)) == 4
    assert "purchase" not in ids and all(isinstance(i, str) and i.startswith("mk_") for i in ids)
    assert "mk_same" in ids
    assert normalize_imported_asset(normalized) == normalized
