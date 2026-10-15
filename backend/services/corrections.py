"""
corrections_service — learn-from-corrections → auto-propose categorization rules (P2-SP2).

When a user re-categorizes the *same payee* over and over, they are doing work the
rules engine (SP1) was built to eliminate. This service closes that loop: it records
each category correction (at the single edit choke point) and, on read, groups recent
corrections by normalized payee and emits a *rule suggestion* when a payee has been
corrected to the **same** target category at least ``min_count`` times within a lookback
window. The suggestion is the exact ``{match:{description_contains}, set:{category}}``
rule body the user would create — accepting it reuses SP1's ``POST /api/rules`` /
``create_rule`` path. Suggestions are always **approve-tier**: we never auto-create a rule.

Where logic lives (api/ai → services → db → core): this is the brain. It imports only
``db`` (CorrectionStore, AutomationStore) + sibling ``services`` + ``core``. It
does NOT import ``api`` — the target category came from a real transaction the user
already saved, so no "is this category valid" check is needed here.

**No money math anywhere** — this touches only category labels + payee strings + ids.

See .claude/rfcs/phase2/sp2-learn-from-corrections.md.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from backend.db.transaction_store import txn_text

logger = logging.getLogger(__name__)

DEFAULT_MIN_COUNT = 3
DEFAULT_LOOKBACK_DAYS = 180

# Money-movement kinds never carry an expense/income category, so a correction only
# makes sense for these two kinds (mirrors the rules-engine _NO_CATEGORY_KINDS invariant).
_CORRECTABLE_KINDS = {"expense", "income"}

_DIGITS_PUNCT = re.compile(r"[^a-z\s]+")
_WS = re.compile(r"\s+")


# ── Payee normalization (the seam P2-SP4's payee-merge will later replace) ───────


def _payee_key(description: str | None) -> str:
    """A deliberately simple payee normalizer: lowercase, strip digits/punctuation,
    collapse whitespace. So 'STARBUCKS #123' and 'STARBUCKS @ ION' both → 'starbucks'.
    Empty/blank → '' (the caller skips a keyless correction).

    This is documented as the single seam P2-SP4's payee-merge will replace; it is
    isolated to this one function so the upgrade is local."""
    if not description:
        return ""
    s = description.lower()
    s = _DIGITS_PUNCT.sub(" ", s)  # drop digits + punctuation
    s = _WS.sub(" ", s).strip()
    return s


# ── Recording (the single recorder; called from the edit choke point) ────────────


def record_correction(old_txn: dict, new_txn: dict, *, store=None) -> dict | None:
    """Record a category correction IFF the edit is a genuine recategorization:
    the kind is expense/income, the category actually changed, and there is a usable
    payee key. Best-effort — never raises into the edit path. Returns the stored event
    or None (no event recorded).

    This is the single recorder; every transaction-edit path converges on
    ``api/ledger.update_transaction`` which calls this once."""
    try:
        kind = new_txn.get("kind") or old_txn.get("kind")
        if kind not in _CORRECTABLE_KINDS:
            return None
        old_cat = old_txn.get("category")
        new_cat = new_txn.get("category")
        if not new_cat or new_cat == old_cat:
            return None  # no category, or unchanged — not a correction
        sample = txn_text(new_txn) or txn_text(old_txn)
        key = _payee_key(sample)
        if not key:
            return None
        if store is None:
            from backend.db.correction_store import CorrectionStore

            store = CorrectionStore()
        return store.record(
            {
                "payee_key": key,
                "payee_sample": sample,
                # The resolved payee entity (ADR 0010): lets the correction generalize across
                # every description variant of the payee, not just this normalized string.
                "payee_id": new_txn.get("payee_id") or old_txn.get("payee_id"),
                "txn_id": new_txn.get("id") or old_txn.get("id") or "",
                "from_category": old_cat,
                "to_category": new_cat,
                "kind": kind,
            }
        )
    except Exception:
        logger.exception("record_correction failed (best-effort, swallowed)")
        return None


# ── Detection (pure aggregation) ─────────────────────────────────────────────────


def _modal_target(group: list[dict]) -> tuple[str, int]:
    """The most-corrected target category in a group and how many corrections went to
    it. Ties broken by most recent (a later correction wins) then category key."""
    counts = Counter(e.get("to_category") for e in group if e.get("to_category"))
    if not counts:
        return "", 0
    top = counts.most_common(1)[0][1]
    # Among categories tied at the top count, prefer the one corrected most recently.
    tied = [cat for cat, n in counts.items() if n == top]
    if len(tied) == 1:
        return tied[0], top
    latest: dict[str, str] = {}
    for e in group:
        cat = e.get("to_category")
        if cat in tied:
            ts = e.get("created_at", "")
            if ts >= latest.get(cat, ""):
                latest[cat] = ts
    winner = max(tied, key=lambda c: (latest.get(c, ""), c))
    return winner, top


def _covered_by_enabled_rule(payee_sample: str, kind: str, enabled_rules: list[dict]) -> bool:
    """True iff a transaction Automation covers this synthetic payee draft.

    Always False — see categorize._covered_by_enabled_rule for why: a rule is a Lucius run
    that judges the match AFTER commit, so nothing here can answer this without the model.
    Being wrong costs a redundant suggestion, never a wrong write.
    """
    return False


def suggestions(
    *,
    events: list[dict] | None = None,
    enabled_rules: list[dict] | None = None,
    dismissals: list[dict] | None = None,
    min_count: int = DEFAULT_MIN_COUNT,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now: datetime | None = None,
) -> list[dict]:
    """Group recent correction events by ``payee_key``, pick each payee's modal recent
    target category, and emit a ready-to-create rule suggestion when that category's count
    is ``>= min_count`` — EXCLUDING payees already covered by an enabled rule and dismissed
    ``(payee_key, target)`` pairs. Pure: pass events/rules/dismissals in for testing, or
    omit to load from the stores.

    Returns ``[{payee_key, payee_sample, target_category, count, kind, suggested_rule:
    {name, match, set}}]`` sorted by descending count (then payee_key)."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if events is None or enabled_rules is None or dismissals is None:
        from backend.db.correction_store import CorrectionStore

        store = CorrectionStore()
        if events is None:
            events = store.recent(cutoff)
        if dismissals is None:
            dismissals = store.dismissals()
        if enabled_rules is None:
            enabled_rules = []

    # Filter to the lookback window even when events were passed in.
    recent = [e for e in events if e.get("created_at", "") >= cutoff]

    dismissed = {(d.get("payee_key"), d.get("target_category")) for d in dismissals}

    groups: dict[str, list[dict]] = {}
    for e in recent:
        key = e.get("payee_key")
        if not key:
            continue
        groups.setdefault(key, []).append(e)

    out: list[dict] = []
    for key, group in groups.items():
        target, count = _modal_target(group)
        if not target or count < min_count:
            continue
        if (key, target) in dismissed:
            continue
        # The most recent sample for display (and for the rule's description_contains
        # we use the normalized key — stable across the raw "STARBUCKS #123" variants).
        latest_sample = max(group, key=lambda e: e.get("created_at", "")).get("payee_sample", key)
        kind = group[0].get("kind", "expense")
        if _covered_by_enabled_rule(latest_sample, kind, enabled_rules):
            continue
        out.append(
            {
                "payee_key": key,
                "payee_sample": latest_sample,
                "target_category": target,
                "count": count,
                "kind": kind,
                "suggested_rule": {
                    "name": f"{key} → {target}",
                    "match": {"description_contains": key},
                    "set": {"category": target},
                },
            }
        )

    out.sort(key=lambda s: (-s["count"], s["payee_key"]))
    return out


def dismiss(payee_key: str, target_category: str, *, store=None) -> dict:
    """Persist a dismissal so the ``(payee_key → target_category)`` suggestion never
    re-surfaces. Thin pass-through to the store."""
    if store is None:
        from backend.db.correction_store import CorrectionStore

        store = CorrectionStore()
    return store.add_dismissal(payee_key, target_category)
