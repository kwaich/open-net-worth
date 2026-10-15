---
name: catch-up
description: "Use this skill when the user wants their books brought up to date — new transactions in, duplicates merged, unsorted transactions labelled, balances checked against the bank, and the budget settled before the month rolls. Use it for 'catch me up', 'tidy my books', a month-end close, or 'I've been away for a few weeks', even if they never say 'reconcile'. Not for importing one statement on its own (statement-import), not for how the month went (monthly-report), not for changing budget targets (budget-review)."
---

Bring the books up to date in one pass. This skill fixes the data; it does not analyse it —
once the books are clean, offer a report instead of writing one.

## Gotchas

- A statement is never hand-entered. If the user brings one, activate the statement-import skill
  and follow it, then return here.
- check_statement_drift's `timing_only` is normal posting lag and `pending` is undecided until the
  next statement — neither is a problem to report. Only real drift is.
- A card can read near zero while last month's statement is unpaid. Card status comes from
  get_card_statement_status, never from a balance.
- Covering an overspent envelope COSTS Ready to Assign, up to the size of the hole. Say the
  amount before offering it. This applies to Zero-Based only — the system prompt's Budget method
  line says which method the user runs.
- A match review with `recommended_action: accept` is at the engine's strong band; dismissing
  one leaves a visible duplicate. When in doubt, accept.
- Propose every write in batches and ask before making it; never apply what the user has not
  approved. Amounts are in the base currency the tools report.

## Checklist

Load the tools first, in one call: list_match_reviews, resolve_match_reviews, find_duplicates, merge_transactions, get_uncategorized_summary, get_category_suggestions, get_rule_suggestions, check_statement_drift, get_card_statement_status, reconcile_accounts, get_budget_month, auto_assign.

- [ ] **New transactions** — a statement goes through statement-import. Do not fetch from
      connectors unasked.
- [ ] **Duplicates** — list_match_reviews, settle each with resolve_match_reviews once the user
      agrees. Then find_duplicates over the last 60 days (or the user's period) and confirm each
      pair before merge_transactions.
- [ ] **Uncategorized** — get_uncategorized_summary, then get_category_suggestions. Present the
      suggestions grouped by category; apply the approved ones with update_transactions and ask
      about the rest. A payee get_rule_suggestions says the user keeps correcting gets an offer
      of a rule (create_automation).
- [ ] **Balances** — check_statement_drift and get_card_statement_status. For an account that
      looks off, ask what the bank shows, run reconcile_accounts with dry_run=true, and apply
      only on approval.
- [ ] **Budget** (Zero-Based) — get_budget_month: overspent envelopes and unsorted cash still
      held. Before the month rolls, offer auto_assign(mode="cover_overspending").

Skip a step with nothing to do in one line — a clean book is the common, good outcome.

## Close

```
Books are current to <date>.
Fixed: <n> duplicates merged · <n> transactions labelled · <account> reconciled
Still open: <item> — <why, and what would close it>
```

Then offer the monthly-report if a month just ended, otherwise the weekly-report.
