---
title: agent_findings.json cutover absorbs the data, not the retired product's API
status: Accepted
date: 2026-08-10
deciders: repo owner (Wave 5B storage-migration brief, which named agent_findings as one of four stores to migrate), Claude (implementer agent, storage plan Wave 5B)
---

# 2026-08-10. `agent_findings.json` cutover absorbs the data, not the retired product's API

## Context

Wave 5B's brief named `agent_findings` as one of four Tier-2 logs/telemetry stores to migrate
off its JSON file and onto a `payload_table`, "following the EXACT pattern the 46
already-migrated stores use" and preserving "every existing method signature, cap/eviction
rule, and validation." That instruction assumes a live store class exists to convert.

It does not. `git log` shows `backend/db/agent_finding_store.py` (a `JsonStore` subclass,
`id_field="id"`, prepend + capped at 200, with domain methods `query`/`count`/`append`/
`mark_reviewed`/`dismiss`/`record_outcome`/`recent_for_agent`/`find_recent_by_dedupe_key`) was
deleted three commits before this task started, in `d0da1a09` ("feat!: retire the Agent
product" — "No live path invoked an Agent... Deleted: models/agent, db/agent_store, the
lesson/finding/memory stores, services/agents, ... the agent-findings inbox source..."). An
exhaustive grep of `backend/` (excluding tests) for `AgentFindingStore`, `AGENT_FINDINGS_JSON`,
and `agent_findings` found zero production readers or writers — only the path constant itself
(`core/paths.py`), its checkpoint-exclusion entry (`ai/checkpoint/_policy.py`), and stale
docstring mentions in `services/inbox.py`/`api/inbox.py`/`services/proposal_apply.py`
describing a fourth Inbox source (`AgentFindingStore`) whose actual wiring was already removed
from `inbox_items()` — only three sources (intel/signals/harness suggestions) remain live there.

The JSON file itself was NOT deleted by that commit, and still holds real data: 200 rows (the
old store's own `MAX_FINDINGS` cap), newest `2026-08-01`, oldest `2026-06-09` — genuine
historical output from when the Agent product was live, orphaned but not garbage.

This is a genuine fork the brief didn't anticipate: migrate a store with zero live callers
exactly like a live one (full domain-method resurrection), or migrate only what has a
reason to exist going forward, while still not losing the data.

## Decision

We will absorb `agent_findings.json`'s 200 rows byte-identically into a new `agent_findings`
table (revisions 0022/0023, generic `payload_table` shape, reusing
`logs_io.import_payload_list`/`export_payload_list` with `reverse=True` — the source is
natural-id, prepend-order, exactly like `harness_suggestions`), but we will NOT re-implement
`AgentFindingStore`'s retired domain surface. The new `backend/db/agent_finding_store.py`
subclasses `PayloadTableStore` for its constructor only, exposing just the generic read surface
(`all`/`find_by_id`/`query`/`count`) — no `append`/`mark_reviewed`/`dismiss`/`record_outcome`/
`recent_for_agent`/`find_recent_by_dedupe_key`, and no rewiring of `services/inbox.py` (whose
own "four sources" framing already documents a source it stopped calling before this task ever
touched it — a `d0da1a09` follow-up, not this one's to fix). The table is checkpoint-EXCLUDED
(mirrors the pre-existing `AGENT_FINDINGS_JSON` file-level exclusion) and joins neither
`portable_bridge.BRIDGED` nor `_WIPE_FNS` (mirrors `run_history`/`tool_call_log` — a
non-portable runtime log with no reset role).

## Consequences

### Positive
- The wave's stated goal ("everything must be in database, no runtime `.json` file... except
  the ones that really deserve it") is met for this file without an owner having to first decide
  whether the Agent product's data should simply be deleted — a bigger, more consequential call
  than a storage-migration task should make unilaterally.
- No dead code: the store's public surface is exactly as large as its (zero) current callers
  need, consistent with CLAUDE.md's "no dead code, no leftover scaffolding" standard. A future
  feature that wants a findings-like queue again extends this store then, with real requirements
  driving its shape instead of a three-commits-stale one.
- Every table in `backend/db/tables.py` still has exactly one corresponding store file — the
  repository-layer convention holds even for a vestigial table.

### Negative
- The `agent_findings` table itself is now unreachable by any code path except the generic
  `PayloadTableStore` reads a future caller might add — effectively parked, not actively useful
  today. A stricter reading of "no dead code" could argue the whole table is speculative.
- `services/inbox.py`'s docstring ("Four sources compete for the same job... Agent findings
  (`AgentFindingStore`)") and `services/proposal_apply.py`'s `/api/agent-findings/{id}/apply`
  mention describe an endpoint/source that does not exist — left uncorrected here (out of this
  task's scope; see Follow-ups).

### Follow-ups
- `services/inbox.py`'s module docstring and `_priority()`'s `kind == "finding"` branch (dead:
  `_normalize_intel`/`_normalize_signal`/`_normalize_harness_suggestion` never produce
  `kind="finding"`), `api/inbox.py`'s docstring, and `services/proposal_apply.py`'s
  `/api/agent-findings/{id}/apply` mention are all stale `d0da1a09` leftovers this task found but
  did not fix (unrelated to storage migration). Worth a small follow-up cleanup pass.
- If the owner decides the Agent product's historical findings have no future value at all,
  deleting the `agent_findings` table (and this ADR's table-level exclusion entries) is a
  one-line follow-up — but that is a product decision, not implied by this one.

## Alternatives considered

- **Skip `agent_findings` entirely, leave the JSON file in place** — rejected: it was explicitly
  named in the brief, the wave's stated goal is zero runtime `.json`, and the file holds real
  (if orphaned) historical data a storage-migration task has no standing to silently decide is
  worthless.
- **Fully resurrect `AgentFindingStore`'s original domain API** (`query`/`append`/
  `mark_reviewed`/`dismiss`/`record_outcome`/`recent_for_agent`/`find_recent_by_dedupe_key`) —
  rejected: every one of those verbs belongs to a product `d0da1a09` already decided to remove;
  re-adding them now, with zero callers, would be dead code re-introduced by the very migration
  meant to reduce it, and would silently paper over the fact the product is gone.
- **Delete the JSON file and the `AGENT_FINDINGS_JSON` constant outright, treating this as
  `d0da1a09`'s unfinished cleanup** — rejected: deleting a self-hoster's real historical data is
  a more consequential, less reversible call than "migrate storage," and belongs to whoever owns
  the Agent-retirement decision, not to a Wave 5B storage task that happened to notice the gap.

## Agent provenance

- **Decided by:** Claude (implementer agent, Wave 5B of the JSON→DB storage migration). Lane:
  worktree `storage-plan1`, branch `perf/latency-fixes` (task run directly on the working tree,
  no separate feature branch cut for this slice).
- **Alternatives weighed:** skip-entirely (rejected — contradicts the wave's stated goal and the
  explicit brief); full domain-API resurrection (rejected — dead code reviving a retired
  product); outright deletion of the file/constant (rejected — a bigger, unilateral call this
  task didn't have standing to make).
- **Driving artifacts:** `docs/superpowers/plans/2026-08-10-json-to-db-wave5.md` (the wave brief
  naming `agent_findings` as one of four 5B stores); commit `d0da1a09` ("feat!: retire the Agent
  product", the actual deletion of `AgentFindingStore` and its callers); `backend/db/
  run_history_store.py`/`harness_suggestion_store.py` (the checkpoint-excluded Tier-2 logs
  pattern this table's shape mirrors).
- **Why this way (one line):** absorb the real data because the wave's goal and the brief both
  require it, but don't re-animate a product's API surface that a separate, deliberate commit
  already decided to remove — that line is not this task's to redraw.
