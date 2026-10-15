---
title: My net worth looks wrong
description: The common reasons the net worth figure isn't what you expected, how to check each one, and how to fix it.
category: troubleshooting
order: 40
audience: [user]
status: stable
related: [concepts/net-worth, concepts/base-currency-and-fx, concepts/accounts-and-opening-balances, troubleshooting/prices-arent-updating, troubleshooting/import-created-duplicates]
keywords: [net worth, wrong, incorrect, discrepancy, cash on hand, balance, duplicate, transfer, account type]
---

# My net worth looks wrong

Work through these in order — one of the first few explains most cases.

## 1. You're comparing against a different number

- **Cash on hand** is only your Cash-type accounts. It leaves out fixed deposits, investments,
  retirement, property and debt, so it's always much smaller than net worth.
- The **Dashboard** headline includes retirement accounts and your assets (property, vehicles).
  If you're comparing with a figure that leaves those out, they won't match.

See [Net worth](../concepts/net-worth.md) for what each number includes.

## 2. One account's balance is off

Net worth is the sum of your accounts, so a single wrong account is the usual culprit.

1. Open **Accounts** and compare each account's balance with your bank or card app.
2. For the one that's off, open it and look at the **Starting Balance** — it should be what the
   account held on that date. To fix it, open that row and correct its amount or date. See
   [Accounts and opening balances](../concepts/accounts-and-opening-balances.md#the-starting-balance).
3. Then look for a missing, doubled or wrong-amount transaction since then. Ticking **Cleared**
   as you check rows against your statement makes the rest easy to spot.

## 3. Something is counted twice

- **A duplicate** — the same purchase entered by hand and imported from a statement. See
  [An import created duplicates](import-created-duplicates.md).
- **A transfer recorded as two transactions** — moving money from Checking to Savings as an
  expense in one and income in the other. Your net worth comes out right, but spending and
  income are both inflated. Delete one, and change the other's kind to **Transfer** (or pick
  **To/From <account>** as its payee). See [The ledger](../concepts/the-ledger.md#transfers-one-transaction-two-accounts).
- **A credit card payment recorded as an expense** — the purchases were already expenses; the
  payment is a transfer from your bank account to the card.

## 4. An account has the wrong type

A debt entered as a **Cash** account (or a savings account entered as **Credit**) is added
instead of subtracted. Check the type shown on each account. The type can't be changed after
creation — create a new account of the right type and move the transactions into it (select
them, then **Move to Account**). See
[the seven account types](../concepts/accounts-and-opening-balances.md#the-seven-account-types).

## 5. A price is stale

Holdings are valued at the latest price the app could fetch. If the top bar says **Prices:
cached**, or one holding looks stuck, see [Prices aren't updating](prices-arent-updating.md).

## 6. An asset's value is out of date

Property, vehicles and other items on the **Assets** screen don't update on their own — they
count at the newest value you recorded. Open the asset and click **Update value**. See
[Assets](../features/assets.md#record-a-new-value).

## 7. Unvested shares aren't included

Shares or RSUs that haven't vested are left out of net worth on purpose — they aren't yours yet.
They're shown separately as **Unvested**. See
[Net worth](../concepts/net-worth.md#whats-left-out-and-why).

## 8. A foreign-currency amount doesn't match today's rate

A foreign-currency **transaction** keeps the value it had when you recorded it; it isn't
re-converted every day. If you need the exact amount your card charged, open the transaction and
set **Value in <your base currency>**. Account **balances** and holdings, on the other hand, are
always shown at today's rate, so a foreign-currency account moves when exchange rates do. See
[Base currency and FX](../concepts/base-currency-and-fx.md#a-transactions-value-is-fixed-when-you-record-it).

## 9. You recently changed your base currency

Balances and transactions convert correctly. Amounts you typed in by hand — monthly income and
expenses, retirement spending, the low-balance threshold, liabilities you entered by hand,
category budgets — are re-read in the new currency without converting. Re-enter them. See
[Base currency and FX](../concepts/base-currency-and-fx.md#setting-your-base-currency).

## Still not right

Narrow it down to one account, then one transaction — that's much easier to fix than the total.
Lucius can help: *"Which of my accounts changed the most this month, and why?"* or *"Compare my
Checking balance with this statement"* (attach it). If the numbers still don't add up,
[report a problem](reporting-a-problem.md).

## Related

- [Net worth](../concepts/net-worth.md)
- [Base currency and FX](../concepts/base-currency-and-fx.md)
- [An import created duplicates](import-created-duplicates.md)
- [Prices aren't updating](prices-arent-updating.md)
