"""``alembic_version.version_num`` must hold our timestamp-named revision ids."""

import pytest

from backend.db.alembic_config import make_config


def test_version_column_is_wide_enough_for_every_revision_id():
    """Revision ids carry their slug (``202609301800_notification_channels_per_kind_and_per_
    alert``), far past Alembic's own VARCHAR(32) for ``alembic_version.version_num`` — which
    Postgres enforces and SQLite does not, so SQLite never notices a new id outgrowing it."""
    from alembic.script import ScriptDirectory

    from backend.db.alembic_config import VERSION_NUM_WIDTH

    script = ScriptDirectory.from_config(make_config("sqlite://"))
    longest = max(script.walk_revisions(), key=lambda rev: len(rev.revision))
    assert len(longest.revision) <= VERSION_NUM_WIDTH, longest.revision


def test_version_column_is_widened_on_postgres(db_url, db_engine):
    """Alembic creates the table lazily with its own VARCHAR(32); ``env.py`` widens it before
    the first revision stamps (``ensure_wide_version_table``). Only meaningful on a server that
    enforces column lengths, so the Postgres CI job is where this runs."""
    from sqlalchemy import create_engine, text

    from backend.db.alembic_config import VERSION_NUM_WIDTH

    if not db_url.startswith("postgresql"):
        pytest.skip("SQLite does not enforce VARCHAR length")
    e = create_engine(db_url)
    try:
        with e.connect() as c:
            width = c.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = 'alembic_version' AND column_name = 'version_num'"
                )
            ).scalar_one()
    finally:
        e.dispose()
    assert width == VERSION_NUM_WIDTH
