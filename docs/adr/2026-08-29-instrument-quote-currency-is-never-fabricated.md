---
title: Instrument quote_currency is fetched or refused — never fabricated; a pin fetches; update can re-bind valuation fields
status: Accepted
date: 2026-08-29
deciders: repo owner (fail-closed rule, pin-still-fetches rule, resolve/update split) + Claude agent (implementation)
---

# Instrument quote_currency is fetched or refused — never fabricated; a pin fetches; update can re-bind valuation fields

## Context

Owner incident 2026-08-28: a EUR-quoted XETRA position (Kestrel Dash, `KSTL.DE`) was
resolved with an explicit pin (`price_source='yahoo', price_source_id='KSTL.DE'`) and
shipped with `quote_currency: "USD"` — so every valuation of the position was silently
wrong by the EUR/USD rate. Three defects compounded, and together they left the bad
row with NO correction path:

1. **The pin branch skipped every fetch.** `resolve_instrument` treated an explicit
   `price_source`+`price_source_id` as "caller knows everything" and minted straight
   from the pin — no price, no currency, no name was ever read from the source.
2. **The store fabricated the missing fact.** `InstrumentStore._make_instrument`
   defaulted a missing `quote_currency` to `or "USD"`. The resolver's `_resolve_yahoo`
   had the same `or "USD"` fallback. Both are guesses dressed as data.
3. **The loop couldn't be broken.** Re-resolving hit the identity-key dedupe and
   returned the same broken row forever; `update_instrument` deliberately refused all
   valuation fields (an earlier decision: "valuation fields are not updatable at all").
   Resolve was get-only, update was metadata-only — the one fact that was wrong had no
   editor.

## Decision

**Fail-closed: no code path may ever fabricate a quote currency.** The currency an
instrument is priced in is a FACT about its price source, so it is fetched from the
source or the call is refused:

1. `_resolve_yahoo` returns `None` when the fetch yields a price but no currency —
   "unresolved" is the only safe answer, never "USD".
2. `InstrumentStore._make_instrument` raises `ValidationError` on a missing
   `quote_currency` (and writes nothing). The one exception is coingecko → USD, which
   runs BEFORE the refuse: the CoinGecko API prices vs=usd, so USD there is
   definitional, not a guess.
3. A manual instrument (no feed exists) requires an explicit `quote_currency` param at
   resolve — the only source of truth is the caller, and refusing is the only honest
   answer. `resolve_instrument` validates it as a 3-letter ISO code.

**A pin is authoritative for IDENTITY only, never for FACTS.** An explicit
`(price_source, price_source_id)` picks the feed entry — it does not carry the entry's
facts. `_pin_facts()` fetches them: yahoo → a live fetch; coingecko → USD by
definition; manual → the explicit param. Consequences at the two call sites:

- **Existing row:** refreshed with what its source currently reports
  (`_refresh_pinned_facts` — quote_currency always; name only when stored is a
  placeholder; identifiers fill gaps without clobbering). An unconfirmable fetch
  never *blocks* an existing row — it stays as-is; only a mint is refused. This is the
  healing path for the incident itself: re-resolving the mislabeled row with the pin
  fixes its currency.
- **Mint:** REFUSED (`ValidationError`) when the source won't confirm the id — an
  instrument must never exist priced in a currency nobody fetched.

`quote_currency` is threaded through the whole resolve surface (`resolve_instrument`
signature, POST `/api/instruments/resolve`, `/api/intel/assets/resolve`, and the
`resolve_instruments` AI tool's single + batch paths).

**`update_instrument` gains valuation fields as a controlled RE-BIND** — reversing the
"not updatable at all" decision, which the incident showed to be the worse evil: a
currency label with no editor is a permanent, silent mis-valuation. Re-bind rules
(`services.instruments._rebind_key` / `_rebind_updates`):

- Metadata stays whitelisted as before; `quote_currency`, `price_source`,
  `price_source_id`, `symbol`, `precision`, `unit` route through the re-bind.
- A **currency-only** change (same feed key) is allowed only while NO price history is
  stored under the id — history points carry no currency, so relabelling them would
  silently re-denominate every historical valuation. Once history exists, the answer
  is "re-bind the feed key to a series in the right currency instead."
- A **key change** (source and/or id) re-establishes the price series in the new
  terms: the old id's stored points are deleted when no other instrument reads them
  (`PriceHistoryStore.delete_series`), then the new key's history is backfilled
  (`backfill_history`). The new currency comes from the new feed — a yahoo re-bind
  must be CONFIRMED by a live fetch (unconfirmable id refused, explicit disagreement
  refused with the feed's answer), coingecko is pinned USD, manual keeps the row's
  currency or takes an explicit one.
- A new key that duplicates another instrument's pricing identity is refused, naming
  the existing instrument (merge onto it instead).

The resolve/update tool pair now states the split explicitly: `resolve_instruments`
is the FETCH-FACTS half (pins fetch, mints are confirmed, existing rows refresh);
`update_instruments` is the RECORD-EDIT half (metadata + controlled re-bind).

## Alternatives considered

- **Keep valuation fields un-editable; rely on resolve-refresh to heal.** Rejected:
  manual instruments have no feed to refresh from, and a wrong *key* (the incident's
  `KSTL.DE` row was also duplicated under workaround ids) needs an editor. The old
  stance produced the unfixable loop this ADR exists to close.
- **Allow relabelling currency over stored history (re-denominate the points).**
  Rejected: history points carry no currency, so "re-denominating" them would mean
  inventing a historical FX series nobody fetched — fabrication of the exact class
  this ADR bans. Wiping + re-fetching the series under the (possibly new) key is the
  honest version: every stored point then comes from the source that owns the currency.
- **Default a missing currency to the book's base instead of USD.** Rejected: still a
  guess — for KSTL.DE the base (SGD) was as wrong as USD; only the feed's answer is a
  fact.
- **Validate the currency label at write time only (trust the caller).** Rejected for
  feed-backed rows: the caller stating a currency for a yahoo/coingecko instrument is
  exactly how a plausible-but-wrong label gets in. Feed-backed rows take the feed's
  currency; only manual rows trust the caller (they must — no feed exists).

## Consequences

### Positive
- The incident's exact shape cannot recur at three independent layers (fetch, store,
  resolve pin), and the existing broken row class now heals by re-resolving with the
  pin or by a guarded re-bind.
- Every stored price point is denominated in the currency its instrument row claims,
  because a currency change can never silently reinterpret stored history.

### Negative / accepted trade-offs
- A pinned resolve is no longer a guaranteed-offline lookup: a mint fetches from the
  source (an existing row refreshes best-effort and is never blocked by a failed
  fetch). This is the cost of "facts come from the feed" — accepted because the old
  offline pin is precisely what shipped the incident.
- `quote_currency` is locked once history exists, so a mis-denominated row WITH
  history needs a key re-bind (or an explicit wipe-and-refetch) rather than a one-line
  label fix — deliberately, because the label fix is the corrupting one.
- All instrument creators were audited and updated in the same change (ibkr/alpaca/
  benchmark_seed/demo already passed a currency; the evals fixture was fixed); any
  FUTURE creator must supply a `quote_currency` or hit the store's refusal — the
  refusal, not convention, is what carries the rule.

### Follow-ups (out of scope)
- The owner's live book still carries the incident's workaround rows (duplicate
  instruments minted while chasing the bug); cleaning them is a deliberate,
  user-driven repair, not part of this code change.

## Agent provenance

- **Decided with the repo owner**, in order: "if we can't get the price, better to
  fail it rather than fall back to USD" (fail-closed fetch); "even with price_source
  and price_source_id, it should still look up, to get the latest data and update
  accordingly" (pin-still-fetches); "remove `or \"USD\"` — raise the error instead"
  (store refusal); "ensure quote_currency can be updated, and all other fields"
  (re-bind); "split: resolve_instrument → get data, update_instrument → update data"
  (tool-contract framing). "Deliver e2e."
- **Driving artifacts:** `backend/services/instruments.py` (module docstring §4),
  `backend/db/instrument_store.py` (`_make_instrument`), `backend/db/
  price_history_store.py` (`delete_series`), `backend/ai/tools/portfolio.py`,
  `backend/api/instruments.py`, `backend/api/intel_assets.py`;
  regression suite `tests/test_instrument_quote_currency_bugfixes.py`.
- **Why this way (one line):** a currency nobody fetched is a guess, a guess in the
  money path is a silent mis-valuation, and the only cure worse than the guard was
  leaving the bad label un-editable — so fetch or refuse, and make the correction a
  controlled, history-aware re-bind.
