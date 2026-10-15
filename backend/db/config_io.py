"""Config-store import/export between the JSON file shapes and the 12 Tier-1/Tier-3
config tables (built by revision 0015) — the plan-4d task-5 absorb, revision 0016.

SCOPE (controller ruling, plan 4d task 5): this module migrates ONLY the 12 config
JsonStore subclasses that hold no secrets — connector config/secrets (Tasks 3/4, the
Fernet encryption seam + the ~17 connector stores) are DEFERRED to a separate
post-terminal security track. Consequently ``config_io`` does NO encryption or
decryption: no ``secret_fields=`` parameter anywhere, no ``CONNECTOR_CONFIG_SPECS``,
no ``connector_settings`` table touched. ``import_namespaced``/``export_namespaced``
are plain dict round-trips for the two non-secret ``NamespacedSettingsStore``
singletons only (``memory_settings``, ``preferred_models``).

Mirrors ``logs_io.py``/``planning_io.py``'s generic pair, merged: THIS wave has BOTH
book-scoped Tier-1 stores (``payees``, ``automations``, ``alerts`` — ``book_id`` is a
promoted COLUMN, never inside ``payload``, written ``DEFAULT_BOOK_ID`` unconditionally,
exactly ``planning_io``'s reasoning) and plain Tier-2/Tier-3 stores with no book concept
at all (the other 6 flat-list stores), so ``import_payload_list`` takes an optional
``book_id`` — omitted/``None`` for the 6 book-less stores, matching every prior wave's
own generic pair otherwise. Three of the twelve are NOT flat lists: ``memory_settings``/
``preferred_models`` share ONE ``settings`` table row each, keyed by ``namespace``
(mirrors ``plan_state``'s book-keyed singleton pair, keyed by ``namespace`` instead);
``dashboard_layout`` is a per-book singleton in its OWN table, keyed by ``book_id``
(mirrors ``plan_state``/``watchlists`` exactly).

ORDER FIX (the one real correctness trap this wave — the Wave C logs-cutover scar,
guarded against here from the start): ``signals`` is the ONE prepend/ring source among
these twelve — its JSON file is newest-first (the pre-rewrite ``SignalsStore`` always
``items.insert(0, new)``) and ``NotificationLogStore.query_signals`` reads it back via an
explicit ``ORDER BY seq DESC`` (module docstring, ``notification_log_store.py`` — renamed
from ``SignalsStore``/``signals_store.py`` at revision 0044). It therefore
passes ``reverse=True`` here so ascending seq means oldest→newest (matching
``_next_seq``'s ``max(seq)+1`` convention for a post-migration append — the newest row
must land on the HIGHEST seq) — importing it in plain file order via ``enumerate``
would hand the newest pre-existing row seq=0, inverting chronological order the first
time ``seq DESC`` is read post-migration. The matching ``export_payload_list(...,
reverse=True)`` reads ``seq DESC`` right back, so the cutover's ``export == source``
round-trip check still holds byte-exact. Every OTHER store defaults ``reverse=False``
(their JSON files are already append-order, oldest-first on disk). See
``logs_io.import_payload_list``'s own docstring for the full mechanics this mirrors.

NO MONEY GUARD: none of the twelve stores carries a net-worth-bearing amount (an
``alerts`` row's ``value`` is a price threshold, not a holding; ``dashboard_layout``
widget config is explicitly never a derived figure — see that store's own docstring) —
so every field stays payload-verbatim like every other non-promoted field, no
``_assert_finite_money`` call this wave, mirroring ``logs_io``'s identical rule.

``CONFIG_PAYLOAD_SPECS``/``CONFIG_FILES`` mirror ``LOGS_PAYLOAD_SPECS``/
``PLANNING_PAYLOAD_SPECS``: revision 0016 AND ``portable_bridge`` both reference
``backend.db.tables`` directly rather than iterating these lists (matching every prior
cutover/bridge), so the constants are not on the write path — they survive as test-pinned
cross-checks (``tests/db/test_configs_cutover.py``) that the cutover covers exactly this set.

``_INTEL_SIGNALS_LEGACY`` is a throwaway ``Table`` on its OWN private ``MetaData()`` —
NOT imported from ``backend.db.tables`` (RFC ``intel-signals-data-model`` split
``intel_signals`` into ``intel_stories``/``signal_timeline_entries``/``daily_digests`` and
dropped it there), mirroring the ``tool_grants``/``signals`` lesson revision 0016's own
docstring records: a frozen cutover module must not hold a live handle to a table that a
later revision renames or drops. It is a real ``Table`` instance (not a bare ``sa.table()``
proxy) purely so it still satisfies this module's ``CONFIG_PAYLOAD_SPECS`` type hint below.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, Connection, Integer, MetaData, Table, Text, insert, select

from backend.core.io import fire_db_write_hook
from backend.db.tables import (
    DEFAULT_BOOK_ID,
    alerts,
    automations,
    dashboard_layouts,
    memory,
    news_sources,
    notification_log,
    payees,
    settings,
    statement_formats,
)

_INTEL_SIGNALS_LEGACY = Table(
    "intel_signals",
    MetaData(),
    Column("seq", Integer),
    Column("id", Text),
    Column("payload", JSON),
)

_BATCH = 1000


# ── Generic pair — the flat-list stores. book_id is an optional promoted COLUMN
# (never inside payload): given for the 3 Tier-1 members (payees/automations/alerts),
# omitted for the other 6 (no book concept). reverse mirrors logs_io.py's flag exactly
# — see the module docstring's ORDER FIX section. ──────────────────────────────────


def import_payload_list(
    conn: Connection,
    table: Table,
    raw: list[dict],
    *,
    id_field: str = "id",
    book_id: str | None = None,
    reverse: bool = False,
) -> int:
    """Absorb a flat JSON list into ``table``, one row per element with an ascending
    ``seq`` (0-based, file order). ``book_id`` promotes a book column (Tier-1 stores);
    ``None`` leaves it off (Tier-2). ``reverse=True`` walks the source oldest→newest
    (``list(reversed(raw))``) so the OLDEST element gets the lowest ``seq`` — required for
    a PREPEND-order source (one whose on-disk JSON is newest-first and whose store reads
    ``seq DESC``, i.e. ``signals``): the matching ``export_payload_list(..., reverse=True)``
    then reads ``seq DESC`` and reproduces the newest-first order byte-for-byte, so the
    cutover's ``export == source`` round-trip holds. Append-order sources keep the default
    ``reverse=False``. See the module docstring's ORDER FIX section."""
    ordered = list(reversed(raw)) if reverse else raw
    rows = []
    for seq, row in enumerate(ordered):
        entry: dict = {"seq": seq, "id": row.get(id_field), "payload": row}
        if book_id is not None:
            entry["book_id"] = book_id
        rows.append(entry)
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_payload_list(conn: Connection, table: Table, *, reverse: bool = False) -> list[dict]:
    """``reverse`` must be the SAME value the matching ``import_payload_list`` call
    used — see that function's docstring / the module docstring's ORDER FIX section."""
    order = table.c.seq.desc() if reverse else table.c.seq
    return [r.payload for r in conn.execute(select(table).order_by(order))]


# ── memory_settings / preferred_models: ONE shared `settings` table, one row per
# namespace, payload verbatim — mirrors plan_state's book-keyed singleton pair, keyed
# by namespace instead of book_id. PLAIN — no secret_fields, see module docstring. ──


def import_namespaced(conn: Connection, namespace: str, raw: dict) -> int:
    fire_db_write_hook(settings.name, [(namespace, None)], conn=conn)
    conn.execute(insert(settings).values(namespace=namespace, payload=raw))
    return 1


def export_namespaced(conn: Connection, namespace: str) -> dict:
    row = conn.execute(select(settings).where(settings.c.namespace == namespace)).first()
    return row.payload if row else {}


# ── dashboard_layout: per-book singleton, keyed by book_id — mirrors plan_state's own
# import_plan_state/export_plan_state pair exactly. ─────────────────────────────────


def import_dashboard(conn: Connection, raw: dict) -> int:
    fire_db_write_hook(dashboard_layouts.name, [(DEFAULT_BOOK_ID, None)], conn=conn)
    conn.execute(insert(dashboard_layouts).values(book_id=DEFAULT_BOOK_ID, payload=raw))
    return 1


def export_dashboard(conn: Connection) -> dict:
    row = conn.execute(
        select(dashboard_layouts).where(dashboard_layouts.c.book_id == DEFAULT_BOOK_ID)
    ).first()
    return row.payload if row else {"activeViewId": None, "views": []}


# ── Filename/table/id_field/book_id/reverse specs — future-facing scaffolding (see
# module docstring); 9 entries, not 12: the 2 namespaced singletons and dashboard_layout
# are dict-shaped, addressed by name, not through this generic spec list — mirrors
# LOGS_PAYLOAD_SPECS/PLANNING_PAYLOAD_SPECS excluding their own dict-shaped stores for
# the same reason. ───────────────────────────────────────────────────────────────────

CONFIG_PAYLOAD_SPECS: list[tuple[str, Table, str, str | None, bool]] = [
    ("payees.json", payees, "id", DEFAULT_BOOK_ID, False),
    ("automations.json", automations, "id", DEFAULT_BOOK_ID, False),
    ("alerts.json", alerts, "id", DEFAULT_BOOK_ID, False),
    ("memory.json", memory, "id", None, False),
    ("statement_formats.json", statement_formats, "id", None, False),
    ("intel_signals.json", _INTEL_SIGNALS_LEGACY, "id", None, False),
    ("news_sources.json", news_sources, "id", None, False),
    # Filename stays "signals.json" — this is the historical JsonStore-era source path
    # (revision 0016), not renamed retroactively. The table object is `notification_log`,
    # its CURRENT name (renamed from `signals` at revision 0044).
    ("signals.json", notification_log, "id", None, True),
]

CONFIG_FILES: tuple[str, ...] = tuple(fn for fn, *_rest in CONFIG_PAYLOAD_SPECS) + (
    "memory_settings.json",
    "preferred_models.json",
    "dashboard_layout.json",
)
