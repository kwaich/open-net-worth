---
title: An Automation amount is in its account's currency
status: Accepted
date: 2026-10-11
deciders: Repo owner, Claude
---

# An Automation amount is in its account's currency

## Context

A transaction trigger's amount filter (`over`, `min_amount`, `max_amount`) with no `currency` of its
own compared the transaction's **base-currency** amount — even when the trigger was scoped to one
account. "A charge over 500 on my USD card" therefore meant 500 in the base currency, not USD 500.
The editor and the card hid this behind a hard-coded "SGD" in the label (wrong for every non-SGD
book), and the balance trigger's label said "SGD" too, though a balance threshold was always compared
in the account's own currency. A test pinned the base-currency rule without recording why.

## Decision

- A transaction amount on **one account** is compared in **that account's currency** (the
  transaction's own leg on it).
- On **any account**, it is compared in the **base currency** — the one unit every account shares.
- A trigger that names a `currency` keeps comparing in that currency.
- A **balance** threshold stays in its account's currency (unchanged).
- The editor shows that currency beside every amount box, and the card label names it; nothing in
  Automations hard-codes a currency (`triggerAmountCurrency` in `automationTriggerPredicates.ts`,
  `services/automations/events._amount`).

## Consequences

- The amount a user types means what the box beside it says.
- An existing account-scoped automation on a non-base-currency account now compares a different
  number — the one its owner meant. Base-currency accounts are unaffected.
- `tests/test_automation_event_amount_matching.py` flips to the new rule;
  `tests/test_automation_txn_conditions.py` pins both cases.

## Amendment (2026-10-11) — several accounts

"Account is" conditions can now name several accounts (a list is any-of; two separate "Account
is" conditions match a transfer between them). "On one account" means exactly one distinct account
named across every "Account is" condition. Any of several, or two of them, share no currency but the
base, so the amount is compared in the **base currency** — before, the first "Account is" condition
silently decided (`txn_conditions._amount_account`, `txnConditions.conditionAccount`).
