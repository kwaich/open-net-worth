from pathlib import Path

from alembic import command
from sqlalchemy import inspect, select

from backend.db.alembic_config import make_config


def test_upgrade_head_creates_and_seeds_identity(db_engine):
    from backend.db import tables as t

    with db_engine.connect() as c:
        assert c.execute(select(t.users.c.id)).scalar() == t.DEFAULT_USER_ID
        assert c.execute(select(t.books.c.id)).scalar() == t.DEFAULT_BOOK_ID
        row = c.execute(select(t.book_members)).one()
        assert (row.user_id, row.book_id, row.role) == (
            t.DEFAULT_USER_ID,
            t.DEFAULT_BOOK_ID,
            "owner",
        )


# Two revisions refuse a downgrade on purpose: the connectors table (it replaces fifteen settings
# rows and the mcp_clients table in place — tests/db/test_connectors_table_migration.py pins the
# refusal) and, below it, the account-institution normalization. The reversible span is
# everything between them, so the cycle runs there: up to the connectors revision's parent, down
# to the irreversible floor, then up to head.
_CONNECTORS_REVISION = "202610101200_connectors_table"
_IRREVERSIBLE_FLOOR = "202609121000_normalize_account_institutions"


def _stamped(db_url: str) -> str:
    from sqlalchemy import create_engine, text

    e = create_engine(db_url)
    try:
        with e.connect() as c:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        e.dispose()


def test_upgrade_downgrade_upgrade_cycle(scrubbed_db_url):
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    db_url = scrubbed_db_url
    cfg = make_config(db_url)
    scripts = ScriptDirectory.from_config(cfg)
    command.upgrade(cfg, scripts.get_revision(_CONNECTORS_REVISION).down_revision)
    command.downgrade(cfg, _IRREVERSIBLE_FLOOR)
    assert _stamped(db_url) == _IRREVERSIBLE_FLOOR
    command.upgrade(cfg, "head")
    assert _stamped(db_url) == scripts.get_current_head()

    e = create_engine(db_url)
    tables = set(inspect(e).get_table_names())
    e.dispose()
    assert {"users", "connectors", "connector_secrets"} <= tables
    assert "mcp_clients" not in tables


def test_every_refused_downgrade_is_marked_irreversible():
    """``tests/db/conftest.py::reversible_tip`` trusts the ``irreversible`` flag to know where
    a downgrade test must stop. A revision whose ``downgrade()`` raises without the flag would
    send those tests across it; a flag on a revision that can downgrade would hide coverage."""
    import ast

    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(make_config("sqlite://"))
    for rev in script.walk_revisions():
        tree = ast.parse(Path(rev.path).read_text())
        downgrade = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade"
        )
        refuses = any(isinstance(n, ast.Raise) for n in ast.walk(downgrade))
        assert getattr(rev.module, "irreversible", False) == refuses, rev.revision


def test_alembic_check_is_clean(db_engine, db_url):
    """A table changed in tables.py without a revision must fail CI right here."""
    command.check(make_config(db_url))


def test_env_py_bare_cli_branch_constructs_its_engine_via_make_engine(tmp_path, monkeypatch):
    """CONTROLLER INCLUSION (plan 3 task 3 review -> task 4): env.py's ``connectable is
    None`` branch (``context.config.attributes.get("connection")`` unset — the real path
    BOTH the bare ``alembic`` CLI and the app's own boot wrapper
    ``db.migrate.ensure_migrated()`` take, since ``db.alembic_config.make_config`` never
    sets ``attributes["connection"]``) used to build its engine via a bare
    ``create_engine(url)``, which never attaches the ``PRAGMA foreign_keys=ON``
    connect-listener — task 3's own finding (the money cutover needed Python-level
    cross-file consistency pre-checks precisely BECAUSE this pragma wasn't actually
    enforcing anything during a migration's own connection). Fixed: env.py now builds its
    engine via ``backend.db.engine._make_engine`` — the ONE place that listener is wired,
    already relied on for every real DB connection the app itself opens via
    ``get_engine()``.

    Proven by spying on ``_make_engine`` during a REAL ``command.upgrade`` run through
    env.py's actual, unmocked bare-Config code path — not by asserting the PRAGMA value
    directly (calling ``_make_engine`` ourselves would prove ``_make_engine`` works, which
    was never in question; it would prove NOTHING about which constructor env.py itself
    chooses — confirmed empirically: an earlier draft of this test that opened its own
    ``_make_engine``-built connection AFTER the upgrade passed against the UNFIXED env.py
    too, i.e. it didn't discriminate the bug at all). Alembic re-execs env.py fresh on
    every ``command.*`` call (loaded via ``ScriptDirectory``'s own file-exec machinery, not
    Python's module cache), so patching the ALREADY-IMPORTED ``backend.db.engine``
    module's ``_make_engine`` attribute is visible to env.py's own ``from
    backend.db.engine import _make_engine`` the next time it runs. RED-verified against
    the pre-fix env.py (a bare ``create_engine``): ``calls == []`` — GREEN only once env.py
    actually calls through ``_make_engine``."""
    import backend.db.engine as eng_mod

    calls: list[str] = []
    real_make_engine = eng_mod._make_engine

    def _spy(url):
        calls.append(url)
        return real_make_engine(url)

    monkeypatch.setattr(eng_mod, "_make_engine", _spy)

    url = f"sqlite:///{tmp_path}/env_fk_spy.db"
    command.upgrade(make_config(url), "head")

    assert calls == [url], (
        "env.py's bare-CLI branch must construct its engine via backend.db.engine."
        "_make_engine — a bare create_engine(url) never enforces SQLite foreign keys"
    )


def test_env_py_falls_back_to_app_url_when_config_has_none(db_url, db_engine, monkeypatch):
    """The bare `alembic` CLI (root alembic.ini, no sqlalchemy.url) must work: env.py
    falls back to database_url() — which honors ONW_DATABASE_URL. db_engine is taken
    (unused) to force the per-test scrub: on Postgres db_url is the SHARED server DB,
    and without the scrub this test would couple to whatever ran before it."""
    from alembic import command
    from alembic.config import Config

    from backend.db.alembic_config import _SCRIPT_LOCATION

    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    command.upgrade(cfg, "head")
    command.check(cfg)
