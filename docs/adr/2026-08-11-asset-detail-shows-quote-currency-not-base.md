---
title: Asset-detail headline price/chart/quote-stats show the instrument's quote currency, not base
status: Accepted
date: 2026-08-11
deciders: pairing agent (Claude) + repo owner (explicit request, same session)
---

# Asset-detail headline price/chart/quote-stats show quote currency, not base

## Context

This is the THIRD stance the codebase has taken on this exact question:

1. **ADR 0002 (2026-06-15):** crypto follows the *tapped holding's* cost currency — open the
   Binance row → a BTC/USD chart; open Gemini → BTC/SGD. Defaults to base only when opened
   without a holding context (search/watchlist).
2. **`2026-07-15-multi-position-read-surfaces.md`:** dropped the per-holding preference —
   "two holders of the same coin (or the same holder via two accounts) always see an
   identical chart" was the goal, achieved by always converting to the portfolio's BASE
   currency (falling back to quote only when CoinGecko doesn't serve that fiat).
3. **This ADR:** dropped the base-currency conversion too — always the instrument's own
   `quote_currency`. Never converted, never base, never per-holding.

The trigger was a live debugging session (this session) working through a chain of real
currency-accuracy bugs in the Investments tab and asset-detail page: a mislabeled CUR
column (`investments_book.py` reading `quote_currency` instead of `display_currency`), a
sub-cent crypto price silently rounding to `0.0` (`to_base`'s blanket 2dp rounding applied
to a per-unit price), and — during that investigation — the observation that "native
currency" isn't even well-defined at the asset-detail level: the SAME instrument (BTC) can
have TWO different `cost_currency` values across two positions (SGD @ Gemini, USD @
Binance) on the SAME user's own portfolio. Once you're already accepting "no single
holding currency," base-currency-always (stance 2) doesn't fully solve the "identical for
everyone" goal it was adopted for either — it's identical for one viewer, but a USD-based
viewer and an SGD-based viewer still see two different numbers for the same real BTC price.

## Decision

`_display_currency(inst)` — the ONE function `price_series` (chart), `_price_block`
(headline), and `quote_stats` all resolve through — collapses to:

```python
def _display_currency(inst: dict) -> str:
    return (inst.get("quote_currency") or "USD").upper()
```

No branch on `asset_class`, no `base_currency()` read, no `coingecko_supports_vs` check.
Equities and crypto now follow the identical rule (equities always did; crypto now matches).

**"What's this worth to me" stays answered separately, in base currency, by the
Position/YourPosition card** below the header — that job was never the header's alone, so
removing base-currency from the header doesn't remove the base-currency answer from the
page.

As a consequence:
- `_price_block`'s `_conv()` — the FX-conversion + significant-figure-rounding closure —
  is now dead code (its `currency == quote` early-return fires unconditionally) and was
  removed; the four call sites now use the raw live/stored value directly.
- `backend/core/fx.base_currency` import dropped from `asset_detail.py` (no remaining
  caller in this file).
- `prices.coingecko_supports_vs` / `CG_SUPPORTED_VS_FIAT` deleted — `_display_currency` was
  their only caller.
- The sub-cent-price regression tests (`test_price_block_sub_cent_crypto_price_survives_fx_
  conversion` et al., added earlier this same session for the `to_base`-rounds-to-zero bug)
  were rewritten: the scenario they guarded (a conversion branch running on a sub-cent
  price) can no longer occur at all, which is a strictly stronger guarantee than "survives
  conversion" — there is no conversion to survive.

## Consequences

### Positive
- Global consistency, not just per-viewer consistency: EVERY viewer, on every base
  currency, sees the identical number for the same coin — not just "identical within one
  user's own multi-account holdings" (stance 2's actual guarantee).
- No live-FX dependency for the headline number at all — removes an entire class of bug
  (missing-rate passthrough, stale-cache, the sub-cent-rounds-to-zero bug this session
  already fixed once) from the highest-visibility number on the page.
- Matches how every crypto price source (CoinGecko, exchanges, price alerts) actually
  reports a coin's price — directly comparable outside the app.
- Smaller surface: `_conv`, `coingecko_supports_vs`, `CG_SUPPORTED_VS_FIAT`, and the
  `base_currency` import are gone rather than left inert.

### Negative
- The headline number no longer reads as "in my money" at a glance — a SGD-based holder
  sees `$63,808`, not `S$81,632`, and must look to the Position card below for the
  base-currency figure. This is the real, deliberate trade-off (accuracy/consistency vs.
  at-a-glance home-currency legibility), not an oversight.
- Every crypto asset-detail page now shows `$` regardless of which account/currency any of
  the viewer's own positions were bought through — a change in what existing users will see
  on their very next page load, with no migration or opt-out.

### Follow-ups
- None planned. If at-a-glance home-currency legibility turns out to matter more than
  expected, the cheapest next step is a small secondary base-currency figure next to the
  header (using the already-computed `base_value` from the Position block), not reverting
  this decision.

## Alternatives considered

- **Keep base-currency-always (stance 2)** — rejected: doesn't achieve true
  viewer-independent consistency (a USD user and an SGD user still disagree), and keeps the
  live-FX dependency this session already had to patch once for the exact same code path.
- **Follow the tapped holding's currency again (stance 1)** — rejected: undefined once an
  instrument spans positions in different currencies (confirmed against this session's own
  real BTC data — Gemini SGD, Binance USD), and reintroduces the per-holding-preference
  plumbing stance 2 deliberately deleted as dead weight.
- **Show both quote and base side by side in the header** — not rejected, deferred: a
  reasonable future enhancement, but a bigger UI surface than what was asked for this turn;
  noted as the natural follow-up if needed.

## Agent provenance

- **Decided by:** repo owner, explicit request ("the chart, the header price and stuff,
  should show the true values of the quote price") after a design discussion in-session
  weighing quote vs. native vs. base, informed by discovering same-session that "native"
  isn't well-defined across a multi-currency-per-instrument portfolio.
- **Driving artifacts:** `backend/services/asset_detail.py` (`_display_currency`,
  `price_series`, `quote_stats`, `_price_block`), `backend/services/prices.py`
  (`coingecko_supports_vs` removed), `tests/test_crypto_chart_currency.py`,
  `tests/test_asset_detail_service.py`.
- **Why this way (one line):** quote price is the one number every viewer and every other
  price source already agrees on — stop fighting that and paying an FX-dependency tax for a
  "home currency" job the Position card already does correctly.
