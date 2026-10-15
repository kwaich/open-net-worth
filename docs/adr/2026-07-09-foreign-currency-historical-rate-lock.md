# 2026-07-09 — Foreign-currency transactions locked at the historical rate

**Status:** accepted (owner-specified design) · narrows the "never trust stored `base_amount`"
invariant · design: `docs/superpowers/specs/2026-07-09-foreign-currency-historical-rate-lock-design.md` ·
plan: `docs/superpowers/plans/2026-07-09-foreign-currency-historical-rate-lock.md`
**Where:** `backend/core/fx.py` (`txn_base_amount`), `backend/services/ledger.py` (transfer FX
stamp + base-value PIN), `backend/services/planning.py` (`_has_foreign_cash`),
`frontend/.../accounts` (register + txn-detail show the locked value; "Value in <base>" pin
field), `frontend/.../plan` (RTA-popover foreign-cash note)

## Decision

`txn_base_amount(t, base, rates)` **trusts the write-time `base_amount`** when the row's stored
`base_currency` still matches the current base, instead of always recomputing the reporting
value from the native leg at today's FX rate:

```python
if (t.get("base_currency") or "").upper() == base and t.get("base_amount") is not None:
    return abs(float(t["base_amount"]))          # LOCKED — value on the day
# else recompute from the native leg (base-currency change, or a legacy/unstamped row)
```

A completed transaction is a historical fact: the SGD (base) value of a 300k-VND coffee is
fixed on the day it happened. The prior behavior recomputed it from a single *current* FX
snapshot (`fx_cache.json` holds only the latest rates — there is no dated history), so every
foreign-currency transaction silently drifted as the market moved. The write path already
stamped `base_amount` + `base_currency` + `fx_rate` at write time; this change simply stops
throwing that locked value away.

This **narrows** the CLAUDE.md invariant "never trust stored `base_amount` — recompute via
`txn_base_amount()`" to: *never trust it **across a base-currency change** (or on a legacy row
with no stamp)*. Those two cases still recompute — correctly re-expressing a stale snapshot in
the new base — which is why the existing base-rebase characterization tests
(`tests/test_txn_rebase.py`, `tests/test_char_ledger_math.py`) pass unchanged: their fixtures
carry no matching `base_currency`, so they take the recompute path.

### Update 2026-07-18 — `base_amount_pinned` flag REMOVED (stamp-once + preserve-on-edit)

The original design added a per-row `base_amount_pinned` flag so a user-entered "Value in SGD"
survived `create_transaction`'s habit of recomputing `base_amount` from today's FX cache on every
write. That flag was a symptom, not a feature: the deeper bug was that **any edit re-priced an
unpinned foreign row** (`update_transaction` re-runs the create logic → recomputes `base_amount`
at the current rate), so editing an unrelated field like the payee silently shifted `fx_rate`
(owner-reported on a USD dividend). The flag is now GONE. New model: **`base_amount` is stamped
once at create and PRESERVED on every edit that doesn't touch the native leg** — it recomputes
only when the amount/currency actually changes, or when the edit explicitly sets a new
`base_amount` (the "Value in SGD" override). Mechanism: `create_transaction` now honors an
explicit `data["base_amount"]` (no service caller passes one, so it's safe); `update_transaction`
drops the value from its `merged` row only when the leg value changed. `preserve_base` (FIX-4)
stays as a merge-choke force. Evidence: `tests/test_ledger_base_pin.py` rewritten around
preserve-on-unrelated-edit / recompute-on-leg-change / explicit-override; full suite green.

### Owner scope decisions (2026-07-09)

1. **Transactions are locked** at the transaction-date rate (this ADR).
2. **Held foreign balances keep floating** at the current rate in net worth — real unrealized
   FX gain/loss on VND/EUR/USD you still hold is preserved (`ledger.py:235`, untouched).
3. **The budget values held foreign cash at CURRENT value** — an FX gain on cash you still
   hold is *immediately assignable* (Ready-to-Assign reflects current worth). Rationale: an
   appreciated VND balance is real, spendable purchasing power; "gained value but can't assign
   it" is wrong. Cost-basis would only *delay* recognition (the gain trickles into RTA as the
   VND is spent) in exchange for volatility protection; the owner chose immediate recognition,
   which is also the simpler build. RTA's computation (`_ready_to_assign` / `_cash_as_of`) is
   **unchanged**.
4. **Note (don't quantify) the FX exposure** — the budget month response reports a read-only
   boolean `has_foreign_cash`; when true the RTA breakdown popover notes that Ready-to-Assign
   can shift as exchange rates move. **Revised (owner, later same day):** an earlier version
   computed a precise `unrealized_fx` amount (current-value minus a historical-cost basis of the
   on-budget cash pool). It was removed — for a personal budget the amount is sub-dollar noise
   (~S$1 on this book), the cost-basis computation was fragile (a per-step running-balance
   division blew the figure up to ~S$530B on a heavily-churned VND account before a blended-rate
   rewrite bounded it), and it is display-only. A plain qualitative note carries the same signal
   without a number to compute, explain, or get wrong.

## Why

The prior invariant optimized for a **rare** event (changing your base currency, which clears
the FX cache and re-anchors) and paid for it by corrupting the **common** case (rates drift
daily). On the live book this meant **425** non-transfer foreign rows revaluing continuously,
and a real pass-through — cousin sent VND, owner forwarded the money out, recorded as income
(VND) + expense — showing a phantom gain/loss as SGD/VND moved even though the two legs netted
to zero on the day. Locking makes past numbers stable and correct; base-currency change remains
a deliberate, explicit re-anchoring.

## Accepted divergences / non-goals

- `_cash_at_cost` (the display-only FX figure) reconstructs historical cost from stored per-leg
  `base_amount`s. Internal cash↔cash transfers cancel (value conserved in the pool); boundary
  legs use the locked value. The **7** historical foreign↔foreign transfers (neither leg in
  base currency — verified count) approximate at the current rate; this affects only the
  display figure, never RTA or net worth, and is fixed prospectively by stamping
  `base_amount`/`fx_rate` on transfers at write time.
- A foreign account's pre-migration `initial_balance` scalar is valued at the current rate in
  `_cash_at_cost` (no locked opening rate exists) until the starting-balance migration turns it
  into a real, base-stamped adjustment txn — after which it is exact. Display-only.
- **No dated FX-history table** — rejected as a redundant subsystem; each row already carries
  its own locked rate. YAGNI.

## Agent provenance

Built by an agent (Claude Opus 4.8) in a brainstorm→spec→plan→implement loop with the owner.
The owner's four scope decisions above were made interactively; the agent surfaced the
transaction-vs-held-balance and cost-vs-current-value distinctions and the RTA/FX interaction
(measured: on-budget cash is ~53% foreign, ~380 SGD, mostly VND) before the owner chose
current-value budgeting with a visible FX figure.
