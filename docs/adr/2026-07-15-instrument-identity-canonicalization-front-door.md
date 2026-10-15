---
title: Instrument identity stays feed-keyed; add a resolve-time dedup/canonicalization front door
status: Accepted
date: 2026-07-15
deciders: orchestrating Claude agent (instrument-identity-layer lane) + repo owner (design agreed pre-session)
---

# Instrument identity stays feed-keyed; add a resolve-time dedup/canonicalization front door

## Context

Instrument identity today *is* the price feed: the dedupe key is
`(price_source, price_source_id)` (`InstrumentStore.find_by_source` /
`find_or_create`). Three concrete failure modes fell out of that:

1. **Same real-world asset via two vendors = two instruments.** `coingecko:bitcoin`
   and `yahoo:BTC-USD` are the same coin but mint two separate `Instrument` rows — the
   duplicate-position guard (`apply_position_change`'s "refuse a second active
   position for the same instrument×account") is keyed on `instrument_id`, so it can't
   see the collision at all.
2. **Yahoo crypto pairs multiply per fiat pairing.** `resolve_instrument`'s Yahoo path
   splits a symbol on `.` (exchange suffix), not `-` (Yahoo's crypto-pair separator),
   so `BTC-USD`, `BTC-EUR`, `BTC-SGD` each resolve to a DISTINCT instrument with a
   different `quote_currency`, instead of one coin priced in one currency.
3. **Manual instruments with an empty `price_source_id` never dedupe.**
   `find_by_source` returns `None` on an empty id by design (so two manual entries
   never mint a bogus exact-key match) — but that also means two independent manual
   entries for the same real thing (typed twice, or by two different flows) silently
   duplicate, with nothing to catch it.

There were also two near-identical `resolve_instrument` implementations —
`backend/services/instruments.py` (used by the AI `resolve_instrument` tool) and
`backend/api/instruments.py` (the REST route) — that had drifted into full copies of
the same yahoo/coingecko/metal resolution logic, so a fix to one silently missed the
other.

## Decision

**Identity for pricing stays `(price_source, price_source_id)` — unchanged.** We are
not migrating to a canonical cross-vendor id (FIGI/ISIN-as-primary-key); we are adding
a dedupe/canonicalization FRONT DOOR that runs before a new instrument is minted,
following the industry pattern of "canonical asset + bag of external identifiers,
identity separated from pricing."

1. **One `resolve_instrument` implementation.** `backend/services/instruments.py` is
   now the only implementation; `backend/api/instruments.py`'s POST
   `/api/instruments/resolve` route is a thin translation layer that imports and calls
   it (routers stay logic-free per the dependency rule). The AI tool
   (`ai/tools/portfolio.py`) and the command-palette route
   (`api/intel_assets.py::resolve_asset`) already called the service function and
   needed no logic changes beyond threading the new `allow_duplicate_symbol` flag and
   translating a raised `ValidationError` to HTTP 422.

2. **Crypto canonicalization at resolve time.** A query matching a Yahoo-style crypto
   pair (`BASE-FIAT`, e.g. `"BTC-USD"`, `"eth-eur"` — base 2-10 chars, a recognized
   ISO-4217 fiat suffix) is stripped to its base symbol and resolved via CoinGecko
   FIRST, before Yahoo is ever tried — but only once CoinGecko actually recognizes the
   base symbol (`_split_crypto_pair` is a syntactic hint only; the caller still
   confirms via a real CoinGecko search). If CoinGecko doesn't recognize it, resolution
   falls through unchanged to the pre-existing yahoo-then-coingecko order. Known
   precious-metal codes (`xau`, `xauusd`, …) are excluded from pair-detection so a
   stray `"XAU-USD"` never gets treated as a coin search.

3. **The `identifiers` bag becomes load-bearing.** It was always on the `Instrument`
   schema but always empty. `_resolve_yahoo` now opportunistically stamps `isin` (via
   yfinance's free `Ticker.isin` — a second, best-effort network call; NO paid ISIN
   provider) and `mic` (the exchange, already fetched via `_fetch_name`);
   `_resolve_coingecko` stamps `coingecko_id` (redundant with `price_source_id` on a
   coingecko-sourced row, but it's what lets a DIFFERENTLY-sourced future candidate —
   e.g. a manual entry someone cross-references — match against it).

4. **The resolution ladder** (`services.instruments.find_existing(candidate, store,
   allow_duplicate_symbol=False)`), run before minting a new instrument on the
   auto-resolve and manual-no-id paths:
   - **(a)** exact `(price_source, price_source_id)` — the identity key, unchanged.
   - **(b)** a shared value in the `identifiers` bag (`isin`/`figi`/`coingecko_id`;
     `mic` deliberately excluded — an exchange alone never identifies an asset).
   - **(c)** normalized symbol + same `asset_class` — for **crypto**, silently reused
     (a symbol/class collision on crypto is essentially always the same coin across
     venues); for **everything else**, a `ValidationError` REFUSAL carrying the
     existing instrument's id/symbol/name, unless the caller passes
     `allow_duplicate_symbol=true` (a stock ticker genuinely can be two different
     companies on two exchanges — AAPL-the-NASDAQ-stock vs. a coincidental ticker
     clash elsewhere — so silent reuse would be wrong, and silent duplication would
     resurrect failure mode #1 with a worse UX).

   The ladder deliberately does **not** run on the explicit
   `price_source`+`price_source_id` fast path: a caller-supplied external id is
   authoritative (a legitimate cross-exchange/cross-vendor id, not an ambiguous
   guess), matching "identity stays feed-keyed."

5. **Manual instruments with no external id** (`price_source="manual"`, empty
   `price_source_id`) route through the same ladder's symbol+class step instead of the
   never-firing exact-key dedupe, so two manual entries for the same thing are caught
   the same way a query-resolved collision is.

`InstrumentStore` gained two new pure data-access lookups used by the ladder —
`find_by_identifier(key, value)` and `find_by_symbol_and_class(symbol, asset_class)` —
alongside the existing `find_by_source`/`find_by_symbol`; the ladder's DECISION logic
(which branch wins, the refusal, the override) lives in the service, per the
repo's `db = data access only` / `services = business logic` split.

## Alternatives considered

- **Migrate identity to a canonical cross-vendor id (FIGI/ISIN as the primary key).**
  Rejected for this pass: FIGI is a paid/licensed dataset for reliable coverage, ISIN
  coverage from a free source (yfinance) is inconsistent (many tickers, and all of
  crypto, have none), and it would require a full data migration + re-keying every
  price/position/lot reference. The dedupe-front-door approach gets the practical
  benefit (no more accidental duplicates) without a schema migration or new failure
  surface if a free ISIN lookup is wrong/stale.
- **Silently reuse on ANY symbol+class collision (not just crypto).** Rejected: a
  stock ticker collision across exchanges is a real, not-uncommon case (the same
  3-4 letter ticker reused by unrelated companies on different exchanges); silently
  merging them would corrupt cost basis/lots/positions for two genuinely different
  assets. Crypto is the one asset class where this risk is negligible in practice.
- **Silently refuse (hard error, no override) on a symbol collision.** Rejected: would
  make it impossible to legitimately hold the same ticker cross-listed on two
  exchanges (a real, supported use case — see `test_resolve_explicit_source_*`-style
  cross-listing tests) without falling back to the explicit-id escape hatch every
  time. `allow_duplicate_symbol` keeps the safe default while leaving an explicit,
  auditable escape hatch.
- **Put the ladder inside `InstrumentStore.find_or_create` itself.** Rejected: the
  ladder's decisions (crypto silent-reuse vs. non-crypto refusal, the
  `allow_duplicate_symbol` override, the raised `ValidationError`) are business logic,
  not data access — belongs in `services/`, matching the `db` = dumb repository
  convention. `find_or_create` is left untouched (exact-key only) for callers that
  intentionally bypass the ladder because they already hold an authoritative external
  id (`aggregation/alpaca.py`, `aggregation/ibkr.py`, `benchmark_seed.py` — all pass an
  explicit non-empty `price_source_id`, so they hit the untouched exact-match path and
  were deliberately left unchanged rather than risking an unattended broker-sync job
  hitting a symbol-collision refusal it has no way to confirm).

## Consequences

### Positive
- `BTC-USD`/`BTC-EUR`/`BTC-SGD`/… all resolve to ONE CoinGecko-priced Bitcoin
  instrument — the duplicate-position guard, allocation views, and net-worth folds
  now see one coin, not N phantom ones.
- A same-asset-different-vendor collision (a coin re-added manually, an equity
  cross-referenced by ISIN) now dedupes via the `identifiers` bag instead of silently
  duplicating.
- Manual instruments (private/illiquid holdings with no price feed) get the same
  duplicate protection as fed instruments, with an explicit, auditable override.

### Negative / accepted trade-offs
- The crypto-pair short-circuit only fires when CoinGecko's search actually
  recognizes the base symbol; a very obscure/newly-listed coin not yet indexed by
  CoinGecko's search would still fall through to Yahoo's literal pair ticker (the
  pre-existing failure mode), on that first resolve only. Accepted: CoinGecko's search
  reliably covers anything with real trading volume, and this only affects
  first-resolution of genuinely obscure coins.
- ISIN enrichment is a second best-effort network call per NEW equity/ETF resolution
  (not on every price refresh) — a modest latency cost on first-add, swallowed
  silently on failure (offline, rate-limited, unsupported ticker).
- The symbol-collision refusal is a new failure mode a caller must handle (REST → 422,
  AI tool → a `{"error": ...}` result the model can retry with
  `allow_duplicate_symbol=true`, both exercised by tests). Both call sites were
  audited and updated in this change; a future new caller of
  `resolve_instrument`/`find_existing` must remember to do the same.

### Follow-ups (explicitly out of scope)
- `aggregation/alpaca.py`'s crypto positions are resolved as `yahoo:<SYMBOL>USD`
  (e.g. `yahoo:BTCUSD`), not through this canonicalization front door — a synced
  Alpaca crypto position can still end up on a different instrument than one added
  manually via `resolve_instrument`. Left out of scope: changing an unattended
  broker-sync path's price-source strategy is a separate, riskier change (no user in
  the loop to confirm anything), and Alpaca's own crypto symbol format doesn't cleanly
  reuse `_split_crypto_pair`'s Yahoo-pair convention.
- No backfill/migration for instruments already duplicated before this change — per
  the "no backward-compat shims, migrate data directly" convention, a targeted
  cleanup script would be a separate, deliberate follow-up once real duplicates are
  identified in a live book (none exist yet — this ships ahead of the failure, not as
  a repair).

## Agent provenance

- **Decided by:** orchestrating Claude agent (instrument-identity-layer lane); the
  overall design (front-door dedup, not identity migration; the three-part ladder;
  the crypto-canonicalization rule) was agreed with the repo owner before
  implementation began.
- **Branch / worktree:** `worktree-agent-a2ec9bb5bb9dd219a`
  (`.claude/worktrees/agent-a2ec9bb5bb9dd219a`), based on `main` @ `07aec5b8`.
- **Alternatives weighed:** FIGI/ISIN-as-canonical-id migration / silently reuse any
  symbol collision / hard-refuse with no override / ladder inside the store instead of
  the service — all rejected, reasons above.
- **Deviations from the literal task brief:** identifier key names use the
  already-documented `{isin, figi, mic}` shape (`instrument_store.py`'s existing
  docstring) plus `coingecko_id`, rather than inventing a separate `yahoo_symbol` key
  (redundant with `price_source_id` on a yahoo-sourced row). The ladder function is
  named `find_existing` (module-level in `services/instruments.py`) per the brief's
  own suggested name.
- **Driving artifacts:** this file; `backend/services/instruments.py` (module
  docstring cross-references this ADR); tests `tests/test_instruments_service.py`,
  `tests/test_instruments_api.py`, `tests/test_intel_assets_api.py`,
  `tests/test_positions_stores.py`.
- **Why this way (one line):** identity is cheap to keep simple (one feed, one
  price-source key) as long as duplicates are caught BEFORE they're minted — a resolve-
  time front door gets that without a schema migration or a new "which id is
  canonical" question every future price-source integration would have to answer.
