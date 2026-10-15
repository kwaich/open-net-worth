"""Cross-subject push-dedup registry — owner override of RFC intel-signals-data-model §4's
"NO-GO". See `docs/adr/2026-08-30-claims-registry-cross-subject-push-dedup.md` for the full
reasoning; this module is the write choke (`apply_claim_push`) plus the two pure/read
primitives `backend.monitoring.analyst`'s push-decision gate calls.

## The problem this closes

`docs/adr/2026-08-28-cross-subject-fan-out-and-asset-brief-consolidation.md` pairs a topic row
with its linked ticker's asset row ONLY when both are pending in the SAME sweep tick
(`analyst._find_linked_pairs`) — deliberately narrow, and it cannot by construction catch a
duplicate that arrives across TWO ticks (the proven incident: a topic-side and an asset-side
signal each independently pushed the same real corporate development, ~10 hours apart, on the
same night). This registry is the cross-TICK counterpart: `group_key_for` gives two
independently-processed story rows a shared identity when they're genuinely about the same
underlying fact, and `apply_claim_push`/`is_recent_duplicate_push` let a later push recognize
"this fact was already communicated" without either row knowing the other one exists.

## Why this does NOT reintroduce the RFC's conflation concern

The RFC's caution (quoted in the ADR) is that a price-alert-type story and an M&A-topic-type
story sharing a ticker are NOT duplicates — a price threshold crossing and a corporate
development are different KINDS of things and must never collapse into one claim just because
they share a subject. `group_key_for` enforces this structurally, not by convention:
`price_alert`/`daily_digest`/`monitoring` never produce a group_key at all (see
`_GROUPABLE_STORY_TYPES`), and a story that resolves to more than one instrument (a topic
linked to several tickers, a risk finding spanning a whole sector/region) is left UNGROUPED
rather than guessed at — the same conservative "skipped, never guessed" posture
`analyst._linked_instrument_ids` already takes for an ambiguous ticker match.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from backend.core.timestamps import sort_key as _ts_sort_key
from backend.db.claims_store import ClaimsStore

logger = logging.getLogger(__name__)

# Only the news/development-tracking story types ever resolve a group_key — a `price_alert`
# (a price threshold crossing) or `daily_digest`/`monitoring` row is never "about" a fact in the
# sense this registry dedups (RFC's own scoping distinction, preserved structurally).
_GROUPABLE_STORY_TYPES = frozenset({"asset", "topic", "risk", "opportunity", "macro"})

# 24h — "this fact is still the one we just told the user about." Deliberately NOT
# `analyst._STALE_PUSH_HOURS` (6h, a DIFFERENT question — "is this push now too late to be
# worth sending at all") — reusing that window would fail to catch the proven incident this
# registry exists for (the topic/asset pushes were ~10h apart, past a 6h window). Mirrors this
# pipeline's existing "how long is a fact still fresh" convention instead —
# `services.evidence._DEFAULT_TTL_HOURS` (24) and
# `monitoring.price_monitor._NEWS_CONTEXT_MAX_AGE_HOURS` (24.0) both treat a day as the horizon
# for "still current," not an arbitrary new number.
_CLAIM_PUSH_FRESHNESS_HOURS = 24.0


def group_key_for(story_type: str, instrument_ids: Iterable[str]) -> str | None:
    """Subject-agnostic identity for the claims registry. Two DIFFERENT `intel_stories` rows
    (e.g. a `topic` row "Kestrel Dash M&A" and an `asset` row "Kestrel Dash SE") resolve to
    the SAME group_key exactly when both hold true:

      * `story_type` is a news/development-tracking type (`asset`/`topic`/`risk`/
        `opportunity`/`macro`) — never `price_alert`/`daily_digest`/`monitoring`.
      * `instrument_ids` (whatever the CALLER already resolved for this specific row — an
        asset row's own `instrument_id`; a topic row's `analyst._linked_instrument_ids(item)`;
        a risk/macro row's `affected_instrument_ids`) contains EXACTLY ONE distinct, non-empty
        id. Zero means nothing to group on. More than one — a topic linked to several tickers,
        a risk finding spanning a whole asset class/sector/region/currency — means this row
        isn't "about" one single company the way the proven incident was; it is left UNGROUPED
        rather than guessed at, never collapsed with anything.

    This function does no resolution of its own (mirrors `analyst._find_linked_pairs`'
    existing "resolve elsewhere, match here" split) — it is a pure, cheap classification, safe
    to call on every push decision."""
    if story_type not in _GROUPABLE_STORY_TYPES:
        return None
    distinct = {iid for iid in instrument_ids if iid}
    if len(distinct) != 1:
        return None
    return f"news-fact:{next(iter(distinct))}"


def _hours_since(ts: str | None) -> float:
    """Hours between `ts` and now, via `core.timestamps.sort_key`'s naive/aware normalization
    — a missing/unparseable timestamp sorts as the oldest possible instant, so it always reads
    as "not recent" here rather than needing its own None-check."""
    return (datetime.now(timezone.utc) - _ts_sort_key(ts)).total_seconds() / 3600.0


def is_recent_duplicate_push(group_key: str) -> bool:
    """True when `group_key` already has an active claim whose `pushed_at` is within
    `_CLAIM_PUSH_FRESHNESS_HOURS` — the read side `analyst._push_decision`'s new gate checks
    before firing a second, independent push about a fact another story already communicated."""
    if not group_key:
        return False
    claim = ClaimsStore().find_active_by_group_key(group_key)
    if claim is None:
        return False
    return _hours_since(claim.get("pushed_at")) <= _CLAIM_PUSH_FRESHNESS_HOURS


def apply_claim_push(
    group_key: str,
    *,
    story_id: str,
    statement: str,
    sources: list[dict],
    materiality: str,
    decision_relevant: bool,
    confirmed_at: str | None = None,
) -> dict:
    """The write choke: record that `story_id` just pushed a claim under `group_key` — creates
    the claim if this is the first push for this fact, or refreshes the existing one's
    `pushed_at`/`statement`/`sources` and appends `story_id` to `story_ids` if this is a repeat.
    A single-row write (`ClaimsStore.record_push` does its own atomic read-then-decide under
    the table's own lock) — no `locked_unit_of_work` composition needed, since this never
    touches more than one table in one call."""
    now = datetime.now(timezone.utc).isoformat()
    claim = ClaimsStore().record_push(
        group_key,
        story_id=story_id,
        statement=statement,
        sources=sources,
        materiality=materiality,
        decision_relevant=decision_relevant,
        confirmed_at=confirmed_at,
        now=now,
    )
    logger.info(
        "claims: recorded push for group_key=%s story_id=%s (claim=%s)",
        group_key,
        story_id,
        claim["id"],
    )
    return claim
