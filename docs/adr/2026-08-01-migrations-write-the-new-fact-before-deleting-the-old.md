# 2026-08-01 — A migration writes the new fact BEFORE deleting the old one

**Status:** accepted · **AMENDS** ADR `2026-07-31-opening-balance-is-one-fact` (its
"net worth is unchanged at every point of the history" claim was wrong by one day — see below)
**Where:** `backend/scripts/migrate_opening_txn_pointer.py`
**Tests:** `tests/test_opening_txn_pointer.py` — `test_a_crash_mid_apply_is_resumable`
(6 kill points), `test_a_crash_in_the_mint_loop_keeps_the_cutoff_it_has_not_used_yet`,
`test_it_refuses_a_book_whose_opening_was_already_lost`,
`test_phase_b_refuses_to_strip_a_cutoff_off_an_unpointed_account`,
`test_it_refuses_when_another_process_writes_the_book_mid_run`, plus the three history tests

## Context

The opening-pointer migration was one `_write` of three unrollbackable phases —
`transactions.replace_all` → `accounts.replace_all` → a loop of `set_account_opening` mints —
inside a `main()` with no `try`. A security review killed it at each phase and found two
silent-corruption paths:

1. **Killed inside the mint loop:** the accounts rewrite had already dropped
   `initial_balance_date` and written `opening_txn_id: null`, which the re-run's `already` test
   read as *"this account never had an opening"*. The re-run printed `Nothing to do`, exited 0,
   and the account's balance had moved by its whole opening. The balance check could not see it
   — it compared post-crash state against post-crash state.
2. **Killed between the two rewrites:** the rows had lost their legacy markers but the accounts
   still looked pre-migration, so every adopted account was reclassified to "mint zero row" —
   producing an account pointing at a NEW zero row while the real Starting Balance became an
   ordinary unpointed `adjustment` (deletable, sweepable, mergeable) and the API reported
   `initial_balance: 0.00`. Balance-neutral, so the check passed.

Both have the same root cause: **the old fact was deleted before the new one was written**, so
a partial state was indistinguishable from a finished one.

## Decision

**Two independently idempotent phases, in this order.**

- **Phase A** mints/adopts the rows, re-dates what must move, and points every account at its
  row — while KEEPING `initial_balance_date`. Everything the classifier needs is still on disk
  while it runs, so any crash is simply resumed. A mint killed between its row write and its
  pointer write is *adopted* next time (`_lookalike_openings`: a live, unpointed, unmarked,
  ZERO-amount "Starting Balance" adjustment dated at the cutoff is exactly what this script
  leaves behind, and nothing else), never minted twice.
- **Phase B** strips the legacy account keys, the row markers, and the tombstones — all
  derivable from the pointers alone — and REFUSES to strip a cutoff off an account whose
  pointer is still null, even if handed a stale plan.

That ordering is what makes "null pointer + no cutoff" mean *no opening* and nothing else. Two
detectors back it up: a **dangling pointer** refuses (a migration must not guess an amount), and
a **null pointer with no cutoff beside an unpointed Starting Balance row** refuses (the tracking
start was lost by hand — it is not knowable).

**The book must be exclusively ours.** `main()` reads both stores once and later rewrites them
wholesale, so anything written in between is destroyed with no error and no trace. Both files
are hashed at read time and re-hashed immediately before each phase (`_ExclusiveFiles`), and a
run targeting the live tree refuses while something is listening on `:8080`.

**The today-only balance check is not enough.** Re-dating an opening row moves the account's
balance for the whole window between the two dates — its net-worth HISTORY — while today's
balance is identical either way. Every such window is now priced per account and printed, and
`--apply` refuses until the owner passes `--accept-history-shift`.

## What the history check found

The previous ADR asserted the two re-dated accounts "cancel exactly, so net worth is unchanged
at every point of the history". They do not. Their opening rows carried *different* inferred
dates (the apartment 2026-02-07, its installment liability 2026-02-06), so on **2026-02-06** the
liability was counted and the asset was not. On a representative book, day by day:

```
2026-02-06   OLD  -500,000.00 SGD   NEW  0.00 SGD   delta +500,000.00
(every other day in the window: identical — the ±₫6,000,000,000 legs cancel)
```

The migration *removes* a spurious one-day −SGD 500k dip. That is the right outcome, but it is
the owner's history, and it was being reported as "identical". A claim that only holds for
today must not be printed as if it held for all time.

## Alternatives rejected

- **A transaction/journal file.** Real crash-safety, real cost — for a one-shot script on two
  JSON files, phase ordering plus a backup buys the same guarantee.
- **Refuse on any historical shift.** The shift is the intended repair (the user's asserted
  date beats an inferred one). Refusing outright would make the migration unrunnable; a printed
  number and an explicit flag put the decision where it belongs.
- **Detect the corrupted state instead of preventing it.** For the mint-loop crash it is
  undetectable in principle: an account with no cutoff, no pointer and no row is
  byte-identical to an account that never had an opening. Only the ordering fixes it.

## Agent provenance

Fixed by a Claude agent (senior-engineer lane, branch `fix/starting-balance-one-place`) from a
security review that reproduced both crash paths. The reviewer asked for the `already` test at
line 122 to be tightened; the agent's judgement was that no test on that state can be correct —
the two shapes are identical on disk — so the fix is the phase order, and the tightened checks
(dangling pointer, lost cutoff, phase-B precondition) are the belt beside those braces. The
crash contract is pinned by six parametrized kill points and was rehearsed against a copy of
a representative book at four of them.
