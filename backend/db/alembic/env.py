from alembic import context

from backend.core.errors import MigrationError
from backend.core.instance_lock import held_by_other
from backend.db.alembic_config import ensure_wide_version_table
from backend.db.engine import _make_engine, database_url
from backend.db.migrate import DATA_DIR, refuse_live_instance
from backend.db.tables import metadata

target_metadata = metadata


def _live_instance_allowed() -> bool:
    """``ensure_migrated`` sets the attribute once its own check has passed; the alembic CLI
    opts in with ``-x allow_live_instance=1``. Anything else that reaches env.py — a script
    calling ``command.upgrade`` with a Config of its own, a bare ``alembic upgrade head`` —
    is judged here, so the live instance has no unguarded door."""
    if context.config.attributes.get("allow_live_instance"):
        return True
    return context.get_x_argument(as_dictionary=True).get("allow_live_instance") == "1"


def run_migrations_online() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or database_url()
    refuse_live_instance(url, allowed=_live_instance_allowed())
    # Security review finding MEDIUM 4: `ensure_migrated()`'s own live-instance-lock
    # guard (`db.migrate._refuse_if_locked_by_other`, with its bounded wait for a
    # legitimate sibling) never runs for a raw `alembic -x allow_live_instance=1
    # upgrade head` — that command bypasses `ensure_migrated` entirely and lands
    # straight here. No polling: a raw CLI invocation is an operator/script action,
    # not a boot racing sibling workers, so an immediate, clear refusal is correct.
    folder = DATA_DIR.parent
    if held_by_other(folder):
        raise MigrationError(
            f"another process holds {folder}; refusing to migrate a database another "
            "instance has open. Let a migration in progress finish; stop an idle "
            "instance, then retry.",
            path=str(folder),
        )
    connectable = context.config.attributes.get("connection", None)
    if connectable is None:
        # PLAN 3 TASK 4 — CONTROLLER INCLUSION (task 3 review): a bare `create_engine(url)`
        # here (as opposed to `backend.db.engine._make_engine`, what every real store uses
        # via `get_engine()`) never attaches the `connect`-event listener that turns
        # `PRAGMA foreign_keys=ON` on for SQLite — found by task 3 (the money cutover, which
        # needed its OWN Python-level cross-file consistency pre-checks precisely because
        # this pragma was NOT actually enforcing anything during a migration's own
        # connection). `_make_engine` is the ONE place that pragma is wired; reusing it here
        # means every SQLite migration connection enforces real FKs, exactly like the app's
        # own runtime engine does. Task 3's review ran a blast-radius experiment forcing this
        # pragma on and replaying revisions 0001-0007 — all pass FK-ordered under it, so this
        # is a pure hardening, not a behavior change to any existing revision. Postgres is
        # unaffected either way (it always enforces FKs; `_make_engine`'s postgres branch is
        # a bare `create_engine(url, pool_pre_ping=True)`, identical in effect to the old
        # bare call here).
        engine = _make_engine(url)
        with engine.connect() as connection:
            ensure_wide_version_table(connection)
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=connection.dialect.name == "sqlite",
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()
    else:
        ensure_wide_version_table(connectable)
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            render_as_batch=connectable.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
