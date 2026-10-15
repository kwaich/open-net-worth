"""Config/settings schema (plan 4d, task 1) — the settings primitive's table plus the 10
per-store tables it ships alongside: payees/automations/alerts (Tier-1, book-scoped flat
list), dashboard_layouts (Tier-1, per-book singleton), memory (user-scoped — spec:276 — NOT
book_id), news_sources/statement_formats/notification_log/intel_stories (Tier-3, no book_id;
``intel_stories`` was ``intel_signals`` before migration 202608301500's RFC
intel-signals-data-model split — ``signal_timeline_entries``/``daily_digests``, its two new
sibling tables from that split, are intentionally NOT added to this list, matching how this
module scopes to exactly the tables this wave shipped, not everything Tier-3/no-book_id).
``memory_settings``/``preferred_models`` are ROWS in ``settings``, not tables of their own —
see test_settings_store.py for that round trip. Mirrors test_planning_tables.py's
structure/depth.

``tool_grants`` note (2026-08-14, owner ruling): this wave originally shipped a
``tool_grants`` Tier-3 table alongside the ten listed above. Revision 0040 absorbs it into
``tool_permissions`` (the renamed ``connector_tool_permissions``) and DROPS it — see
``tests/db/test_tool_permissions_migration.py``. Per the owner's "delete rather than
deprecate" ruling, ``backend.db.tables`` no longer keeps ANY Python handle for it (not even
a historical one for revision 0016's replay — that replay job was removed outright, since
its only purpose was an instance upgrading from files, which cannot apply here); revision
0015 alone still creates the table's raw SQL schema (needed so revision 0040 has something
to fold real, already-written rows out of and then drop), with no `backend.db.tables`
counterpart at all. So ``tool_grants`` is simply ABSENT from every list/assertion below —
there is no metadata object to register, size, or classify.
"""

from sqlalchemy import inspect

from backend.db import tables as t

BOOK_PAYLOAD_CONFIG_TABLES = ["payees", "automations", "alerts"]

TIER3_CONFIG_TABLES = [
    "memory",
    "news_sources",
    "statement_formats",
    "notification_log",
    "intel_stories",
]

ALL_CONFIG_TABLES = (
    ["settings"] + BOOK_PAYLOAD_CONFIG_TABLES + ["dashboard_layouts"] + TIER3_CONFIG_TABLES
)


def test_all_config_tables_registered():
    for name in ALL_CONFIG_TABLES:
        assert name in t.metadata.tables


def test_book_payload_config_tables_are_book_scoped_single_pk():
    for name in BOOK_PAYLOAD_CONFIG_TABLES:
        table = t.metadata.tables[name]
        assert [c.name for c in table.primary_key.columns] == ["id"]  # single-col PK (write_log)
        assert {c.name for c in table.c} == {"book_id", "seq", "id", "payload"}
        assert "book_id" in [fk.parent.name for fk in table.foreign_keys]


def test_book_scoped_config_tables_carry_book_id():
    for name in BOOK_PAYLOAD_CONFIG_TABLES:
        assert "book_id" in [fk.parent.name for fk in t.metadata.tables[name].foreign_keys]
    assert [c.name for c in t.dashboard_layouts.primary_key.columns] == ["book_id"]


def test_tier3_config_tables_have_no_book_id():
    for name in TIER3_CONFIG_TABLES:
        assert "book_id" not in t.metadata.tables[name].c
    assert [c.name for c in t.settings.primary_key.columns] == ["namespace"]


def test_settings_table_shape():
    """PK is the namespace string; payload is the whole config dict verbatim. No FK — a
    namespace row belongs to no book and no user (Tier-3 installation config, spec:205)."""
    assert {c.name for c in t.settings.c} == {"namespace", "payload"}
    assert not t.settings.foreign_keys


def test_dashboard_layouts_is_a_per_book_singleton():
    """PK IS book_id (one row per book) — mirrors plan_state/watchlists, not the flat-list
    payload-table shape the other 3 Tier-1 config tables use."""
    assert {c.name for c in t.dashboard_layouts.c} == {"book_id", "payload"}
    assert [c.name for c in t.dashboard_layouts.primary_key.columns] == ["book_id"]


def test_config_schema_creates_on_both_dialects(db_engine):
    insp = inspect(db_engine)  # SQLite here, Postgres in CI
    for name in ALL_CONFIG_TABLES:
        assert insp.has_table(name)
    # tool_grants is absorbed into tool_permissions and dropped by revision 0040, which is
    # part of the SAME "head" chain this fixture runs to — see the module docstring.
    assert not insp.has_table("tool_grants")
    assert insp.has_table("tool_permissions")


def test_signals_is_checkpoint_excluded():
    """The controller's checkpoint split for this wave: ``notification_log`` (renamed
    from ``signals`` at revision 0044) joins ai.checkpoint._EXCLUDED_TABLES — it mirrors
    its existing SIGNALS_JSON file-level exclusion in that module's _EXCLUDED_PATHS (a
    runtime notification cache must not be resurrected by a /rewind). Every OTHER config
    table in this wave, settings included, stays checkpoint-INCLUDED (an edit there IS
    undoable by /rewind).

    Was ``test_tool_grant_and_signals_are_checkpoint_excluded`` — ``tool_grants`` is GONE
    (absorbed into ``tool_permissions`` at revision 0040, which inherits this exact
    exclusion unchanged; see ``tests/db/test_tool_permission_store.py``), so this test now
    covers only the table that still exists in this wave's own scope.
    """
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "tool_grants" not in _EXCLUDED_TABLES  # the table itself no longer exists
    assert "tool_permissions" in _EXCLUDED_TABLES  # its successor inherits the exclusion
    assert "notification_log" in _EXCLUDED_TABLES
    for name in ALL_CONFIG_TABLES:
        if name == "notification_log":
            continue
        assert name not in _EXCLUDED_TABLES, f"{name} must be checkpoint-included"
