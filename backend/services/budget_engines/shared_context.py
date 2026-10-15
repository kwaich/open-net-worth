"""The paradigm-neutral read-only context (RFC ``2026-08-22-pluggable-budget-engines.md``,
Decision C — ``base``/``rates``/``txns``/``categories``/``envelopes``/``groups``) a SELF-SUFFICIENT
engine's ``fold()``/``draft()`` falls back to when the caller didn't pre-build one. Extracted out of
``proportional.py`` so every self-sufficient engine shares one copy of these reads (unlike
``zero_based``, which ignores ``shared`` entirely and reads ``services.planning`` internally
instead).

``ai/tools/budget_engines.py``'s ``get_budget_view``/``draft_budget_engine_config`` call
every engine's ``fold``/``draft`` with ``shared={}``/``history={}`` (a 288-tool-registry
surface that doesn't pay for a real read until an engine needs one);
``services/budget_engines/service.py`` (the REST path) pre-builds a real ``shared`` once
per request instead.
"""

from __future__ import annotations

from backend.core.fx import base_currency as fx_base_currency
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as ledger_service
from backend.services import planning
from backend.services.cashflow import is_plan_source
from backend.services.fx_cache import load_fx_cache


def resolve_shared(shared: dict | None) -> dict:
    """``shared`` as given, with every missing/empty key computed fresh."""
    shared = shared or {}
    txns = shared["txns"] if shared.get("txns") is not None else ledger_service.transactions.all()
    # Lazy, and cached across the two keys below: a fully-formed `shared` (every test's own
    # convention — see module docstring) must short-circuit this read entirely, so it's
    # fetched at most once and ONLY when actually missing either derived key.
    accounts: list[dict] | None = None
    if shared.get("plan_source_ids") is None or shared.get("cutoff_by_id") is None:
        accounts = ledger_service.accounts.all()
    return {
        "base": shared.get("base") or fx_base_currency(),
        "rates": shared["rates"] if shared.get("rates") is not None else load_fx_cache(),
        "txns": txns,
        "categories": (
            shared["categories"]
            if shared.get("categories") is not None
            else categories_service.all_category_rows()
        ),
        # Envelopes are the budget's shared containers, read by every engine. They belong
        # HERE, not read directly inside an engine:
        # a fold must stay a pure function of its context, or it cannot be tested without a
        # database and the caller loses the "build shared once per request" optimisation.
        "envelopes": (
            shared["envelopes"]
            if shared.get("envelopes") is not None
            else envelopes_service.all_envelopes()
        ),
        # Envelope GROUPS joined the context for the same reason (2026-09-25, the
        # envelope-based 50/30/20 redesign): a row's display order/group name is
        # "the Zero-Based table's own order", which only the envelope groups store knows.
        # A READ ONLY list (``list_ordered()``, never ``ensure_others()`` — this module
        # never writes) — an envelope whose ``group_id`` resolves to nothing real (a
        # deleted group, or a book that has never lazily created "Others") reads as an
        # empty group name rather than minting one, self-healing the moment any other
        # read path (``planning.budget_month``, which already calls ``ensure_others()``
        # on every call) does.
        "groups": (
            shared["groups"] if shared.get("groups") is not None else planning.list_groups()
        ),
        # Zero-Based's own account-eligibility gate (``planning.plan_leg_ok``), joined
        # 2026-09-25 so 50/30/20's activity fold (``budget_engines.proportional_fold``) can
        # reuse it verbatim instead of re-deriving its own notion of "on-budget": an
        # off-budget account's legs, and any leg dated before its account's tracking start,
        # must be excluded from BOTH methods' envelope activity identically. Mirrors
        # ``planning._build_context``'s own derivation (kept separate rather than shared
        # code — that function also threads ``cash_ids``/``card_pay_env_of``, which this
        # paradigm-neutral context has no use for).
        "plan_source_ids": (
            shared["plan_source_ids"]
            if shared.get("plan_source_ids") is not None
            else frozenset(a["id"] for a in accounts if is_plan_source(a))
        ),
        "cutoff_by_id": (
            shared["cutoff_by_id"]
            if shared.get("cutoff_by_id") is not None
            else {
                aid: cut
                for aid, cut in ledger_service.cutoffs_by_account(accounts, txns).items()
                if cut
            }
        ),
    }
