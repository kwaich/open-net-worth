---
title: mcp_grant, code_grants and mcp_proposals are not migrated — already deleted
status: Accepted
date: 2026-08-10
deciders: Claude (implementer agent, storage plan Wave 5D2b) — investigation finding, not a judgment call requiring owner sign-off
---

# 2026-08-10. `mcp_grant`, `code_grants` and `mcp_proposals` are not migrated — already deleted

## Context

Wave 5D2b's brief named six stores to migrate off JSON and onto the DB: `pending_plans`,
`mcp_pending_calls`, `chat_pairing`, `mcp_grant`, `code_grants`, `mcp_proposals` — carried
forward from Wave 5C's original store inventory into Wave 5D2's own plan (`docs/superpowers/
plans/2026-08-10-json-to-db-wave5.md`), and neither planning pass re-verified that all six
still existed before naming them again.

An exhaustive grep of `backend/` for `mcp_grant`, `code_grant`, and `mcp_proposal` (case-
insensitive, every form: `MCP_GRANT_JSON`, `CodeGrantStore`, `mcp_proposal_store`, etc.) found
**zero hits anywhere in the codebase** — no store file, no path constant, no API route, no
import. `git log` resolves why:

- `mcp_grant` and `mcp_proposals` were the inbound-MCP T0/T1/T2 authorization-tier
  grant/proposal machinery, deleted wholesale in `50626ce2` ("refactor(mcp): remove inbound
  authorization tiering", 2026-07-18) — `backend/api/mcp_grant_api.py`,
  `backend/api/mcp_proposals_api.py`, `backend/db/mcp_grant_store.py`,
  `backend/db/mcp_proposal_store.py`, `backend/mcp/proposals.py`, `backend/services/
  mcp_grant.py` all removed in that one commit, along with both `*_JSON` path constants. That
  commit's own message states plainly: *"the chmod-600 test guarded mcp_grant.json, which held
  no secret"* — so even while it existed, `mcp_grant` was never a credential. This is also the
  removal `CLAUDE.md`'s own MCP security section already documents ("The tier ladder is gone
  (2026-07-18)").
- `code_grants` was `run_code`'s per-conversation "Allow for this conversation" grant,
  deleted in `f581b86d` ("feat!: delete CodeGrantStore with the conversation grant tier",
  2026-08-02) — `backend/db/code_grant_store.py` and `POST`/`DELETE /api/code-grants` removed;
  superseded by the durable `code_execution_auto_run` flag, which is unrelated store state
  (still live, untouched by this wave).

Both removals predate this migration wave by 12 and 8 days respectively. There is no lingering
JSON file, no dead store class, no orphaned path constant — unlike the "vestigial but present"
cases this same migration hit twice before (`agent_findings`, `subscriptions` — both had a real
`JsonStore` subclass and a real `.json` file still sitting on disk with zero readers, absorbed
byte-identically anyway per their own ADRs). This is a different, smaller case: the *entire
mechanism*, store included, is gone. There is nothing to absorb, no data to lose, and no table
to mint that any code would ever read from.

## Decision

We will migrate only the three stores that still exist — `pending_plans`, `mcp_pending_calls`,
`chat_pairing` — in revisions 0030/0031. We will NOT create `mcp_grant`/`code_grants`/
`mcp_proposals` tables, stores, or any other placeholder for the three deleted stores. Their
absence is recorded inline, exactly once, at the two places a future reader would look: a
comment in `backend/db/tables.py` next to the three real Wave 5D2b tables, and this ADR
(referenced from the wave plan doc's progress ledger and `docs/DATA_MODEL.md`).

Separately, `pending_plans` carries one design decision of its own worth recording here rather
than opening a second ADR for it: `db.pending_plan_store._migrate_from_cache_dir` — the
one-time adoption of the pre-2026-07-30 `CACHE_DIR` location, previously run on every
`PendingPlanStore()` construction — has no more "target path" to adopt into once the store is
DB-backed. Its one remaining job moves into the 0031 cutover itself, as pure read-only
discovery (`_pending_plans_source`): prefer the current `DATA_DIR` source if present, else fall
back to the legacy `CACHE_DIR` file, else correctly absorb nothing. This was not a hypothetical
edge case invented for completeness — checking this worktree's own real dev data found
`~/.open-net-worth/cache/pending_plans.json` still present from 2026-07-30 (a separate, newer
copy already existed at the `DATA_DIR` location too, from later real usage, so the fallback
path is not this instance's actual absorption path — but it is exercised and proven correct on
an isolated copy holding ONLY the legacy file, mirroring exactly how `pending_analysis.json`'s
pre-existing `CACHE_DIR` source already works with the unmodified `_absorb_one`/
`_finish_cutovers` machinery). The discovery function never touches the filesystem itself —
whichever path it returns is what `_finish_cutovers` renames to `.migrated-<ts>` only after the
whole migration has verifiably succeeded, so nothing moves before success is confirmed.

## Consequences

### Positive
- No dead tables, no invented store classes with zero callers — `backend/db/tables.py` keeps
  its one-table-per-store-file invariant intact.
- The wave's actual remaining scope (`pending_plans`/`mcp_pending_calls`/`chat_pairing`) is
  unblocked and complete; it was never actually gated on the other three.
- The pre-2026-07-30 `CACHE_DIR` leftover is absorbed instead of silently orphaned by the
  cutover, on any install where it still exists — a real, if narrow, correctness improvement
  over what a naive "just migrate the DATA_DIR path" port would have done.

### Negative
- The wave plan doc and `docs/DATA_MODEL.md` both still name all six stores as of this
  writing; each needs a one-line correction pointing here (done in the same change as this
  ADR).
- A future reader who only skims `db/tables.py` might still wonder why three names are absent
  without following the comment to this file — mitigated by making the comment itself state
  the two commit hashes and the one-line reason, not just "see ADR".

### Follow-ups
- None — this is a closed investigation, not a deferred decision. If a `mcp_grant`-shaped
  inbound-authorization mechanism is ever reintroduced, it starts fresh on the DB substrate
  like every other new store; there is no migration debt waiting for it.

## Alternatives considered

- **Silently drop the three names with no record** — rejected: a future agent re-reading the
  wave plan's original six-store brief would waste time re-investigating the exact same
  question, or worse, assume an oversight and try to "finish" the migration by inventing empty
  tables for a mechanism that no longer exists.
- **Mint empty `mcp_grant`/`code_grants`/`mcp_proposals` tables anyway, for brief-completeness**
  — rejected: a table with zero readers and zero writers is dead code by construction, exactly
  the class of thing `backend/db/json_store.py`'s own retirement (`tests/db/test_no_json_store.py`)
  was fought for; "the brief said six" is not a reason to manufacture a table nothing will ever
  touch.
- **Keep `_migrate_from_cache_dir` as a permanent no-op method on the new DB-backed
  `PendingPlanStore`, for behavioral parity** — rejected: once the store has no `path`, the
  method has nothing left to do on every construction (a pure no-op forever, since the DB row
  either exists or doesn't and a JSON file's location is irrelevant either way) — that is dead
  code, not parity. The ADOPTION its callers actually needed moves to the one place a one-time
  migration event belongs: the cutover.

## Agent provenance

- **Decided by:** Claude (implementer agent, Wave 5D2b of the JSON→DB storage migration). Lane:
  worktree `storage-plan1`.
- **Alternatives weighed:** silent drop (rejected — wastes a future agent's investigation);
  mint empty placeholder tables (rejected — dead code, the exact thing this migration
  program has fought to retire); keep a permanently-no-op adoption method (rejected — dead
  code in a different shape).
- **Driving artifacts:** `50626ce2` ("refactor(mcp): remove inbound authorization tiering") and
  `f581b86d` ("feat!: delete CodeGrantStore with the conversation grant tier") — the two commits
  whose diffs and messages are this ADR's evidence; `docs/superpowers/plans/
  2026-08-10-json-to-db-wave5.md` (the wave brief that carried the stale six-store inventory
  forward); `docs/adr/2026-08-10-vestigial-subscriptions-absorbed-not-resurrected.md` and
  `docs/adr/2026-08-10-connector-sessions-deferred-to-secrets-wave.md` (this wave's own nearest
  precedents for "a named store turned out not to be a straightforward migration").
- **Why this way (one line):** a storage-migration task found that three of its six named
  stores were already fully deleted weeks earlier, and recorded that fact once instead of
  guessing at resurrection or staying silent about the gap.
