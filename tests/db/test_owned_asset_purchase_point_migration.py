"""Revision 202609261500: the stored purchase mark goes, the purchase point is derived.

Built on synthetic rows in the exact shapes the old write paths produced
(``tests/_legacy_owned_assets.py``): 1µs create pairs, back-dated values with later stamps,
purchase/category edits that left the stored copy behind, the `source` key, "— none" notes,
"Added" facts — including one written across a UTC month boundary.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import logging
import pathlib
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, event, text

from backend.db.alembic_config import make_config
from backend.services import owned_assets_valuation as valuation
from tests._legacy_owned_assets import (
    legacy_book,
    legacy_id_clashes,
    old_add_mark,
    old_create,
    old_update,
)

_FROM = "202609231130_pending_position_fmv_basis"
_TARGET = "202609261500_owned_asset_purchase_point_derived"
_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "backend/db/alembic/versions" / f"{_TARGET}.py"
)


def _mod():
    spec = importlib.util.spec_from_file_location("_purchase_point_rev", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _book_at_from(tmp_path, assets: list[dict]):
    db_path = tmp_path / "onw.db"
    config = make_config(f"sqlite:///{db_path}")
    command.upgrade(config, _FROM)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO books (id, name, base_currency, created_at) "
                    "VALUES ('default', '', 'SGD', '')"
                )
            )
            for seq, asset in enumerate(assets):
                conn.execute(
                    text(
                        "INSERT INTO owned_assets (id, book_id, seq, payload) "
                        "VALUES (:id, 'default', :seq, :p)"
                    ),
                    {"id": asset["id"], "seq": seq, "p": json.dumps(asset)},
                )
        yield config, engine
    finally:
        engine.dispose()


def _rows(engine) -> dict[str, dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, payload FROM owned_assets")).fetchall()
    return {row_id: json.loads(raw) if isinstance(raw, str) else raw for row_id, raw in rows}


def _upgraded(tmp_path, assets: list[dict]) -> dict[str, dict]:
    with _book_at_from(tmp_path, assets) as (config, engine):
        command.upgrade(config, _TARGET)
        return _rows(engine)


def _notes(asset: dict) -> list[str]:
    return [m["note"] for m in asset["marks"]]


def test_upgrade_drops_create_time_purchase_marks_only(tmp_path):
    dated = old_create(
        "flat",
        value=1_150_000,
        cost=900_000,
        cost_date="2019-03-15",
        created="2026-09-06T02:10:00.123456+00:00",
        note="valuation.pdf",
    )
    undated = old_create("watch", value=15_000, cost=10_000, created="2026-09-07T05:00:00+00:00")
    costless = old_create("art", value=5_000, created="2026-09-08T05:00:00+00:00")
    rows = _upgraded(tmp_path, [dated, undated, costless])

    assert [(m["id"], m["value"]) for m in rows["flat"]["marks"]] == [("mk_flat_v", 1_150_000)]
    assert [(m["id"], m["value"]) for m in rows["watch"]["marks"]] == [("mk_watch_v", 15_000)]
    assert [m["id"] for m in rows["art"]["marks"]] == ["mk_art_v"]
    assert (rows["flat"]["cost"], rows["flat"]["cost_date"]) == (900_000, "2019-03-15")


def test_upgrade_keeps_a_user_note_reading_entered_on_add(tmp_path, caplog):
    """Only the create-time copy — 1µs older than its sibling value — is the purchase mark. A
    value the user noted "entered on add", or a bundle whose stamps were hand-edited, keeps
    its row; two candidates on one asset keep both."""
    typed = old_add_mark(
        old_create("gold", value=34_300, created="2026-09-05T08:00:00+00:00"),
        day="2026-09-10",
        value=35_000,
        stamp="2026-09-10T09:00:00+00:00",
        note="entered on add",
    )
    retimed = old_create("coin", value=900, cost=700, created="2026-09-06T08:00:00+00:00")
    retimed["marks"][1]["created_at"] = "2026-09-01T00:00:00+00:00"
    doubled = old_create("pair", value=500, cost=400, created="2026-09-07T08:00:00+00:00")
    twin = {
        **doubled["marks"][1],
        "id": "mk_twin",
        "created_at": "2026-09-08T07:59:59.999999+00:00",
    }
    doubled["marks"] = [
        *doubled["marks"],
        twin,
        {**doubled["marks"][0], "id": "mk_sib", "created_at": "2026-09-08T08:00:00+00:00"},
    ]

    with caplog.at_level(logging.WARNING):
        rows = _upgraded(tmp_path, [typed, retimed, doubled])

    assert "entered on add" in _notes(rows["gold"])
    assert len(rows["coin"]["marks"]) == 2
    assert len(rows["pair"]["marks"]) == 4
    assert "2 create-time purchase marks" in caplog.text


def test_upgrade_normalizes_source_and_none_notes(tmp_path):
    asset = old_create("gold", value=34_300, created="2026-09-05T08:00:00+00:00")
    first = asset["marks"][0]
    asset["marks"] = [
        {k: v for k, v in first.items() if k != "note"} | {"source": "dealer invoice"},
        {**first, "id": "mk_none", "date": "2026-01-01", "note": "— none"},
        {**first, "id": "mk_both", "date": "2025-01-01", "note": "", "source": "old source"},
    ]
    marks = _upgraded(tmp_path, [asset])["gold"]["marks"]
    assert [(m["id"], m["note"]) for m in marks] == [
        ("mk_gold_v", "dealer invoice"),
        ("mk_none", ""),
        ("mk_both", "old source"),
    ]
    assert all("source" not in m and m["edited_at"] is None for m in marks)


def test_upgrade_strips_only_the_system_added_fact(tmp_path):
    plain = old_create("art", value=5_000, created="2026-09-11T08:00:00+00:00")
    plain["facts"] += [["Added", "2019"], ["Artist", "Unknown"], ["Added", "Jan 2025"]]
    # Created 23:30 UTC on 30 Sep by a server eight hours ahead: its local day was 1 Oct.
    boundary = old_create(
        "bike", value=900, created="2026-09-30T23:30:00+00:00", local_day="2026-10-01"
    )
    dateless = {
        **old_create("coin", value=10, created="2026-09-12T08:00:00+00:00"),
        "created_at": None,
    }
    rows = _upgraded(tmp_path, [plain, boundary, dateless])

    assert rows["art"]["facts"] == [["Added", "2019"], ["Artist", "Unknown"], ["Added", "Jan 2025"]]
    assert rows["bike"]["facts"] == []
    assert rows["coin"]["facts"] == [["Added", "Sep 2026"]]


def test_upgrade_releases_loans_of_disposed_assets(tmp_path, caplog):
    sold = old_create(
        "scooter",
        value=2_000,
        created="2026-09-12T08:00:00+00:00",
        loan_account_id="acc_scooter",
        archived=True,
        disposed_on="2026-09-20",
    )
    home = old_create(
        "flat", value=1_000_000, created="2026-09-13T08:00:00+00:00", loan_account_id="acc_mortgage"
    )
    annex = old_create(
        "annex", value=300_000, created="2026-09-14T08:00:00+00:00", loan_account_id="acc_mortgage"
    )
    with caplog.at_level(logging.WARNING):
        rows = _upgraded(tmp_path, [sold, home, annex])
    assert rows["scooter"]["loan_account_id"] is None
    assert rows["flat"]["loan_account_id"] == rows["annex"]["loan_account_id"] == "acc_mortgage"
    assert "loan acc_mortgage secures 2 live assets" in caplog.text


def _random_legacy_asset(rng: random.Random, i: int) -> dict:
    created = datetime(2026, 9, 1, 8, tzinfo=timezone.utc) + timedelta(
        days=rng.randint(0, 20), microseconds=rng.randint(0, 999_999)
    )
    create_day = created.date()
    cost = rng.choice([None, round(rng.uniform(100, 900_000), 2)])
    cost_date = rng.choice([None, (create_day - timedelta(days=rng.randint(0, 4000))).isoformat()])
    if cost is None:
        cost_date = None
    asset = old_create(
        f"a{i}",
        value=round(rng.uniform(100, 1_000_000), 2),
        created=created.isoformat(),
        cost=cost,
        cost_date=cost_date,
        category=rng.choice(["property", "vehicle", "watch", "other"]),
    )
    for n in range(rng.randint(0, 4)):
        stamp = created + timedelta(days=n + 1, seconds=rng.randint(0, 9_999))
        # A back-dated observation, or one dated the day it was recorded (after creation).
        back = create_day - timedelta(days=rng.randint(0, 5000))
        asset = old_add_mark(
            asset,
            day=rng.choice([back, stamp.date()]).isoformat(),
            value=round(rng.uniform(100, 1_000_000), 2),
            stamp=stamp.isoformat(),
        )
    for _ in range(rng.randint(0, 2)):
        asset = old_update(
            asset,
            **rng.choice(
                [
                    {"cost_date": (create_day - timedelta(days=rng.randint(0, 6000))).isoformat()},
                    {"cost": round(rng.uniform(1, 2_000_000), 2)},
                    {"category": "policy"},
                    {"cost": None},
                ]
            ),
        )
    return asset


def test_upgrade_never_moves_todays_value(tmp_path):
    """The old reader took stored marks[0]; after the upgrade the value rule takes the newest
    valuation (the purchase point, now derived, can never be it). Over random legacy books
    built with the old write shapes they agree on every asset."""
    rng = random.Random(20260926)
    assets = [_random_legacy_asset(rng, i) for i in range(300)]
    rows = _upgraded(tmp_path, copy.deepcopy(assets))
    for asset in assets:
        assert valuation.value_as_of(rows[asset["id"]], None) == asset["marks"][0]["value"], asset


def _old_value_as_of(asset: dict, as_of: str) -> float | None:
    for mark in asset["marks"]:  # the old reader: stored order, stored purchase mark included
        if mark["date"] <= as_of:
            return mark["value"]
    return None


def test_upgrade_preserves_history_for_unedited_purchases(tmp_path):
    """History is unchanged wherever the purchase was never edited — the derived point equals
    the stored one — including a price with no date, whose fabricated copy never won a day."""
    book = [asset for asset in legacy_book() if asset["id"] in {"flat", "watch", "bike"}]
    rows = _upgraded(tmp_path, copy.deepcopy(book))
    days = [date(2019, 1, 1) + timedelta(days=d) for d in range(0, 3000, 13)] + [date(2026, 9, 30)]
    for asset in book:
        for day in days:
            as_of = day.isoformat()
            assert valuation.value_as_of(rows[asset["id"]], as_of) == _old_value_as_of(
                asset, as_of
            ), (asset["id"], as_of)


def test_migration_is_idempotent(tmp_path):
    mod = _mod()
    for asset in [*legacy_book(), *legacy_id_clashes()]:
        once = mod._migrate_asset(asset)
        assert mod._migrate_asset(once) == once, asset["id"]

    book = [*legacy_book(), *legacy_id_clashes()]
    with _book_at_from(tmp_path, book) as (config, engine):
        with engine.begin() as conn:
            _snapshot(conn, "cp_1", 1, "owned_assets", "flat", _row_image(book[0], 0))
        command.upgrade(config, _TARGET)
        updates: list[str] = []

        def _record(_conn, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("UPDATE"):
                updates.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        with engine.begin() as conn:
            assert mod._migrate_all(conn) == 0
        assert updates == []


def test_downgrade_rematerializes_purchase_and_reupgrade_is_stable(tmp_path):
    book = legacy_book()
    with _book_at_from(tmp_path, book) as (config, engine):
        with engine.begin() as conn:
            _snapshot(conn, "cp_1", 1, "owned_assets", "flat", _row_image(book[0], 0))
        command.upgrade(config, _TARGET)
        upgraded, upgraded_images = _rows(engine), _snapshots(engine)
        command.downgrade(config, _FROM)
        downgraded, downgraded_images = _rows(engine), _snapshots(engine)
        command.upgrade(config, _TARGET)
        assert _rows(engine) == upgraded
        assert _snapshots(engine) == upgraded_images

    assert downgraded_images[("cp_1", 1)]["payload"] == downgraded["flat"]

    flat = downgraded["flat"]["marks"]
    purchase = next(m for m in flat if m["note"] == "entered on add")
    assert (purchase["date"], purchase["value"]) == ("2019-03-15", 900_000)
    oldest = min(datetime.fromisoformat(m["created_at"]) for m in flat if m is not purchase)
    assert datetime.fromisoformat(purchase["created_at"]) == oldest - timedelta(microseconds=1)
    assert all("edited_at" not in m for m in flat)
    # No purchase point shown, nothing written back: a policy, and a price with no date.
    assert all(m["note"] != "entered on add" for m in downgraded["endowment"]["marks"])
    assert all(m["note"] != "entered on add" for m in downgraded["watch"]["marks"])


# ── Checkpoint snapshots (code review R1), fail-closed (security review S7), the summary ──


def _snapshot(conn, checkpoint_id: str, seq: int, table: str, pk: str, image) -> None:
    conn.execute(
        text(
            "INSERT INTO write_log (checkpoint_id, seq, table_name, pk, before_payload, "
            "created_at) VALUES (:c, :s, :t, :pk, :image, '')"
        ),
        {"c": checkpoint_id, "s": seq, "t": table, "pk": pk, "image": json.dumps(image)},
    )


def _snapshots(engine) -> dict[tuple[str, int], object]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT checkpoint_id, seq, before_payload FROM write_log"))
        return {(c, s): json.loads(raw) if isinstance(raw, str) else raw for c, s, raw in rows}


def _row_image(asset: dict, seq: int) -> dict:
    return {"book_id": "default", "seq": seq, "id": asset["id"], "payload": asset}


def test_upgrade_migrates_checkpoint_snapshots_of_owned_assets(tmp_path):
    """A checkpoint taken before the upgrade holds verbatim owned-asset rows; restoring it
    afterwards would bring back the stored purchase copy (now read as a valuation), the
    "Added" fact, blank `source` notes and a sold asset's loan link."""
    book = {asset["id"]: asset for asset in legacy_book()}
    with _book_at_from(tmp_path, []) as (config, engine):
        with engine.begin() as conn:
            _snapshot(conn, "cp_1", 1, "owned_assets", "flat", _row_image(book["flat"], 0))
            _snapshot(conn, "cp_1", 2, "owned_assets", "oa_new", None)  # created in that turn
            _snapshot(conn, "cp_2", 1, "owned_assets", "scooter", _row_image(book["scooter"], 7))
            _snapshot(conn, "cp_2", 2, "accounts", "acc_1", {"id": "acc_1", "type": "cash"})
        command.upgrade(config, _TARGET)
        images = _snapshots(engine)

    migrate = _mod()._migrate_asset
    assert images[("cp_1", 1)] == {**_row_image(book["flat"], 0), "payload": migrate(book["flat"])}
    assert [m["id"] for m in images[("cp_1", 1)]["payload"]["marks"]] == ["mk_flat_v"]
    assert images[("cp_1", 2)] is None
    assert images[("cp_2", 1)]["payload"]["loan_account_id"] is None
    assert images[("cp_2", 1)]["seq"] == 7
    assert images[("cp_2", 2)] == {"id": "acc_1", "type": "cash"}


def test_a_malformed_row_refuses_the_upgrade_and_changes_nothing(tmp_path):
    """Fail-closed is all-or-nothing: every row and snapshot is read and migrated before
    anything is written, so a malformed second row leaves the first exactly as it was."""
    first = old_create(
        "flat", value=1_000, cost=900, cost_date="2020-01-01", created="2026-09-06T02:10:00+00:00"
    )
    for table, bad in (("owned_assets", ["not", "an", "object"]), ("write_log", {"payload": 7})):
        (tmp_path / table).mkdir()
        with _book_at_from(tmp_path / table, [first]) as (config, engine):
            with engine.begin() as conn:
                if table == "owned_assets":
                    conn.execute(
                        text(
                            "INSERT INTO owned_assets (id, book_id, seq, payload) "
                            "VALUES ('broken', 'default', 1, :p)"
                        ),
                        {"p": json.dumps(bad)},
                    )
                else:
                    _snapshot(conn, "cp_1", 1, "owned_assets", "flat", bad)
            with pytest.raises(Exception, match="migration refused"):
                command.upgrade(config, _TARGET)
            assert _rows(engine)["flat"] == first
            with engine.connect() as conn:
                version = conn.execute(text("SELECT version_num FROM alembic_version"))
                assert version.scalar_one() == _FROM


def test_upgrade_logs_what_it_changed(tmp_path, caplog):
    book = [*legacy_book(), *legacy_id_clashes()]
    with caplog.at_level(logging.INFO):
        with _book_at_from(tmp_path, book) as (config, engine):
            with engine.begin() as conn:
                _snapshot(conn, "cp_1", 1, "owned_assets", "flat", _row_image(book[0], 0))
            command.upgrade(config, _TARGET)
    line = next(r.getMessage() for r in caplog.records if "purchase marks" in r.getMessage())
    assert line == (
        "owned assets: 9 of 9 rewritten (4 create-time purchase marks dropped, 11 notes "
        'normalized, 3 mark ids re-minted, 9 "Added" facts removed, 1 loans released on sold '
        "assets); 1 checkpoint snapshots migrated"
    )


def test_upgrade_re_mints_duplicate_missing_and_reserved_mark_ids(tmp_path):
    """Security review S4: correct and remove find a value by its id, so an id held twice,
    missing, or equal to the derived point's "purchase" is re-minted. The new id comes from
    the mark's own content, so a checkpoint image holding the same values (taken before a
    rename) re-mints exactly as the row does."""
    [clash] = legacy_id_clashes()
    with _book_at_from(tmp_path, [clash]) as (config, engine):
        with engine.begin() as conn:
            before_rename = _row_image({**clash, "name": "old name"}, 0)
            _snapshot(conn, "cp_1", 1, "owned_assets", "clash", before_rename)
        command.upgrade(config, _TARGET)
        row, image = _rows(engine)["clash"], _snapshots(engine)[("cp_1", 1)]["payload"]

    ids = {m["date"]: m["id"] for m in row["marks"]}
    assert len(set(ids.values())) == 4
    assert "purchase" not in ids.values() and ids["2026-01-01"] == "mk_same"
    assert {m["date"]: m["id"] for m in image["marks"]} == ids
