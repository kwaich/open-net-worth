---
title: A row may keep the ref it already holds, and staged deletions run first
status: Accepted
date: 2026-08-27
deciders: orchestrating agent (Claude) + repo owner
---

# A row may keep the ref it already holds, and staged deletions run first

## Context

A statement import reported **"9 linked · 0 imported new · 1 left out · 3 deleted"** against a
preview that had promised "Link 10". The left-out row came back offering to link on every
re-import. The owner: *"why does it keep having 1 left out and show up as will link here again?"*

The log named it:

```
decision_commit: merge failed for row=7 existing_id=txn_3a5b85d70000
  ConflictError: external reference ('email', 'aaaa1111bbbb')
                 already belongs to transaction txn_52a02c3c0000
```

Three LIVE rows carried `email:aaaa1111bbbb` — one Apple charge booked by three separate
automation runs on three days. The audit trail shows the ref present at CREATE on all three;
no later update touched `external_refs`.

Two independent defects turned that state into a silent failure.

**1. A merge contested a claim the row already owned.** `apply_import_merge` builds its patch as
`patch["external_refs"] = list(existing.get("external_refs") or []) + inherited`
(`ledger.py:2298`) — it resubmits the row's OWN refs. `resolve_external_refs_patch` ran its
ownership check on every incoming pair *before* testing whether the row already held it, and
`by_external_ref` returns the FIRST live owner, which need not be the row being patched. So the
row was refused for continuing to hold a ref it already held. Nothing was changing.

**2. One half of the user's decision was invisible to the other half.** The reviewer linked the
statement row to one of the three and marked the other two for deletion — a complete, consistent
decision whose end state has exactly one owner. But merges ran at `decisions.py:2404` and the
staged deletions at `:2468`, so at merge time the rows the user had just condemned were still
live and still answering. The deletes freed the ref seconds later.

The failure was then swallowed three ways: caught into `merge_errors`, counted into `skipped`,
rendered as "1 left out" — and `merge_errors` has **zero** consumers in `frontend/src`. A
vanished merge target (`before is None`) was counted with no log at all.

## Decision

We will make **both** fixes, each sufficient on its own:

1. **`resolve_external_refs_patch` skips the ownership check for a pair the row already holds.**
   Re-asserting an existing ref is a no-op, so there is no claim to contest.
2. **Staged ledger deletions run FIRST**, ahead of every create and merge, so each decision is
   judged against the world the user confirmed.

The guarantee that motivated deletes-last — *"a failure must never cost the user an import they
confirmed"* — is kept by **compensation** rather than ordering: `_apply` delegates to
`_apply_rows`, and anything escaping it calls `_restore_deleted`.

A vanished merge target is logged and reported like any other merge failure.

## Consequences

### Positive

- The owner's failure cannot recur: reverting both fixes reproduces the production error
  byte-for-byte in `tests/test_decision_commit_staged_delete.py`; either fix alone passes.
- Compensation is strictly stronger than the ordering it replaces. Deletes-last only ever
  protected the deletions; the creates that ran ahead of them were already written and were
  never rolled back.
- One less silent path: a merge target that vanished between preview and commit was
  indistinguishable from a row the user chose to leave out.

### Negative

- Two mechanisms now defend one failure, so a future reader may remove one believing it
  redundant. It is not: fix 1 covers a merge on a book that already holds duplicate refs with no
  deletion involved, fix 2 covers any decision judged against rows the same commit removes.
- `_apply` is split in two for the compensation boundary, so a reader must follow one more hop.
- The import is still **not atomic**. True atomicity needs `conn=` threaded through
  `bulk_create_transactions`, `update_transaction` and `apply_import_merge` plus their table-lock
  sets — a write-choke refactor, deliberately out of scope here.

### Follow-ups

- `merge_errors` / `delete_errors` reach the UI. The backend has said why since `decisions.py`
  first reported them; the Booked panel still shows a bare count.
- The external-ref floor in `bulk_create_transactions` is opt-in (`dedup_by_external_ref`) and
  exactly one of eight callers opts in — which is how three live rows came to share one
  message-id. These fixes stop that state breaking an import; they do not stop it being created.
- Two collisions remain on the owner's book: `email:cdbbc572d87a` (Trip.com) and
  `email:f98a01fd0157` (Driftpay).

## Alternatives considered

- **Reorder only, leave the ownership check alone** — rejected: a book that already holds
  duplicate refs still fails on any merge with no deletion in the commit.
- **Fix the ownership check only** — rejected: it resolves this failure but leaves every other
  decision in a commit judged against rows that commit is about to remove.
- **Wrap the whole commit in one `locked_unit_of_work`** — the ideal, and the direction the money
  chokes already went. Rejected for now on scope: `bulk_delete_transactions` accepts `conn=` but
  `bulk_create_transactions` / `update_transaction` / `apply_import_merge` do not, and threading
  it through them touches the ledger's central update choke. Recorded as a follow-up.
- **Make `by_external_ref` refuse when several live rows answer** — rejected as a bigger, riskier
  behaviour change on a hot read that many callers depend on, and it would convert a silent wrong
  answer into a hard failure across paths that are working today.

## Agent provenance

- **Decided by:** orchestrating Claude agent + repo owner
- **Lane / branch:** `main`
- **Alternatives weighed:** reorder only; ownership-check only; one transaction for the whole
  commit; a stricter `by_external_ref`
- **Driving artifacts:** a reported bug, 2026-08-27 (`stmt_d3ab87220000`, row 7 →
  `txn_3a5b85d70000`); `decisions.py:1905`'s deletes-last rationale, which this supersedes;
  `resolve_external_refs_patch`'s own 2026-08-05 / 2026-08-16 ref-repair scars
- **Why this way (one line):** a row keeping the ref it already holds changes nothing, and a
  decision must be judged against the world the user confirmed — not against rows the same commit
  is about to remove.
