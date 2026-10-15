---
title: Base currency and how foreign amounts are valued
description: How totals are shown in one base currency, why a foreign transaction's value is fixed when you record it, and what changing your base currency does.
category: concepts
order: 50
audience: [user]
status: stable
related: [concepts/the-ledger, concepts/net-worth, getting-started/first-run, troubleshooting/prices-arent-updating]
keywords: [base currency, fx, exchange rate, historical rate, conversion, multi-currency, value in]
---

# Base currency and how foreign amounts are valued

Every total, report and net worth figure is shown in one currency — your **base currency**.
Each account keeps its own currency (a USD brokerage, a EUR card, an SGD bank account), and the
app converts to your base currency whenever it adds things up.

## Setting your base currency

You choose it during first run. To change it later:

1. Open **Settings → Currency & format**.
2. Under **Base currency**, pick the new currency.
3. A dialog, **Switch base currency to …?**, lists any amounts you typed in by hand (see below).
   Click **Switch to …** to confirm.

**What converts and what doesn't.** Account balances, transactions and holdings carry their own
currency, so they convert correctly. Amounts you typed in yourself don't — the same number is
simply read in the new currency. Switching from USD to EUR turns "$5,000 monthly expenses" into
"€5,000", not its euro equivalent. The dialog lists exactly which of these you have:

- monthly net income and monthly expenses (on your **Profile**)
- the low-balance alert threshold (on your **Profile**)
- monthly retirement spending (**Plan → FIRE Settings**)
- liability balances and monthly payments you entered by hand
- category budgets

Re-enter those afterwards. If you have none, the dialog says so.

## A transaction's value is fixed when you record it

When you record a transaction in a foreign currency, its value in your base currency is
**worked out once, when you save it**, using the exchange rate at that moment — and then kept.
It doesn't move every time exchange rates change.

Why: a transaction is something that already happened. If last month's €4.50 coffee were
re-valued every day, your past spending would keep changing on its own — a month that cost
$2,000 could show as $2,050 next week with nothing you did. Fixing the value keeps your history
steady.

Two things to know:

- **The rate used is the one when you saved it**, not the rate on the transaction's date. For
  something you enter the same day, that's the same thing. For a transaction you enter or
  import weeks later, it's the later rate.
- **You can set the exact value yourself.** Open the transaction and enter **Value in <your base
  currency>** — for example, the amount your card actually charged after conversion. It stays
  as you entered it. Change the transaction's own amount and the value is worked out again.

**Changing your base currency** is the one time past values are recalculated: every
foreign-currency transaction is re-expressed in the new base currency at **current** rates.

Account balances and holdings are always shown at the current rate — they're what you have
now, not past events.

## Where exchange rates come from

Rates are fetched automatically from Yahoo Finance, on the same schedule as prices — you never
enter or maintain them. If a rate can't be fetched, the last good one is kept until a fresh
one arrives. Each rate is also checked against its reverse (USD→EUR against EUR→USD) so a
provider glitch can't suddenly make a currency look a thousand times bigger. See
[Prices aren't updating](../troubleshooting/prices-arent-updating.md#an-exchange-rate-looks-wrong).

## Related

- [The ledger and how transactions work](the-ledger.md)
- [Net worth](net-worth.md)
- [My net worth looks wrong](../troubleshooting/net-worth-looks-wrong.md)
