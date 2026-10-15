"""InstrumentStore on PayloadTableStore (plan 2, task 5) — behavioral parity with the
retired flat-list-JSON implementation, the checkpoint /rewind proof (the program's first
end-to-end proof a DB store participates in /rewind), the import/export cutover contract,
and the full-migration path.

Fixtures below are HAND-COMPUTED against ``backend/db/instrument_store.py``'s exact
pre-cutover semantics (read line by line before writing these): the case-insensitive
symbol/source/identifier lookups, ``find_by_symbol_and_class``'s exact (case-SENSITIVE)
asset_class match, ``_make_instrument``'s coercion defaults (etf→equity collapse, coingecko
quote_currency pin, a single shared ``now()`` backing both created_at/updated_at),
``find_or_create``'s (price_source, price_source_id) dedupe key, and ``touch``'s
unconditional updated_at bump (it always wins over a caller-supplied value).
"""

from __future__ import annotations

import json

import pytest

from backend.core.errors import MigrationError, ValidationError


def _make(store, **overrides):
    """A minimal, valid instrument via the real create() factory — same coercion path
    every production caller goes through."""
    data = {
        "symbol": "aapl",
        "name": "Apple Inc",
        "asset_class": "equity",
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "quote_currency": "USD",
        **overrides,
    }
    return store.create(data)


# ── find_by_source — case-insensitive on BOTH fields; "" price_source_id never matches ──


def test_find_by_source_case_insensitive_and_empty_id_never_matches(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store, price_source="yahoo", price_source_id="AAPL")

    assert store.find_by_source("YAHOO", "aapl") == inst  # both sides case-folded
    assert store.find_by_source("yahoo", "AAPL") == inst
    assert store.find_by_source("yahoo", "") is None  # empty id short-circuits
    assert store.find_by_source("coingecko", "AAPL") is None  # wrong source


# ── find_by_price_source_id — case-insensitive, vendor-agnostic (the /quote/$symbol
# URL's lookup: it carries only the source-qualified id, never which vendor priced it) ──


def test_find_by_price_source_id_case_insensitive_any_source(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store, price_source="coingecko", price_source_id="bitcoin")

    assert store.find_by_price_source_id("bitcoin") == inst
    assert store.find_by_price_source_id("BITCOIN") == inst  # case-insensitive
    assert store.find_by_price_source_id("") is None
    assert store.find_by_price_source_id("ethereum") is None


def test_find_by_price_source_id_distinguishes_same_bare_symbol(db_isolated):
    """Regression for the real "oklo" collision: two distinct instruments can share a
    bare symbol but never a price_source_id — find_by_price_source_id must return the
    exact one asked for, not whichever the bare-symbol lookup would have picked."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    stock = _make(
        store,
        symbol="OKLO",
        price_source="yahoo",
        price_source_id="OKLO",
        name="Oklo Inc.",
    )
    wrapper = _make(
        store,
        symbol="OKLO",
        price_source="coingecko",
        price_source_id="oklo-inc-robinhood-token",
        name="Oklo (tokenized)",
    )

    assert store.find_by_price_source_id("OKLO") == stock
    assert store.find_by_price_source_id("oklo-inc-robinhood-token") == wrapper


# ── find_all_by_symbol — every match, any class, so a caller can check uniqueness ──


def test_find_all_by_symbol_returns_every_match_case_insensitive(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    stock = _make(store, symbol="OKLO", price_source="yahoo", price_source_id="OKLO")
    wrapper = _make(
        store, symbol="OKLO", price_source="coingecko", price_source_id="oklo-inc-robinhood-token"
    )

    assert store.find_all_by_symbol("OKLO") == [stock, wrapper]
    assert store.find_all_by_symbol("oklo") == [stock, wrapper]  # case-insensitive
    assert store.find_all_by_symbol("") == []
    assert store.find_all_by_symbol("NOPE") == []


def test_find_all_by_symbol_single_match_for_a_unique_symbol(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store, symbol="ONWAI", price_source="yahoo", price_source_id="ONWAI.DE")

    assert store.find_all_by_symbol("ONWAI") == [inst]


# ── find_by_symbol — case-insensitive, first match wins ──────────────────────────


def test_find_by_symbol_case_insensitive_first_match(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    first = _make(store, symbol="aapl", price_source_id="AAPL")
    _make(store, symbol="AAPL", price_source="manual", price_source_id="")  # dup symbol, later

    assert store.find_by_symbol("aapl") == first
    assert store.find_by_symbol("AAPL") == first
    assert store.find_by_symbol("") is None
    assert store.find_by_symbol("MSFT") is None


# ── find_by_symbol_and_class — symbol case-insensitive, class EXACT ──────────────


def test_find_by_symbol_and_class_requires_exact_class(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    equity = _make(store, symbol="btc", asset_class="equity", price_source_id="BTC-CORP")
    crypto = _make(
        store,
        symbol="BTC",
        asset_class="crypto",
        price_source="coingecko",
        price_source_id="bitcoin",
    )

    assert store.find_by_symbol_and_class("btc", "equity") == equity
    assert store.find_by_symbol_and_class("BTC", "crypto") == crypto
    assert store.find_by_symbol_and_class("btc", "commodity") is None
    assert store.find_by_symbol_and_class("", "equity") is None


# ── find_by_identifier — case-insensitive value match inside the identifiers bag ──


def test_find_by_identifier_case_insensitive(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store, identifiers={"isin": "US0378331005", "coingecko_id": ""})

    assert store.find_by_identifier("isin", "us0378331005") == inst
    assert store.find_by_identifier("isin", "US0378331005") == inst
    assert store.find_by_identifier("isin", "") is None  # falsy value short-circuits
    assert store.find_by_identifier("", "us0378331005") is None  # falsy key short-circuits
    assert store.find_by_identifier("figi", "us0378331005") is None  # missing key
    assert store.find_by_identifier("coingecko_id", "x") is None  # stored value is empty


# ── query_instruments — exact asset_class, case-insensitive price_source, combinable ──


def test_query_instruments_filters(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    eq = _make(
        store, symbol="AAPL", asset_class="equity", price_source="yahoo", price_source_id="AAPL"
    )
    cr = _make(
        store,
        symbol="BTC",
        asset_class="crypto",
        price_source="coingecko",
        price_source_id="bitcoin",
    )

    assert store.query_instruments() == [eq, cr]
    assert store.query_instruments(asset_class="equity") == [eq]
    assert store.query_instruments(price_source="COINGECKO") == [cr]
    assert store.query_instruments(asset_class="equity", price_source="coingecko") == []
    assert store.query_instruments(asset_class="bond") == []


# ── create — _make_instrument's coercion, exercised end-to-end through the store ──


def test_create_applies_factory_defaults_and_persists(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = store.create({"symbol": "vwra", "price_source_id": "VWRA.L", "quote_currency": "EUR"})

    assert inst["id"].startswith("inst_")
    assert inst["symbol"] == "VWRA"  # uppercased
    assert inst["asset_class"] == "equity"  # default
    assert inst["price_source"] == "yahoo"  # default
    assert inst["quote_currency"] == "EUR"  # caller-supplied, upper-cased — never DEFAULTED
    # (a missing one is refused: tests/test_instrument_quote_currency_bugfixes.py)
    assert inst["created_at"] == inst["updated_at"]  # a single now() call backs both
    assert store.find_by_id(inst["id"]) == inst  # actually persisted, not just returned


def test_create_collapses_etf_class_to_equity_wrapper(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = store.create(
        {"symbol": "VOO", "asset_class": "etf", "price_source_id": "VOO", "quote_currency": "USD"}
    )
    assert inst["asset_class"] == "equity"
    assert inst["asset_subclass"] == "etf"


def test_create_pins_coingecko_quote_currency_to_usd(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = store.create(
        {
            "symbol": "SOL",
            "asset_class": "crypto",
            "price_source": "coingecko",
            "price_source_id": "solana",
            "quote_currency": "EUR",  # deliberately wrong — must be pinned to USD
        }
    )
    assert inst["quote_currency"] == "USD"


def test_create_rejects_unsafe_yahoo_price_source_id(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    with pytest.raises(ValidationError):
        store.create(
            {
                "symbol": "X",
                "price_source": "yahoo",
                "price_source_id": "bad id!",
                "quote_currency": "USD",
            }
        )
    assert store.query_instruments() == []  # the failed create wrote nothing


# ── find_or_create — dedupe on (price_source, price_source_id) ───────────────────


def test_find_or_create_dedupes_on_identity_key(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    first, created1 = store.find_or_create(
        {
            "symbol": "AAPL",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
            "quote_currency": "USD",
        }
    )
    assert created1 is True

    second, created2 = store.find_or_create(
        {  # case-different
            "symbol": "AAPL",
            "price_source": "YAHOO",
            "price_source_id": "aapl",
            "quote_currency": "USD",
        }
    )
    assert created2 is False
    assert second == first
    assert len(store.all()) == 1  # no duplicate minted


# ── touch — always bumps updated_at, merges changes, no-op on missing id ─────────


def test_touch_bumps_updated_at_and_merges(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store)
    updated = store.touch(inst["id"], {"name": "Apple Inc.", "sector": "Technology"})

    assert updated["name"] == "Apple Inc."
    assert updated["sector"] == "Technology"
    assert updated["updated_at"] != inst["updated_at"]
    assert updated["created_at"] == inst["created_at"]  # untouched
    assert updated["symbol"] == inst["symbol"]  # untouched fields preserved


def test_touch_missing_id_is_a_noop(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    assert store.touch("ghost", {"name": "x"}) is None
    assert store.all() == []


def test_touch_ignores_caller_supplied_updated_at(db_isolated):
    """touch's own `_now()` always wins — `{**changes, "updated_at": _now()}` overwrites
    whatever the caller put in `changes`."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store)
    updated = store.touch(inst["id"], {"updated_at": "1999-01-01T00:00:00+00:00"})
    assert updated["updated_at"] != "1999-01-01T00:00:00+00:00"


# ── set_constituents — normalizes + persists via touch ───────────────────────────


def test_set_constituents_normalizes_and_persists(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store)
    updated = store.set_constituents(
        inst["id"],
        [
            {"weight": "0.5", "sector": "Tech", "region": "US", "label": "AAPL", "junk": "drop"},
            {"weight": None, "sector": "", "region": "", "label": ""},
        ],
    )
    assert updated["constituents"] == [
        {"weight": 0.5, "sector": "Tech", "region": "US", "label": "AAPL"},
        {"weight": 0.0, "sector": "", "region": "", "label": ""},
    ]
    assert updated["updated_at"] != inst["updated_at"]


def test_set_constituents_empty_list_clears(db_isolated):
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store)
    store.set_constituents(inst["id"], [{"weight": 1.0}])
    cleared = store.set_constituents(inst["id"], [])
    assert cleared["constituents"] == []


# ── Checkpoint /rewind proof (spec D7) — the program's first end-to-end DB proof ──


def test_checkpoint_restore_reverts_touch_and_preview_shows_db_row(db_isolated):
    """touch() an instrument inside a real begin_turn/end_turn, restore, and assert the
    change reverted AND preview() surfaces a db:instruments row — instruments is
    checkpoint-INCLUDED (not in ai.checkpoint._EXCLUDED_TABLES)."""
    from backend.ai import checkpoint as cp
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = _make(store, name="Before")

    ctx, token = cp.begin_turn("s1", 0, "touch instrument")
    store.touch(inst["id"], {"name": "After"})
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert entry.get("db_tables") == ["instruments"]
    assert store.find_by_id(inst["id"])["name"] == "After"

    preview = cp.preview(entry["id"])
    db_rows = [c for c in preview["changes"] if c["file"] == "db:instruments"]
    assert len(db_rows) == 1
    assert db_rows[0]["delta"] == 0  # in-place update: no net row-count change

    result = cp.restore(entry["id"], mode="data")
    assert result["undo_checkpoint_id"]  # the restore itself is undoable

    reverted = store.find_by_id(inst["id"])
    assert reverted["name"] == "Before"
    assert reverted["updated_at"] == inst["updated_at"]  # touch's bump is undone too


def test_checkpoint_restore_reverts_a_create_and_preview_shows_positive_delta(db_isolated):
    """The insert-within-a-turn half: restore must DELETE the row minted mid-turn, and
    preview must report that as restored_count < current_count (delta -1)."""
    from backend.ai import checkpoint as cp
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()

    ctx, token = cp.begin_turn("s1", 0, "create instrument")
    inst = _make(store, symbol="NEWCO")
    entry = cp.end_turn(ctx, token)

    assert entry is not None and entry.get("db_tables") == ["instruments"]
    preview = cp.preview(entry["id"])
    db_rows = [c for c in preview["changes"] if c["file"] == "db:instruments"]
    assert db_rows[0]["delta"] == -1  # restore would delete the newly-minted row

    cp.restore(entry["id"], mode="data")
    assert store.find_by_id(inst["id"]) is None
    assert store.all() == []


# ── instruments_io — import/export round-trip (root_key=None, flat list) ─────────

FIXTURE = [
    {
        "id": "inst_a",
        "symbol": "AAPL",
        "asset_class": "equity",
        "price_source": "yahoo",
        "price_source_id": "AAPL",
        "name": "Apple Inc",
    },
    {
        "id": "inst_b",
        "symbol": "BTC",
        "asset_class": "crypto",
        "price_source": "coingecko",
        "price_source_id": "bitcoin",
        "name": "Bitcoin",
    },
]


def test_round_trip_import_export_exact(db_isolated):
    from backend.db.engine import get_engine
    from backend.db.instruments_io import export_instruments, import_instruments

    with get_engine().begin() as conn:
        n = import_instruments(conn, FIXTURE)
    assert n == 2
    with get_engine().connect() as conn:
        assert export_instruments(conn) == FIXTURE  # exact, ordered equality


def test_missing_source_file_is_a_clean_empty_import(db_isolated):
    from backend.db.engine import get_engine
    from backend.db.instruments_io import export_instruments

    with get_engine().connect() as conn:
        assert export_instruments(conn) == []


# ── Full migration — absorb instruments.json, verify, rename, cutover row ────────


def test_full_migration_absorbs_and_renames(tmp_path, monkeypatch):
    src = tmp_path / "instruments.json"
    src.write_text(json.dumps(FIXTURE))
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.INSTRUMENTS_SOURCE", src)
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    from backend.db.instruments_io import export_instruments

    with eng.get_engine().connect() as conn:
        assert export_instruments(conn) == FIXTURE
    assert not src.exists()
    assert len(list(tmp_path.glob("instruments.json.migrated-*"))) == 1

    from sqlalchemy import select

    from backend.db.tables import json_cutovers

    with eng.get_engine().connect() as conn:
        row = conn.execute(
            select(json_cutovers).where(json_cutovers.c.store == "instruments")
        ).one()
    assert row.renamed == 1
    assert row.source_hash  # hash guard recorded, non-empty

    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_source_missing(tmp_path, monkeypatch):
    """Fresh install: no instruments.json to absorb — head is reached, no cutover row."""
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.INSTRUMENTS_SOURCE",
        tmp_path / "does_not_exist.json",
    )
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    from backend.db.instruments_io import export_instruments

    with eng.get_engine().connect() as conn:
        assert export_instruments(conn) == []
        from sqlalchemy import func, select

        from backend.db.tables import json_cutovers

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store == "instruments")
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


def test_full_migration_aborts_on_non_list_source_shape(tmp_path, monkeypatch):
    """instruments.json is root_key=None (a flat list) — any other top-level shape (e.g. a
    hand-edited dict) must abort the migration rather than silently coercing/dropping data."""
    src = tmp_path / "instruments.json"
    src.write_text(json.dumps({"unexpected": "shape"}))
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.INSTRUMENTS_SOURCE", src)
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    with pytest.raises(MigrationError):
        ensure_migrated()

    eng.dispose_engine()
