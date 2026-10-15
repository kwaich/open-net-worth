---
title: FX rates are validated against their own reciprocal before being cached
status: Accepted
date: 2026-07-27
deciders: Claude, on owner direction after a live money-correctness incident
---

# FX rates are validated against their own reciprocal before being cached

## Context — a live incident, found by the owner looking at a chart

The Commitments cushion chart reported **"Short S$150,000,000.00"** and "Committed remaining
S$295,000,000.00" — exactly 1000× the correct figures. The chart was reporting faithfully; the
underlying FX rate was wrong.

`services/prices.fetch_live` builds one Yahoo symbol per currency (`f"{c}{base}=X"`) and stored
whatever came back, with no validation:

```python
for c, sym in fx_map.items():
    _, rate, _ = raw_yf.get(sym, (sym, None, None))
    if rate is not None:
        fx[c] = rate          # ← no sanity check
```

Queried live during the investigation:

| Yahoo symbol | Returned | Implies | Verdict |
|---|---|---|---|
| `VNDSGD=X` | 0.048984 | 1 SGD = 20.4 VND | **wrong** — what we stored |
| `SGDVND=X` | 20,369 | 1 SGD = 20,369 VND | correct |
| `USDVND=X` | 26,324 | — | correct |

Yahoo's low-value-currency **inverse** pairs are served scaled — `VNDSGD=X` quotes as if VND
were priced per 1,000 units. The tell is a mathematical impossibility: `0.048984 × 20,369 =
997.7`, when a currency pair and its reciprocal must multiply to **1.0**.

**This is not VND-specific.** `IDRSGD=X` was observed at `0.007183` (~100× off) and then, on a
second call minutes later, at the correct `7.183e-05` — Yahoo flaps between scaled and
unscaled for the same symbol. Any low-value currency can be hit, at any sync.

**Blast radius on a real production book:** a large VND holding across several accounts (mostly
a real-estate asset) was valued ~1000× too high instead of correctly — a large chunk of
phantom net worth, and every VND-denominated figure app-wide inflated 1000×.

## Decision

`services/prices._reconcile_fx_rate(currency, direct, inverse, previous)` validates every
fetched rate before it is cached. `fetch_live` now also requests the reciprocal pair
(`f"{base}{c}=X"`) purely to arbitrate.

1. **Both legs present** → require `direct × inverse ≈ 1` (±2%). On failure, take `1 / inverse`
   and log at ERROR naming the currency, both quotes, the product, and the magnitude of the
   error. The conventional quote direction (SGD/VND, USD/VND) is the liquid, correctly-scaled
   one, and was correct in every observed case.
2. **No reciprocal available** → accept the quote, but **refuse** an order-of-magnitude jump
   (>10× or <0.1×) away from the already-cached rate, returning `None` so the trusted cached
   value survives. A stale rate is materially better than a 1000×-wrong one.
3. **Only the reciprocal available** → use `1 / inverse`.
4. **Nothing usable** → `None`; never write a zero or negative rate.

The ±2% tolerance is a **scale** check, not a precision check: the two legs are independent
quotes with their own spread and timestamp, so small disagreement is market noise. A magnitude
error is 100× or 1000× away — nowhere near the boundary.

**Why this specific check:** `direct × inverse == 1` is an identity every correct currency pair
satisfies, so it **cannot produce a false positive on a real quote**. That is what makes it
preferable to any plausibility heuristic (a hardcoded expected-range table, a
"looks-too-big" threshold), all of which need per-currency maintenance and go stale as
currencies genuinely move.

## Consequences

### Positive
- A corrupted upstream quote can no longer silently poison net worth, commitments, budgets, or
  any base-currency conversion.
- The failure is **loud** (ERROR with both quotes and the computed product) instead of a
  plausible-looking wrong number.
- Two independent layers: the reciprocal check, plus a magnitude-jump refusal when no
  reciprocal exists.

### Negative / accepted
- **FX symbol count doubles** (one reciprocal per currency). These ride the same batched quote
  request, and the currency list is small — measured negligible.
- **If Yahoo served BOTH legs scaled consistently**, the product would still be 1.0 and the
  error would pass. Not observed (the conventional direction was always correct), and the
  magnitude-jump guard would still catch it on the next sync against the cached value.
- A genuine >10× move with no reciprocal quote would be refused and log an ERROR. For real
  currencies this is a redenomination — rare, and warranting a look anyway.

### Live outcome
The dev server (`--reload`) picked up the guard and the next scheduled sync self-corrected the
cache: VND `0.048984 → 4.909e-05` (1 SGD = 20,369 VND). VND holdings now value at S$88,200 and
net worth reads S$362,500. No manual data edit was needed — the guard fixed it on the next fetch.

## Provenance

- **Found by:** the owner, questioning a chart ("hmmm, is there something wrong here?") — the
  number was implausible even though every layer below it was working as written.
- **Not a regression:** `prices.py`, `fx_cache.py` and `core/fx.py` were unmodified; the bad
  cache was written by a routine sync at 01:19. The defect we own is the *absence of
  validation*, not the bad quote.
- **Verified against live Yahoo**, not fixtures — including catching the IDR magnitude flap
  between two calls minutes apart, which is what established this as systematic rather than a
  one-off VND glitch.
