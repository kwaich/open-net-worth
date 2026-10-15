"""Planning cutover (plan 4a, task 6): revision 0010 absorbs all 12 planning JSON
sources into their Tier-1 tables (built by revision 0009), one all-or-nothing unit.

Mirrors ``tests/db/test_money_cutover.py``'s shape, simplified: no cross-file
consistency pre-check (no FKs BETWEEN planning tables) and no embedded-child
explosion (every planning table is either a flat payload list or a bespoke
dict-shaped store). Covers: exact round-trip per store (direct ``planning_io`` calls,
fast) AND through the real migration chain (monkeypatched sources + ``command.
upgrade``); the two dict-shaped stores (``budget_assignments``/``plan_state``); the
``match_decisions`` legacy-surrogate absorb; the dict-shape guard; full absorb + 12
``json_cutovers`` rows + 12 renames; the up/down/up cycle.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.core.errors import ValidationError
from backend.db import planning_io as pio
from backend.db.tables import (
    budget_assignments,
    categories,
    commitments,
    corrections,
    envelope_groups,
    envelopes,
    income_events,
    json_cutovers,
    match_decisions,
    match_reviews,
    plan_state,
    recurring_streams,
    targets,
)

PLANNING_STORE_NAMES = (
    # The legacy-JSON cutover job keeps the store name the schema had when it ran.
    "category_groups",
    "categories",
    "commitments",
    "envelopes",
    "income_events",
    "recurring_streams",
    "targets",
    "corrections",
    "match_reviews",
    "match_decisions",
    "budget_assignments",
    "plan_state",
)


def _set_database_url_for_test(url: str, monkeypatch) -> None:
    """Postgres's ``db_url`` is the ONE shared CI server (unlike SQLite's
    fresh-tmp_path-per-test): scrub the public schema first so an earlier test's
    already-applied migration history doesn't make THIS test's ``command.upgrade(...,
    "head")`` a silent no-op (revision 0010's body would then never execute, and every
    assertion below it would be testing nothing). Mirrors ``test_money_cutover.py``'s
    identical helper verbatim."""
    if url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)


# ── Fixtures (deliberately small but non-trivial: unicode, nulls, nested dicts,
# empty lists — the round-trip proof needs a diff to actually be possible) ──────

CATEGORY_GROUPS = [
    {
        "id": "grp_1",
        "name": "Essentials",
        "categories": ["food", "transport"],
        "collapsed": False,
        "order": 0,
        "is_others": False,
    },
    {
        "id": "grp_2",
        "name": "Others",
        "categories": [],
        "collapsed": False,
        "order": 1000,
        "is_others": True,
    },
]

# What the book holds at head: revision 202610031200 retires category->group membership, so the
# groups lose their ``categories`` list (both fixture groups survive: one is the catch-all, the
# other holds only spending categories).
CATEGORY_GROUPS_AT_HEAD = [
    {k: v for k, v in group.items() if k != "categories"} for group in CATEGORY_GROUPS
]

CATEGORIES = [
    {"id": "cat_1", "label": "Groceries", "group_id": "grp_1"},
    {"id": "split", "label": "Split", "group_id": None},
]

COMMITMENTS = [
    {
        "id": "cmt_1",
        "name": "Car loan",
        "kind": "loan",
        "installments": [{"due": "2026-09-01", "amount": 450.0, "paid": False}],
        "funding_plan": {"envelope_id": "env_1", "monthly": 450.0},
        "notes": "café ☕ — unicode round-trip",
    }
]

ENVELOPES = [
    {"id": "env_1", "name": "Car", "category_ids": ["cat_1"], "order": 0, "archived": False},
]

INCOME_EVENTS = [
    {
        "id": "inc_1",
        "date": "2026-08-01",
        "gross": 5000.0,
        "tax": 750.25,
        "base_amount": 4249.75,
        "fx_rate": 1.0,
        "idempotency_key": "payroll-2026-08",
    }
]

RECURRING_STREAMS = [
    {"type": "overlay", "id": "sig_1", "status": "confirmed"},
    {
        "type": "declared",
        "id": "rst_1",
        "direction": "outflow",
        "amount": 15.99,
        "currency": "USD",
        "cadence": "monthly",
        "start_date": "2026-01-01",
        "end_date": None,
    },
]

TARGETS = [
    {
        "id": "tgt_1",
        "name": "Emergency fund",
        "target_amount": 10000.0,
        "current_amount": 3200.5,
        "deadline": "2027-01-01",
    }
]

CORRECTIONS = [
    {"id": "corr_1", "kind": "correction", "from_category": "cat_1", "to_category": "cat_2"},
    {"id": "dis_1", "kind": "dismissal", "signature": "sig_x"},
]

MATCH_REVIEWS = [
    {"id": "mr_1", "incoming_id": "txn_a", "status": "pending", "score": 0.87},
]

MATCH_DECISIONS_WITH_KEY = [
    {
        "pair_key": "pk_abc123",
        "incoming_id": "txn_a",
        "canonical_id": "txn_b",
        "signature": "sig_1",
        "verdict": "same",
        "become_transfer": False,
        "llm_reason": "identical amount/date/payee",
        "source": "llm",
        "created_at": "2026-08-01T00:00:00Z",
        "payee_label": "Coffee Co",
        "amount_bucket": "~5",
    }
]

MATCH_DECISIONS_LEGACY = [{"signature": "s1", "verdict": "same"}]  # no pair_key — the legacy shape

BUDGET_ASSIGNMENTS = {"2026-06": {"cat_1": 0.1 + 0.2, "cat_2": 800.0}, "2026-07": {"cat_1": 500.0}}

PLAN_STATE = {
    "scenarios": [{"id": "scen_1", "name": "Base case", "saving": 2000.0, "events": []}],
    "baseline": {"ret": 0.07, "swr": 0.04, "streams": []},
}

# (store name, table, fixture) for the 9 stores whose import AND export are both generic.
_GENERIC_SPECS = [
    ("category_groups", envelope_groups, CATEGORY_GROUPS),
    ("categories", categories, CATEGORIES),
    ("commitments", commitments, COMMITMENTS),
    ("envelopes", envelopes, ENVELOPES),
    ("income_events", income_events, INCOME_EVENTS),
    ("recurring_streams", recurring_streams, RECURRING_STREAMS),
    ("targets", targets, TARGETS),
    ("corrections", corrections, CORRECTIONS),
    ("match_reviews", match_reviews, MATCH_REVIEWS),
]


def _patch_all_sources_missing(monkeypatch, tmp_path) -> None:
    """Point every cutover source seam — not just this wave's own twelve — at a file
    that does not exist, so a test exercising the real migration chain never
    accidentally reads whatever the ambient (session-shared) DATA_DIR happens to hold.
    Delegates to the canonical cross-wave helper (``tests/db/conftest.py``): a bare
    ``command.upgrade(..., "head")`` runs every revision up to head, not just
    planning's own, so a DIFFERENT wave's source (e.g. Wave D's ``news_sources.json``,
    unconditionally reseeded before every test — see the canonical helper's docstring)
    can still get absorbed and collide on a second upgrade in the same test unless
    EVERY source is patched, not just this file's twelve."""
    from tests.db.conftest import patch_all_cutover_sources_missing

    patch_all_cutover_sources_missing(monkeypatch, tmp_path)


def _write_all_twelve_fixtures(tmp_path, monkeypatch) -> dict[str, tuple]:
    """Write every store's fixture to its own file under ``tmp_path`` and monkeypatch
    the matching ``versions_evidence_source`` constant at it. Returns {attr: (filename,
    original)} for assertion convenience."""
    files = {
        "CATEGORY_GROUPS_SOURCE": ("category_groups.json", CATEGORY_GROUPS),
        "CATEGORIES_SOURCE": ("categories.json", CATEGORIES),
        "COMMITMENTS_SOURCE": ("commitments.json", COMMITMENTS),
        "ENVELOPES_SOURCE": ("envelopes.json", ENVELOPES),
        "INCOME_EVENTS_SOURCE": ("income_events.json", INCOME_EVENTS),
        "RECURRING_STREAMS_SOURCE": ("recurring_streams.json", RECURRING_STREAMS),
        "TARGETS_SOURCE": ("targets.json", TARGETS),
        "CORRECTIONS_SOURCE": ("corrections.json", CORRECTIONS),
        "MATCH_REVIEWS_SOURCE": ("match_reviews.json", MATCH_REVIEWS),
        "MATCH_DECISIONS_SOURCE": ("match_decisions.json", MATCH_DECISIONS_WITH_KEY),
        "BUDGET_ASSIGNMENTS_SOURCE": ("budget_assignments.json", BUDGET_ASSIGNMENTS),
        "PLAN_STATE_SOURCE": ("plan_state.json", PLAN_STATE),
    }
    for attr, (filename, fixture) in files.items():
        path = tmp_path / filename
        path.write_text(json.dumps(fixture, ensure_ascii=False))
        monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
    return files


# ── Direct round-trip per store (fixture-scale, no migration chain needed) ──────────


@pytest.mark.parametrize(
    "store,table,fixture", _GENERIC_SPECS, ids=[spec[0] for spec in _GENERIC_SPECS]
)
def test_import_then_export_round_trips_exactly(db_engine, store, table, fixture):
    with db_engine.begin() as conn:
        n = pio.import_payload_list(conn, table, fixture)
    assert n == len(fixture)
    with db_engine.connect() as conn:
        assert pio.export_payload_list(conn, table) == fixture


def test_generic_import_promotes_book_id_but_payload_stays_verbatim(db_engine):
    with db_engine.begin() as conn:
        pio.import_payload_list(conn, envelope_groups, CATEGORY_GROUPS)
    with db_engine.connect() as conn:
        row = conn.execute(select(envelope_groups).where(envelope_groups.c.id == "grp_1")).one()
    assert row.book_id == "book_000000000000"
    assert row.seq == 0
    assert "book_id" not in row.payload


def test_import_payload_list_honors_custom_id_field(db_engine):
    """The generic pair's ``id_field`` kwarg (a public parameter future callers may use
    directly) proven against ``match_decisions`` with a row that already carries
    ``pair_key`` (no surrogate needed) — independent of the bespoke
    ``import_match_decisions`` path, which is exercised separately below."""
    with db_engine.begin() as conn:
        n = pio.import_payload_list(
            conn, match_decisions, MATCH_DECISIONS_WITH_KEY, id_field="pair_key"
        )
    assert n == 1
    with db_engine.connect() as conn:
        assert pio.export_payload_list(conn, match_decisions) == MATCH_DECISIONS_WITH_KEY
        row = conn.execute(select(match_decisions)).one()
    assert row.id == "pk_abc123"


def test_missing_source_files_are_a_clean_empty_export_for_every_generic_store(db_engine):
    with db_engine.connect() as conn:
        for _store, table, _fixture in _GENERIC_SPECS:
            assert pio.export_payload_list(conn, table) == []
        assert pio.export_payload_list(conn, match_decisions) == []


# ── match_decisions: bespoke import (surrogate id when pair_key absent) ─────────────


def test_match_decisions_with_pair_key_uses_it_as_the_id(db_engine):
    with db_engine.begin() as conn:
        n = pio.import_match_decisions(conn, MATCH_DECISIONS_WITH_KEY)
    assert n == 1
    with db_engine.connect() as conn:
        row = conn.execute(select(match_decisions)).one()
        assert row.id == "pk_abc123"
        assert pio.export_payload_list(conn, match_decisions) == MATCH_DECISIONS_WITH_KEY


def test_match_decisions_legacy_row_without_pair_key_gets_deterministic_surrogate(db_engine):
    with db_engine.begin() as conn:
        n = pio.import_match_decisions(conn, MATCH_DECISIONS_LEGACY)
    assert n == 1
    with db_engine.connect() as conn:
        row = conn.execute(select(match_decisions)).one()
        assert row.id.startswith("mdlegacy_")
        assert row.payload == MATCH_DECISIONS_LEGACY[0]  # payload VERBATIM — no pair_key added
        # export round-trips exactly despite the synthesized id living only in the id column
        assert pio.export_payload_list(conn, match_decisions) == MATCH_DECISIONS_LEGACY


def test_match_decisions_surrogate_id_is_deterministic_across_two_independent_imports(db_engine):
    """Same reasoning as money_io's ``_child_id`` determinism proof: absorbing the same
    legacy row twice (two fresh databases) must mint the SAME surrogate id, or a
    restored-from-backup re-absorb would silently change primary keys."""
    ids = []
    for _ in range(2):
        with db_engine.begin() as conn:
            conn.execute(match_decisions.delete())
            pio.import_match_decisions(conn, MATCH_DECISIONS_LEGACY)
        with db_engine.connect() as conn:
            ids.append(conn.execute(select(match_decisions.c.id)).scalar_one())
    assert ids[0] == ids[1]
    assert ids[0].startswith("mdlegacy_")


def test_match_decisions_empty_pair_key_string_is_treated_as_absent(db_engine):
    """``pair_key: ""`` (falsy but present) must fall to the surrogate, not become the
    literal empty-string primary key — ``_match_decision_id``'s ``row.get("pair_key") or
    ""`` -> ``if pk:`` branch."""
    row = {"pair_key": "", "signature": "s2", "verdict": "different"}
    with db_engine.begin() as conn:
        pio.import_match_decisions(conn, [row])
    with db_engine.connect() as conn:
        stored_id = conn.execute(select(match_decisions.c.id)).scalar_one()
    assert stored_id.startswith("mdlegacy_")
    assert stored_id != ""


# ── budget_assignments: nested dict <-> rows, amount as decimal Text ────────────────


def test_budget_assignments_round_trips_exactly(db_engine):
    with db_engine.begin() as conn:
        n = pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)
    assert n == 3  # 2 categories in 2026-06 + 1 in 2026-07
    with db_engine.connect() as conn:
        assert pio.export_budget_assignments(conn) == BUDGET_ASSIGNMENTS


def test_budget_assignments_float_exactness_survives_the_decimal_text_column(db_engine):
    """0.1 + 0.2 is the canonical float-imprecision trap (0.30000000000000004) — the
    decimal-Text encoding (``money_io._money_text`` = ``repr(float(x))``) must still
    round-trip it EXACTLY, the same guarantee revision 0008 proved for the money core."""
    original = {"2026-06": {"food": 0.1 + 0.2, "rent": 800.0, "misc": -25.5}}
    with db_engine.begin() as conn:
        pio.import_budget_assignments(conn, original)
    with db_engine.connect() as conn:
        exported = pio.export_budget_assignments(conn)
    assert exported == original
    assert exported["2026-06"]["food"] == 0.30000000000000004


def test_budget_assignments_promotes_book_id_and_id_never_in_output(db_engine):
    with db_engine.begin() as conn:
        pio.import_budget_assignments(conn, {"2026-06": {"cat_1": 100.0}})
    with db_engine.connect() as conn:
        row = conn.execute(select(budget_assignments)).one()
        out = pio.export_budget_assignments(conn)
    assert row.id == "book_000000000000:2026-06:cat_1"
    assert row.book_id == "book_000000000000"
    assert out == {"2026-06": {"cat_1": 100.0}}


def test_budget_assignments_empty_dict_imports_zero_rows(db_engine):
    with db_engine.begin() as conn:
        n = pio.import_budget_assignments(conn, {})
    assert n == 0
    with db_engine.connect() as conn:
        assert pio.export_budget_assignments(conn) == {}


# ── M1 (security review, plan 4a task 9): reject non-finite/non-numeric amounts — the
# SAME guard the money core's import_transactions uses (money_io._assert_finite_money,
# FR-M7). Mirrors tests/db/test_money_bundle_security.py's own FR-M7 section. ───────────


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_import_budget_assignments_rejects_non_finite_amount(db_engine, bad):
    with db_engine.begin() as conn:
        with pytest.raises(ValidationError):
            pio.import_budget_assignments(conn, {"2026-06": {"food": bad}})


def test_import_budget_assignments_rejects_non_numeric_amount(db_engine):
    with db_engine.begin() as conn:
        with pytest.raises(ValidationError):
            pio.import_budget_assignments(conn, {"2026-06": {"food": "abc"}})


def test_import_budget_assignments_non_finite_amount_aborts_whole_import(db_engine):
    """One poisoned category must abort the WHOLE import — nothing partially written,
    matching import_transactions' own FR-M7 all-or-nothing behavior."""
    with db_engine.begin() as conn:
        with pytest.raises(ValidationError):
            pio.import_budget_assignments(
                conn, {"2026-06": {"food": 800.0, "transport": float("inf")}}
            )
    with db_engine.connect() as conn:
        assert pio.export_budget_assignments(conn) == {}


def test_import_budget_assignments_zero_and_negative_amounts_still_import(db_engine):
    """Finite non-positive amounts are untouched by the M1 guard — only inf/nan/non-numeric
    are new rejections."""
    with db_engine.begin() as conn:
        n = pio.import_budget_assignments(conn, {"2026-06": {"food": 0.0, "misc": -25.5}})
    assert n == 2
    with db_engine.connect() as conn:
        assert pio.export_budget_assignments(conn) == {"2026-06": {"food": 0.0, "misc": -25.5}}


# ── plan_state: one row per book, verbatim payload ───────────────────────────────────


def test_plan_state_round_trips_the_singleton_dict_verbatim(db_engine):
    with db_engine.begin() as conn:
        n = pio.import_plan_state(conn, PLAN_STATE)
    assert n == 1
    with db_engine.connect() as conn:
        assert pio.export_plan_state(conn) == PLAN_STATE


def test_plan_state_default_when_no_row_exists(db_engine):
    with db_engine.connect() as conn:
        assert pio.export_plan_state(conn) == {"scenarios": [], "baseline": None}


def test_plan_state_keyed_by_book_id_not_a_seq_id_payload_shape(db_engine):
    with db_engine.begin() as conn:
        pio.import_plan_state(conn, PLAN_STATE)
    with db_engine.connect() as conn:
        row = conn.execute(select(plan_state)).one()
    assert row.book_id == "book_000000000000"
    assert row.payload == PLAN_STATE  # verbatim — camelCase-style keys untouched
    assert not hasattr(row, "id")  # plan_state has no id/seq column at all


# ── PLANNING_PAYLOAD_SPECS / PLANNING_FILES constants ────────────────────────────────


def test_planning_payload_specs_covers_the_nine_generic_stores():
    filenames = {fn for fn, _table, _id_field in pio.PLANNING_PAYLOAD_SPECS}
    assert filenames == {
        "category_groups.json",
        "categories.json",
        "commitments.json",
        "envelopes.json",
        "income_events.json",
        "recurring_streams.json",
        "targets.json",
        "corrections.json",
        "match_reviews.json",
    }
    id_fields = {id_field for _fn, _table, id_field in pio.PLANNING_PAYLOAD_SPECS}
    assert id_fields == {"id"}


def test_planning_files_lists_all_twelve():
    assert set(pio.PLANNING_FILES) == {
        "category_groups.json",
        "categories.json",
        "commitments.json",
        "envelopes.json",
        "income_events.json",
        "recurring_streams.json",
        "targets.json",
        "corrections.json",
        "match_reviews.json",
        "match_decisions.json",
        "budget_assignments.json",
        "plan_state.json",
    }
    assert len(pio.PLANNING_FILES) == 12


# ── Full migration chain: monkeypatched sources + command.upgrade(head) ─────────────


def test_all_twelve_stores_round_trip_exactly_through_the_real_migration(
    tmp_path, db_url, monkeypatch
):
    files = _write_all_twelve_fixtures(tmp_path, monkeypatch)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    exporters = {
        "CATEGORY_GROUPS_SOURCE": lambda c: pio.export_payload_list(c, envelope_groups),
        "CATEGORIES_SOURCE": lambda c: pio.export_payload_list(c, categories),
        "COMMITMENTS_SOURCE": lambda c: pio.export_payload_list(c, commitments),
        "ENVELOPES_SOURCE": lambda c: pio.export_payload_list(c, envelopes),
        "INCOME_EVENTS_SOURCE": lambda c: pio.export_payload_list(c, income_events),
        "RECURRING_STREAMS_SOURCE": lambda c: pio.export_payload_list(c, recurring_streams),
        "TARGETS_SOURCE": lambda c: pio.export_payload_list(c, targets),
        "CORRECTIONS_SOURCE": lambda c: pio.export_payload_list(c, corrections),
        "MATCH_REVIEWS_SOURCE": lambda c: pio.export_payload_list(c, match_reviews),
        "MATCH_DECISIONS_SOURCE": lambda c: pio.export_payload_list(c, match_decisions),
        "BUDGET_ASSIGNMENTS_SOURCE": lambda c: pio.export_budget_assignments(c),
        "PLAN_STATE_SOURCE": lambda c: pio.export_plan_state(c),
    }
    with eng.get_engine().connect() as conn:
        for attr, (_filename, original) in files.items():
            if attr == "CATEGORY_GROUPS_SOURCE":
                original = CATEGORY_GROUPS_AT_HEAD
            assert exporters[attr](conn) == original, f"{attr} did not round-trip exactly"

    eng.dispose_engine()


def test_budget_assignments_amounts_are_exact_after_absorb(tmp_path, db_url, monkeypatch):
    original = {"2026-06": {"food": 0.1 + 0.2, "rent": 800.0}}
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "budget_assignments.json"
    path.write_text(json.dumps(original))
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.BUDGET_ASSIGNMENTS_SOURCE", path
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert pio.export_budget_assignments(conn) == original

    eng.dispose_engine()


def test_match_decisions_legacy_row_without_pair_key_absorbs(tmp_path, db_url, monkeypatch):
    original = [{"signature": "s1", "verdict": "same"}]  # no pair_key
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "match_decisions.json"
    path.write_text(json.dumps(original))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.MATCH_DECISIONS_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert pio.export_payload_list(conn, match_decisions) == original

    eng.dispose_engine()


@pytest.mark.parametrize(
    "attr,filename,bad_payload",
    [
        ("BUDGET_ASSIGNMENTS_SOURCE", "budget_assignments.json", [1, 2, 3]),  # list, not dict
        ("PLAN_STATE_SOURCE", "plan_state.json", ["scenarios"]),  # list, not dict
        ("CATEGORY_GROUPS_SOURCE", "category_groups.json", {"not": "a list"}),  # dict, not list
    ],
    ids=[
        "budget_assignments-list-not-dict",
        "plan_state-list-not-dict",
        "category_groups-dict-not-list",
    ],
)
def test_wrong_top_level_shape_aborts_the_whole_migration(
    tmp_path, db_url, monkeypatch, attr, filename, bad_payload
):
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / filename
    path.write_text(json.dumps(bad_payload))
    monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError):
        command.upgrade(make_config(db_url), "head")
    eng.dispose_engine()


def test_export_mismatch_after_import_aborts_the_whole_migration(tmp_path, db_url, monkeypatch):
    """M2 (plan 4a whole-branch final review): ``_absorb_one``'s OTHER raise branch —
    ``export_fn(conn) != original`` AFTER a successful import (202608081826_planning_cutover.py
    ~line 65-70) — had only happy-path coverage; the test above this one only covers the
    top-level-shape ``isinstance`` guard. Proven by monkeypatching ONE store's export_fn
    (``budget_assignments``, addressed directly in the job list, so this is isolated —
    the other eleven stores' absorb is untouched) to return data that disagrees with what
    was actually imported: the WHOLE migration must still RAISE (fail-closed,
    all-or-nothing), not silently accept the drifted store."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    original = {"2026-06": {"food": 100.0}}
    path = tmp_path / "budget_assignments.json"
    path.write_text(json.dumps(original))
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.BUDGET_ASSIGNMENTS_SOURCE", path
    )
    monkeypatch.setattr(pio, "export_budget_assignments", lambda conn: {"2026-06": {"food": 999.0}})
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError, match="round-trip verification FAILED") as exc_info:
        command.upgrade(make_config(db_url), "head")
    assert "budget_assignments" in str(exc_info.value)
    eng.dispose_engine()


def test_full_migration_absorbs_all_twelve_and_renames(tmp_path, db_url, monkeypatch):
    files = _write_all_twelve_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    for attr, (filename, _original) in files.items():
        src = tmp_path / filename
        assert not src.exists(), f"{attr}'s source must be renamed away, not left in place"
        assert len(list(tmp_path.glob(f"{filename}.migrated-*"))) == 1

    with eng.get_engine().connect() as conn:
        rows = {r.store: r for r in conn.execute(select(json_cutovers))}
    for store in PLANNING_STORE_NAMES:
        assert rows[store].renamed == 1
        assert rows[store].source_hash

    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_all_twelve_sources_missing(
    tmp_path, db_url, monkeypatch
):
    """Fresh install: none of the twelve planning files exist — head is reached, zero
    rows, zero cutover-ledger entries for the planning stores."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        for _store, table, _fixture in _GENERIC_SPECS:
            assert pio.export_payload_list(conn, table) == []
        assert pio.export_payload_list(conn, match_decisions) == []
        assert pio.export_budget_assignments(conn) == {}
        assert pio.export_plan_state(conn) == {"scenarios": [], "baseline": None}

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store.in_(PLANNING_STORE_NAMES))
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


# ── Up/down/up cycle ──────────────────────────────────────────────────────────────────


def test_up_down_up_cycle_is_reversible(db_url, tmp_path, monkeypatch):
    """Structural reversibility of the SCHEMA CHAIN, isolated from data-absorption
    correctness (already fully proven by the tests above): every one of the twelve
    sources is explicitly pointed at a nonexistent file, so both upgrade passes are
    clean empty absorbs — the down step (to 0008, dropping the 12 planning tables via
    0009's own downgrade; revision 0010's OWN downgrade() is a documented no-op — see
    that revision) has nothing to lose, and re-upgrading cannot collide on a stale
    ``json_cutovers`` primary key from a first pass that inserted zero rows. Uses its
    own throwaway ``create_engine`` probes for inspection (never the app singleton)
    around the downgrade step, so there is no question of a stale cached engine."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command
    from sqlalchemy import create_engine, inspect

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config
    from tests.db.conftest import reversible_tip

    eng.dispose_engine()
    cfg = make_config(db_url)
    command.upgrade(cfg, reversible_tip(db_url))  # head's chain refuses a downgrade past it

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        # The tip predates revision 202610031200, which renames the table to envelope_groups.
        assert "category_groups" in names and "plan_state" in names
    finally:
        probe.dispose()

    command.downgrade(cfg, "202608080315_money_decimal")

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        assert "category_groups" not in names and "plan_state" not in names
    finally:
        probe.dispose()

    command.upgrade(cfg, "head")  # re-absorbs (still nothing to absorb) — must not raise

    eng.dispose_engine()
    with eng.get_engine().connect() as conn:
        assert pio.export_payload_list(conn, envelope_groups) == []
        assert pio.export_plan_state(conn) == {"scenarios": [], "baseline": None}
    eng.dispose_engine()
