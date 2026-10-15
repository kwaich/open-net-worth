# Remove the Alpaca TRADING half — a money-movement surface that bought nothing

- **Date:** 2026-07-19
- **Status:** Accepted (owner decision)
- **Supersedes:** `docs/rfc/alpaca-connector.md` §3 (S2 gated trading) — that section is now
  historical, and the RFC carries a banner saying so.
- **Does NOT touch:** the Alpaca READ-SYNC connector (S1), which stays fully functional.

## Context

The Alpaca connector shipped in two strictly separated slices:

- **S1 — read sync.** Pulls `/v2/account` + `/v2/positions` and maps them into our
  instrument/position model through the `apply_position_change` choke point. Hourly scheduler
  job, REST status/config/sync routes, `get_alpaca_status` / `sync_alpaca_now` tools.
- **S2 — trading.** Three AI tools (`propose_alpaca_order` / `apply_alpaca_order` /
  `cancel_alpaca_order`), a nonce store (`alpaca_pending_orders.json`) that fired
  server-STORED args so a confirm card could not be swapped, a second kill switch
  (`trading_enabled`, default OFF, never env-seedable to true), a human-only CSRF-guarded
  `PUT /api/alpaca/trading`, the `/v2/orders` wire calls, and a danger-styled trading
  sub-panel in the UI.

S2 was built carefully. It was also, on inspection, buying nothing.

## The finding that decided it

**`apply_alpaca_order` books NOTHING locally.** It fires an order at Alpaca and returns. No
transaction, no lot, no position write. The fill is only ever noticed by the NEXT hourly read
sync — the same sync that would have noticed a fill from an order placed in Alpaca's own web
UI, or from Alpaca's own MCP server, or from a phone app.

That is the whole case. The value of an *in-app* trading tool over an *external* one is
integration: booking the trade locally, linking it to a lot, reconciling it against the
ledger. This one did none of that. It was a thin RPC to `/v2/orders` wearing a confirm card.

Weighed against that zero:

- **3 tools** in the registry (259 → 256), each carrying schema bytes into the tool catalog.
- **A store** (`db/alpaca_pending_order_store.py`) plus its `backend/db/__init__.py`
  registration, its `core/paths.py` constant, and its `.gitignore` + secret-store test entries.
- **A checkpoint exclusion** — a permanent carve-out in the restore path.
- **A REST router half** with its own CSRF gate and its own 422 semantics.
- **A security review**, and the standing obligation to re-review it on every change, because
  it is the highest-stakes write in the app: real money in an external system, irreversible by
  a checkpoint restore.

And the connector **has never been configured on this instance.** The trading half has cost
real review attention and carried real risk while sitting at zero usage since it shipped.

## Decision

Remove the trading half. Keep the read-sync connector fully functional.

Trading, if it is ever wanted, goes to **Alpaca's own MCP server via the existing
external-MCP path** — which already forces propose→confirm on money movement
(`core.mcp_external.is_money_movement` classifies any tool whose name contains `order`), is
nonce-bound, and is fail-closed against untrusted third-party servers. That path is strictly
better than what we deleted: it is one integration we maintain instead of one per broker.

**This is a REDUCTION of a money-movement surface.** That is the point, not a side effect.

## What was removed

| Layer | Removed |
|---|---|
| Tools | `propose_alpaca_order`, `apply_alpaca_order`, `cancel_alpaca_order` (whole `ai/tools/alpaca.py`) |
| Store | `db/alpaca_pending_order_store.py`, its `db/__init__.py` export, `ALPACA_PENDING_ORDERS_JSON` in `core/paths.py`, its `.gitignore` line |
| Checkpoint | the `ALPACA_PENDING_ORDERS_JSON` exclusion entry + its import |
| REST | `PUT /api/alpaca/trading`, `AlpacaTradingUpdate` |
| Config | `trading_enabled` (default, coercion, `public()`), `is_trading_active()` |
| Service | `place_order`, `cancel_order`, `submit_alpaca_order`, `cancel_alpaca_order` — the `/v2/orders` wire calls |
| Frontend | the `alpaca_order` change type + its 10 display fields, `AlpacaOrderPreview`, the trading sub-panel and `setTrading()` |
| Tests | 20 trading cases across 5 files; 3 keys hand-deleted from `tests/golden/tool_input_schemas.json` |

## What was deliberately KEPT

- **`get_alpaca_status` / `sync_alpaca_now`**, `services/aggregation/alpaca.py`'s
  `run_alpaca_sync`, the hourly `ingest:alpaca` scheduler job, the REST status/config/sync
  routes, and the sync-configuring UI.
- **`environment` (paper|live)** — it *reads* as trading-flavoured but is load-bearing for
  READ sync: it derives the base URL for `/v2/account` and `/v2/positions`. Its UI labels were
  reworded from "Paper (test trading)" / "Live (real money)" to "Paper account" / "Live
  account", since they described a capability the app no longer has.
- **`ALPACA_CONFIG_JSON` stays checkpoint-EXCLUDED.** Its exclusion comment used to give TWO
  reasons: it holds a secret key pair, AND it holds the `trading_enabled` permission flag a
  restore must never silently flip on. Only one of those reasons goes away. The key pair is
  still a secret, and a restore must still never resurrect a superseded or revoked credential.
  The comment was rewritten to that surviving reason; the exclusion itself is unchanged.

## Consequences

- **The capability is gone, not hidden.** There is no flag to flip back on. Restoring it means
  restoring code — which is the intent, since the next person to want broker trading should be
  routed to the MCP path rather than finding a dormant switch.
- **The removal is pinned by tests**, not just by absence. Four new assertions fail loudly if
  any of it comes back: the three tool names must not resolve in the registry
  (`test_trading_tools_are_gone_from_the_registry`), `PUT /api/alpaca/trading` must 404 and the
  config route must not accept the flag (`test_trading_route_is_gone`), the store must expose
  no `trading_enabled` on any surface (`test_trading_flag_is_gone`), and the service module
  must expose no order entrypoint (`test_order_wire_calls_are_gone`). A silent re-add is not
  possible.
- **No alias, no migration entry.** Deliberate, and a departure from the repo's standing
  "renaming a tool requires an alias" rule — because that rule exists so a tool name persisted
  in `agents.json` / a skill's `allowed-tools` keeps RESOLVING after a rename. Here nothing was
  renamed; a capability was withdrawn. An alias would have to point somewhere, and there is
  nowhere correct to point. A stored list naming these three now drops them the LOUD way
  (`effective_scope`/`effective_tools` log a WARNING; a skill referencing one is advertised
  `[degraded — unavailable: …]`), which is the right outcome: the operator should learn the
  capability is gone, not silently get something else.
- **`registry._LEGACY_TOOL_ALIASES` and `migrate_tool_names.RENAMES` needed no row deleted** —
  they only ever carried a prose note explaining why the Alpaca pair was excluded from the
  2026-07-19 propose/apply merge. The note was dropped with the tools.

## Notes for whoever reads this next

- **`backend/services/aggregation/alpaca.py` was the one file where sync and trading genuinely
  shared a module.** The four trading functions were not in the original removal brief — they
  had no caller left once the tools went, but leaving them would have kept a live, untested
  wire path to `POST /v2/orders` gated by a flag nothing set any more. That is worse than
  removing them: it keeps the capability one import away. They were removed, and the module
  docstring now carries an explicit "do NOT re-add a wire call to `/v2/orders` here."
- **Two sites neither pre-written map caught**, both found by sweeping rather than by reading
  the maps: `services/connectors.py::_alpaca_view` surfaced `trading_enabled` into the
  `ConnectorView` config the connector CARD renders (it would have silently started emitting
  `None`), and `db/pending_plan_store.py`'s docstring cited `AlpacaPendingOrderStore` as prior
  art for a class that no longer exists. Verify a map; don't trust it.
- **IBKR has an identical dormant `trading_enabled` flag** (`db/ibkr_config_store.py`,
  `PUT /api/ibkr/trading`) with **no** order tools behind it — the same dead-switch shape, and
  arguably the same argument applies. It was deliberately left ALONE: it is a separate
  connector and was out of this change's scope. Worth its own decision.
- **`.claude/rfcs/phase3/ibkr-connector.md`** specs an unbuilt IBKR "S2 trading" phase whose
  entire design is "mirror Alpaca exactly, byte-for-byte." It now points at a deleted reference
  implementation. No code depends on it, but a future reader should know the pattern it cites
  was retired on purpose.

## Agent provenance

Executed by a worktree agent on `lane/remove-alpaca-trading` off
`harness/reliability-program`, from an owner directive naming the exact removal surface plus
three independently-produced maps (backend / frontend / test). The maps were treated as
leads and each claim verified against the source before acting — which is how the two missed
sites above surfaced.

Gate (all run, output reported, none asserted): import smoke OK · registry 259 → **256**
exactly · full suite **11,360 passed, 7 failed** — the 7 being exactly the known
whole-suite-only failures (`test_briefing_api`, `test_email_snapshot_api`,
`test_price_backfill`, `test_recurring_transaction`), re-run in isolation as **144 passed, 1
skipped**, proving cross-test pollution rather than a regression from this change · `make
lint` clean with **5/5 import-linter contracts kept** · `tsc --noEmit` clean · `npm run build`
exit 0 with `check-css-classes` clean · `tool_inventory --lint` **0 defects** over 256 tools.
