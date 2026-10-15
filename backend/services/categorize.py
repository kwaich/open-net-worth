"""
categorize_service — learned per-row category suggestion (competitor gap #1, L1).

A *productized suggestion layer* on top of the existing categorization stack — NOT a
second categorizer. The rules engine (``services/rules.py``) stays the deterministic
default applied inside ``ledger.bulk_create_transactions``; this module handles the
**unruled long tail**: rows the rules engine left uncategorized (``""``/``None``/
``"other"``). For each such row it suggests a category from the user's OWN history,
ranked by a deterministic confidence score.

The signal is the SAME payee-key the corrections + payees code already use
(``corrections._payee_key``) — there is no new normalization and no new payee entity.
Two evidence sources per payee, both already on disk:

  1. **Corrections** (``CorrectionStore.events()``) — every time the user re-categorized
     a row of this payee, the chosen ``to_category`` is a deliberate teaching signal
     (this is the same evidence ``corrections.suggestions()`` aggregates, generalized
     from "propose a rule" to "propose a category for THIS row").
  2. **Already-categorized transactions** of the same payee in the ledger — the user's
     standing choice for that merchant.

Confidence = the modal category's share of that payee's combined evidence, with a small
floor on the evidence count so a single one-off observation never reads as "high".

**Deterministic + compute-only + pure** (inject ``txns``/``corrections``/``rules`` for
tests). NO LLM (categorization quality is a trust primitive — it must be free and
model-independent). NO money math — labels/strings/ids only.

**Reconciliation with the rules engine (no duplication):** a payee already covered by an
ENABLED rule is EXCLUDED — the rule is the deterministic categorizer there, so a learned
suggestion would be redundant (and would fight the rule). This mirrors
``corrections._covered_by_enabled_rule``.

Applying a suggestion REUSES the existing write choke point: the suggestion → the user
confirms (chat propose / Inbox) → ``ledger.bulk_update_transactions`` (the existing
``categorize`` capability). There is NO new write path here — this service is read-only.

Layering (api/ai → services → db → core): imports only ``db`` (CorrectionStore,
TransactionStore, AutomationStore) + sibling ``services`` (corrections, categories,
automations) + core. Never imports ``api``/``ai``. A suggestion's category is also checked
LIVE against the category store: the evidence (corrections' ``to_category`` especially) is
never scrubbed when a category is later deleted or merged, so "the user already saved this"
is not by itself proof the category still exists.

See docs/rfc/competitor-gap-features.md §1.
"""

from __future__ import annotations

import logging
from collections import Counter

from backend.db.transaction_store import txn_text
from backend.services import corrections as corrections_service

logger = logging.getLogger(__name__)

# Default minimum confidence a suggestion must clear to be returned. A payee seen once
# (count 1) yields confidence ~= 0.5 (see _score), below this — so a never-/barely-seen
# payee produces NO fabricated suggestion. ~4 consistent observations → ~0.8.
DEFAULT_MIN_CONFIDENCE = 0.6

# How many uncategorized rows to suggest for in one pass (newest/strongest first). A bound
# so a large historical backlog can't produce an unwieldy response.
DEFAULT_LIMIT = 50

# Kinds that carry a category at all (mirrors the rules-engine / corrections invariant —
# transfers/adjustments never get an expense/income category).
_CATEGORIZABLE_KINDS = {"expense", "income"}

# Category values that mean "the user hasn't categorized this row" — the unruled tail this
# layer targets. The SPLIT sentinel is intentionally NOT here (a split IS categorized).
_UNCATEGORIZED = {"", "other"}


def _is_uncategorized(txn: dict) -> bool:
    """True iff this txn is an expense/income that still needs a category (the long tail)."""
    if txn.get("deleted"):
        return False
    if txn.get("kind") not in _CATEGORIZABLE_KINDS:
        return False
    cat = (txn.get("category") or "").strip().lower()
    return cat in _UNCATEGORIZED


def _score(top_count: int, total: int) -> float:
    """Confidence the modal category is right for this payee.

    ``top_count / total`` is the consistency (how dominant the modal category is), and we
    multiply by an evidence factor ``total / (total + 1)`` so thin evidence is discounted:
    a single perfectly-consistent observation (1/1) scores 0.5, two score ~0.67, four
    score 0.8, ten ~0.91 — converging on the raw consistency as evidence accumulates. This
    keeps a one-off payee below ``DEFAULT_MIN_CONFIDENCE`` while a repeatedly-seen payee
    reads as high-confidence. Pure arithmetic, no money."""
    if total <= 0:
        return 0.0
    consistency = top_count / total
    evidence = total / (total + 1)
    return round(consistency * evidence, 4)


def _evidence_bucket(payee_id: str | None, fallback_key: str) -> str:
    """The grouping key for one piece of evidence (ADR 0010): the payee ENTITY
    (``payee_id``) when known — so every description variant of a payee shares a bucket —
    else the description-derived ``_payee_key`` (entity-less rows keep the legacy
    behavior). Namespaced (``id:`` / ``key:``) so an id can never collide with a
    normalized key."""
    if payee_id:
        return f"id:{payee_id}"
    return f"key:{fallback_key}" if fallback_key else ""


def _live_payee_defaults(txns: list[dict]) -> dict[str, str]:
    """Map ``payee_id → default_category`` for the payees that have an UNCATEGORIZED row
    (the only ones a suggestion can touch). Used to let a pinned default participate in the
    suggestion. Live path only — the pure/injected path passes ``payee_defaults`` in."""
    from backend.services import payee_registry

    pids = {t.get("payee_id") for t in txns if t.get("payee_id") and _is_uncategorized(t)}
    out: dict[str, str] = {}
    for pid in pids:
        default = payee_registry.default_category_for(pid)
        if default:
            out[pid] = default
    return out


def _payee_history(
    txns: list[dict], events: list[dict], payee_defaults: dict[str, str] | None = None
) -> dict[str, Counter]:
    """Build, per payee ENTITY (``payee_id`` when known, else description ``_payee_key``), a
    Counter of observed categories from the user's corrections (``to_category``) and
    already-categorized expense/income txns.

    A correction is the stronger teaching signal but we weight both at 1 (a deliberate edit
    and a standing categorized row are each one observation of "this payee → this category")
    — keeping the model simple and explainable. Only categorizable kinds with a real
    (non-uncategorized) category contribute from the ledger.

    A pinned ``default_category`` (``payee_defaults``) seeds a payee with NO learned
    evidence as a FALLBACK (ADR 0010) — an explicit standing instruction — so real history
    always wins where it exists; weight 2 clears the confidence floor on its own."""
    hist: dict[str, Counter] = {}

    # 1. Corrections: the user explicitly moved this payee to ``to_category``.
    for e in events:
        target = e.get("to_category")
        bucket = _evidence_bucket(e.get("payee_id"), e.get("payee_key") or "")
        if not bucket or not target:
            continue
        hist.setdefault(bucket, Counter())[target] += 1

    # 2. Already-categorized ledger rows for the same payee.
    for t in txns:
        if t.get("deleted") or t.get("kind") not in _CATEGORIZABLE_KINDS:
            continue
        cat = (t.get("category") or "").strip()
        if not cat or cat.lower() in _UNCATEGORIZED or cat == "split":
            continue  # split sentinel is not a real single category
        bucket = _evidence_bucket(t.get("payee_id"), corrections_service._payee_key(txn_text(t)))
        if not bucket:
            continue
        hist.setdefault(bucket, Counter())[cat] += 1

    # 3. Pinned default as a fallback ONLY for an entity with no learned evidence yet.
    for pid, default in (payee_defaults or {}).items():
        if not pid or not default:
            continue
        bucket = f"id:{pid}"
        if bucket not in hist:
            hist[bucket] = Counter({default: 2})

    return hist


def _covered_by_enabled_rule(description: str, kind: str, enabled_rules: list[dict]) -> bool:
    """True iff a transaction Automation already covers this synthetic row.

    Always False since categorization rules became event-triggered Automations
    (2026-08-02): matching is now a Lucius run's own judgement AFTER the row commits, so
    nothing here can answer "would a rule have handled this?" without running the model.
    The cost of being wrong is a redundant SUGGESTION, never a wrong write — so this
    reports "not covered" rather than guessing.
    """
    return False


def _all_category_suggestions(
    *,
    txns: list[dict] | None = None,
    corrections: list[dict] | None = None,
    enabled_rules: list[dict] | None = None,
    payee_defaults: dict[str, str] | None = None,
    category_ids: set[str] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[dict]:
    """The FULL (unpaged) suggestion list — the shared computation behind
    ``suggest_categories``/``count_category_suggestions`` so a reported total can never
    disagree with the page it describes.

    PURE — pass ``txns``/``corrections``/``enabled_rules`` for deterministic tests; omit to
    load live from the stores. Steps:
      1. Build the per-payee category history from both evidence sources.
      2. For each uncategorized row, look up its payee's modal category + confidence.
      3. DROP a row whose payee is already covered by an enabled rule (the rule wins there),
         whose confidence is below ``min_confidence``, whose modal category is no longer a
         LIVE category (deleted/merged away since the evidence was recorded — corrections in
         particular are never scrubbed on delete), or whose payee has no history (a
         never-seen payee yields NO suggestion — empty/young book → []).

    Returns ``[{transaction_id, payee_key, description, suggested_category, confidence,
    evidence_count, kind}]`` sorted by descending confidence (then by description for a
    stable order) — UNCAPPED.
    """
    loaded_live = txns is None
    if txns is None:
        from backend.db.transaction_store import TransactionStore

        txns = TransactionStore().all()
    if corrections is None:
        from backend.db.correction_store import CorrectionStore

        # Every correction carries a payee_id: record_correction stamps it going forward,
        # and a one-time backfill (ADR 0010) stamped the pre-existing rows — so the read
        # path is forward-only, no resolve-at-read shim.
        corrections = CorrectionStore().events()
    if enabled_rules is None:
        enabled_rules = []
    if payee_defaults is None:
        # Build from the live payee store only when we also loaded txns live — keeps the
        # injected (pure) path free of any store access.
        payee_defaults = _live_payee_defaults(txns) if loaded_live else {}
    if category_ids is None and loaded_live:
        # Same rule as payee_defaults above: only a fully-live call checks the live category
        # store — an injected/pure call (tests) stays offline and unfiltered unless the
        # caller explicitly passes category_ids itself.
        from backend.services import categories as categories_service

        category_ids = {row["id"] for row in categories_service.all_category_rows()}

    hist = _payee_history(txns, corrections, payee_defaults)

    out: list[dict] = []
    for t in txns:
        if not _is_uncategorized(t):
            continue
        desc = txn_text(t)
        pkey = corrections_service._payee_key(desc)
        bucket = _evidence_bucket(t.get("payee_id"), pkey)
        if not bucket:
            continue
        counter = hist.get(bucket)
        if not counter:
            continue  # never-seen payee → no fabricated suggestion
        category, top_count = counter.most_common(1)[0]
        if category_ids is not None and category not in category_ids:
            continue  # the evidence points at a category that no longer exists
        total = sum(counter.values())
        confidence = _score(top_count, total)
        if confidence < min_confidence:
            continue
        kind = t.get("kind", "expense")
        if _covered_by_enabled_rule(desc, kind, enabled_rules):
            continue  # the rules engine is the categorizer here — don't duplicate
        out.append(
            {
                "transaction_id": t.get("id", ""),
                "payee_key": pkey,
                "description": desc,
                "suggested_category": category,
                "confidence": confidence,
                "evidence_count": total,
                "kind": kind,
            }
        )

    out.sort(key=lambda s: (-s["confidence"], s["description"]))
    return out


def suggest_categories(
    *,
    txns: list[dict] | None = None,
    corrections: list[dict] | None = None,
    enabled_rules: list[dict] | None = None,
    payee_defaults: dict[str, str] | None = None,
    category_ids: set[str] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> list[dict]:
    """Suggest a category for each UNCATEGORIZED expense/income transaction, drawn from the
    user's own history of that payee (corrections + already-categorized rows). See
    ``_all_category_suggestions`` for the full computation; this just pages it.

    Returns ``[{transaction_id, payee_key, description, suggested_category, confidence,
    evidence_count, kind}]`` sorted by descending confidence (then by description for a
    stable order), sliced to ``[offset : offset + limit]``. ``offset`` pages through further
    suggestions — pair with ``count_category_suggestions`` for the true total. The caller
    groups by ``suggested_category`` and applies the user-confirmed ids through
    ``bulk_update_transactions`` (the existing write path — no new write here).
    """
    out = _all_category_suggestions(
        txns=txns,
        corrections=corrections,
        enabled_rules=enabled_rules,
        payee_defaults=payee_defaults,
        category_ids=category_ids,
        min_confidence=min_confidence,
    )
    start = max(0, offset)
    return out[start : start + max(0, limit)]


def count_category_suggestions(
    *,
    txns: list[dict] | None = None,
    corrections: list[dict] | None = None,
    enabled_rules: list[dict] | None = None,
    payee_defaults: dict[str, str] | None = None,
    category_ids: set[str] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> int:
    """How many suggestions match, ignoring limit/offset — the denominator a capped
    ``suggest_categories`` page must report so it can't be mistaken for the whole feed."""
    return len(
        _all_category_suggestions(
            txns=txns,
            corrections=corrections,
            enabled_rules=enabled_rules,
            payee_defaults=payee_defaults,
            category_ids=category_ids,
            min_confidence=min_confidence,
        )
    )
