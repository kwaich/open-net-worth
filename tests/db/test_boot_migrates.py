"""Boot wiring (storage plan 1, task 8): ``ensure_migrated()`` must be the FIRST
act of the FastAPI lifespan — the app must never serve a request against an
un-migrated or half-migrated database. See ``backend/db/migrate.py`` and spec
2026-08-05 D3.
"""

import ast
import logging
from pathlib import Path

from sqlalchemy import text


def _point_at_tmp_db(tmp_path, monkeypatch):
    """Isolate to a fresh tmp DB, exactly like tests/db/test_migrate_wrapper.py's
    ``_boot``. tests/conftest.py's session-scoped ``_migrate_default_evidence_db``
    fixture has already migrated the DEFAULT db before this test ever runs, so
    proving the LIFESPAN (not that fixture) does the migrating requires pointing
    at a database nothing else has touched. BACKUP_DIR is patched by the autouse
    _isolated_backup_dir fixture (tests/db/conftest.py)."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    from backend.db import engine as eng

    eng.dispose_engine()


def test_lifespan_migrates_before_serving(tmp_path, monkeypatch):
    _point_at_tmp_db(tmp_path, monkeypatch)

    # Keep lifespan's QA-gated network hooks (price backfill, benchmark-instrument
    # seed) dormant — this is the only test in the suite that enters the REAL app's
    # context manager, so it is the only one that would otherwise reach them and
    # make live HTTP calls. `get_settings()` caches a process-wide singleton, so the
    # env var alone is not enough — bust the cache too, then restore it so the next
    # test to call get_settings() re-reads the (by-then-reverted) real environment.
    import backend.core.config as config

    monkeypatch.setenv("ONW_QA_MODE", "1")
    config._settings = None

    # lifespan also re-routes the "backend" logger through uvicorn's handlers (see
    # main.py's own comment: "Must run after uvicorn calls dictConfig") — a no-op
    # under a real uvicorn boot, but under TestClient's in-process ASGI call uvicorn
    # never ran dictConfig, so entering the context leaves "backend" with
    # propagate=False and no handlers, which silently breaks every later test's
    # caplog capture of backend.* loggers for the rest of the SESSION (this is the
    # exact hazard tests/test_recurring_api.py documents and avoids by never using
    # `with TestClient(app)` on the full app). Save/restore so this test's proof
    # doesn't leak into the ~20 other files that assert on log records.
    backend_logger = logging.getLogger("backend")
    saved_handlers = list(backend_logger.handlers)
    saved_level = backend_logger.level
    saved_propagate = backend_logger.propagate

    from fastapi.testclient import TestClient

    from backend.db import engine as eng
    from backend.main import app

    try:
        with TestClient(app):  # entering the context runs lifespan
            pass
    finally:
        backend_logger.handlers = saved_handlers
        backend_logger.setLevel(saved_level)
        backend_logger.propagate = saved_propagate
        config._settings = None

    with eng.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1
    eng.dispose_engine()


def test_only_the_instance_lock_precedes_ensure_migrated_in_lifespan():
    """Fail-closed boot posture (spec 2026-08-05 D3): the schema check runs before the
    uvicorn-logger copy, before skill installs, and before every pre-existing one-time
    JSON migration/ensure block. Asserted by AST position (not a substring/regex search)
    so the ordering can't silently drift as the function grows.

    ONE thing is allowed ahead of it, added 2026-08-24: the working-folder claim (see
    ADR ``2026-08-24-the-instance-lock-precedes-the-migration``). It reads no data and
    writes none — it either takes a lock or aborts the boot — and running a
    backup-and-modify migration against a database ANOTHER live instance is serving is
    the failure the lock exists to prevent. Migrating first and discovering the folder
    is taken second would be strictly worse.

    Nothing else may join it. The list below is the whole allowance."""
    import backend.main as main_module

    tree = ast.parse(Path(main_module.__file__).read_text())
    lifespan_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )

    def _names(node):
        if isinstance(node, ast.ImportFrom):
            return {alias.asname or alias.name for alias in node.names}
        return set()

    allowed_before = {
        "get_settings as _settings_for_lock",
        "_settings_for_lock",
        "acquire as _acquire_instance",
        "_acquire_instance",
        "DATA_DIR as _lock_dir",
        "_lock_dir",
    }

    body = lifespan_node.body
    i = 0
    while i < len(body):
        node = body[i]
        if isinstance(node, ast.ImportFrom) and _names(node) <= allowed_before:
            i += 1
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", "") == "_acquire_instance"
        ):
            i += 1
            continue
        break

    first_import, first_call = body[i], body[i + 1]

    assert isinstance(first_import, ast.ImportFrom), (
        "only the instance-lock preamble may precede the schema check in lifespan"
    )
    assert first_import.module == "backend.db.migrate"
    assert [alias.name for alias in first_import.names] == ["ensure_migrated"]

    assert isinstance(first_call, ast.Expr)
    assert isinstance(first_call.value, ast.Call)
    assert first_call.value.func.id == "ensure_migrated"
