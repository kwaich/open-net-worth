---
title: Accounts and opening balances
description: The seven account types and what each one means for your budget and net worth, and how an account's Starting Balance works.
category: concepts
order: 20
audience: [user]
status: stable
related: [concepts/the-ledger, concepts/net-worth, features/accounts, getting-started/your-first-account-and-transactions]
keywords: [account, account type, starting balance, opening balance, current balance, delete account, archive]
---

# Accounts and opening balances

## The seven account types

Every account has exactly one type. The type is the only setting that decides how the account
counts — whether it's spendable cash, whether it's part of your budget, and where it appears in
your net worth.

| Type | For | Spendable cash? | In your budget? |
|---|---|---|---|
| **Cash** | Bank accounts, e-wallets, physical cash | Yes | Yes |
| **Fixed deposit** | Time deposits, CDs — cash locked for a term | No | No |
| **Investment** | A brokerage or crypto exchange: your holdings plus the cash inside it | No | No |
| **Retirement** | Pension, 401(k), IRA, CPF, SRS — locked until retirement | No | No |
| **Receivable** | Money someone owes you | No | No |
| **Credit** | Credit cards, buy-now-pay-later, lines of credit | No (it's debt) | Yes |
| **Loan** | Mortgage, car, student, personal or margin loans | No (it's debt) | No |

Pick the type that matches what the account really is. The type and the currency are set when
you create the account and can't be changed afterwards — to fix a wrong one, create a new account
and move the transactions. See [Net worth](net-worth.md) for how each type adds up.

## Adding an account

Click **Add account** on the Accounts screen, then fill in:

1. **Name** — whatever you call it, e.g. "Everyday account".
2. **Account type** — one of the seven above.
3. **Currency** — the currency the account is held in.
4. **Current balance** — what it holds **today**, as your bank shows it. For a Credit or Loan
   account the field is **Amount currently owed**. Zero is fine.

Click **Add account**. Step-by-step: [Adding your first account](../getting-started/your-first-account-and-transactions.md).

## The Starting Balance

The balance you enter isn't stored as a number on the account. It becomes the account's first
transaction — the **Starting Balance** — dated today. Everything after it is ordinary activity,
so the account's balance is always "Starting Balance plus everything since".

That's why you don't need years of history. Most people enter today's real balance and record
from there. If you want earlier history, import older statements and move the Starting
Balance's date back to before them.

**To change it:** open the account in **Accounts**, find the Starting Balance row (it shows a
"Balance adjustment" badge), and edit its **amount** or **date**. You can also reach it from
**Edit account**, which shows the Starting Balance and its date with an **Open** link.

What you can't do, because the account's whole history depends on it:

- delete it on its own (the Delete button is disabled),
- merge it with another transaction, from either side,
- untick **Cleared**,
- move it to another account or turn it into a transfer,
- set a category, payee or anything other than its amount and date.

If you try through Lucius or an import, the change is refused with a message such as *"the
Starting Balance transaction can't be merged"*.

## Archiving and deleting an account

From the account's **⋯** menu:

- **Archive account** hides the account but keeps its transactions. Use this for a closed
  account whose history you want to keep. You can find archived accounts again, and remove them
  for good with **Delete permanently**.
- **Delete account** removes it permanently.
  - An account with no transactions besides its Starting Balance is deleted straight away.
  - An account with transactions asks a second time — **Delete account and its
    transactions?** — and names how many will go, including transfers that also touch other
    accounts. **Force delete** removes them all. This can't be undone.
  - An account that still holds investments, open lots or option contracts, or is linked to an
    asset as its loan, can't be deleted until you close, move or unlink those. The message tells
    you which.

## Related

- [The ledger and how transactions work](the-ledger.md)
- [Net worth](net-worth.md)
- [Accounts](../features/accounts.md)
- [Adding your first account and transactions](../getting-started/your-first-account-and-transactions.md)
