"""Decimal normalization — revision 0008 (plan 3, task 5): the seven money columns
(``transactions.{from_amount,to_amount,base_amount,fx_rate,original_amount,quantity}``,
``transaction_splits.amount``) convert ``Float`` -> ``Text``, holding ``repr(float)``-exact
decimal strings, verified per-row and via a per-book signed-sum invariant, all-or-nothing.

Every test here seeds a fresh database at revision ``"202608072129_money_core_cutover"`` FIRST (via ``money_io.
import_accounts``/``import_transactions(..., money_as_text=False)`` — the exact shape 0007's
own ``upgrade()`` uses, real Float-column data, no revision-0008 involvement yet), THEN
advances to ``"202608080315_money_decimal"``/``"head"`` and inspects the result — the only way to genuinely prove
what the CONVERSION did, as opposed to what a live, already-migrated store would write.

Edge floats deliberately span: a value with a long binary-vs-decimal mismatch (``0.1``), a
repeating-decimal tail (``1/3``), negative (``-5.5``), very large
(``123456789012345.678``), an integer-shaped input (``7``, proving ``quantity``'s
``float()`` coercion), and ``None`` (must stay ``None``, never ``"None"``/``"null"``).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, text

from backend.db import money_io as mio
from backend.db.alembic_config import make_config
from tests.db.conftest import reversible_tip

_QUARTER = 1 / 3  # 0.3333333333333333 — a real repr() tail, not a rounded literal
_HUGE = 123456789012345.678


def _scrub_and_redirect_to(url: str, target_revision: str, monkeypatch, tmp_path) -> None:
    """The DIALECT-CONTRACT setup every test in this file needs, extracted since none of
    them can use ``db_isolated`` directly (it always upgrades to ``"head"``; several tests
    here need to stop at ``"202608072129_money_core_cutover"`` first to seed genuinely-Float data). Mirrors ``tests/db/
    conftest.py``'s ``db_isolated`` fixture EXACTLY: on Postgres, ``db_url`` is the ONE
    shared CI server (unlike SQLite's fresh-tmp_path-per-test), so skipping the schema scrub
    here silently reuses whatever a PRIOR test in this file left behind — reproduced
    directly against a live postgres:16 container (every test past the first failed with
    ``psycopg.errors.UniqueViolation: duplicate key value violates unique constraint
    "pk_accounts"`` before this fix). ``monkeypatch.setenv`` + ``dispose_engine()`` is the
    same env-var-redirect ``db_isolated`` uses, needed here because every test also calls
    ``backend.db.engine.get_engine()`` directly to inspect results.

    Also patches every cutover source seam missing (``tests/db/conftest.py``'s
    ``patch_all_cutover_sources_missing``) BEFORE the upgrade to ``target_revision``
    below — even though ``target_revision`` itself never reaches the config-cutover
    revision, several tests in this file go on to call ``command.upgrade(cfg, "head")``
    a SECOND time later in their own body (the up/down/up cycle), directly, without
    ever touching the source constants themselves. ``monkeypatch`` stays live for the
    rest of the test regardless of which nested call first set it, so patching here is
    what protects that later, unrelated call too: without it, the second upgrade would
    re-absorb the ambient, session-shared DATA_DIR's ``news_sources.json``
    (unconditionally reseeded before every test) and collide on its ``json_cutovers``
    row a second time (plan 4d fix-A)."""
    from backend.db import engine as eng
    from tests.db.conftest import patch_all_cutover_sources_missing

    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    if url.startswith("postgresql"):
        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    eng.dispose_engine()
    command.upgrade(make_config(url), target_revision)


ACCOUNTS = [
    {"id": "acc_a", "name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"},
    {"id": "acc_b", "name": "Savings", "kind": "asset", "type": "cash", "currency": "USD"},
]

# Every one of the seven money columns gets at least one edge value across these four rows;
# txn_none additionally proves every nullable money field can genuinely be None at once.
TRANSACTIONS = [
    {
        "id": "txn_tenth",
        "date": "2026-01-01",
        "kind": "expense",
        "description": "coffee",
        "from_account_id": "acc_a",
        "from_amount": 0.1,
        "from_currency": "USD",
        "base_amount": 0.1,
        "base_currency": "USD",
    },
    {
        "id": "txn_xfer",
        "date": "2026-01-02",
        "kind": "transfer",
        "description": "move",
        "from_account_id": "acc_a",
        "from_amount": _QUARTER,
        "from_currency": "USD",
        "to_account_id": "acc_b",
        "to_amount": _QUARTER,
        "to_currency": "USD",
        "base_amount": _QUARTER,
        "base_currency": "USD",
        "fx_rate": -5.5,
        "original_amount": _HUGE,
        "original_currency": "USD",
    },
    {
        "id": "txn_split",
        "date": "2026-01-03",
        "kind": "income",
        "description": "payout",
        "to_account_id": "acc_b",
        "to_amount": 50.06,
        "to_currency": "USD",
        "quantity": 7,  # integer-shaped input — proves float() coercion in the promoted col
        "splits": [
            {"category": "food", "amount": 30.0},
            {"category": "household", "amount": 20.06},
        ],
        "external_refs": [
            {"source": "csv", "ref": "row-1"},
            {"source": None, "ref": "bare-ref"},  # the documented bare no-prefix ref state
        ],
    },
    {
        "id": "txn_none",
        "date": "2026-01-04",
        "kind": "adjustment",
        "description": "correction",
        "to_account_id": "acc_a",
        "to_amount": 5.0,
        "to_currency": "USD",
        # from_amount/base_amount/fx_rate/original_amount/quantity all absent -> None
    },
]

_MONEY_COLS = (
    "from_amount",
    "to_amount",
    "base_amount",
    "fx_rate",
    "original_amount",
    "quantity",
)


def _seed_at_0007(url: str) -> None:
    """The caller must already have scrubbed/upgraded to revision 0007 and redirected the
    engine singleton (``_scrub_and_redirect_to``) — this just inserts ACCOUNTS/TRANSACTIONS
    via the SAME builder path 0007's own upgrade() uses (``money_as_text=False`` — see
    money_io.py's module docstring). Disposes the engine before returning so a subsequent
    ``command.upgrade``/test read starts clean."""
    from alembic.script import ScriptDirectory

    from backend.db import engine as eng

    # The accounts go in through 0007's own frozen builder: at this revision the document column
    # is still `payload`, which the live `money_io.import_accounts` (`data`) no longer targets.
    cutover = ScriptDirectory.from_config(make_config(url)).get_revision(
        "202608072129_money_core_cutover"
    )
    with eng.get_engine().begin() as conn:
        cutover.module._import_accounts_0007(conn, ACCOUNTS)
        mio.import_transactions(conn, TRANSACTIONS, money_as_text=False)
    eng.dispose_engine()


def _txn_money_rows(conn: sa.Connection) -> dict[str, dict]:
    rows = conn.execute(
        sa.text(
            "SELECT id, from_amount, to_amount, base_amount, fx_rate, original_amount, "
            "quantity FROM transactions ORDER BY seq"
        )
    ).mappings()
    return {r["id"]: dict(r) for r in rows}


def _split_amounts(conn: sa.Connection) -> dict[str, str]:
    rows = conn.execute(sa.text("SELECT category, amount FROM transaction_splits")).fetchall()
    return {category: amount for category, amount in rows}


@pytest.fixture
def seeded_url(db_url, monkeypatch, tmp_path):
    """A fresh (Postgres: freshly-scrubbed-schema) database at revision 0007, seeded with
    ACCOUNTS/TRANSACTIONS, with the process-wide engine singleton ALSO redirected here (see
    ``_scrub_and_redirect_to`` — the dialect contract every test in this file depends on)."""
    from backend.db import engine as eng

    _scrub_and_redirect_to(db_url, "202608072129_money_core_cutover", monkeypatch, tmp_path)
    try:
        _seed_at_0007(db_url)
        yield db_url
    finally:
        eng.dispose_engine()


# ── Per-row exactness ──────────────────────────────────────────────────────────────


def test_every_promoted_money_column_is_repr_float_exact(seeded_url):
    """For every (row, column) that was non-NULL pre-migration: the post-migration TEXT is
    LITERALLY ``repr(old_float)`` (not just numerically equal via some other formatting),
    and ``float(new_text) == old_float`` exactly. Covers all six ``transactions`` columns
    across four rows including the long-tail (1/3), negative, and very-large edge values."""
    from backend.db import engine as eng

    # The expected repr() is computed from the ORIGINAL Python floats in the fixture above,
    # never re-read pre-migration — the fixture values ARE the genuinely-stored old values
    # (import_transactions with money_as_text=False stores them byte-identical).
    command.upgrade(make_config(seeded_url), "head")
    with eng.get_engine().connect() as conn:
        post = _txn_money_rows(conn)

    for txn in TRANSACTIONS:
        row = post[txn["id"]]
        for col in _MONEY_COLS:
            old = txn.get(col)
            new_text = row[col]
            if old is None:
                assert new_text is None, f"{txn['id']}.{col}: expected None, got {new_text!r}"
                continue
            assert new_text == repr(float(old)), (
                f"{txn['id']}.{col}: expected repr {repr(float(old))!r}, got {new_text!r}"
            )
            assert float(new_text) == old, f"{txn['id']}.{col}: round-trip mismatch"
    eng.dispose_engine()


def test_split_amount_converts_exactly(seeded_url):
    command.upgrade(make_config(seeded_url), "head")
    from backend.db import engine as eng

    with eng.get_engine().connect() as conn:
        amounts = _split_amounts(conn)
    assert amounts == {"food": repr(30.0), "household": repr(20.06)}
    assert float(amounts["household"]) == 20.06
    eng.dispose_engine()


def test_none_stays_none_never_the_string_none(seeded_url):
    """txn_none has every optional money field absent — every one of the six columns must
    stay a genuine SQL NULL, never the string "None"/"null"/"" after conversion."""
    command.upgrade(make_config(seeded_url), "head")
    from backend.db import engine as eng

    with eng.get_engine().connect() as conn:
        row = _txn_money_rows(conn)["txn_none"]
    for col in ("from_amount", "base_amount", "fx_rate", "original_amount", "quantity"):
        assert row[col] is None, f"txn_none.{col} should stay NULL, got {row[col]!r}"
    assert row["to_amount"] == repr(5.0)  # the one leg that WAS set
    eng.dispose_engine()


def test_external_refs_survive_untouched_incl_bare_no_prefix_ref(seeded_url):
    """transaction_external_refs carries no money column — this revision has no other
    reason to touch it — but SQLite's batch-mode recreate of `transactions` cascades onto
    it too (see 0008's own module docstring, finding 3). Proves it survives byte-identical,
    including the documented bare (source=None) ref domain state."""
    from backend.db import engine as eng

    # Sort by ref (never None — source, the bare-ref column, may be) to avoid comparing
    # None against str directly.
    with eng.get_engine().connect() as conn:
        before = sorted(
            conn.execute(sa.text("SELECT source, ref FROM transaction_external_refs")).fetchall(),
            key=lambda row: row[1],
        )
    eng.dispose_engine()

    command.upgrade(make_config(seeded_url), "head")
    with eng.get_engine().connect() as conn:
        after = sorted(
            conn.execute(sa.text("SELECT source, ref FROM transaction_external_refs")).fetchall(),
            key=lambda row: row[1],
        )
    assert after == before == [(None, "bare-ref"), ("csv", "row-1")]
    eng.dispose_engine()


# ── Per-book signed-sum invariant ──────────────────────────────────────────────────


def test_per_book_base_amount_sum_invariant_exact(seeded_url):
    """Decimal-summed over repr() strings on BOTH sides — never float-summed (the whole
    point of the invariant is to catch a drift a float sum could itself hide)."""
    from backend.db import engine as eng

    pre_expected = sum(
        (Decimal(repr(t["base_amount"])) for t in TRANSACTIONS if t.get("base_amount") is not None),
        Decimal(0),
    )
    command.upgrade(make_config(seeded_url), "head")
    with eng.get_engine().connect() as conn:
        post_amounts = [
            r[0]
            for r in conn.execute(
                sa.text("SELECT base_amount FROM transactions WHERE base_amount IS NOT NULL")
            ).fetchall()
        ]
    post_sum = sum((Decimal(a) for a in post_amounts), Decimal(0))
    assert post_sum == pre_expected
    eng.dispose_engine()


def test_per_book_sum_invariant_is_grouped_by_book_not_global(db_url, monkeypatch, tmp_path):
    """A synthetic two-book scenario (the app only ever writes DEFAULT_BOOK_ID today, but
    the invariant's own code groups by book_id — this proves that grouping is real, not
    just correct by coincidence with a single book). Seeds two `transactions` rows directly
    (bypassing money_io, which hardcodes DEFAULT_BOOK_ID) with DIFFERENT book_id values,
    each summing to a DIFFERENT total — a bug that summed globally instead of per-book
    would still pass the single-book test above but would fail this one."""
    from backend.db import engine as eng

    cfg = make_config(db_url)
    _scrub_and_redirect_to(db_url, "202608072129_money_core_cutover", monkeypatch, tmp_path)
    with eng.get_engine().begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO books (id, name, base_currency, created_at) VALUES "
                "('book_second', 'Second', 'USD', '2026-01-01')"
            )
        )
        for book_id, txn_id, amt in (
            ("book_000000000000", "txn_book1", 10.5),
            ("book_second", "txn_book2", 99.75),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO transactions (id, book_id, seq, date, kind, source, "
                    "description, base_amount, payload) VALUES "
                    "(:id, :book_id, (SELECT COALESCE(MAX(seq), -1) + 1 FROM transactions), "
                    "'2026-01-01', 'adjustment', 'manual', 'x', :amt, '{}')"
                ),
                {"id": txn_id, "book_id": book_id, "amt": amt},
            )
    eng.dispose_engine()

    command.upgrade(cfg, "head")
    with eng.get_engine().connect() as conn:
        rows = conn.execute(
            sa.text("SELECT book_id, base_amount FROM transactions WHERE base_amount IS NOT NULL")
        ).fetchall()
    by_book: dict[str, Decimal] = {}
    for book_id, amount in rows:
        by_book[book_id] = by_book.get(book_id, Decimal(0)) + Decimal(amount)
    assert by_book["book_000000000000"] == Decimal(repr(10.5))
    assert by_book["book_second"] == Decimal(repr(99.75))
    eng.dispose_engine()


# ── Abort-all on a poisoned row ─────────────────────────────────────────────────────


def test_poisoned_row_aborts_the_whole_migration_no_partial_write(seeded_url, monkeypatch):
    """Simulates a corrupted conversion by monkeypatching money_io._money_text (the ONE
    conversion primitive 0008 calls — see its module header for why this is the correct,
    reliable patch point given Alembic reloads the revision file fresh on every command)
    to return a WRONG value for exactly one row. _verify_upgrade_exact must catch it and
    raise, rolling back the ENTIRE transaction — schema included, not just the data rewrite."""
    from backend.db import engine as eng

    real = mio._money_text

    def poisoned(value):
        if value == 0.1:
            return "999.0"  # deliberately wrong
        return real(value)

    monkeypatch.setattr(mio, "_money_text", poisoned)

    with pytest.raises(RuntimeError, match="failed exact verification"):
        command.upgrade(make_config(seeded_url), "head")

    monkeypatch.setattr(mio, "_money_text", real)
    eng.dispose_engine()

    # Nothing committed: still at 0007, schema still Float, original data intact.
    with eng.get_engine().connect() as conn:
        version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "202608072129_money_core_cutover"
        still_there = conn.execute(
            sa.text("SELECT from_amount FROM transactions WHERE id = 'txn_tenth'")
        ).scalar_one()
        assert still_there == 0.1
    # Dialect-portable column-type check (sqlalchemy.inspect, not a SQLite-only PRAGMA) —
    # Postgres reflects `double precision` back as a sa.Float subtype, same as SQLite's REAL.
    cols = {c["name"]: c["type"] for c in sa.inspect(eng.get_engine()).get_columns("transactions")}
    assert isinstance(cols["from_amount"], sa.Float), (
        f"from_amount must still be Float after the aborted migration, got {cols['from_amount']!r}"
    )
    eng.dispose_engine()


# ── Full up/down/up cycle ───────────────────────────────────────────────────────────


def test_downgrade_restores_float_exactly_then_reupgrade_matches(seeded_url):
    """Gate 3's cycle, with real seeded data (not an empty schema): upgrade -> assert
    text; downgrade -> assert the ORIGINAL floats came back bit-exact (every edge value);
    upgrade again -> assert text again, identical to the first upgrade."""
    from backend.db import engine as eng

    cfg = make_config(seeded_url)
    command.upgrade(cfg, reversible_tip(seeded_url))  # head's chain refuses a downgrade past it
    with eng.get_engine().connect() as conn:
        first_text = _txn_money_rows(conn)
        first_splits = _split_amounts(conn)
    eng.dispose_engine()

    # Target "202608072129_money_core_cutover" explicitly, not "-1": "head" has moved past 0008 (plan 4a onward adds
    # revisions on top of the money core), so a relative "-1" no longer lands on 0008's own
    # downgrade — it would just undo whatever revision currently sits directly below head.
    # "202608072129_money_core_cutover" is what this test actually means: undo every revision back through and
    # including 0008, landing on the one immediately BEFORE 0008's Float->Text conversion.
    command.downgrade(cfg, "202608072129_money_core_cutover")
    with eng.get_engine().connect() as conn:
        floats = _txn_money_rows(conn)
        split_floats = _split_amounts(conn)
    for txn in TRANSACTIONS:
        row = floats[txn["id"]]
        for col in _MONEY_COLS:
            assert row[col] == txn.get(col), f"{txn['id']}.{col} did not survive the downgrade"
    assert split_floats == {"food": 30.0, "household": 20.06}
    eng.dispose_engine()

    command.upgrade(cfg, "head")
    with eng.get_engine().connect() as conn:
        second_text = _txn_money_rows(conn)
        second_splits = _split_amounts(conn)
        command.check(cfg)
    assert second_text == first_text
    assert second_splits == first_splits
    eng.dispose_engine()


def test_poisoned_downgrade_aborts_the_whole_migration_no_partial_write(seeded_url):
    """FIX ROUND 1 (review, FIX 1 — downgrade symmetry): the downgrade counterpart to
    ``test_poisoned_row_aborts_the_whole_migration_no_partial_write`` above — same style,
    opposite direction. ``downgrade()`` never calls a mockable Python conversion function
    (unlike ``upgrade()``'s ``money_io._money_text`` — it trusts the dialect-native
    Text->Float cast for the VALUE, only verifying it), so poisoning here means directly
    corrupting the STORED TEXT with a syntactically-valid-but-NON-canonical decimal literal
    (``"0.1000"`` instead of ``repr(0.1) == "0.1"``) via raw SQL — the exact scenario
    ``_verify_downgrade_exact``'s per-row check exists to catch: ``float("0.1000") == 0.1``
    parses fine, but ``repr(0.1) == "0.1" != "0.1000"``, so the canonical-text assumption is
    violated. Confirms the abort is genuine (not just "an exception happened somewhere"):
    still at 0008 afterward, the poisoned text itself untouched.

    Upgrades to the explicit revision ``"202608080315_money_decimal"``, not ``"head"``: head has moved past 0008
    (plan 4a onward adds revisions on top of the money core), and this test's whole point is
    to isolate 0008's OWN downgrade — pinning to "202608080315_money_decimal" makes the subsequent ``"-1"`` an
    unambiguous single hop (0008 -> 0007) regardless of how many revisions exist above it,
    now or later, and sidesteps ever having to reason about whether a later multi-hop
    downgrade would roll back as one transaction or several."""
    from backend.db import engine as eng

    cfg = make_config(seeded_url)
    command.upgrade(cfg, "202608080315_money_decimal")
    with eng.get_engine().begin() as conn:
        conn.execute(
            sa.text("UPDATE transactions SET base_amount = '0.1000' WHERE id = 'txn_tenth'")
        )
    eng.dispose_engine()

    with pytest.raises(RuntimeError, match="failed exact verification"):
        command.downgrade(cfg, "-1")

    eng.dispose_engine()
    # Nothing committed: still at 0008, schema still Text, the poisoned text itself untouched
    # (this migration never rewrites transactions' OWN columns on the way down — only
    # verifies — so an aborted downgrade must leave the exact poisoned string in place).
    with eng.get_engine().connect() as conn:
        version = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "202608080315_money_decimal"
        still_there = conn.execute(
            sa.text("SELECT base_amount FROM transactions WHERE id = 'txn_tenth'")
        ).scalar_one()
        assert still_there == "0.1000"
    cols = {c["name"]: c["type"] for c in sa.inspect(eng.get_engine()).get_columns("transactions")}
    assert isinstance(cols["base_amount"], sa.Text), (
        f"base_amount must still be Text after the aborted downgrade, got {cols['base_amount']!r}"
    )
    eng.dispose_engine()
