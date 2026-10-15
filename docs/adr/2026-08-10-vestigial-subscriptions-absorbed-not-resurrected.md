---
title: subscriptions.json cutover absorbs the data, not the retired module's API
status: Accepted
date: 2026-08-10
deciders: repo owner (Wave 5C storage-migration brief, which named subscriptions as one of six stores to migrate), Claude (implementer agent, storage plan Wave 5C)
---

# 2026-08-10. `subscriptions.json` cutover absorbs the data, not the retired module's API

## Context

Wave 5C's brief named `subscriptions` as one of six runtime-state stores to migrate off its
JSON file and onto the DB, asking the implementer to "investigate its shape, store, checkpoint
+ portable status, and preserve them." That instruction assumes a live store class exists to
convert.

It does not. `git log` shows the subscription-detection module (`SubscriptionStore`, a
`JsonStore` subclass, flat list, `root_key=None`, keyed by detection signature) was dropped in
a commit titled "feat(phase-2): payee registry, skill improvements, agent UX, lots panel, CSS
design system, subscription removal" — "Drop subscriptions module (moved to payee/automation
detection)." An exhaustive grep of `backend/` and `frontend/src` for `SubscriptionStore`,
`subscription_store`, and `/api/subscriptions` found zero production readers or writers, zero
references anywhere outside `core/paths.py` needing a new constant for this migration itself.
Unlike `agent_findings.json` (Wave 5B's own version of this exact situation —
`docs/adr/2026-08-10-vestigial-agent-findings-absorbed-not-resurrected.md`), `subscriptions.json`
never even had a live path constant in `core/paths.py`, so it was never added to
`ai.checkpoint._EXCLUDED_PATHS` or `core.portable.PORTABLE_FILES` either — a smaller footprint
than `agent_findings` left behind, but the same underlying situation: a retired feature's real
JSON file, still sitting in `DATA_DIR`, orphaned but not garbage.

The file itself holds 5 real rows — genuine historical data from when the feature was live: each
a `{id, status, note, created_at, updated_at}` overlay record (`status` ∈ `dismissed`/`confirmed`)
the user recorded against a detected recurring-charge group, per the feature's own retired RFC
(`.claude/rfcs/phase2/sp3-subscription-detection.md`). This is a user's own dismiss/confirm
DECISION, not a behavioral/telemetry log — closer in kind to `RECURRING_STREAMS_JSON`'s overlay
rows (which explicitly superseded this exact module, per that file's own `core/paths.py`
comment: "for a KNOWN future stream history can't show yet (a raise, **a cancelled
subscription**)") than to `agent_findings`' own AI-run output log.

## Decision

We will absorb `subscriptions.json`'s 5 rows byte-identically into a new `subscriptions` table
(revisions 0024/0025, generic `payload_table` shape, reusing `wave5c_io.import_payload_list`/
`export_payload_list` — the source is natural-id, append-order, like most flat-list JsonStore
stores), but we will NOT re-implement `SubscriptionStore`'s retired domain surface (whatever
`dismiss`/`confirm`/lookup-by-signature methods it once exposed — none survive in git history
at any commit this task inspected close to the removal). The new
`backend/db/subscription_store.py` subclasses `PayloadTableStore` for its constructor only,
exposing just the generic read surface (`all`/`find_by_id`/`query`/`count`) — mirrors
`agent_finding_store.py`'s own shape exactly.

Two differences from the `agent_findings` precedent, both because `SUBSCRIPTIONS_JSON` never
had the file-level classifications `AGENT_FINDINGS_JSON` did:

1. **Checkpoint-INCLUDED, not excluded.** `agent_findings` mirrors its own pre-existing
   `AGENT_FINDINGS_JSON` exclusion (a behavioral log, like `corrections`/`harness_suggestions`).
   `subscriptions` was never excluded, and its rows are genuinely user-authored (a dismiss/
   confirm decision), so the new table stays checkpoint-INCLUDED — preserving its never-excluded
   status, not a new classification either way.
2. **No portable/bridge role**, same conclusion as `agent_findings` but for the same reason:
   `SUBSCRIPTIONS_JSON` was never in `core.portable.PORTABLE_FILES`, so the new table joins
   neither `portable_bridge.BRIDGED` nor `_WIPE_FNS` — a retired feature's absorbed-but-dead data
   earns no new reset/export role it never had.

## Consequences

### Positive
- The wave's stated goal (no runtime `.json` file "except the ones that really deserve it") is
  met for this file without an owner having to first decide whether this retired module's data
  should simply be deleted — a bigger, more consequential call than a storage-migration task
  should make unilaterally (mirrors the `agent_findings` ADR's identical reasoning).
- No dead code: the store's public surface is exactly as large as its (zero) current callers
  need.
- Every table in `backend/db/tables.py` still has exactly one corresponding store file.

### Negative
- The `subscriptions` table itself is now unreachable by any code path except the generic
  `PayloadTableStore` reads a future caller might add — effectively parked, not actively useful
  today.

### Follow-ups
- If the owner decides this retired module's historical overlay data has no future value at
  all, deleting the `subscriptions` table is a one-line follow-up — but that is a product
  decision, not implied by this one.

## Alternatives considered

- **Skip `subscriptions` entirely, leave the JSON file in place** — rejected: it was explicitly
  named in the Wave 5C brief, and the file holds real (if orphaned) historical data a
  storage-migration task has no standing to silently decide is worthless.
- **Attempt to resurrect a domain surface** (`dismiss`/`confirm`/lookup-by-signature) — rejected:
  no such surface survives in git history to resurrect faithfully, and inventing new methods
  with zero callers would be dead code introduced BY the very migration meant to reduce it.
- **Delete the JSON file and skip absorbing it, treating this as the subscription-removal
  commit's unfinished cleanup** — rejected: deleting a self-hoster's real historical data is a
  more consequential, less reversible call than "migrate storage," and belongs to whoever owns
  that removal decision, not to a Wave 5C storage task that happened to notice the leftover file.

## Agent provenance

- **Decided by:** Claude (implementer agent, Wave 5C of the JSON→DB storage migration). Lane:
  worktree `storage-plan1`.
- **Alternatives weighed:** skip-entirely (rejected — contradicts the wave's stated goal and the
  explicit brief); domain-surface resurrection (rejected — no real prior surface to resurrect,
  would be invented dead code); outright deletion of the file (rejected — a bigger, unilateral
  call this task didn't have standing to make).
- **Driving artifacts:** `docs/superpowers/plans/2026-08-10-json-to-db-wave5.md` (the wave brief
  naming `subscriptions` as a 5C store); `docs/adr/2026-08-10-vestigial-agent-findings-absorbed-not-resurrected.md`
  (the Wave 5B precedent this ADR mirrors); `.claude/rfcs/phase2/sp3-subscription-detection.md`
  (the retired feature's own RFC, confirming the row shape and the user-authored, not
  behavioral-log, nature of the data).
- **Why this way (one line):** absorb the real data because the wave's goal and the brief both
  require it, but don't invent an API surface for a module that's already gone — that line is
  not this task's to redraw.
