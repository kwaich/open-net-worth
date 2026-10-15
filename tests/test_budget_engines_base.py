"""Unit tests for services.budget_engines.base — the registry, NotFoundError recovery,
home_engine_id()'s read-with-default, the Phase 2 home-lens WRITE (set_home_engine_id) and
Decision A's byte-identical-state proof, and the Phase 3 verb dispatch (get_verb,
ZeroBasedEngine.apply_verb routing through apply_budget_change) (RFC
2026-08-22-pluggable-budget-engines, Phases 0-3; the 2026-08-23 zero_based/envelope naming
correction; the 2026-09-25 removal of the strict cash-envelope engine).
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert, select

from backend.core.errors import NotFoundError, ValidationError
from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.budget_engine_state_store import BudgetEngineStateStore
from backend.db.profile_store import ProfileStore
from backend.db.tables import DEFAULT_BOOK_ID, plan_state
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services.budget_engines import base
from backend.services.budget_engines.proportional import PROPORTIONAL_ENGINE
from backend.services.budget_engines.types import Descriptor, Verb
from backend.services.budget_engines.zero_based import ZERO_BASED_ENGINE


@pytest.fixture
def isolated_book(monkeypatch, db_isolated):
    """Same isolation as tests/test_budget_engine_tools.py's `isolated` fixture — DB-backed
    stores redirected to a fresh empty database, a fixed FX cache, SGD base currency, and
    the first-boot migration that mints a real 1:1 envelope per builtin expense category.
    Needed by every test below that actually WRITES through zero_based's own stores
    (apply_budget_change) rather than just reading the registry."""
    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    envelopes_service.ensure_envelopes_migrated()


# ── Registry ───────────────────────────────────────────────────────────────────────


def test_engines_registry_has_both_engines():
    assert base.ENGINES == {
        "zero_based": ZERO_BASED_ENGINE,
        "proportional": PROPORTIONAL_ENGINE,
    }


def test_get_engine_returns_registered_engine():
    assert base.get_engine("zero_based") is ZERO_BASED_ENGINE
    assert base.get_engine("proportional") is PROPORTIONAL_ENGINE


def test_the_removed_cash_envelope_engine_is_unknown():
    """Cash Envelopes (id "envelope") was removed on 2026-09-25. Asking for it is an ordinary
    unknown-engine error that names the real ones — never a silent fallback to another method."""
    with pytest.raises(NotFoundError) as exc_info:
        base.get_engine("envelope")
    message = str(exc_info.value)
    assert "'envelope'" in message
    assert "list_budget_engines" in message


def test_get_engine_unknown_raises_not_found_with_recovery_hint():
    with pytest.raises(NotFoundError) as exc_info:
        base.get_engine("fifty_thirty_twenty")
    message = str(exc_info.value)
    assert "fifty_thirty_twenty" in message
    # recovery_hint always names the tool to call, even with no close match.
    assert "list_budget_engines" in message


def test_every_engine_satisfies_the_protocol():
    for engine_id, engine in base.ENGINES.items():
        assert isinstance(engine, base.BudgetEngine), f"{engine_id} does not satisfy BudgetEngine"
        assert isinstance(engine.descriptor, Descriptor)
        assert engine.descriptor.id == engine_id
        assert engine.descriptor.name
        assert engine.descriptor.question
        assert engine.descriptor.kind in ("derived", "configured")
        assert engine.descriptor.render in ("generic", "custom")
        assert isinstance(engine.verbs, list) and engine.verbs
        for verb in engine.verbs:
            assert isinstance(verb, Verb)
            assert verb.id and verb.label


# ── Descriptor display names / copy (owner, 2026-08-23 naming correction) ──────────────
#
# Envelope budgeting is a KIND of zero-based budgeting in every real-world source (YNAB,
# Ramsey, Actual, NerdWallet, Fidelity). A strict-cash-pot engine once sat beside zero-based
# under the envelope name, and the collision confused both the user and Lucius (asked to set
# up the Envelope lens, Lucius described YNAB's Ready-to-Assign model). That engine is gone
# (2026-09-25); these tests pin the names and copy that remain.


def test_zero_based_display_name_and_description():
    d = ZERO_BASED_ENGINE.descriptor
    assert d.id == "zero_based"  # id unchanged — a separate rename-with-alias job
    assert d.name == "Zero-Based"
    assert d.description == "Give every dollar a job until nothing's left unassigned."


def test_proportional_display_name_and_description():
    """The redesign (2026-09-26) dropped the fixed 50/30/20 needs/wants/savings split for
    a per-GROUP target share of income — the subtitle reflects that, not the old literal
    fractions (which no book is guaranteed to actually use any more)."""
    d = PROPORTIONAL_ENGINE.descriptor
    assert d.name == "50/30/20"
    assert d.description == "A share of income for each group; what's left is savings."


# Model-facing vs. user-facing markers — the exact vocabulary that must NEVER cross into
# `description` (rendered verbatim as body copy by GenericEngineSetup/ProportionalSetup)
# and MUST still exist somewhere in `model_note` (Lucius's own disambiguation guidance,
# never sent to the frontend at all — see services/budget_engines/service.py::list_engines
# docstring for where that split is enforced on the wire).
_MODEL_FACING_MARKERS = (
    "NOTE FOR MODELS",
    "zero_based",
    "envelope_create",
    "move_money",
)


def test_description_is_strictly_user_facing_no_model_facing_markers():
    """Regression test for the incident this whole split exists to prevent: a
    disambiguation paragraph full of internal ids/verb names rendered on screen as a
    setup card's body copy. `description` must read as a plain subtitle — short, no
    engine ids, no verb names, no "NOTE FOR MODELS" — for EVERY engine, forever."""
    for engine in base.ENGINES.values():
        description = engine.descriptor.description
        for marker in _MODEL_FACING_MARKERS:
            assert marker not in description, (
                f"{engine.descriptor.id}: description leaks model-facing text {marker!r}: "
                f"{description!r}"
            )
        assert len(description) <= 80, (
            f"{engine.descriptor.id}: description reads like a paragraph, not a subtitle "
            f"({len(description)} chars): {description!r}"
        )


def test_zero_based_model_note_claims_envelope_budgeting_and_names_no_removed_engine():
    """The substance the split preserves: Lucius must still be told, in SOME field, that
    'envelope budgeting' (popular usage) means zero_based here — the guidance the incident
    showed is load-bearing. Lives in `model_note`, not `description`. And it must not steer
    Lucius toward the removed Cash Envelopes engine, which no longer exists to use."""
    zb_note = ZERO_BASED_ENGINE.descriptor.model_note
    assert zb_note and "envelope budgeting" in zb_note.lower() and "YNAB" in zb_note
    assert "Cash Envelopes" not in zb_note and "'envelope'" not in zb_note


def test_proportional_has_a_model_note_since_the_envelope_based_redesign():
    """50/30/20 never had a naming collision to disambiguate (pre-2026-09-25) — but the
    envelope-based redesign gave it real state Lucius must not confuse with the legacy
    v1 category mapping, so it gained a model_note (never shown to the user verbatim)."""
    assert PROPORTIONAL_ENGINE.descriptor.model_note is not None
    assert "verbatim" in PROPORTIONAL_ENGINE.descriptor.model_note


def test_zero_based_apply_verb_routes_through_apply_budget_change(isolated_book):
    """Phase 3: zero_based's own apply_verb routes STRAIGHT through the EXISTING
    planning.apply_budget_change choke point — no new write choke, per the RFC's "Data
    flow, end to end". Proven by the actual money landing where apply_budget_change
    itself would put it, not just by the call not raising."""
    envelope_id = envelopes_service.env_of()["cat_13095ba70000"]

    result = ZERO_BASED_ENGINE.apply_verb(
        "assign", {"month": "2026-06", "envelope_id": envelope_id, "amount": 500}, {}
    )
    assert result == {
        "ok": True,
        "month": "2026-06",
        "envelope_id": envelope_id,
        "assigned": 500.0,
    }
    table = planning.budget_month("2026-06")
    row = next(
        c for g in table["groups"] for c in g["categories"] if c["envelope_id"] == envelope_id
    )
    assert row["assigned"] == 500.0


def test_zero_based_apply_verb_unknown_type_still_raises_validation_error(isolated_book):
    """A verb_id that reaches this far (bypassing base.get_verb's own naming-the-real-verbs
    check) hits apply_budget_change's own unknown-type guard — never silently no-ops."""
    with pytest.raises(ValidationError):
        ZERO_BASED_ENGINE.apply_verb("not_a_real_verb", {}, {})


def test_zero_based_draft_proposes_monthly_targets_per_envelope():
    """Zero-based used to return None ("already owns real state, nothing to propose"). True
    of a book that already budgets, and exactly wrong for a new one — it left the most
    recognisable method as the only one with no assisted start (owner, 2026-08-23)."""
    shared = {
        "base": "SGD",
        "rates": {"SGD": 1.0},
        "txns": [],
        "categories": [],
        "envelopes": [{"id": "env_rent", "name": "Rent", "category_ids": ["cat_rent"]}],
    }
    proposal = ZERO_BASED_ENGINE.draft(shared, {"cat_rent": [2150.0, 2150.0, 2150.0]})
    assert proposal is not None
    row = proposal["rows"][0]
    assert row["envelope_id"] == "env_rent"
    assert row["suggested_amount"] == 2150.0
    assert proposal["targets"] == [{"envelope_id": "env_rent", "amount": 2150.0}]


def test_zero_based_draft_proposes_a_TARGET_not_an_assignment():
    """Targets say what an envelope NEEDS each month, which is what makes "underfunded" mean
    anything. Assigning the money is the user's own ritual and must never be done for them."""
    shared = {
        "base": "SGD",
        "rates": {"SGD": 1.0},
        "txns": [],
        "categories": [],
        "envelopes": [{"id": "env_food", "name": "Food", "category_ids": ["cat_food"]}],
    }
    proposal = ZERO_BASED_ENGINE.draft(shared, {"cat_food": [400.0, 400.0]})
    assert "targets" in proposal
    assert "assignments" not in proposal and "assigned" not in proposal


def test_zero_based_set_targets_applies_many_and_reports_failures(db_isolated):
    """The setup screen applies a whole book of targets in ONE request. A row that cannot be
    applied is REPORTED, never raised on: dying halfway leaves the book half-configured with
    no way to tell which half."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES
    from backend.services import envelopes as envelopes_service
    from backend.services import planning

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    env = envelopes_service.create_envelope(name="Rent")["id"]

    out = ZERO_BASED_ENGINE.apply_verb(
        "set_targets",
        {
            "targets": [
                {"envelope_id": env, "amount": 2150},
                {"envelope_id": "env_does_not_exist", "amount": 10},
                {"amount": 5},
            ]
        },
        {},
    )
    assert out["applied"] == 1
    assert [f["envelope_id"] for f in out["failed"]] == ["env_does_not_exist", None]
    assert planning.read_category_targets()[env]["amount"] == 2150.0


def test_zero_based_set_targets_zero_clears_rather_than_storing_a_zero_target(db_isolated):
    """set_target expresses "no target" by omitting target_type — storing amount 0 would make
    an envelope look targeted-at-nothing rather than untargeted."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES
    from backend.services import envelopes as envelopes_service
    from backend.services import planning

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    env = envelopes_service.create_envelope(name="Odd Jobs")["id"]

    ZERO_BASED_ENGINE.apply_verb(
        "set_targets", {"targets": [{"envelope_id": env, "amount": 300}]}, {}
    )
    assert planning.read_category_targets().get(env)

    ZERO_BASED_ENGINE.apply_verb(
        "set_targets", {"targets": [{"envelope_id": env, "amount": 0}]}, {}
    )
    assert not (planning.read_category_targets().get(env) or {}).get("target_type")


def test_zero_based_set_targets_rejects_a_non_list():
    with pytest.raises(ValidationError):
        ZERO_BASED_ENGINE.apply_verb("set_targets", {"targets": "nope"}, {})


# ── get_verb() ───────────────────────────────────────────────────────────────────────


def test_get_verb_returns_the_declared_verb():
    verb = base.get_verb(ZERO_BASED_ENGINE, "assign")
    assert verb.id == "assign"
    assert verb.label


def test_get_verb_unknown_raises_not_found_naming_the_real_verbs():
    with pytest.raises(NotFoundError) as exc_info:
        base.get_verb(ZERO_BASED_ENGINE, "asign")
    message = str(exc_info.value)
    assert "asign" in message
    assert "'assign'" in message
    assert "list_budget_engines" in message


# ── set_home_engine_id() — Phase 2 write path ───────────────────────────────────────


def test_set_home_engine_id_validates_unknown_engine(db_isolated):
    # Seeded so the unset default is deterministically "zero_based" — the point of the final
    # assertion is that a REJECTED write left plan_state untouched, not which default applies.
    _seed_an_assignment()
    with pytest.raises(NotFoundError) as exc_info:
        base.set_home_engine_id("fifty_thirty_twenty")
    message = str(exc_info.value)
    assert "fifty_thirty_twenty" in message
    assert "list_budget_engines" in message
    # A rejected write must not have touched plan_state at all.
    assert base.home_engine_id() == "zero_based"


def test_set_home_engine_id_persists_and_reads_back(db_isolated):
    assert base.set_home_engine_id("proportional") == "proportional"
    assert base.home_engine_id() == "proportional"
    assert base.set_home_engine_id("zero_based") == "zero_based"
    assert base.home_engine_id() == "zero_based"


def test_set_home_engine_id_refuses_the_removed_cash_envelope_engine(db_isolated):
    with pytest.raises(NotFoundError):
        base.set_home_engine_id("envelope")


# ── Decision A: switching home never touches another engine's own state ────────────


def test_switching_home_and_back_leaves_every_engines_state_byte_identical(isolated_book):
    """The property Decision A's whole rejection of the "archive the departing engine"
    alternative rests on — proven directly against the raw stores, not asserted."""
    envelope_id = envelopes_service.env_of()["cat_13095ba70000"]
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": envelope_id, "amount": 500}
    )
    BudgetEngineStateStore().set(
        "proportional", {"targets": {"needs": 50, "wants": 30}, "mapping": {}}
    )

    assignments_before = BudgetAssignmentsStore().month_map("2026-06")
    proportional_state_before = BudgetEngineStateStore().get("proportional")

    assert base.home_engine_id() == "zero_based"
    base.set_home_engine_id("proportional")
    assert base.home_engine_id() == "proportional"
    base.set_home_engine_id("zero_based")
    assert base.home_engine_id() == "zero_based"

    assert BudgetAssignmentsStore().month_map("2026-06") == assignments_before
    assert BudgetEngineStateStore().get("proportional") == proportional_state_before


# ── home_engine_id() ─────────────────────────────────────────────────────────────────


def _seed_an_assignment():
    """One budget_assignments row — the marker of a book that already budgets zero-based."""
    from backend.db.budget_assignments_store import BudgetAssignmentsStore

    BudgetAssignmentsStore().set_assigned("2026-08", "cat_13095ba70000", 100.0)


def test_home_engine_defaults_to_zero_based_for_an_existing_budgeter(db_isolated):
    """An EXISTING budgeter with no stored ``home_engine_id`` resolves to their real
    zero-based budget, never a setup screen for some other method."""
    _seed_an_assignment()
    assert base.home_engine_id() == "zero_based"


def test_home_engine_defaults_to_proportional_for_a_book_that_never_budgeted(db_isolated):
    """Owner, 2026-08-23: a book that has NEVER assigned anything starts on 50/30/20 — it is
    derived, so it says something true immediately, where zero-based shows an empty table.
    The pairing with the test above is the whole rule: friendlier default ONLY where there is
    nothing to lose by it."""
    assert base.home_engine_id() == "proportional"


def test_home_engine_defaults_to_zero_based_when_plan_state_row_has_no_budget_key(db_isolated):
    from backend.db.engine import get_engine as get_db_engine

    with get_db_engine().begin() as conn:
        conn.execute(
            insert(plan_state).values(
                book_id=DEFAULT_BOOK_ID, payload={"scenarios": [], "baseline": None}
            )
        )
    _seed_an_assignment()
    assert base.home_engine_id() == "zero_based"


def test_home_engine_id_reads_stored_value(db_isolated):
    """A future Phase-2 write lands plan_state.payload["budget"]["home_engine_id"] — this
    proves the READ side of that contract now, ahead of any writer existing. Read directly
    off the raw table (not PlanStateStore) since this phase never writes through the store."""
    from backend.db.engine import get_engine as get_db_engine

    with get_db_engine().begin() as conn:
        conn.execute(
            insert(plan_state).values(
                book_id=DEFAULT_BOOK_ID,
                payload={
                    "scenarios": [],
                    "baseline": None,
                    "budget": {"home_engine_id": "proportional"},
                },
            )
        )
    assert base.home_engine_id() == "proportional"

    # And the raw row really does carry the budget key untouched — home_engine_id() must
    # never have written anything back (Phase 0 is READ-ONLY on plan_state).
    with get_db_engine().connect() as conn:
        row = conn.execute(
            select(plan_state).where(plan_state.c.book_id == DEFAULT_BOOK_ID)
        ).first()
    assert row.payload["budget"] == {"home_engine_id": "proportional"}


def _store_home(engine_id: str) -> None:
    from backend.db.engine import get_engine as get_db_engine

    with get_db_engine().begin() as conn:
        conn.execute(
            insert(plan_state).values(
                book_id=DEFAULT_BOOK_ID,
                payload={
                    "scenarios": [],
                    "baseline": None,
                    "budget": {"home_engine_id": engine_id},
                },
            )
        )


def test_a_home_on_the_removed_cash_envelope_engine_reads_as_never_chosen(db_isolated):
    """A book whose stored home is "envelope" (Cash Envelopes, removed 2026-09-25) gets the
    default a book that never chose gets — here, a book with no assignments: 50/30/20. Handing
    back the stored id would point the Plan tab at a lens every read 404s on."""
    _store_home("envelope")
    assert base.home_engine_id() == "proportional"


def test_a_home_on_the_removed_engine_keeps_an_existing_budgeter_on_zero_based(db_isolated):
    _store_home("envelope")
    _seed_an_assignment()
    assert base.home_engine_id() == "zero_based"


def test_reading_a_removed_home_never_rewrites_the_stored_value(db_isolated):
    """Read-only on plan_state, as ever: the stale id stops mattering the moment the user picks
    a method, so nothing needs migrating."""
    from backend.db.engine import get_engine as get_db_engine

    _store_home("envelope")
    base.home_engine_id()
    with get_db_engine().connect() as conn:
        row = conn.execute(
            select(plan_state).where(plan_state.c.book_id == DEFAULT_BOOK_ID)
        ).first()
    assert row.payload["budget"] == {"home_engine_id": "envelope"}
