---
title: Reconcile the ledger against each statement's printed closing balance
status: Accepted
date: 2026-08-05
deciders: Claude (senior-engineer lane) + owner
---

## Context

An error in a card's opening balance survived three weeks of imports, a
duplicate-review queue, two data migrations and a duplicate write-block. It was found only when
the owner compared a statement by hand.

Nothing in the system asked the one question the bank had already answered. Worse, the check that
looks like it should have caught it **structurally cannot**: `verify_statement_balance` compares
the imported batch's *net change* against the statement's own opening→closing delta, and its own
docstring states the property that makes it blind — *"This does NOT touch full account history —
it only looks at what was just imported."* When the opening is wrong, every import's net change
is still perfectly correct. So is every per-row duplicate rule: no single row was wrong.

The missing check is on the **absolute** balance, and it catches a whole class at once — a
mis-derived opening, a real charge deleted long ago, a double-booked payment, a bad merge — because
all of them surface identically: the ledger's balance on the close date ≠ the printed one.

## Decision

Add `services/statement_drift.statement_drift()`, plus `GET /api/statements/drift` and the
read-only `check_statement_drift` tool. For every statement carrying a printed `closing_balance`
and a `statement_date`, compare it to `account_balance_as_of(close_date)`.

It answers three questions in order, because each narrows the next — and the ordering is the
design, not decoration:

1. **Is it an error at all?** A charge dated on the cutoff day that the bank posts *after* it puts
   the ledger legitimately ahead. This is not hypothetical: on a representative book one
   close was off by **exactly** the single row dated on its own cutoff day, and reconciled to
   0.00 at the next close once the bank posted it — which is the evidence that makes it lag
   rather than loss. Such drift *clears at the next close*, so only drift that persists counts
   (`resolved_at_next_close`). Without this the check fires on nearly every card that has a charge
   near its cutoff — and a check that cries wolf is one nobody reads, which would reproduce the
   original failure with extra steps.
2. **Was it there from the start?** The same drift at every unreconciled close — *including the
   earliest one checked* — means the opening (`constant_drift`).
3. **Otherwise, where?** `drift_change` + `window_from` name the window between two closes that
   introduced it: a searchable date range instead of a whole account.

**It never edits and never guesses which side is right.** It reports a disagreement; the owner
decides. On the newest statement, lag and a missing row are genuinely indistinguishable, so the
result says `pending` rather than picking one.

## Consequences

**Positive.** The error class that cost three weeks is now detectable in one call, and the two
diagnostics turn "something is wrong somewhere" into either "the opening" or a date range. It is
pure computation over existing data — no new store, no migration, no write.

**Negative / limits.**
- **Only as good as `import_meta`.** Statements without a printed closing balance are skipped and
  counted — on the owner's book today that is the large majority of records, leaving only a
  handful checkable. The number is reported rather than hidden, but the coverage is genuinely thin.
- **Timing is inferred, not read.** We store transaction dates, not posting dates, so
  "resolved at the next close" is the only available evidence that a drift was lag. A real error
  that happens to be cancelled out by an equal, opposite later error would be misread as resolved.
- **A cross-currency statement is skipped, not converted.** Legs are stored in the account's
  currency; subtracting a differently-denominated closing balance would produce a large, confident,
  meaningless number.
- Not yet surfaced in the UI — API + tool only.

**Follow-up — and a caution the first run earned.** On the owner's book the check flagged a
card at both its closes. Forensics showed neither was a missing transaction: both statements
reconcile to the cent and every statement row was present. One close was fully accounted for by
a hand-entered `Balance adjustment` plug; the other by cutoff timing on both sides of the window.
Only an inherited discrepancy predating the earliest statement held survived.

The lesson is about EVIDENCE, not arithmetic. A drift that equals the sum of some nearby rows is
not thereby explained by them — on this book one such match was exact to the cent and still
wrong, because the real cause was the plug. What distinguishes lag from loss is that lag
*reconciles at the next close*. `resolved_at_next_close` is the only claim this module makes, and
that is deliberate.

## Alternatives considered

- **Extend `verify_statement_balance`.** Rejected: its net-change scoping is deliberate and
  correct — it is what makes it work on accounts with pre-ledger history. Folding an absolute-balance
  check into it would break that property and give one tool two incompatible definitions of "ok".
- **Run it automatically at import commit and block on drift.** Rejected: the newest statement is
  precisely the case where lag and error are indistinguishable, so this would block good imports on
  a coin flip. It is a diagnostic the owner (or Lucius) runs.
- **Report raw drift with no cause analysis.** Rejected — that is the column the owner already had
  to eyeball by hand, and eyeballing it is what took three weeks.
- **Treat drift near the cutoff as timing by matching amounts.** Rejected as too clever: it is a
  subset-sum over candidate rows, and a wrong "explained" verdict is worse than an honest
  "undecided".

## Agent provenance

- **Decided by:** Claude (senior-engineer lane), owner AFK, directing an end-to-end run
  (`/goal` step 5).
- **Alternatives weighed:** see above; the timing-vs-error distinction was added *after* the first
  implementation reported a clean card as drifting, and checking the real rows showed the drift
  was exactly one cutoff-day charge. The `constant_drift` guard requiring the earliest close to be drifting was
  added after a test showed the first implementation would blame the opening even when an earlier
  close had reconciled — i.e. would send the owner to fix the one number already proven correct.
- **Why this way:** the check exists because a per-row rule structurally cannot catch a wrong
  opening. Making it trustworthy on the first real run mattered more than making it loud: it must
  be quiet enough that a red row means something.
