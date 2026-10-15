---
title: Prices aren't updating
description: Why a holding or exchange rate looks stale, how often prices refresh, and the steps to get them moving again.
category: troubleshooting
order: 20
audience: [user]
status: stable
related: [connectors/price-sources, concepts/positions-lots-and-cost-basis, concepts/base-currency-and-fx, troubleshooting/net-worth-looks-wrong]
keywords: [price, stale, yahoo finance, coingecko, fx rate, exchange rate, quote, monitoring, cached]
---

# Prices aren't updating

## What you'd see

A holding's price — and the net worth built on it — hasn't moved though the market has, or the
top bar says **Prices: cached** instead of **Prices: live**.

## How often prices should change

While the app is running:

- A price you're looking at is at most about a minute old; it's fetched again after that.
- In the background, the app checks every holding and watchlist ticker every minute (you can
  change this under **Settings → Intelligence → Monitoring → Price check**).
- Stocks only move while their market is open. Outside trading hours you see the last trade —
  that's correct, not stale.
- **Delayed 15m** beside a ticker means the price itself comes from a delayed feed, as on
  XETRA and some other non-US exchanges — it trails the market by that much. US stocks come in
  real time, so they show no delay.

Prices come from Yahoo Finance and, for crypto, CoinGecko. If a fetch fails, the app keeps
showing the last good price rather than zero or a blank.

## Work through these in order

1. **Is the app running?** Nothing refreshes while it's stopped. If you only run it now and
   then, prices are as old as your last session until the first refresh. See
   [The app won't start](backend-wont-start.md) if it won't come up.
2. **Is the market open?** Check the exchange's hours before assuming a stock is stuck.
3. **Is background checking on?** Open **Settings → Intelligence → Monitoring**. If it says
   *Paused — no price checks, scans or alerts*, turn **Monitoring active** back on.
4. **Is the provider answering?** Open **Connectors**, click **Yahoo Finance** (or
   **CoinGecko**), then **⋯ → Edit configuration**. The status should be **Available**.
   **Cooling down** or **Recovering** means the provider asked the app to slow down — it retries
   by itself within minutes. **Unavailable** means recent calls failed; check your internet
   connection. See [Price sources](../connectors/price-sources.md#check-that-prices-are-flowing).
5. **Is it a coin Yahoo doesn't list?** Add a free CoinGecko key so the fallback has more room —
   see [Price sources](../connectors/price-sources.md#add-a-coingecko-api-key-optional).
6. **Is the symbol right?** A ticker on a non-US exchange usually needs its exchange suffix
   (for example `D05.SI`, `VOD.L`). See [the note below](#things-that-never-update-automatically)
   on re-linking a holding to the right symbol.
7. **Still stuck?** Look in the app log (`logs/app.log` in your working folder) for price fetch
   errors, then [report a problem](reporting-a-problem.md).

## Things that never update automatically

- **Property, vehicles and other items on the Assets screen** keep the value you last entered.
  Open the asset and click **Update value** to record a new one. See
  [Assets](../features/assets.md#record-a-new-value).
- **A holding with no market feed** (a private fund, an unlisted share) keeps the price it was
  created with, and there's no way to change that price by hand. Track something like this on
  the Assets screen instead, where you can record a new value whenever you like.

If a listed holding is stuck because it's linked to the wrong symbol, ask Lucius to re-link it:
*"VWRA should be priced from Yahoo symbol VWRA.L."* He checks the new symbol returns a price
before switching.

Physical gold, silver, platinum and palladium *are* live: they're valued at the current spot
price.

## An exchange rate looks wrong

Exchange rates come from the same source and refresh on the same schedule. Each rate is checked
against its reverse (for example USD→EUR against EUR→USD). If the two disagree, the app uses the
reverse quote instead; if there's nothing to check against and the rate suddenly jumps more than
tenfold, the app rejects it and keeps the previous rate. So a wildly wrong rate shouldn't reach
your totals.

A transaction in a foreign currency doesn't follow today's rate at all — its value is fixed when
you record it. See [Base currency and FX](../concepts/base-currency-and-fx.md#a-transactions-value-is-fixed-when-you-record-it).

## An exchange rate is missing

The app never guesses a rate. If a currency you hold has no rate yet, the figures that need it say
so instead of showing a made-up number:

- **Investments → Income** shows the payment in its own currency, marked *FX unavailable*, and
  leaves it out of the base-currency totals.
- **Risk** shows one *FX Mismatch* finding that names the missing currency, and pauses the
  concentration checks until the rate is back (a share of a partly valued book would overstate).
- **Lucius** is told your net worth is unavailable and which currency is missing, rather than
  being handed a zero.

Refreshing prices fetches the missing rate; everything above returns on its own.

## How to confirm it's fixed

The top bar shows **Prices: live**, and the price changes within a minute or two of the market
moving.

## Related

- [Price sources — Yahoo Finance & CoinGecko](../connectors/price-sources.md)
- [My net worth looks wrong](net-worth-looks-wrong.md)
- [Base currency and FX](../concepts/base-currency-and-fx.md)
