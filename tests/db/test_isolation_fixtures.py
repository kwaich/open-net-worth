"""The one test-isolation system (plan 2, task 1): ``db_isolated`` — a function-scoped
fixture that redirects ``ONW_DATABASE_URL`` to a tmp sqlite file, migrates it to head, and
ALWAYS disposes the process-wide engine singleton on teardown (the stale-singleton fix that
``tests/db_test_engine.py::isolate_evidence_db`` never had — it monkeypatched the
``get_engine`` NAME instead of touching the singleton, so nothing here ever disposed).

Order across these three tests matters and is deliberate: the first two prove the fixture's
teardown actually resets the singleton (not merely that the fixture WORKS while active), the
third proves the metadata-driven Tier-2 cleanup used by tests/conftest.py's per-test hook.
"""

from __future__ import annotations

# Set by test_db_isolated_disposes_singleton_on_teardown, read by
# test_singleton_is_reset_between_tests — see that test's docstring for why file
# order (not an explicit dependency) is what makes this pairing valid.
_PREVIOUS_ISOLATED_URL: str | None = None


def test_db_isolated_disposes_singleton_on_teardown(db_isolated):
    from backend.db import engine as eng

    global _PREVIOUS_ISOLATED_URL
    _PREVIOUS_ISOLATED_URL = db_isolated
    assert eng.get_engine() is not None  # bound to the isolated tmp DB inside the test


def test_singleton_is_reset_between_tests():
    """Runs AFTER the test above — relies on pytest's default collection order (top to
    bottom, declaration order within a file) to run second, NOT on an explicit fixture
    dependency; there is none between the two tests. The singleton must not still point
    at the previous test's tmp DB: get_engine() here must bind to the session default —
    resolvable and migrated by the session fixture — not the vanished tmp path.

    ``users == 1`` alone is a BLIND SPOT: revision 0001 seeds exactly one row into
    EVERY migrated database, including the previous test's orphaned tmp DB, so that
    assertion alone passes whether the singleton points at the session default OR a
    stale tmp DB. The URL comparison below is the part that actually distinguishes
    them — it must fail if tests/conftest.py's per-test cleanup ever lazily rebinds
    the singleton via a bare ``get_engine()`` call during LIFO teardown (the bug this
    guards against: db_isolated's dispose runs before monkeypatch restores
    ONW_DATABASE_URL, so a naive get_engine() in between would recreate the engine
    against the still-set, soon-to-be-orphaned tmp URL)."""
    from sqlalchemy import text

    from backend.db import engine as eng

    current_url = str(eng.get_engine().url)
    assert current_url != _PREVIOUS_ISOLATED_URL, (
        f"singleton still bound to the previous test's isolated tmp DB ({current_url!r}) — "
        "something between db_isolated's dispose and monkeypatch's env restore lazily "
        "rebound the process-wide engine singleton"
    )
    with eng.get_engine().connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 1


def test_metadata_driven_cleanup_clears_all_tier2_tables(db_isolated):
    from sqlalchemy import insert, select

    from backend.db import engine as eng
    from backend.db import tables as t

    with eng.get_engine().begin() as c:
        c.execute(
            insert(t.evidence).values(
                id="zz", seq=1, expires_at="", domain="", created_at="", payload={"id": "zz"}
            )
        )
    from tests.conftest import _clear_tier2_tables

    _clear_tier2_tables(eng.get_engine())
    with eng.get_engine().connect() as c:
        assert c.execute(select(t.evidence)).fetchall() == []
        assert c.execute(select(t.users)).one()  # identity seed SURVIVES
