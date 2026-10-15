"""Programmatic Alembic config — one construction, shared by the CLI shim
(alembic.ini), the boot wrapper (db/migrate.py) and the tests."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

_SCRIPT_LOCATION = Path(__file__).parent / "alembic"

# Alembic creates ``alembic_version.version_num`` as VARCHAR(32) — enough for its own 12-hex
# ids, not for ours: a timestamp id carries its slug (``202609301800_notification_channels_
# per_kind_and_per_alert``, 57 chars). SQLite ignores a declared length; Postgres enforces it
# and refuses the very first stamp.
VERSION_NUM_WIDTH = 255


def make_config(url: str | None = None) -> Config:
    from backend.db.engine import database_url

    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    resolved_url = url or database_url()
    # ConfigParser applies %(name)s interpolation on every get; a bare "%" (e.g. a
    # URL-encoded space, "%20", in a sqlite file path) reads as the start of a
    # reference and raises ValueError from set_main_option itself. Escape it to "%%"
    # so it round-trips literally; env.py's `or database_url()` fallback bypasses
    # ConfigParser entirely and needs no equivalent change.
    cfg.set_main_option("sqlalchemy.url", resolved_url.replace("%", "%%"))

    # Revision ids and filenames are TIMESTAMPS, not a counter. A counter (`0061`, `0062`, …)
    # forces every contributor to guess the next free number, and two branches that both guess
    # right collide on the same id — a conflict git cannot see, because the files have different
    # names and only the `down_revision` links disagree. A UTC timestamp is collision-free by
    # construction and still sorts chronologically in a directory listing.
    #
    # `rev` is what `alembic revision` puts in the file AND uses as the id, so this one option
    # governs both. The historical `0001`-`0063` revisions were themselves renamed to
    # timestamp ids (2026-08-29) — every revision in the chain now follows the same
    # convention. A rename invalidates the `alembic_version` stamp on any existing database,
    # so it was done once, deliberately, with every live instance's stamp remapped in the
    # same change — not something to repeat casually.
    cfg.set_main_option(
        "file_template", "%%(year)d%%(month).2d%%(day).2d%%(hour).2d%%(minute).2d_%%(slug)s"
    )
    return cfg


def ensure_wide_version_table(connection) -> None:
    """Make ``alembic_version.version_num`` wide enough for a timestamp-named revision id.

    Alembic only creates the table lazily, at the first stamp, with its own VARCHAR(32), and
    offers no option for the width — so on Postgres the table is created (or widened) here,
    before any revision runs. Idempotent; SQLite never enforces a length and is left alone."""
    if connection.dialect.name != "postgresql":
        return
    sa.Table(
        "alembic_version",
        sa.MetaData(),
        sa.Column("version_num", sa.String(VERSION_NUM_WIDTH), nullable=False),
        sa.PrimaryKeyConstraint("version_num", name="alembic_version_pkc"),
    ).create(connection, checkfirst=True)
    width = connection.execute(
        sa.text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'alembic_version' "
            "AND column_name = 'version_num'"
        )
    ).scalar_one()
    if width < VERSION_NUM_WIDTH:
        connection.execute(
            sa.text(
                f"ALTER TABLE alembic_version ALTER COLUMN version_num TYPE "
                f"VARCHAR({VERSION_NUM_WIDTH})"
            )
        )
