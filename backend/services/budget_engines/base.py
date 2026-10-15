"""The paradigm-neutral ``BudgetEngine`` contract (RFC
``2026-08-22-pluggable-budget-engines.md``, Decisions B/C). One engine == one module in this
package, registered explicitly in ``ENGINES`` below — NOT decorator auto-collected like the
288-member ``@tool`` registry. That pattern earns its keep at 288 members, not at 3: an
explicit dict stays reviewable in one glance, and a future P4 plugin loader can register into
this same dict without changing its shape (Decision B).

Two engines are registered: ``zero_based`` (``zero_based.py`` — Phase 0's original member)
and ``proportional`` (``proportional.py`` — 50/30/20). A third, the strict cash-envelope
method (id ``envelope``), was removed on 2026-09-25 — ADR
``2026-09-25-cash-envelopes-engine-is-removed``; ``home_engine_id`` reads a book still
pointing at it as one that never chose. Every engine is equivalence- or unit-tested before
anything else depends on it — see ``tests/test_budget_engine_equivalence.py`` (the gate for
``zero_based``) and ``tests/test_budget_engine_proportional.py``.

Phase 2 (home-lens switch) and Phase 3 (write verbs) add two module-level functions beside
the Phase 0/1 registry helpers: ``set_home_engine_id`` (validates against ``ENGINES``, then
persists via ``PlanStateStore``'s own locked read-modify-write — see that method's
docstring for the checkpoint/Decision-A contract) and ``get_verb`` (resolves a declared
``Verb`` off an engine, or ``NotFoundError`` naming the real ones — the ``get_engine`` of
verb lookup). Both are consumed by ``services/budget_engines/service.py``, the REST/AI
orchestration layer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import select

from backend.core.errors import NotFoundError, recovery_hint
from backend.db.engine import get_engine as _get_db_engine
from backend.db.plan_state_store import PlanStateStore
from backend.db.tables import DEFAULT_BOOK_ID
from backend.db.tables import budget_assignments as _budget_assignments_table
from backend.db.tables import plan_state as _plan_state_table
from backend.services import planning
from backend.services.budget_engines.proportional import PROPORTIONAL_ENGINE
from backend.services.budget_engines.types import (
    Descriptor,
    Group,
    Headline,
    Health,
    Measure,
    Row,
    Unmapped,
    Verb,
    ViewModel,
)
from backend.services.budget_engines.zero_based import ZERO_BASED_ENGINE

__all__ = [
    "BudgetEngine",
    "ENGINES",
    "get_engine",
    "get_verb",
    "home_engine_id",
    "set_home_engine_id",
    "Descriptor",
    "Group",
    "Headline",
    "Health",
    "Measure",
    "Row",
    "Unmapped",
    "Verb",
    "ViewModel",
]

_plan_state_store = PlanStateStore()


@runtime_checkable
class BudgetEngine(Protocol):
    """The contract every budget paradigm implements — Thread B's own six members
    (``docs/design/design-notes.md:50-79``), minus the state SCHEMA (each engine defines its
    own, there is nothing generic to protocol-ize about it).

    ``descriptor``/``verbs`` are static data (class- or instance-level); ``fold``/``draft``/
    ``apply_verb`` are the three behaviors. ``@runtime_checkable`` so a registry-membership
    test can assert conformance with a plain ``isinstance`` check, without every engine
    inheriting from this class."""

    descriptor: Descriptor
    verbs: list[Verb]

    def fold(
        self,
        period: str,
        shared: dict,
        own_state: dict,
        prior_state: dict | None,
    ) -> ViewModel:
        """Compute this engine's ``ViewModel`` for ``period`` (``"YYYY-MM"``). ``shared`` is
        the paradigm-neutral read-only context (categories, transactions, base currency, FX
        — never another engine's state); ``own_state``/``prior_state`` are THIS engine's own
        persisted state for this period / the period immediately before it (for an engine
        that needs a carry, e.g. a rollover cap). Never does its own FX — every amount
        arrives already base-currency (CLAUDE.md: "Engines never do their own FX")."""
        ...

    def draft(self, shared: dict, history: dict) -> dict | None:
        """Propose an initial ``own_state`` from shared data + spending history (e.g.
        50/30/20's needs/wants category mapping, drafted from category labels since nothing
        in the data model carries that signal). Returns ``None`` when the engine has nothing
        to propose (already configured, or needs no draft at all). Never writes anything —
        purely a preview the caller may choose to apply."""
        ...

    def apply_verb(self, verb_id: str, params: dict, own_state: dict) -> dict:
        """Execute one of this engine's declared ``verbs`` against its own state. A Phase 0
        engine may leave this raising ``NotImplementedError`` — the write path lands in
        Phase 3, routed through the existing ``planning.apply_budget_change`` choke point
        (RFC "Data flow, end to end": no new write choke is created)."""
        ...


# Explicit registry (see module docstring for why this isn't decorator-collected). A future
# engine (e.g. caps) lands later as its own module adds one line here.
ENGINES: dict[str, BudgetEngine] = {
    "zero_based": ZERO_BASED_ENGINE,
    "proportional": PROPORTIONAL_ENGINE,
}


def get_engine(engine_id: str) -> BudgetEngine:
    """The registered engine for ``engine_id``, or a ``NotFoundError`` naming the real ones
    (the same "recoverable in one round trip" contract every other unknown-ref error in this
    codebase follows — ``core.errors.recovery_hint``)."""
    engine = ENGINES.get(engine_id)
    if engine is None:
        raise NotFoundError(
            f"Unknown budget engine {engine_id!r}."
            + recovery_hint(engine_id, ENGINES.keys(), tool_hint="list_budget_engines")
        )
    return engine


def home_engine_id() -> str:
    """The book's designated home lens — ``plan_state.payload["budget"]["home_engine_id"]``,
    falling back to ``_unset_home_engine_id`` when nothing is stored (RFC Decision E).

    A stored id that no longer names a registered engine reads as UNSET too. That is not
    hypothetical: ``"envelope"`` (the strict cash-envelope method) was a valid home until it
    was removed on 2026-09-25, and returning it as-is would point the Plan tab at a lens that
    resolves nothing — every read would 404 on ``get_engine``. Such a book gets exactly the
    default a book that never chose gets; nothing is written back, so the stale value simply
    stops mattering the moment the user picks a method.

    STILL READS THE RAW ROW directly rather than through ``PlanStateStore.get_state()`` —
    unchanged from Phase 0/1, now for a narrower reason: ``_ensure_state`` (fixed in Phase 2
    to carry a ``budget`` key through rather than drop it — see that function's own
    docstring) would work fine here too, but this call site has never needed any of
    ``PlanStateStore``'s scenario/baseline machinery, only the one nested value, so pulling
    in the whole store buys nothing a raw ``SELECT`` doesn't already give it."""
    with _get_db_engine().connect() as conn:
        row = conn.execute(
            select(_plan_state_table.c.payload).where(
                _plan_state_table.c.book_id == DEFAULT_BOOK_ID
            )
        ).first()
    payload = row.payload if row and isinstance(row.payload, dict) else {}
    budget_cfg = payload.get("budget")
    if isinstance(budget_cfg, dict):
        candidate = budget_cfg.get("home_engine_id")
        if isinstance(candidate, str) and candidate in ENGINES:
            return candidate
    return _unset_home_engine_id()


def zero_based_is_configured() -> bool:
    """Has this book actually STARTED zero-based budgeting?

    Was hardcoded True on the reasoning that "zero_based's bespoke stores are never empty for
    an existing book" — true, and it quietly assumed every book is an existing one. A brand-new
    book therefore reported itself configured and got no setup screen at all, which is why the
    most recognisable method was the only one that dropped a first-time user into an empty
    table (owner, 2026-08-23).

    Either signal counts. An assignment is the ritual itself; a target is the plan the ritual
    fills. Someone who has set targets and not yet assigned has unambiguously started, and must
    not be shown the setup screen a second time.
    """
    if planning.read_category_targets():
        return True
    with _get_db_engine().connect() as conn:
        row = conn.execute(
            select(_budget_assignments_table.c.id)
            .where(_budget_assignments_table.c.book_id == DEFAULT_BOOK_ID)
            .limit(1)
        ).first()
    return row is not None


def _unset_home_engine_id() -> str:
    """The home lens for a book that has NEVER chosen one (owner, 2026-08-23).

    An EXISTING budgeter keeps ``zero_based``. Their envelopes, assignments and Ready-to-Assign
    are zero-based facts; opening them on an unconfigured lens would hide their real budget
    behind a setup screen — a silent, data-visible switch, and the reason this function is
    regression-tested at all.

    A book that has never assigned anything gets ``proportional``. 50/30/20 is DERIVED: it can
    say something true about a fresh book's spending immediately, whereas zero-based shows an
    empty table until the user assigns money. So the friendlier default is only friendlier when
    there is nothing to lose by it.

    "Has ever assigned" (any ``budget_assignments`` row, any month) is deliberately the test,
    not "has assignments THIS month" — a returning user who skipped a month must not be treated
    as new and have their budget swapped out from under them."""
    with _get_db_engine().connect() as conn:
        row = conn.execute(
            select(_budget_assignments_table.c.id)
            .where(_budget_assignments_table.c.book_id == DEFAULT_BOOK_ID)
            .limit(1)
        ).first()
    return "zero_based" if row is not None else "proportional"


def set_home_engine_id(engine_id: str) -> str:
    """Switch the book's home budget-engine lens (RFC Decision A/E, Phase 2) —
    ``services/budget_engines/service.py``'s ``set_home_engine`` and the
    ``set_home_budget_engine`` AI tool both call this as their one validation+write step.

    Validates ``engine_id`` against the real ``ENGINES`` registry FIRST (via ``get_engine``,
    which raises ``NotFoundError`` naming the real ones) — writing an unknown id would brick
    the Plan tab onto a lens that resolves nothing. Only then persists, via
    ``PlanStateStore.set_home_engine_id``'s own locked read-modify-write (checkpoint-covered,
    ``/rewind``-able).

    Touches EXACTLY one key. Decision A is not a promise kept elsewhere — it is what NOT
    reading or writing anything else here structurally guarantees: no other engine's own
    state (``budget_engine_state`` rows, ``budget_assignments``, ``envelopes``, ...) is ever
    touched by a home-lens switch, so switching away and back is lossless by construction,
    not by care taken to preserve it."""
    get_engine(engine_id)
    _plan_state_store.set_home_engine_id(engine_id)
    return engine_id


def get_verb(engine: BudgetEngine, verb_id: str) -> Verb:
    """This engine's own declared ``Verb`` for ``verb_id`` — the verb-level twin of
    ``get_engine``: RAISES ``NotFoundError`` naming the engine's real verbs (never a silent
    fallback, never a bare ``KeyError``) when ``verb_id`` isn't one of them. The generic
    ``apply_budget_operation`` write surface (RFC Decision F, Phase 3) calls this BEFORE ever
    invoking ``engine.apply_verb``, so an unknown verb is always recoverable in one round
    trip regardless of which engine it was aimed at."""
    for verb in engine.verbs:
        if verb.id == verb_id:
            return verb
    raise NotFoundError(
        f"Unknown operation {verb_id!r} for budget engine {engine.descriptor.id!r}."
        + recovery_hint(verb_id, (v.id for v in engine.verbs), tool_hint="list_budget_engines")
    )
