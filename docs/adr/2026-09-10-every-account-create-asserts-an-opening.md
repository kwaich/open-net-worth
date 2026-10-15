---
title: Every account create asserts an opening
status: Accepted
date: 2026-09-10
deciders: orchestrating Codex agent + repo owner
---

# Every account create asserts an opening

## Context

Account creation previously treated an omitted or zero `initial_balance` as “no tracking start.”
That made a new empty account structurally different from every other newly created account and
allowed REST, service, and Lucius callers to create an account without stating what it held.
Creation also relied on compensating deletes across separately committed writes, leaving a window
where the account, Starting Balance transaction, and `opening_txn_id` pointer could disagree.

## Decision

We will require `initial_balance` on every account-create contract, including REST, the account
service, and Lucius's `create_account` tool. Zero is valid and means “this account held zero when
tracking started”; it creates a real zero-valued Starting Balance transaction dated today unless an
explicit date is supplied. The account row, opening transaction, pointer, and transaction audit are
written through one `locked_unit_of_work` database transaction.

## Consequences

### Positive

- Every newly created account has one explicit, editable tracking-start fact.
- A failure at any point leaves neither a partial account nor an orphaned opening transaction.
- Human and Lucius creation paths enforce the same contract.

### Negative

- Existing API/tool callers must now send `initial_balance`, using `0` when the account is empty.
- A zero-balance account now has one additional ledger row and a tracking-start cutoff at creation.

### Follow-ups

- Keep contract tests for omitted, zero, non-zero, and failed atomic writes.
- Keep generated Lucius tool schemas pinned with `initial_balance` in `required`.

## Alternatives considered

- Keep omission equivalent to zero — rejected because absence cannot distinguish an intentional
  empty opening from a caller that forgot a required financial fact.
- Mint openings only for non-zero values — rejected because zero is still an asserted balance and
  should have the same editable date and transaction semantics.
- Retain compensating cleanup — rejected because cleanup after separately committed writes cannot
  provide the all-or-nothing guarantee of one database transaction.

## Agent provenance

- **Decided by:** orchestrating Codex agent with the repo owner
- **Lane / branch:** root lane on `main`
- **Alternatives weighed:** optional/implicit zero, non-zero-only openings, compensating cleanup
- **Driving artifacts:** owner account-modal screenshots and `tests/test_starting_balance.py`
- **Why this way (one line):** A required explicit opening makes zero unambiguous and keeps the ledger structurally atomic.
