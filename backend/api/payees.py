"""
REST API for the payee registry (P4-PAYEE phase 1 + 2).

Routes:
    GET  /api/payees
        First-class payee entities {payees, count} from PayeeStore — the payeeMap +
        autocomplete source (id, name, aliases, default_categories, ...).

    GET  /api/payees/report
        Derived spend report {payees, base_currency, count} — aggregate spend/count per
        normalized payee key, for "who do I spend the most with" / rule-suggestion UIs.
        Split from GET /api/payees (2026-07-06) — the two shapes shared one flag-gated
        route (?registry=1) and a caller that forgot the flag silently got entity-shaped
        rows with no id/name at all. Mirrors the same split already done for the
        get_payees AI tool (commit d56f6543): one route, one shape, no flag.

    PATCH  /api/payees/{payee_id}
        Update name / default_categories / autocategorize / hidden.

    POST   /api/payees/{payee_id}/merge
        Merge payee into another: folds aliases, re-points transactions, deletes source.

    DELETE /api/payees/{payee_id}
        Delete. Optional ?reassign_to=<id> re-points transactions to that payee.

    POST   /api/payees/{payee_id}/aliases
        Add a normalized alias (from raw user text) to a payee.

    DELETE /api/payees/{payee_id}/aliases/{alias}
        Remove a specific normalized alias from a payee.

    POST   /api/payees/{payee_id}/rules
        Add a raw-match renaming rule {match_type, value} to a payee.

    DELETE /api/payees/{payee_id}/rules/{rule_index}
        Remove the rule at the given index.

    PUT    /api/payees/{payee_id}/rules
        Replace all rules on a payee.

    POST   /api/payees/apply-rules
        Re-point existing transactions to match current rules + aliases. Requires confirm=true.
"""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core import audit_context
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import payee_registry
from backend.services import payees as payees_service


async def _ui_audit_scope() -> None:
    """Router-level dependency: every direct REST call to a payee route is a human acting
    through the app UI. Stamp actor=user / surface=ui so the transaction writes these routes
    trigger (merge/apply-rules/apply-category re-point or re-label many rows at once) are
    attributed correctly (transaction-provenance RFC) instead of falling back to the
    scope-absent "system"/"unknown" default. Mirrors ``api/ledger.py``'s dependency of the
    same name — MUST be ``async`` for the same reason (a sync dependency's ContextVar write
    is discarded before the threadpool-run endpoint sees it)."""
    audit_context.set_audit_scope("user", "ui")


router = APIRouter(dependencies=[Depends(_ui_audit_scope)])


# ── Request models ────────────────────────────────────────────────────────────


class CreatePayeeRequest(BaseModel):
    name: str
    default_category: Optional[str] = None


class PayeeUpdate(BaseModel):
    name: Optional[str] = None
    # Per-kind defaults {expense|income|transfer|investment → category id/label}; the
    # service validates + resolves and drops unknown kinds/ids. Replaces the whole map.
    default_categories: Optional[Dict[str, Optional[str]]] = None
    autocategorize: Optional[bool] = None
    hidden: Optional[bool] = None


class MergeRequest(BaseModel):
    target_id: str


class AddAliasRequest(BaseModel):
    text: str  # raw user text; service normalizes it


class AddRuleRequest(BaseModel):
    match_type: str
    value: str


class SetRulesRequest(BaseModel):
    rules: list[dict]


class ApplyRulesRequest(BaseModel):
    confirm: bool = False


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/api/payees")
def create_payee(body: CreatePayeeRequest):
    """Create a new payee entity with the given display name."""
    try:
        return payee_registry.create_payee(
            body.name, default_category=body.default_category or None
        )
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.get("/api/payees")
def get_payees(usage: bool = False):
    """First-class payee entities for the pickers + autocomplete — id, name, aliases,
    default_categories, etc., most-used first. ``usage=true`` adds each payee's live
    ``txn_count``/``last_used`` (the Manage-Payees editor). Rows label themselves via the
    transaction's own ``payee_name``. For the derived spend report, see GET /api/payees/report."""
    rows = payee_registry.list_registry(with_usage=usage)
    return {"payees": rows, "count": len(rows)}


@router.get("/api/payees/report")
def get_payees_report():
    """Derived payee spend report (aggregated live from the ledger) — no stable id, just
    a display name/key + spend stats. For the first-class entities, see GET /api/payees."""
    return payees_service.list_payees()


@router.patch("/api/payees/{payee_id}")
def update_payee(payee_id: str, body: PayeeUpdate):
    """Patch editable fields on a payee entity."""
    changes = body.model_dump(exclude_unset=True)
    try:
        return payee_registry.update_payee(payee_id, changes)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.post("/api/payees/{payee_id}/merge")
def merge_payee(payee_id: str, body: MergeRequest):
    """Merge payee_id (source) into body.target_id. Folds aliases, re-points all
    transactions, deletes source. Returns the updated target entity."""
    try:
        return payee_registry.merge_payees(payee_id, body.target_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.delete("/api/payees/{payee_id}")
def delete_payee(payee_id: str, reassign_to: Optional[str] = None):
    """Delete a payee. Optional ``?reassign_to=<id>`` re-points its transactions to
    that payee instead of clearing them."""
    try:
        payee_registry.delete_payee(payee_id, reassign_to=reassign_to)
        return {"deleted": payee_id}
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.post("/api/payees/{payee_id}/aliases")
def add_alias(payee_id: str, body: AddAliasRequest):
    """Normalize body.text and add it as an alias to this payee."""
    try:
        return payee_registry.add_alias(payee_id, body.text)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.delete("/api/payees/{payee_id}/aliases/{alias}")
def remove_alias(payee_id: str, alias: str):
    """Remove a specific normalized alias from this payee."""
    try:
        return payee_registry.remove_alias(payee_id, alias)
    except NotFoundError as e:
        raise HTTPException(404, str(e))


@router.post("/api/payees/{payee_id}/rules")
def add_rule(payee_id: str, body: AddRuleRequest):
    """Append a raw-match renaming rule to this payee."""
    try:
        return payee_registry.add_payee_rule(payee_id, body.match_type, body.value)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.delete("/api/payees/{payee_id}/rules/{rule_index}")
def remove_rule(payee_id: str, rule_index: int):
    """Remove the renaming rule at position rule_index."""
    try:
        return payee_registry.remove_payee_rule(payee_id, rule_index)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.put("/api/payees/{payee_id}/rules")
def set_rules(payee_id: str, body: SetRulesRequest):
    """Replace all renaming rules on this payee."""
    try:
        return payee_registry.set_payee_rules(payee_id, body.rules)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.post("/api/payees/{payee_id}/apply-category")
def apply_category_to_history(payee_id: str):
    """Stamp the payee's per-kind default categories onto all its non-deleted expense/
    income/transfer/investment transactions. Skips splits and rows that already have
    the category. Returns {updated: N}."""
    try:
        return payee_registry.apply_default_category_to_transactions(payee_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ValidationError as e:
        raise HTTPException(422, str(e))


@router.post("/api/payees/{payee_id}/apply-rules")
def apply_payee_rules_to_existing(payee_id: str):
    """Re-point existing transactions whose description matches this payee's renaming
    rules but are not yet assigned to this payee. Returns {updated: N}."""
    try:
        return payee_registry.apply_payee_rules_to_existing(payee_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e))


@router.post("/api/payees/apply-rules")
def apply_rules_to_history(body: ApplyRulesRequest):
    """Re-point existing transactions to match the current rules + aliases.
    Requires confirm=true. Returns {updated: N}."""
    if not body.confirm:
        raise HTTPException(400, "confirm=true is required to apply rules to existing transactions")
    return payee_registry.apply_payee_rules_to_history()
