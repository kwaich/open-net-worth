---
title: Adding your first account and transactions
description: Add an account by hand on the Accounts screen, set its starting balance, and log your first transaction or transfer.
category: getting-started
order: 50
audience: [user]
status: stable
related: [concepts/accounts-and-opening-balances, concepts/the-ledger, concepts/categories, features/accounts, features/importing, getting-started/first-run]
keywords: [add account, starting balance, current balance, transaction, transfer, register, checking, credit card, account type]
---

# Adding your first account and transactions

The quickest way is to let Lucius do it during [your first run](first-run.md) — send him a
screenshot of your banking app and he adds the accounts. This page is for doing it by hand. Both
ways write to the same book.

## 1. Add an account

1. Open **Accounts** in the sidebar and click **Add account**.
2. **Name** — what you call it, for example "Everyday account".
3. **Account type** — click it to choose one of seven types (you can search, for example
   "401k", "mortgage" or "credit card"):

   | Type | What it's for |
   |---|---|
   | **Cash** | Bank, e-wallet, physical cash · spendable · in budget |
   | **Fixed deposit** | Time-locked cash · counts to net worth, not spendable |
   | **Investment** | Brokerage or crypto exchange · holdings + settlement cash |
   | **Retirement** | CPF, SRS, pension · locked until retirement |
   | **Receivable** | Money someone owes you |
   | **Credit** | Credit card, BNPL, line of credit · paid from cash · in budget |
   | **Loan** | Mortgage, car, personal, student, margin loan |

   A checking or savings account is **Cash**. The type decides how the account counts toward
   your net worth and whether it's in your budget.
4. **Currency** — the account's own currency. It doesn't have to match your base currency.
5. **Current balance** — what's in it today. For a **Credit** or **Loan** account the field is
   **Amount currently owed**: enter what you owe as a positive number, and it's tracked as a debt.
6. Click **Add account**.

To add optional details later — institution, last 4 digits, a note, and for cards and loans the
interest rate, credit limit and statement and payment days — open the account, click the
**More actions** menu (⋯) at the top, and choose **Edit account**.

A house, a car or anything else you own isn't an account — add it on the
[Assets](../features/assets.md) screen. Stocks and funds are added as holdings on the
**Investments** screen, inside an **Investment** account.

## 2. Your starting balance

The balance you entered becomes the account's **Starting Balance** — a transaction dated today.
Your ledger starts from it: transactions dated before it are left out, so you don't need years of
history. Most people just check today's balance in their banking app and start there.

Want to start earlier, for example on the first of the month, so you can import that month's
statement? Open the account, choose **More actions (⋯) → Edit account**, find **Starting balance**, click
**Open**, and change the amount and date to match the balance on that day.

You can edit the Starting Balance's amount and date, but you can't delete it or merge it with
another transaction — it's what the account's balance is built on. More in
[Accounts and opening balances](../concepts/accounts-and-opening-balances.md).

## 3. Log a transaction

1. Open the account and click **Add transaction**. A new row appears at the top of the register.
2. Fill in the **date**, the **payee**, an optional **category**, and the **amount**. The **−**
   button next to the amount means money out; click it to switch to **+** for money in.
3. Click **Save**, or **Save and add another** to keep going. Press **Enter** in the amount to
   save, **Esc** to cancel.

Worth knowing:

- **Category is optional.** A blank category shows as "Uncategorized". That's a valid state, not
  an error. See [Categories](../concepts/categories.md).
- **Picking a saved payee** fills in its usual category for you.
- **Another currency is fine.** Enter the amount in the account's currency; every report converts
  it into your base currency. See [Base currency and FX](../concepts/base-currency-and-fx.md).

## 4. Move money between your own accounts

Paying your credit card or moving savings is a **transfer**, not spending. In the **payee** field,
pick the other account from **Payments & transfers** at the top of the list. The row shows
"To/From …", and both accounts update together from one entry. Don't log it twice.

## 5. What happens next

Your account balance and net worth update the moment you save — there's nothing to recalculate.

Typing every transaction isn't the only way. You can also:

- **Import a statement** — click **Import from file** on an empty account, or see
  [Importing](../features/importing.md) for CSV, OFX, QIF, Excel and PDF.
- **Connect your bank** in **Connectors** — see [Bank and brokerage connectors](../connectors/bank-brokerage.md).
- **Log from email** automatically — see [Email](../connectors/email-ingestion.md).
- **Ask Lucius** — "I spent $12 on lunch at Café Rio with my Visa" is enough.

## Troubleshooting

- **"Name is required"** — every account needs a name.
- **"Enter a valid current balance."** — use a plain number like `1250.50`. Zero is fine.
- **A card balance shows as money you have** — check the account type is **Credit**, not Cash.

## Next: see a full month

[Your first month](your-first-month.md) walks through what a normal month looks like once your
accounts have real data in them.
