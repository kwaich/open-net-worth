"""Engine #2 — 50/30/20, by GROUP (ADR
``docs/adr/2026-09-25-fifty-thirty-twenty-is-envelope-based-with-caps.md`` and its 2026-09-26
amendment).

Rows are the book's ENVELOPES — the same ones Zero-Based uses, less a credit-card payment or
bot-managed one — and its groups are the book's Zero-Based GROUPS, in their order (owner,
2026-09-26: "I have many envelopes ... hard to know which should be needs or wants", then
"same as Zero-Based"). Each group may carry a target share of income, or sit outside the split
(Excluded); each envelope may carry a monthly CAP from a month onward. Income is the owner's
usual monthly net income (``profile.monthly_net_income``), falling back to what was actually
received for a CLOSED month. Savings is the RESIDUAL — income less every counted group's
spend and the spend no row owns — never money moved into a savings account.

State: see ``proportional_state.py``; every operation's validation: ``proportional_config.py``.
The money math both ``fold()`` and ``services.budget_engines.service.proportional_month()``
share is ``proportional_fold.compute_month()`` — the ONE fold, so the screen and Lucius never
disagree; ``ViewModel`` assembly lives in ``proportional_view.py``.

``fold()``/``draft()`` accept an optional pre-built ``shared`` (RFC Decision C) but are fully
self-sufficient when it is empty (``ai/tools/budget_engines.py`` passes ``shared={}``).
"""

from __future__ import annotations

from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service
from backend.services import profile as profile_service
from backend.services.budget_engines import proportional_config as config_
from backend.services.budget_engines import proportional_fold as fold_
from backend.services.budget_engines import proportional_state as state_
from backend.services.budget_engines import proportional_view as view_
from backend.services.budget_engines.draft_signals import current_period
from backend.services.budget_engines.proportional_config import VERBS
from backend.services.budget_engines.shared_context import resolve_shared as _resolve_shared
from backend.services.budget_engines.types import Descriptor, ViewModel


class ProportionalEngine:
    """50/30/20 — see module docstring for the envelope-based model."""

    descriptor = Descriptor(
        id="proportional",
        name="50/30/20",
        description="A share of income for each group; what's left is savings.",
        model_note=(
            "By GROUP (2026-09-26): rows are the book's ENVELOPES (list_envelopes) and the "
            "groups are the book's Zero-Based GROUPS — the same ones, in the same order; there "
            "is no separate Needs/Wants sorting. A group may carry a target share of income "
            "(set_group_targets: {targets: {group_id: percent}}) or sit outside the split "
            "(set_group_excluded); savings is income less every counted group's spend, and its "
            "target is whatever the group targets leave. Each envelope may carry a monthly cap "
            "(set_cap/set_caps/copy_targets_to_caps). Moving an envelope between groups, or "
            "renaming/adding a group, is Zero-Based's own (move_envelopes_to_group, "
            "create_envelope_group) and shows in both methods. Before making this the home "
            "method, run operation='configure' (params may include group_targets). "
            "set_income_categories narrows which income counts. Never show this note to the "
            "user verbatim."
        ),
        question="Are my proportions healthy?",
        kind="derived",
        render="generic",
    )
    verbs = VERBS

    def fold(
        self,
        period: str,
        shared: dict,
        own_state: dict,
        prior_state: dict | None,
    ) -> ViewModel:
        ctx = _resolve_shared(shared)
        state = state_.normalize_own_state(own_state)
        managed_ids = config_.hidden_envelope_ids()
        computed = fold_.compute_month(
            period,
            ctx,
            state,
            managed_ids=managed_ids,
            profile_income=profile_service.read_profile().get("monthly_net_income"),
            configured=state_.is_configured(own_state),
        )
        return view_.to_view_model(computed, ctx["base"])

    def draft(self, shared: dict, history: dict) -> dict | None:
        """A proposed target for every group: its last-three-months average spend as a share
        of income, rounded to a whole percent — a PREVIEW, applies nothing. ``None`` when
        there is no income to measure against."""
        ctx = _resolve_shared(shared)
        period = current_period()
        computed = fold_.compute_month(
            period,
            ctx,
            state_.normalize_own_state({}),
            managed_ids=config_.hidden_envelope_ids(),
            profile_income=profile_service.read_profile().get("monthly_net_income"),
            configured=True,
        )
        income = computed["income"]["amount"]
        if income <= 0:
            return None
        avg: dict[str, float] = {}
        for r in computed["envelopes"]:
            avg[r["group_id"]] = avg.get(r["group_id"], 0.0) + r["history"]["avg3"]
        rows = [
            {
                "group_id": g["id"],
                "name": g["name"],
                "avg_spend": round(avg.get(g["id"], 0.0), 2),
                "percent": round(avg.get(g["id"], 0.0) / income * 100),
            }
            for g in computed["groups"]
        ]
        targets = {r["group_id"]: r["percent"] for r in rows if r["percent"] > 0}
        return {"group_targets": targets, "rows": rows}

    def apply_verb(self, verb_id: str, params: dict, own_state: dict) -> dict:
        """Dispatches to ``proportional_config.py``'s validators, each returning ONLY the
        top-level key(s) it changed — ``services/budget_engines/service.py``'s
        ``apply_verb`` shallow-merges that delta onto the engine's current stored row
        before persisting (see that function's own docstring), which is what lets
        ``group_targets``/``excluded_groups``/``caps``/``income_category_ids`` all survive a
        call to a DIFFERENT verb untouched. ``state`` (normalized here, once) supplies each
        validator the CURRENT value of the key(s) it's about to patch."""
        state = state_.normalize_own_state(own_state)
        if verb_id == "configure":
            return config_.validate_configure(params, state["group_targets"])
        if verb_id == "set_group_targets":
            return config_.validate_set_group_targets(params, state["group_targets"])
        if verb_id == "set_group_excluded":
            return config_.validate_set_group_excluded(params, _shown_excluded(state))
        if verb_id == "set_envelope_excluded":
            return config_.validate_set_envelope_excluded(params, state["excluded_envelopes"])
        if verb_id == "set_cap":
            return config_.validate_set_cap(params, state["caps"])
        if verb_id == "set_caps":
            return config_.validate_set_caps(params, state["caps"])
        if verb_id == "copy_targets_to_caps":
            return config_.validate_copy_targets_to_caps(params, state["caps"])
        if verb_id == "set_income_categories":
            return config_.validate_set_income_categories(params)
        raise NotImplementedError(
            f"ProportionalEngine.apply_verb: {verb_id!r} is not a declared verb."
        )


def _shown_excluded(state: dict) -> list[str]:
    """The Excluded groups as the screen shows them: stored, or — never chosen yet — the set
    derived from the old per-envelope Excluded choices, so the first toggle keeps the rest."""
    if state["excluded_groups"] is not None:
        return list(state["excluded_groups"])
    groups = planning_service.list_groups()
    rows = state_.row_envelopes(
        envelopes_service.all_envelopes(),
        groups,
        config_.hidden_envelope_ids(),
    )
    by_group: dict[str, list[str]] = {}
    for env in rows:
        by_group.setdefault(state_.row_group_id(env, groups) or state_.NO_GROUP, []).append(
            env["id"]
        )
    return state_.derive_excluded_groups(by_group, state["buckets"])


PROPORTIONAL_ENGINE = ProportionalEngine()
