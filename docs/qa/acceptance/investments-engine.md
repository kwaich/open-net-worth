# Acceptance criteria — Investments engine (portfolio money-math)

The money-math trust contract for the **③ Investments** flow's BACKEND engine (the
`③ Investments` lane in [`docs/launch/BOARD.md`](../../launch/BOARD.md)). Computational
criteria — checkable at the service layer (`backend/services/positions.py`, `lots.py`,
`returns.py`, `asset_detail.py`) with constructed inputs. **Scope: the engine, not the UI**
(the Investments/asset-detail front-end is being redesigned separately) and **not** the
parked cross-account aggregation. The QA agent must exercise each criterion and report
PASS/FAIL per ID.

Base currency SGD unless stated.

Legend: ✅ = verified by the 2026-06-28 reality audit · 🧪 = lands with `lane/I1-I3-invest-engine`.

---

### IE-VALUATION ✅
- GIVEN a holding qty=10, price=100 in `quote_currency` USD, base SGD, FX USD→SGD=1.35
- WHEN market value is computed
- THEN it equals qty × price × quote_per_unit converted via the QUOTE currency = S$1350 (never the cost currency).

### IE-FIFO ✅
- GIVEN lot A (qty 5 @ 100, acquired earlier) then lot B (qty 5 @ 120)
- WHEN 7 units are disposed
- THEN FIFO consumes A(5) then B(2), ordered by (acquired_at, created_at).

### IE-REALIZED-GAIN ✅
- GIVEN a disposal consuming lots with a known cost basis
- WHEN realized gain is computed
- THEN gain = proceeds − cost, with the cost (in its native `cost_currency`) converted INTO the proceeds currency as a single `eff_cost × qty` call (multiply-then-convert, no per-unit rounding); `realized_gain_base` = `to_base(gain, proceeds_ccy, base)`.

### IE-UNKNOWN-BASIS ✅
- GIVEN any consumed lot has `unit_cost = None`
- WHEN the disposal is computed
- THEN the disposal's `realized_gain` is `None` (never a fabricated 0).

### IE-ZERO-PROCEEDS-REFUSED ✅
- GIVEN a TAXABLE disposal whose fee-adjusted net proceeds per unit ≤ 0
- WHEN it is attempted
- THEN it is REFUSED (`InvalidLotSelection`); a transfer-out (`is_taxable=False`) instead books `realized_gain=None` and is allowed.

### IE-DOUBLE-BOOK ✅
- GIVEN a buy identical to a fully-open lot (same kind=buy, acquired_at, qty, unit_cost), or a disposal matching an existing realized event (same date, qty, proceeds/unit, currency)
- WHEN applied without `allow_duplicate`
- THEN it is REFUSED as a double-book; a retry with the same `idempotency_key` is a no-op returning the existing id.

### IE-DISPOSE-UNDO ✅
- GIVEN a disposal event
- WHEN `dispose_undo` runs
- THEN it pre-validates every consumed lot still exists, re-opens them (capped at `original_qty`), soft-deletes the cash-proceeds leg if any, deletes the realized event, and resyncs — REFUSED if a consumed lot is gone.

### IE-RESYNC ✅
- GIVEN lots change on a position
- WHEN aggregates resync
- THEN `position.quantity` = Σ remaining_qty and `cost_basis` = the remaining-qty-weighted average of cost-known lots' `effective_unit_cost` (None if no cost-known qty); `cost_currency` synced only when the open lots agree on one.

### IE-UNREALIZED-PNL ✅
- GIVEN a held position
- WHEN unrealized P&L is computed
- THEN it equals market value (quote-ccy → base) − cost basis (native cost ccy → base), recomputed live (not stale after an FX change).

### IE-RETURNS-TWR-MWR ✅
- GIVEN a portfolio value series with mid-period external cash flows
- WHEN returns are computed
- THEN TWR neutralizes flows (manager-skill), MWR/XIRR reflects flow timing (personal return); `period_gain_base` nets out external flows so injections don't inflate the gain.

### IE-BENCHMARK-SUFFICIENT 🧪 (I1)
- GIVEN a benchmark instrument with less price history than the window (e.g. 90 days on a 1Y window), while another instrument has older history
- WHEN the benchmark comparison is computed
- THEN it is marked INSUFFICIENT (`sufficient=False`, `twr=None`) — it does NOT present the short-period return AS the full-period figure. A benchmark with real coverage at/before the window start still computes.

### IE-LOT-FEES 🧪 (I2)
- GIVEN a lot with acquisition fees (unit_cost=100, fees=10, original_qty=5)
- WHEN the asset-detail lot rows are built
- THEN `effective_unit_cost` = 102 and `remaining_basis` is fee-amortized (matching `lots.lots_for_position`), not the raw 100.

### IE-LOT-TERM 🧪 (I3)
- GIVEN a lot held exactly 365 days
- WHEN its term is classified in the asset-detail lot rows
- THEN it is "long" (`>= 365`), consistent with the tax engine's `classify_term`.

---

**Flow is "rock-solid / lockable" when:** every ID is PASS, the regression suite
(`tests/test_investments_acceptance.py`, I4) is green in `make check`, and the owner has
spot-checked real P&L / returns in the running app. (UI verification is separate, tracked
once the front-end redesign settles.)
