"""REST-facing orchestration for the pluggable budget-engine registry (RFC
``2026-08-22-pluggable-budget-engines.md``, Phases 1-3). Thin glue between
``api/planning.py`` and ``services/budget_engines/``'s own modules — this is the "one
service" each new route calls (``add-endpoint`` convention), not a place for engine
business logic.

The one thing that genuinely lives here rather than inside an engine module: assembling
the paradigm-neutral ``shared`` context (RFC Decision C — ``base``/``rates``/``txns``/
``categories``) once per request. It's engine-INDEPENDENT by construction (every engine's
``fold``/``draft`` may ignore it, like envelope, or need it, like 50/30/20 — see either
module for the two ends of that spectrum), so it doesn't belong inside any one engine.

Phase 2/3 add the write surface: ``set_home_engine`` (Decision A/E — the home-lens
switch, validated + persisted by ``base.set_home_engine_id``) and ``apply_verb`` (Decision
F — the generic ``(engine_id, verb_id, params)`` dispatcher every engine's declared verbs
route through, and the AI-tool surface's `apply_budget_verb` uses verbatim). Both are also
this module's write choke: neither creates a NEW one — see ``apply_verb``'s own docstring
for how it composes with ``planning.apply_budget_change`` and ``BudgetEngineStateStore``,
the two that already exist.
"""

from __future__ import annotations

import dataclasses

from backend.core.errors import ValidationError
from backend.core.fx import base_currency
from backend.db.budget_engine_state_store import BudgetEngineStateStore
from backend.db.budget_lens_intro_store import BudgetLensIntroStore
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning as planning_service
from backend.services import profile as profile_service
from backend.services.budget_engines import (
    base,
    proportional_config,
    proportional_fold,
    proportional_state,
)
from backend.services.budget_engines.draft_signals import build_history
from backend.services.budget_engines.types import ViewModel
from backend.services.cashflow import is_plan_source
from backend.services.fx_cache import load_fx_cache

_state_store = BudgetEngineStateStore()
_intro_store = BudgetLensIntroStore()


def _shared_context() -> dict:
    txns = ledger_service.transactions.all()
    accounts = ledger_service.accounts.all()
    return {
        "base": base_currency(),
        "rates": load_fx_cache(),
        "txns": txns,
        "categories": categories_service.all_category_rows(),
        "envelopes": envelopes_service.all_envelopes(),
        # Flagged (``synced``) as the groups route serves them — 50/30/20 shows the book's
        # groups, and must know which ones the app keeps.
        "groups": planning_service.list_groups(),
        # Same eligibility gate Zero-Based folds envelope activity through
        # (``planning.plan_leg_ok``) — see ``shared_context.resolve_shared``'s own copy of
        # this derivation for why it isn't shared code (mirrors ``planning._build_context``,
        # which also threads ``cash_ids``/``card_pay_env_of`` that this context has no use
        # for).
        "plan_source_ids": frozenset(a["id"] for a in accounts if is_plan_source(a)),
        "cutoff_by_id": {
            aid: cut
            for aid, cut in ledger_service.cutoffs_by_account(accounts, txns).items()
            if cut
        },
    }


def list_engines() -> dict:
    """Every registered engine's descriptor, plus which is home and which is configured
    — ``GET /api/planning/engines``. Unlike the AI-tool surface's own helper
    (``ai/tools/budget_engines.py``), "configured" here checks REAL
    ``budget_engine_state`` presence for every non-``zero_based`` engine, regardless of
    ``kind`` — ``zero_based``'s state lives in its own bespoke stores, so it answers via
    ``base.zero_based_is_configured()`` (has this book ever assigned, or set a target?)
    rather than the state store; every other engine (``proportional``'s needs/wants config —
    a ``derived`` engine may still own real per-book state) is only "configured" once it has
    actually been set up. zero_based used
    to report configured UNCONDITIONALLY, which is why a brand-new book never saw a setup
    screen for it.

    Each engine also carries its own ``verbs`` (``[{id, label, params}, ...]``,
    ``dataclasses.asdict`` over ``BudgetEngine.verbs``) — the frontend's generic setup pane
    (``GenericEnginePane``) discovers a verb id/param name from THIS list rather than
    hardcoding one, so a future engine's setup verb works with no frontend change (mirrors
    the AI-tool surface, which gets the same declared verbs via its tool description
    instead).

    Deliberately NOT on this dict: ``engine.descriptor.model_note`` — that field is
    Lucius-only disambiguation guidance (``ai/tools/budget_engines.py``'s own
    ``list_budget_engines`` tool carries it separately); this is the REST/frontend
    surface, so leaving it off here is what structurally guarantees a frontend consumer
    can never render it. ``intro_dismissed`` is the Plan -> Budget first-run
    introduction's own one-time flag (``BudgetLensIntroStore``) — a single book-level
    bit, not per engine, so it rides alongside ``home_engine_id`` rather than inside
    ``engines``."""
    home = base.home_engine_id()
    configured_state = _state_store.all_for_book()
    engines = [
        {
            "id": engine.descriptor.id,
            "name": engine.descriptor.name,
            "description": engine.descriptor.description,
            "question": engine.descriptor.question,
            "kind": engine.descriptor.kind,
            "render": engine.descriptor.render,
            "is_home": engine_id == home,
            "configured": (
                base.zero_based_is_configured()
                if engine_id == "zero_based"
                else engine_id in configured_state
            ),
            "verbs": [dataclasses.asdict(v) for v in engine.verbs],
        }
        for engine_id, engine in sorted(base.ENGINES.items())
    ]
    return {
        "engines": engines,
        "home_engine_id": home,
        "intro_dismissed": _intro_store.dismissed(),
    }


def dismiss_intro() -> dict:
    """Persist that this book has seen the Plan -> Budget introduction — ``POST
    /api/planning/budget-lens-intro/dismiss``'s one service call. Never reappears once
    dismissed (an introduction, not a recurring interstitial); idempotent."""
    _intro_store.dismiss()
    return {"intro_dismissed": True}


def get_view(engine_id: str | None, period: str) -> ViewModel:
    """The ``ViewModel`` for ``engine_id`` (default: the book's home engine) + ``period``
    — ``GET /api/planning/view``'s one service call. Raises ``NotFoundError`` (→ 404) for
    an unknown ``engine_id``, via ``base.get_engine``."""
    resolved_id = (engine_id or "").strip() or base.home_engine_id()
    engine = base.get_engine(resolved_id)
    own_state = _state_store.get(resolved_id) or {}
    return engine.fold(period, _shared_context(), own_state, None)


def draft_engine(engine_id: str, period: str) -> dict | None:
    """Any engine's own ``draft()`` proposal, spend-history anchored at ``period`` — ``GET
    /api/planning/engines/{engine_id}/draft``'s one service call, and
    ``draft_proportional``'s real implementation below (kept for wire compatibility — there
    was never a second draft path, only a second name for the same one). Raises
    ``NotFoundError`` (-> 404) for an unknown ``engine_id``, via ``base.get_engine`` —
    checked BEFORE ``_shared_context()`` builds anything, so an unknown id never pays for a
    transactions/category read it can't use. Never persists anything (``BudgetEngine.draft``
    is a pure preview) — ``zero_based`` returns ``None`` unconditionally (it already owns
    real configured state), which the caller renders as ``has_proposal: false``, not an
    error."""
    engine = base.get_engine(engine_id)
    shared = _shared_context()
    history = build_history(shared["txns"], shared["base"], shared["rates"], period)
    return engine.draft(shared, history)


def draft_proportional(period: str) -> dict | None:
    """The 50/30/20 draft proposal — ``GET /api/planning/engines/proportional/draft``'s
    one service call, kept for wire compatibility (Phase 1). Now a thin wrapper over the
    generic ``draft_engine`` above."""
    return draft_engine("proportional", period)


# ── Write: home-lens switch (RFC Decision A/E, Phase 2) ─────────────────────────────


def set_home_engine(engine_id: str) -> dict:
    """Switch the book's home budget-engine lens — ``POST /api/planning/home-engine``'s
    one service call, and the ``set_home_budget_engine`` AI tool's only write step. Refuses
    (``ValidationError``) switching onto ``proportional`` before it has ever been configured
    — 50/30/20's onboarding now happens BEFORE promotion (the UI's own "Start budgeting"
    setup screen, mirrored here so a direct API/tool call can't leave the book on an empty,
    all-"unsorted" method with no way back to a sensible default): the recovery hint names
    ``apply_budget_verb``'s ``configure`` verb explicitly. ``zero_based`` is untouched by
    this check — Decision E ("no engine state is migrated") means it is never in this
    unconfigured state for a book that has ever budgeted. Validation + the actual write
    otherwise live in
    ``base.set_home_engine_id`` (raises ``NotFoundError`` naming the real engines for an
    unknown id). Returns the SAME shape ``list_engines()`` does (fresh ``is_home`` flags
    for every engine) so a caller doesn't need a second round trip to redraw the
    switcher."""
    if engine_id == "proportional" and "proportional" not in _state_store.all_for_book():
        raise ValidationError(
            "50/30/20 isn't set up yet, so switching to it now would leave the book on an "
            "empty method. Call apply_budget_operation with operation='configure' (params "
            "may carry group_targets) first, then switch."
        )
    base.set_home_engine_id(engine_id)
    return list_engines()


# ── Write: declared verbs (RFC Decision F, Phase 3) ──────────────────────────────────


def apply_verb(engine_id: str, verb_id: str, params: dict) -> dict:
    """Route ``(engine_id, verb_id, params)`` through the target engine's own
    ``apply_verb`` — ``POST /api/planning/verb``'s one service call, and the
    ``apply_budget_verb`` AI tool's only piece of dispatch logic. Raises
    ``NotFoundError`` for an unknown engine (``base.get_engine``) or an unknown verb
    (``base.get_verb``) — either way naming the real ones.

    Two DIFFERENT persistence shapes meet here, and BOTH are already-established choke
    points — no new one is created (RFC "Data flow, end to end"):

    * ``zero_based``'s own ``apply_verb`` routes STRAIGHT through
      ``planning.apply_budget_change`` (its real, existing write choke) and returns
      that choke's own result. This function does not touch a store for it.
    * Every OTHER engine's ``apply_verb`` is pure validation that returns a proposed
      DELTA — the top-level key(s) it actually changed, Decision E's "no ``BudgetEngine``
      implementation writes a store itself" — which THIS function SHALLOW-MERGES onto the
      engine's own CURRENT stored row (``{**own_state, **delta}``) before persisting via
      ``BudgetEngineStateStore``, the default choke an engine with no bespoke store of its
      own gets. The merge (added 2026-09-25 for ``proportional``'s five-key state — targets/
      mapping/buckets/caps/income_category_ids) is what lets a verb that only ever touches
      ONE of those keys (``set_bucket`` returns ``{"buckets": ...}``, say) leave every other
      key exactly as it was. A verb's own return value is never trusted as the WHOLE state,
      only as what it says it changed.

    The branch on ``engine_id == "zero_based"`` is not incidental special-casing: it is
    the same asymmetry Decision D names outright (zero-based is the one engine with a
    real pre-existing choke point) — an explicit `if`, not a new dataclass flag, because
    there are exactly two engines and Decision B's whole argument against
    over-abstracting the registry applies here too.
    """
    engine = base.get_engine(engine_id)
    base.get_verb(engine, verb_id)
    if engine_id == "zero_based":
        return engine.apply_verb(verb_id, params, {})
    own_state = _state_store.get(engine_id) or {}
    delta = engine.apply_verb(verb_id, params, own_state)
    new_state = {**own_state, **delta}
    return _state_store.set(engine_id, new_state)


# ── Read: the 50/30/20 screen's own REST contract ────────────────────────────────────


def proportional_month(ym: str) -> dict:
    """``GET /api/planning/proportional/month``'s one service call — the exact contract
    the frontend is written against. Shares its ONE computation
    (``proportional_fold.compute_month``) with ``ProportionalEngine.fold()`` (Lucius's/the
    generic renderer's ``get_budget_view``/``GET /api/planning/view`` surface), so the two
    can never silently disagree; ``configured`` reads whether ``configure``/the legacy
    ``set_config`` has ever saved ``targets`` specifically (narrower than
    ``list_engines()``'s own "has ANY saved state" check, which a book that called
    ``set_bucket``/``set_cap`` before ever configuring targets would also satisfy — this
    field drives the onboarding screen, so it means "targets exist", literally)."""
    raw_own_state = _state_store.get("proportional") or {}
    state = proportional_state.normalize_own_state(raw_own_state)
    ctx = _shared_context()
    computed = proportional_fold.compute_month(
        ym,
        ctx,
        state,
        managed_ids=proportional_config.hidden_envelope_ids(),
        profile_income=profile_service.read_profile().get("monthly_net_income"),
        configured=proportional_state.is_configured(raw_own_state),
    )
    computed.pop("_uncategorized_count", None)
    return computed
