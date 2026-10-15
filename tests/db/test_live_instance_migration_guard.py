"""A live instance is migrated only by a caller that opted in (2026-09-03).

The 2026-09-02 incident: a lane sub-agent smoke-tested a new revision with
``ONW_DATA_DIR=$(mktemp -d) python -c "...ensure_migrated()"``. The app reads ``DATA_DIR``,
so the "isolation" was a no-op, the default resolved to the owner's ``~/.open-net-worth``,
and an unmerged revision stamped the real database — which then refused to boot on ``main``.
``ONW_QA_MODE=1`` would have caught it, but a process that never set the flag gets nothing
from the QA guards. This guard needs no flag: on a live path, the caller must say so.

Three doors, each pinned here: ``ensure_migrated()`` (the documented entry point), env.py
(every Alembic run, including a script's own ``command.upgrade``), and the boot's opt-in
(checked by AST, because the suite never runs against a live path and a dropped kwarg would
only show on a real install).

ADR ``2026-09-03-live-instance-migrations-require-an-opt-in``.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backend.core import config
from backend.core.errors import MigrationError
from backend.db import engine as eng
from backend.db import migrate
from backend.db.alembic_config import make_config
from tests.db.conftest import patch_all_cutover_sources_missing


@pytest.fixture
def live_root(tmp_path, monkeypatch) -> Path:
    """A stand-in for ``~/.open-net-worth``: the default an unconfigured process falls
    through to. No recorded folder pick, so the default is the only live root."""
    root = tmp_path / "home" / ".open-net-worth"
    monkeypatch.setattr(config, "_INSTANCE_ROOT", root)
    monkeypatch.setattr(config, "POINTER_FILE", tmp_path / "no-pointer.json")
    return root


def _point_db_at(monkeypatch, db_path: Path) -> None:
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{db_path}")
    eng.dispose_engine()


def _users(db_path: Path) -> int:
    e = create_engine(f"sqlite:///{db_path}")
    try:
        with e.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    finally:
        e.dispose()


# ── the predicate ───────────────────────────────────────────────────────────────────


def test_is_live_instance_path_covers_the_default_root_and_the_seed_dir(live_root, tmp_path):
    assert config.is_live_instance_path(live_root / "data" / "onw.db")
    assert config.is_live_instance_path(live_root)
    assert config.is_live_instance_path(config._BACKEND / "data" / "extensions")
    assert not config.is_live_instance_path(tmp_path / "scratch" / "data")


def test_is_live_instance_path_is_not_fooled_by_a_sibling_prefix(live_root):
    """``~/.open-net-worth-demo`` shares a prefix with the live root; it is a different
    instance and must stay migratable without an opt-in."""
    demo = live_root.parent / ".open-net-worth-demo" / "data"
    assert not config.is_live_instance_path(demo)


def test_is_live_instance_path_covers_a_recorded_folder_pick(live_root, tmp_path, monkeypatch):
    """A pointer wins the default for an unconfigured process, so the picked folder is
    exactly as live as ``~/.open-net-worth`` — a misspelled variable lands there instead."""
    picked = tmp_path / "Volumes" / "Money" / "onw"
    pointer = tmp_path / "location.json"
    pointer.write_text(json.dumps({"instance_root": str(picked)}))
    monkeypatch.setattr(config, "POINTER_FILE", pointer)
    assert config.is_live_instance_path(picked / "data" / "onw.db")
    assert config.is_live_instance_path(live_root / "data")


# ── door 1: ensure_migrated() ───────────────────────────────────────────────────────


def test_ensure_migrated_refuses_a_live_database_before_touching_the_file(live_root, monkeypatch):
    """The incident, replayed: no opt-in, database resolving into the live root. Refused,
    and the file does not exist afterwards — ``_pending()``'s probe alone would create it."""
    db_path = live_root / "data" / "onw.db"
    _point_db_at(monkeypatch, db_path)

    with pytest.raises(MigrationError) as info:
        migrate.ensure_migrated()

    assert not db_path.exists()
    message = str(info.value)
    assert "LIVE instance" in message
    assert "allow_live_instance=True" in message
    assert "ONW_QA_MODE=1" in message


def test_ensure_migrated_refuses_when_only_data_dir_is_live(live_root, tmp_path, monkeypatch):
    """A database elsewhere but ``DATA_DIR`` on the live root would still write the
    pre-migrate backup into the owner's tree — judged too."""
    db_path = tmp_path / "elsewhere" / "onw.db"
    _point_db_at(monkeypatch, db_path)
    monkeypatch.setattr(migrate, "DATA_DIR", live_root / "data")

    with pytest.raises(MigrationError, match="LIVE instance"):
        migrate.ensure_migrated()
    assert not db_path.exists()


def test_ensure_migrated_with_the_opt_in_migrates_the_live_database(live_root, monkeypatch):
    db_path = live_root / "data" / "onw.db"
    _point_db_at(monkeypatch, db_path)

    migrate.ensure_migrated(allow_live_instance=True)

    assert _users(db_path) == 1
    eng.dispose_engine()


def test_ensure_migrated_on_a_scratch_path_needs_no_opt_in(live_root, tmp_path, monkeypatch):
    """The whole test-suite's posture, stated once: an isolated path is not live."""
    db_path = tmp_path / "scratch" / "onw.db"
    _point_db_at(monkeypatch, db_path)

    migrate.ensure_migrated()

    assert _users(db_path) == 1
    eng.dispose_engine()


# ── door 2: env.py — every Alembic run, however it was reached ─────────────────────


def test_a_raw_alembic_upgrade_against_the_live_database_is_refused(live_root, monkeypatch):
    """The sub-agent's SECOND command: its own ``Config``, straight to ``command.upgrade``,
    bypassing ``ensure_migrated`` entirely. env.py is the door it cannot bypass."""
    from alembic import command

    db_path = live_root / "data" / "onw.db"
    db_path.parent.mkdir(parents=True)
    _point_db_at(monkeypatch, db_path)

    with pytest.raises(MigrationError, match="LIVE instance"):
        command.upgrade(make_config(f"sqlite:///{db_path}"), "head")
    assert not db_path.exists() or db_path.stat().st_size == 0


@pytest.mark.parametrize("channel", ["attribute", "x_argument"])
def test_a_raw_alembic_upgrade_runs_with_either_opt_in_channel(
    live_root, tmp_path, monkeypatch, channel
):
    """``ensure_migrated`` sets the Config attribute; the CLI passes ``-x``. Both are the
    same deliberate act; both are honoured."""
    from alembic import command

    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    db_path = live_root / "data" / "onw.db"
    db_path.parent.mkdir(parents=True)
    _point_db_at(monkeypatch, db_path)
    cfg = make_config(f"sqlite:///{db_path}")
    if channel == "attribute":
        cfg.attributes["allow_live_instance"] = True
    else:
        cfg.cmd_opts = argparse.Namespace(x=["allow_live_instance=1"])

    command.upgrade(cfg, "head")

    assert _users(db_path) == 1


def test_a_raw_alembic_upgrade_is_refused_while_another_process_holds_the_folder(
    tmp_path, monkeypatch
):
    """Security-review finding MEDIUM 4: `ensure_migrated()`'s own instance-lock guard
    never runs for a raw `alembic -x allow_live_instance=1 upgrade head` -- that
    command bypasses `ensure_migrated` entirely and lands straight in `env.py`.
    Mirrors `test_a_raw_alembic_upgrade_against_the_live_database_is_refused` above,
    but for the OTHER door-2 hazard: not "is this a live instance", but "does
    another process already have this working folder open". A scratch (non-live)
    path is used deliberately -- this guard is independent of the live-instance
    check."""
    import subprocess
    import sys

    from alembic import command

    from backend.core.instance_lock import LOCK_NAME

    db_path = tmp_path / "scratch" / "onw.db"
    db_path.parent.mkdir(parents=True)
    _point_db_at(monkeypatch, db_path)

    lock_folder = tmp_path / "lockfolder"
    monkeypatch.setattr(migrate, "DATA_DIR", lock_folder / "data")
    lock_path = lock_folder / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import json,time;"
            f"h=open({str(lock_path)!r},'a+');"
            f"lock_exclusive(h);"
            f"h.write(json.dumps({{'pid':1,'port':9999}}));h.flush();"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(MigrationError) as exc_info:
            command.upgrade(make_config(f"sqlite:///{db_path}"), "head")

        message = str(exc_info.value)
        assert str(lock_folder) in message
        assert "refusing to migrate" in message
    finally:
        holder.kill()
        holder.wait(timeout=10)


# ── door 3: the boot's opt-in cannot be dropped unnoticed ──────────────────────────


def test_the_boot_passes_the_opt_in():
    """The suite never boots against a live path, so a dropped kwarg would surface only on a
    real install — as a refusal to boot. Pinned by AST, like the lifespan ordering test."""
    import backend.main as main_module

    tree = ast.parse(Path(main_module.__file__).read_text())
    lifespan = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    call = next(
        node.value
        for node in lifespan.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", "") == "ensure_migrated"
    )
    keywords = {kw.arg: kw.value for kw in call.keywords}
    assert isinstance(keywords.get("allow_live_instance"), ast.Constant)
    assert keywords["allow_live_instance"].value is True
