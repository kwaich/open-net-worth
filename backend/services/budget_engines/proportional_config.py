"""50/30/20's declared operations + validation (ADR
``docs/adr/2026-09-25-fifty-thirty-twenty-is-envelope-based-with-caps.md`` and its 2026-09-26
amendment: the method's groups are the book's Zero-Based groups, each with a share of income).
Split out of ``proportional.py`` to keep that file under the ~400-line guidance. This module
owns BOTH validation and the reads it needs to validate against (the live row-envelope set,
the groups, real categories, Zero-Based's own targets).

Every validator here RETURNS ONLY THE KEY(S) IT CHANGED, never the whole state —
``services/budget_engines/service.py``'s ``apply_verb`` does a shallow top-level merge onto
the engine's current stored row before persisting, which is what lets ``caps``/
``group_targets``/``excluded_groups``/``income_category_ids`` all survive a call to a DIFFERENT
operation untouched."""

from __future__ import annotations

from backend.core.errors import ValidationError, recovery_hint
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service
from backend.services.budget_engines import proportional_state as state_
from backend.services.budget_engines.draft_signals import current_period
from backend.services.budget_engines.types import Verb

VERBS: list[Verb] = [
    Verb(
        "configure",
        "Start budgeting by group: the groups are the book's Zero-Based groups, so nothing needs "
        "sorting — optionally set each group's share of income now (set_group_targets later)",
        {
            "group_targets": "{group_id: 0-100}, optional; the total may not exceed 100",
            "excluded_groups": "[group_id, ...], optional; groups to start outside the split",
        },
    ),
    Verb(
        "set_group_targets",
        "Set (or clear) groups' target share of income; what they leave is the savings target",
        {
            "targets": "{group_id: percent 0-100, or null/0 to clear}, required; groups from "
            "list_envelopes (group.id); every group's target together may not exceed 100",
        },
    ),
    Verb(
        "set_group_excluded",
        "Put a group outside the split (its spend doesn't count against income or savings — "
        "e.g. a one-off commitment) or back in",
        {"group_id": "group id, required", "excluded": "true|false, required"},
    ),
    Verb(
        "set_envelope_excluded",
        "Put ONE envelope outside the split (its spend doesn't count against income or savings "
        "— e.g. a pass-through you're paid back for) or back in; it stays in its group",
        {
            "envelope_id": "envelope id (list_envelopes), required",
            "excluded": "true|false, required",
        },
    ),
    Verb(
        "set_cap",
        "Set (or clear) one envelope's monthly spending cap, effective from a month onward",
        {
            "envelope_id": "envelope id (list_envelopes), required",
            "month": "YYYY-MM, required; refused if before the current calendar month",
            "amount": "number >= 0, required; 0 clears the cap from this month on",
        },
    ),
    Verb(
        "set_caps",
        "Set several envelopes' monthly caps for one month at once (bulk form of set_cap)",
        {
            "month": "YYYY-MM, required; refused if before the current calendar month",
            "caps": "{envelope_id: amount}, required, each amount >= 0 (0 clears)",
        },
    ),
    Verb(
        "copy_targets_to_caps",
        "Seed each envelope's cap from its Zero-Based monthly target (the monthly-"
        "equivalent of whatever target type it has); envelopes with no Zero-Based target "
        "are left untouched. Returns how many caps were set",
        {"month": "YYYY-MM, required; refused if before the current calendar month"},
    ),
    Verb(
        "set_income_categories",
        "Choose which income categories count toward this month's income figure",
        {"category_ids": "list of income-category ids, or null for every income category"},
    ),
]


def _split_groups() -> dict[str, dict]:
    """Every group a share can be set on — every one of the book's envelope groups."""
    return {g["id"]: g for g in planning_service.list_groups()}


def _require_group(group_id, groups: dict[str, dict], op: str) -> str:
    if not isinstance(group_id, str) or group_id not in groups:
        raise ValidationError(
            f"{op} references unknown group {group_id!r}."
            + recovery_hint(
                str(group_id),
                (g.get("name", "") for g in groups.values()),
                tool_hint="list_envelopes",
            )
        )
    return group_id


def _validate_group_targets(
    raw, current: dict[str, float], op: str = "set_group_targets"
) -> dict[str, float]:
    """``current`` with ``raw``'s changes applied: a percent sets a group's share, null/0 clears
    it. Refused when a percent is outside 0-100 or every share together would pass 100."""
    if not isinstance(raw, dict):
        raise ValidationError("targets is required: {group_id: percent 0-100, or null to clear}.")
    groups = _split_groups()
    out = dict(current)
    for gid, pct in raw.items():
        _require_group(gid, groups, op)
        if pct in (None, "", 0):
            out.pop(gid, None)
            continue
        try:
            value = float(pct)
        except (TypeError, ValueError):
            raise ValidationError(
                f"The target for {gid!r} must be a number (got {pct!r})."
            ) from None
        if not 0 <= value <= 100:
            raise ValidationError(f"The target for {gid!r} must be 0-100 (got {value:g}).")
        out[gid] = round(value, 6)
    total = round(sum(out.values()), 6)
    if total > 100:
        raise ValidationError(
            f"Group targets can't add up to more than 100% of income (got {total:g}%)."
        )
    return out


def hidden_envelope_ids() -> set[str]:
    """The app-kept envelopes 50/30/20 never lists: a credit card's payment envelope — card
    purchases already count in their own envelopes and paying the card is a transfer, so it
    holds no spending. A commitment's envelope is NOT hidden: its category's spending is real
    spending, so it is a row in a month it spends (owner, 2026-09-28)."""
    from backend.db.account_store import AccountStore

    return {a["payment_envelope_id"] for a in AccountStore().all() if a.get("payment_envelope_id")}


def _row_envelopes() -> dict[str, dict]:
    """Every live envelope 50/30/20 can list, by id — the valid ``envelope_id`` universe for
    every verb below (mirrors ``proportional_state.row_envelopes`` minus the group
    ordering/name annotation, which no validator needs)."""
    hidden = hidden_envelope_ids()
    return {
        e["id"]: e
        for e in envelopes_service.all_envelopes()
        if not e.get("archived") and e["id"] not in hidden
    }


def _require_row_envelope(env_id, rows: dict[str, dict], verb: str) -> str:
    if not isinstance(env_id, str) or env_id not in rows:
        raise ValidationError(
            f"{verb} references unknown or non-row envelope {env_id!r}."
            + recovery_hint(
                str(env_id), (e.get("name", "") for e in rows.values()), tool_hint="list_envelopes"
            )
        )
    return env_id


def _validate_amount(raw) -> float:
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"amount must be a number (got {raw!r}).") from None
    if amount != amount or amount in (float("inf"), float("-inf")):
        raise ValidationError("amount must be a real, finite number.")
    if amount < 0:
        raise ValidationError("amount must be zero or positive — a cap cannot be negative.")
    return round(amount, 2)


# ── configure / set_group_targets / set_group_excluded ───────────────────────────────


def validate_configure(params: dict, current_targets: dict[str, float]) -> dict:
    """``configure`` — marks the method set up; an optional ``group_targets`` sets shares in the
    same call, and an optional ``excluded_groups`` the groups that start outside the split
    (onboarding's own ticks). Nothing is seeded: the groups are the book's own."""
    params = params if isinstance(params, dict) else {}
    raw = params.get("group_targets")
    targets = (
        _validate_group_targets(raw, current_targets, "configure") if raw else dict(current_targets)
    )
    out: dict = {"group_targets": targets}
    if params.get("excluded_groups") is not None:
        raw_out = params["excluded_groups"]
        if not isinstance(raw_out, list):
            raise ValidationError("excluded_groups must be a list of group ids.")
        groups = _split_groups()
        out["excluded_groups"] = list(
            dict.fromkeys(_require_group(g, groups, "configure") for g in raw_out)
        )
    return out


def validate_set_group_targets(params: dict, current_targets: dict[str, float]) -> dict:
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'targets'.")
    return {"group_targets": _validate_group_targets(params.get("targets"), current_targets)}


def validate_set_group_excluded(params: dict, current_excluded: list[str]) -> dict:
    """Stores the WHOLE list from the first toggle on, so the set derived from the old
    per-envelope Excluded choices (``derive_excluded_groups``) is replaced, not lost: the
    caller passes the list the screen shows (``current_excluded``)."""
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'group_id' and 'excluded'.")
    gid = _require_group(params.get("group_id"), _split_groups(), "set_group_excluded")
    excluded = params.get("excluded")
    if not isinstance(excluded, bool):
        raise ValidationError("excluded must be true or false.")
    out = [g for g in current_excluded if g != gid]
    if excluded:
        out.append(gid)
    return {"excluded_groups": out}


def validate_set_envelope_excluded(params: dict, current: list[str]) -> dict:
    """One envelope out of the split, or back in — it keeps its group; only its spend stops
    counting (a pass-through you're paid back for, a one-off paid from savings)."""
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'envelope_id' and 'excluded'.")
    env_id = _require_row_envelope(
        params.get("envelope_id"), _row_envelopes(), "set_envelope_excluded"
    )
    excluded = params.get("excluded")
    if not isinstance(excluded, bool):
        raise ValidationError("excluded must be true or false.")
    out = [e for e in current if e != env_id]
    if excluded:
        out.append(env_id)
    return {"excluded_envelopes": out}


# ── set_cap / set_caps ───────────────────────────────────────────────────────────────


def validate_set_cap(params: dict, current_caps: dict[str, list]) -> dict:
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'envelope_id', 'month', 'amount'.")
    env_id = _require_row_envelope(params.get("envelope_id"), _row_envelopes(), "set_cap")
    month = state_.valid_month(params.get("month"))
    state_.require_not_past_month(month, current_period())
    amount = _validate_amount(params.get("amount"))
    history = state_.normalize_cap_history(current_caps.get(env_id))
    return {"caps": {**current_caps, env_id: state_.splice_cap_history(history, month, amount)}}


def validate_set_caps(params: dict, current_caps: dict[str, list]) -> dict:
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'month' and 'caps'.")
    month = state_.valid_month(params.get("month"))
    state_.require_not_past_month(month, current_period())
    caps_in = params.get("caps")
    if not isinstance(caps_in, dict) or not caps_in:
        raise ValidationError("caps is required: {envelope_id: amount}, at least one entry.")
    rows = _row_envelopes()
    new_caps = dict(current_caps)
    for env_id, raw_amount in caps_in.items():
        _require_row_envelope(env_id, rows, "set_caps")
        amount = _validate_amount(raw_amount)
        history = state_.normalize_cap_history(new_caps.get(env_id))
        new_caps[env_id] = state_.splice_cap_history(history, month, amount)
    return {"caps": new_caps}


# ── copy_targets_to_caps ─────────────────────────────────────────────────────────────


def validate_copy_targets_to_caps(params: dict, current_caps: dict[str, list]) -> dict:
    """Seed each row envelope's cap from its Zero-Based monthly target
    (``planning.read_category_targets()`` — the SAME store ``zero_based.draft()`` reads),
    converted to a monthly-equivalent amount (``proportional_state.target_monthly_equivalent``
    — the same per-type formula ``planning.needed_this_month`` uses, without its
    progress-subtraction: a cap is an independent ceiling, not a remaining-funding gap). An
    envelope with no Zero-Based target, or a snoozed/zero one, is left untouched. Returns
    ``{caps, applied}`` — ``applied`` rides alongside the state delta (harmless: the store's
    payload is opaque JSON, and ``proportional_state.normalize_own_state`` reads only the
    five named keys, so a stray ``applied`` from a past call is simply ignored on read)."""
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'month'.")
    month = state_.valid_month(params.get("month"))
    state_.require_not_past_month(month, current_period())

    zb_targets = planning_service.read_category_targets()
    new_caps = dict(current_caps)
    applied = 0
    for env_id in _row_envelopes():
        equivalent = state_.target_monthly_equivalent(zb_targets.get(env_id), month)
        if equivalent > 0:
            history = state_.normalize_cap_history(new_caps.get(env_id))
            new_caps[env_id] = state_.splice_cap_history(history, month, equivalent)
            applied += 1
    return {"caps": new_caps, "applied": applied}


# ── set_income_categories ────────────────────────────────────────────────────────────


def validate_set_income_categories(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValidationError("params must be an object with 'category_ids'.")
    raw = params.get("category_ids")
    if raw is None:
        return {"income_category_ids": None}
    if not isinstance(raw, list):
        raise ValidationError("category_ids must be a list of category ids, or null.")
    rows = categories_service.all_category_rows()
    resolved: list[str] = []
    for ref in raw:
        cat_id = categories_service.resolve_category_ref(ref, rows=rows)
        if cat_id is None:
            raise ValidationError(
                f"category_ids references unknown category {ref!r}."
                + recovery_hint(
                    str(ref), (r.get("label", "") for r in rows), tool_hint="list_categories"
                )
            )
        if categories_service.kind_for(cat_id) != "income":
            label = next((r.get("label", cat_id) for r in rows if r["id"] == cat_id), cat_id)
            raise ValidationError(
                f"category_ids[{ref!r}] ({label}) is not an income category — only an "
                "income-kind category can be counted toward income."
            )
        if cat_id not in resolved:
            resolved.append(cat_id)
    return {"income_category_ids": resolved}
