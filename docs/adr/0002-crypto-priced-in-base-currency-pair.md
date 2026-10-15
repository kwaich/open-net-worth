---
number: 0002
title: Show crypto in the holder's base currency as a pair; source it natively from CoinGecko
status: Accepted
date: 2026-06-15
deciders: pairing agent (Claude) + repo owner
---

# 0002. Show crypto in the holder's base currency as a pair

## Context

An instrument carries one `quote_currency` (the currency its market price is denominated in).
For crypto this is conventionally USD — but that is just the reference the data source quotes,
not how a holder thinks about a coin they bought in their home currency. A Singapore holder who
bought BTC on Gemini paying SGD wants to see **BTC/SGD**, not a USD line.

Two concrete problems surfaced:

- **A valuation bug** (fixed first, see the diff): the holdings table converted *both* market
  value and cost basis through a single `currency`. When `quote_currency` (USD) differed from
  `cost_currency` (SGD), one leg was double-FX'd, so a BTC holding read **−19%** when it was
  actually **+3%**. Backend `ledger.row_base_value` already valued market via `quote_currency`;
  the frontend table didn't. Fixed by valuing each leg in its own currency, P&L from base legs.
- **A display question** (this ADR): once cost was correctly SGD, the summary row showed `USD`
  while the lots showed `SGD` — inconsistent — and the asset-detail chart was a USD line.

A correct **BTC/SGD chart** looks like it needs historical FX (you can't rescale a USD series by
*today's* rate). But crypto here is already sourced from **CoinGecko**, whose `/market_chart` and
`/simple/price` accept `vs_currency=<fiat>` natively — so the per-fiat pair is fetched directly,
historically accurate, no historical-FX store required.

## Decision

The dividing line is **whose data is it**:

1. **The holder's data follows the holder's currency.** Position value, cost, P&L (base columns)
   and the native Price/Cost columns are shown in the holder's currency. For crypto specifically,
   that is **the currency you transacted in (the holding's cost currency)**, labelled as a
   **pair** — `BTC/USD` for a coin bought on Binance, `BTC/SGD` for one bought on Gemini. (Not a
   single global base: a holder buys on different exchanges in different currencies.)
   Equities/commodities keep their real listing (quote) currency — a US stock is genuinely USD-priced.
2. **The asset's data is canonical / shared.** News, key issues, facts, and peers are about the
   asset and are identical for every holder — never re-quoted per currency.
3. **Crypto price + chart are sourced natively from CoinGecko in the currency of the holding you
   opened the view from** (`vs=<that holding's cost currency>`, defaulting to base when opened
   without a holding) when CoinGecko supports that fiat, falling back to the quote currency
   otherwise. The per-instrument detail view can't show two currencies at once, so it **follows
   the tapped holding** — tap the Binance row → BTC/USD chart; tap Gemini → BTC/SGD. One backend
   rule (`asset_detail._display_currency(inst, prefer)`) governs both the chart series and the
   headline price so they can never disagree.
4. **Valuation is unchanged** (deferred): crypto is still *valued* via the existing `quote × FX`
   path shared with net worth/reports. Sourcing the live crypto price natively in base (removing FX
   for crypto entirely) is a larger, separate change and was explicitly deferred.

## Consequences

### Positive
- The phantom −19% loss is gone; foreign-quoted holdings value correctly.
- Summary row, lots, chart, and headline all agree in the holder's currency for crypto.
- The BTC/SGD chart is real (CoinGecko per-fiat), not an FX-rescaled USD line.

### Negative / known edges
- The holdings-table crypto price uses `quote × FX` while the chart uses CoinGecko's native pair —
  they can differ by a hair (different FX source). Accepted (valuation deferred).
- The detail view is per-instrument but follows the *tapped* holding's currency. Opening the same
  coin from a USD holding vs an SGD holding yields different charts (by design); opened from
  search/watchlist (no holding) it defaults to base.
- `CG_SUPPORTED_VS_FIAT` is a hand-maintained subset; an unlisted currency falls back to the quote.
- FastAPI route params must be `Optional[str]`, not `str | None` — on Python 3.9 FastAPI evaluates
  the annotation at runtime and PEP-604 unions raise at import. (Service-layer `str | None` is fine.)

### Follow-ups
- Optionally source the live crypto price natively in base from CoinGecko and skip FX for crypto
  end-to-end (would also let the table drop `quote × FX` for coins).
- The `fetch_history` cache key now includes `vs` (a USD and an SGD fetch must not collide).

## Alternatives considered

- **Per-currency price pairs as new instruments (BTC.SGD)** — rejected: `quote_currency` is a
  property of the market, not the holder; baking a holder's currency into a shared instrument
  breaks the model, and FX/CoinGecko already derive the pair.
- **FX-convert the USD chart by today's rate** — rejected: historically inaccurate (distorts the
  real pair path).
- **Leave everything in USD** — rejected: it's the bug the owner reported.

## Agent provenance

- **Decided by:** the pairing Claude agent, with the repo owner steering the product call.
- **Lane / branch:** `chore/agent-ready-cleanup`.
- **Alternatives weighed:** new pair-instruments vs. today's-FX chart vs. native CoinGecko per-fiat
  sourcing (chosen) + base-currency display, with crypto *valuation* deferred.
- **Driving artifacts:** `backend/services/asset_detail.py` (`_display_currency`, `price_series`,
  `_price_block`), `backend/services/prices.py` (`coingecko_supports_vs`, cache key),
  `frontend/src/hooks/usePortfolio.ts` (`_disp_ccy`/`_disp_unit_price`/`_disp_unit_cost`).
- **Why this way (one line):** show the holder their own currency from a source that already serves
  it natively, while keeping the shared asset data canonical.
