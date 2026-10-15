"""REST API for the Transaction Match & Dedup engine's review queue (RFC transaction-dedup-
engine §API surface). Thin: validate → one service call → shape response.

Routes:
    GET  /api/matches/reviews                       Pending review items with superseded_count.
    POST /api/matches/merge                         Merge a pair directly.
    POST /api/matches/reviews/{id}/accept           Accept a queued review (merge the pair).
    POST /api/matches/reviews/{id}/dismiss          Keep both + remember the pair as different.
    POST /api/matches/reviews/{id}/swap-canonical   Swap canonical to a tied alternative (F20).
    POST /api/matches/undo                          Reverse a merge: {absorbed_id}.
    GET  /api/matches/decisions                     List remembered decisions (F04b).
    DELETE /api/matches/decisions/{key}             Forget a decision (F04b).
    GET  /api/matches/merges/recent                 Most recent merge-journal entries (UX-3).
    GET  /api/matches/config                        The current dedup_policy + L3 config.
    PUT  /api/matches/config                        Set dedup_policy / l3_* fields.
    GET  /api/matches/policy                        The current dedup_policy (legacy).
    PUT  /api/matches/policy                        Set dedup_policy (legacy).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.import_batch_store import ImportBatchStore
from backend.services import ledger as ledger_service
from backend.services import match_review, rejected_identities
from backend.services import profile as profile_service

router = APIRouter()


class MergeRequest(BaseModel):
    canonical_id: str
    absorbed_id: str
    become_transfer: bool = False
    reason: Optional[str] = None


class UndoRequest(BaseModel):
    absorbed_id: str


class PolicyRequest(BaseModel):
    policy: str


class ConfigRequest(BaseModel):
    # All optional — a partial patch. policy lives in profile; the l3_* fields in DedupConfigStore.
    policy: Optional[str] = None
    l3_enabled: Optional[bool] = None
    l3_model: Optional[str] = None
    l3_max_per_run: Optional[int] = None


class SwapCanonicalRequest(BaseModel):
    canonical_id: str


@router.get("/api/matches/reviews")
def get_reviews(batch_id: Optional[str] = None):
    items, superseded_count = match_review.list_reviews(batch_id)

    # UX-5: enrich each review with the human-readable batch label so the UI can
    # replace raw UUID group headers with the import description + date.
    # One store read per unique batch_id (cache in a dict), not per review.
    batch_cache: dict[str, dict] = {}
    batch_store = ImportBatchStore()
    for it in items:
        bid = it.get("batch_id")
        if bid and bid not in batch_cache:
            batch_cache[bid] = batch_store.find_by_id(bid) or {}
        if bid:
            b = batch_cache[bid]
            it["batch_description"] = b.get("description") or None
            it["batch_created_at"] = b.get("created_at") or None
        else:
            it["batch_description"] = None
            it["batch_created_at"] = None

    # Group by batch so the UI can resolve a messy import in one decision.
    groups: dict[str, list] = {}
    for it in items:
        groups.setdefault(it.get("batch_id") or "_ungrouped", []).append(it)
    return {
        "reviews": items,
        "groups": groups,
        "count": len(items),
        "superseded_count": superseded_count,
    }


@router.post("/api/matches/merge")
def merge(req: MergeRequest):
    try:
        merged, info = match_review.merge_transactions_preserving_fields(
            req.canonical_id,
            req.absorbed_id,
            reason=req.reason or "manual merge",
            become_transfer=req.become_transfer,
        )
        return {"ok": True, "canonical": merged, **info}
    except ValidationError as e:
        raise HTTPException(422, str(e))
    except ConflictError as e:
        raise HTTPException(409, str(e))


@router.post("/api/matches/reviews/{review_id}/accept")
def accept(review_id: str):
    try:
        merged, info = match_review.accept_review(review_id)
        return {"ok": True, "canonical": merged, **info}
    except ValidationError as e:
        raise HTTPException(422, str(e))
    except NotFoundError as e:
        raise HTTPException(404, str(e))
    except ConflictError as e:
        raise HTTPException(409, str(e))


@router.post("/api/matches/reviews/{review_id}/dismiss")
def dismiss(review_id: str):
    try:
        match_review.dismiss_review(review_id)
    except ValidationError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@router.post("/api/matches/reviews/{review_id}/swap-canonical")
def swap_canonical(review_id: str, req: SwapCanonicalRequest):
    """Swap the canonical of a review to a tied alternative (F20).
    Returns the updated enriched review item."""
    try:
        updated = match_review.swap_review_canonical(review_id, req.canonical_id)
        return {"ok": True, "review": updated}
    except ValidationError as e:
        raise HTTPException(422, str(e))
    except NotFoundError as e:
        raise HTTPException(404, str(e))


@router.post("/api/matches/undo")
def undo(req: UndoRequest):
    restored = ledger_service.unmerge_transactions(req.absorbed_id)
    if restored is None:
        raise HTTPException(404, "No merge recorded for that transaction")
    return {"ok": True, "canonical": restored}


@router.post("/api/matches/judge")
async def judge(batch_id: Optional[str] = None):
    """Run the LLM (L3) judgment pass over the pending review queue: auto-merge agreed matches
    per dedup_policy, keep disagreements for the human. Degrades to a no-op (all kept) when the
    LLM is unavailable."""
    return await match_review.judge_reviews_async(batch_id)


# ── Decisions management (F04b) ────────────────────────────────────────────────


@router.get("/api/matches/decisions")
def get_decisions(limit: int = 50):
    """List the most recent remembered decisions (verdict cache) for the settings UI.
    Capped at 50 by default; payee_label and amount_bucket are display derivations."""
    cap = min(max(1, limit), 50)
    return match_review.list_decisions(cap)


@router.delete("/api/matches/decisions/{pair_key_or_signature}")
def forget_decision(pair_key_or_signature: str):
    """Forget a decision so the engine re-evaluates that pair on next import."""
    ok = match_review.forget_decision(pair_key_or_signature)
    if not ok:
        raise HTTPException(404, "Decision not found")
    return {"ok": True}


# ── Rejected identities (ADR 2026-09-02-a-deleted-identity-is-remembered) ─────


@router.get("/api/matches/rejected")
def get_rejected(limit: int = 50, offset: int = 0):
    """Deleted transactions whose source refs are remembered — a re-delivery of any of these is
    refused at the write chokes. Most recent first."""
    cap = min(max(1, limit), 200)
    return rejected_identities.list_rejected(cap, max(0, offset))


@router.delete("/api/matches/rejected/{rejection_id}")
def forget_rejected(rejection_id: str):
    """Forget a deletion so the same source event books normally next time."""
    if not rejected_identities.forget(rejection_id):
        raise HTTPException(404, "Rejected identity not found")
    return {"ok": True}


# ── Recent merges (UX-3) ──────────────────────────────────────────────────────


@router.get("/api/matches/merges/recent")
def get_recent_merges(days: int = 7, limit: int = 20):
    """UX-3: most recent merge-journal entries for the 'Recently auto-merged' UI section.

    days:  look-back window in calendar days (1–90). Default 7.
    limit: maximum rows returned (1–100). Default 20.
    """
    if not 1 <= days <= 90:
        raise HTTPException(422, "days must be between 1 and 90")
    if not 1 <= limit <= 100:
        raise HTTPException(422, "limit must be between 1 and 100")
    merges = match_review.recent_merges(days=days, limit=limit)
    return {"merges": merges, "count": len(merges)}


# ── Config ─────────────────────────────────────────────────────────────────────


@router.get("/api/matches/config")
def get_config():
    """The full dedup-engine settings panel: the auto-merge ``policy`` (from profile) + the L3
    LLM layer config (kill switch / model / cost cap, from DedupConfigStore). The available model
    list for the model dropdown comes from the existing GET /api/models."""
    from backend.db.dedup_config_store import DedupConfigStore

    cfg = DedupConfigStore().get()
    policy = profile_service.normalize_dedup_policy(
        profile_service.read_profile().get("dedup_policy")
    )
    return {
        "policy": policy,
        "policy_options": list(profile_service.DEDUP_POLICIES),
        **cfg,
    }


@router.put("/api/matches/config")
def set_config(req: ConfigRequest):
    """Update any subset of the dedup-engine settings. ``policy`` → profile; ``l3_*`` → store."""
    from backend.db.dedup_config_store import DedupConfigStore

    if req.l3_max_per_run is not None and req.l3_max_per_run < 0:
        raise HTTPException(422, "l3_max_per_run must be 0 (unlimited) or a positive integer")
    if req.policy is not None:
        profile = profile_service.read_profile()
        profile["dedup_policy"] = profile_service.normalize_dedup_policy(req.policy)
        profile_service.write_profile(profile)
    l3_changes = {
        k: v
        for k, v in {
            "l3_enabled": req.l3_enabled,
            "l3_model": req.l3_model,
            "l3_max_per_run": req.l3_max_per_run,
        }.items()
        if v is not None
    }
    if l3_changes:
        DedupConfigStore().update(l3_changes)
    return get_config()


@router.get("/api/matches/policy")
def get_policy():
    policy = profile_service.normalize_dedup_policy(
        profile_service.read_profile().get("dedup_policy")
    )
    return {"policy": policy, "options": list(profile_service.DEDUP_POLICIES)}


@router.put("/api/matches/policy")
def set_policy(req: PolicyRequest):
    policy = profile_service.normalize_dedup_policy(req.policy)
    profile = profile_service.read_profile()
    profile["dedup_policy"] = policy
    profile_service.write_profile(profile)
    return {"policy": policy}
