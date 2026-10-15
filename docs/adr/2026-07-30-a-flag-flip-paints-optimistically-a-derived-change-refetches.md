# 2026-07-30 — A flag flip paints optimistically; a server-derived change refetches

**Status:** accepted (owner report — "it's not showing any load indicator when i tap on them…
OR i suggest to make the UI update immediately first, and in case it failed → just show error
and revert the status", 2026-07-30)
**Where:** `frontend/src/components/tabs/accounts/useAccountsLedger.ts` (`bulkUpdate`,
`localBulkPatch`, `normalizeBulkResult`, `loadTxns`'s new `force`),
`frontend/src/components/tabs/accounts/AccountsTab.tsx` (`runBulk` and every bulk call site)
**Tests:** `frontend/src/components/tabs/accounts/useAccountsLedger.optimisticBulk.test.ts` (10),
`AccountsTab.bulkSelectionPersists.test.tsx` (5), `Toast.anchor.test.tsx` (9)

## Decision

The register's bulk writes split by whether the client can PREDICT the stored row.

1. **Predictable** — `cleared`, `needs_review`. `localBulkPatch` maps them onto the client row
   shape and `bulkUpdate` paints them immediately, then reconciles against the response. The
   register is **not** refetched on success.
2. **Server-derived** — `category` (runs the `explicit > rule > payee-default > None`
   precedence chain), `to_account_id` (re-FXes the leg into the target's currency), `payee_id`
   (resolves an entity), `tags` (additive union). `localBulkPatch` returns `null` and the old
   write-then-refetch path runs. A mixed change set is server-derived: any unpredictable key
   poisons the whole patch.
3. A row the server **refuses** — returned under `not_found` / `skipped` — has its optimistic
   paint reverted individually, even though the request itself succeeded. A rejected request
   reverts everything and rethrows.
4. Toasts report `result.count` (what the server wrote), never `ids.length` (what was asked).
5. **The selection survives a completed action.** The rows are still on screen and still the set
   being worked on, so a status/label change leaves the bulk bar up. Only an action that removes
   the rows from this view — move, delete — clears it.
6. **A bulk action is narrowed to the rows it would actually change**, and the matching menu item
   is **disabled** (not hidden) when that count is zero.

An earlier revision of this ADR had `runBulk` clear the selection up front, reasoning that
"leaving 15 rows checked while the toast fires reads as nothing happened". The owner rejected it
— "the bulk selection bar shouldn't disappear though? after completing one process, cause user
might want to do other things" — and they are right: the argument was already obsolete, because
with the optimistic paint the ROWS themselves are the feedback. Dropping the bar just forced a
full re-select to do the next thing.

## Why

All three status actions in the bulk bar already sent **one** `POST /api/transactions/bulk-update`
for the whole selection — the write was never the problem. What followed it was: a full
`loadTxns(filters, 0, false)` + `refreshAccounts()`, so nothing moved on screen for two round
trips, with no pending affordance anywhere (every call site was a bare `void L.bulkUpdate(…)`).
Then `setOffset(0)` threw away every extra page the user had scrolled in.

A `cleared` toggle is a boolean the server stores verbatim. Making the user wait two round trips
to see a value we already know, and paying for it by resetting their pagination, is strictly
worse than painting it and reconciling. The same reasoning does **not** extend to a category:
the backend may re-derive it, so painting it would show a row the server never stored.

**The silent failure was the worse half.** No bulk call site had a `.catch()` — not
`bulkCategory`, `bulkSetCleared`, `bulkMarkReviewed`, `bulkMemo`, `bulkMove`, nor `bulkDelete`.
A rejected write surfaced as an unhandled promise rejection: no toast, selection intact, rows
still showing their old state. Optimism without a revert path would have made that permanent,
so `runBulk` funnels every bulk write through one `.then`/`.catch` pair.

## The refetch path was already serving pre-write rows

Found while testing the non-optimistic branch. `loadTxns` reads through
`fetchCachedQuery(url, fetcher, { force: append })`, and that helper returns the cached entry
whenever it is inside `staleTime` (30s):

```ts
if (!options.force && entry.snapshot.data !== undefined) { … return Promise.resolve(entry.snapshot.data) }
```

`bulkUpdate` called it with `append=false`, and called `fireMutate()` (which invalidates
`/api/transactions?` keys) **after**. So a bulk category change made within 30s of the last load
re-applied the pre-write list — the refetch cost two round trips and then showed the old rows
anyway. `loadTxns` now takes a `force` flag and the post-write reload passes it. Note that
invalidating first would not have been enough: `invalidateQueryCache` only sets `updatedAt = 0`
and leaves `data` in place, so the unforced read still returns it.

## Alternatives rejected

**Spinner on the pending item, keep write-then-refetch.** Honest, but it makes the user watch
the full two-round-trip refetch for a boolean, and leaves the page-0 reset in place. Offered to
the owner and declined in favour of optimism.

**Optimistic everywhere, including category.** The backend is explicitly allowed to decide a
different category than the one requested (rule provenance, `payee-default`) and to skip a row
outright (an adjustment row takes no category — the no-category invariant). Painting a guess
there would contradict `docs/INVARIANTS.md` on screen.

**Trust `ids.length` for the toast.** The endpoint returns a per-id summary precisely because it
refuses rows. "15 marked cleared" when the server wrote 12 is a lie the response already
disproves.

**Hide a no-op action instead of disabling it.** The menu would change height between
selections — "Mark as Cleared" alone vanishes whenever every selected row is already cleared —
and a missing row reads as broken where a greyed one with a `title` explains itself. Disabling
also keeps the `C` shortcut and the menu item agreeing on one condition.

**Snapshot the pre-write rows off a render-phase ref.** Added a new `react-hooks/refs` lint
error to a file that already carries pre-existing ones. The snapshot is captured inside the
optimistic `setTransactions` updater instead — StrictMode may run it twice, but both runs see
the same `prev` and write the same `id → row` pairs, so it is idempotent.

## Agent provenance

Scope was confirmed against the code before proposing anything: the owner asked whether the
actions were N+1 (they were not) and whether the UI updated (it did, via a full refetch). The
two findings that drove the change — the missing `.catch()` on all six bulk call sites, and the
`staleTime` cache serving pre-write rows to the "refetch" path — were not in the report; the
second was caught only because a test asserted the non-optimistic branch actually re-fetches
and it did not.

**Not run:** the UI Quality Gate (`make qa-e2e`, `make ui-gate`, screenshot evidence). This
change adds no markup, no CSS, and no new control — the geometry gate has nothing to compare —
but the optimistic paint and the revert-on-failure have not been exercised against the running
app, only under vitest.
