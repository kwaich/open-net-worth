---
title: An import created duplicates
description: Stop duplicates at the import review, remove ones that already got in, undo a whole import, and tune how eagerly the app matches.
category: troubleshooting
order: 50
audience: [user]
status: stable
related: [features/importing, concepts/the-ledger, concepts/accounts-and-opening-balances, troubleshooting/net-worth-looks-wrong]
keywords: [duplicate, import, merge, link, leave out, statement, dedup, rollback, undo import]
---

# An import created duplicates

## What you'd see

The same purchase appears twice in an account — usually once from something you entered by hand
(or that Lucius logged from an email) and once from a statement you imported.

## Why it happens

Every import compares each statement row with what's already in your ledger — amount, date,
description and account — and suggests **Link** (it's one you already have), **Import** (it's
new) or **Leave out**. Duplicates get through when:

- **The two copies differ too much to match** — a different amount (a tip added later, a
  currency conversion, a pending charge that settled for a different amount) or a date several
  days apart.
- **One purchase is split differently** — your bank shows one charge as two lines, or two
  charges as one, so neither side matches your single entry.
- **A row marked Import should have been Link** during review.

## Prevent it: check the review before you confirm

When you import a statement, nothing is booked until you confirm:

1. Click the **Needs you** filter first — those are rows the app wasn't sure about, often a
   possible match with a different amount or date.
2. For a row you already have, choose **Link** (or **Same one — link**). Nothing new is booked;
   your existing transaction is linked to the statement and marked cleared.
3. If it linked to the wrong transaction, expand the row and use **Match with a different ledger
   entry**.
4. Check the **Not on statement** filter: it lists transactions in your ledger for that period
   that the statement doesn't have — often a hand-entered duplicate. You can mark them for
   deletion there; they're deleted only when you confirm.

See [Importing statements and receipts](../features/importing.md#review-the-rows).

## Fix it: remove a duplicate that's already booked

1. Open the account in **Accounts** and find the two copies.
2. Decide which to keep — usually the one linked to the statement (it's marked cleared).
3. Tick the extra one and click **Delete** in the bar that appears (or press <kbd>Delete</kbd>).

If the copies each hold something you want — a category on one, a receipt on the other — ask
Lucius to merge them instead: *"Merge these two Starbucks transactions on 3 March in Checking."*
He shows what the merged transaction will look like and waits for your approval.

## Fix it: undo a whole import

If an import went badly, you don't have to delete rows one by one. Ask Lucius:

> *Roll back the statement import I did today for Checking.*

He finds that import and shows you the transactions it will remove before anything happens.
Deleting a statement from the account's **Statements** list does **not** remove the
transactions it imported — only the rollback does.

## Make matching more or less eager

**Settings → Duplicate Detection → Auto-merge policy** decides how much the app merges on its
own:

| Policy | Behavior |
|---|---|
| **Conservative — ask on ambiguity** | Asks you about anything uncertain. Fewest wrong merges. |
| **Balanced — auto-merge transfers** (default) | Joins the two sides of a transfer automatically; asks about the rest. |
| **Aggressive — merge likely dupes** | Merges likely duplicates without asking. |

The same screen lists **Remembered decisions** — pairs you've already said are, or aren't, the
same — so you aren't asked twice.

## The Starting Balance can't be merged or deleted

An account's **Starting Balance** transaction is protected. Trying to merge or delete it gives
`the Starting Balance transaction can't be merged — change the account's opening balance
instead`. If a duplicate involves it, delete the *other* copy. If the starting amount itself is
wrong, open the Starting Balance row in the register and correct its amount or date. See [Accounts and opening balances](../concepts/accounts-and-opening-balances.md).

## How to confirm it's fixed

The transaction appears once in the register, and the account balance matches your bank.

## Related

- [Importing statements and receipts](../features/importing.md)
- [My net worth looks wrong](net-worth-looks-wrong.md)
- [The ledger and how transactions work](../concepts/the-ledger.md)
