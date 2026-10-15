# Remove the IBKR `trading_enabled` flag — a kill switch guarding nothing

- **Date:** 2026-07-20
- **Status:** Accepted
- **Precedent:** `docs/adr/2026-07-19-remove-alpaca-trading-half.md` — same argument, applied
  to an emptier switch. That ADR's closing notes explicitly flagged this flag as "worth its
  own decision"; this is that decision.
- **Supersedes:** `.claude/rfcs/phase3/ibkr-connector.md` §S2 (gated trading) — never built,
  now abandoned. The RFC carries a banner saying so.
- **Does NOT touch:** the IBKR Flex read-sync connector, which stays fully functional.

## Context

The Alpaca lane removed a trading half that *existed*: three order tools, a nonce store, a
`/v2/orders` wire path, a `trading_enabled` kill switch, and a human-only
`PUT /api/alpaca/trading`. Its case was that `apply_alpaca_order` booked nothing locally, so
the integration bought nothing over an external MCP server.

IBKR carried the **same switch shape with none of the substance**:

| Layer | Alpaca (before removal) | IBKR |
|---|---|---|
| Order tools | 3 (`propose`/`apply`/`cancel`) | **0** |
| Order wire calls | `POST /v2/orders` | **none** |
| Nonce store | `alpaca_pending_orders.json` | **none** |
| `trading_enabled` flag | yes | yes |
| `is_trading_active()` | yes, called at the wire | yes, **zero callers** |
| `PUT /api/…/trading` | yes | yes |

## The finding, verified before acting

The brief asserted the flag was dormant. Verified rather than trusted, and the verification
is stronger than the brief claimed:

```
$ grep -rn "is_trading_active" backend/ | grep -v "ibkr_config_store.py"
(no output)
```

`is_trading_active()` had **no caller anywhere in the backend** — not a tool, not a service,
not a route. The flag it read was written by exactly one endpoint and read by nothing.

Second, the connector is structurally incapable of trading. Every HTTP call in
`services/aggregation/ibkr.py` goes to one of two Flex Web Service endpoints —
`SendRequest` and `GetStatement` — a batch **reporting** API. There is no Client Portal
session, no OAuth, no order entry. The credential itself is read-only: a Flex token can
only export a saved query.

So the flag did not guard a dormant capability. It guarded one that **had never existed and
could not exist through this connector**. Whichever way it was set, nothing changed.

## Decision

Remove the flag, its REST route, its `is_trading_active()` gate, and every line of prose
describing an IBKR trading surface. Keep the Flex read-sync connector fully intact.

The reasoning is the precedent's, sharpened: **a permission flag guarding a capability that
does not exist is worse than no flag.** It is a load-bearing-looking control that does
nothing — it tells a reader (and a future agent) that an order path exists somewhere, invites
"just wire it up to the existing switch", and costs a security reviewer real attention on
every pass over this file. A UI string reading "Trading (S2) is not available in this
release" made the same false promise to the user: it describes a feature being withheld, not
one that was never built.

## What was removed

| Layer | Removed |
|---|---|
| Config | `trading_enabled` (default, `bool()` coercion, `public()` exposure), `is_trading_active()` |
| REST | `PUT /api/ibkr/trading`, `IbkrTradingUpdate`, the now-unused `Request` import |
| Service | `_ibkr_view`'s `trading_enabled` config passthrough (would have silently emitted `None`) |
| Frontend | `IbkrStatus.trading_enabled`; the "Trading (S2) is not available in this release" string |
| Prose | `paths.py`, `checkpoint.py`, `.gitignore`, the `aggregation/ibkr.py` module docstring, `CLAUDE.md`, the RFC status banner |
| Tests | `test_trading_enabled_toggle`, `test_ibkr_trading_requires_csrf_header`, `test_ibkr_trading_requires_configured_before_enable` |

## What was deliberately KEPT

- **The whole read-sync connector**: `get_ibkr_status` / `sync_ibkr_now`, `run_ibkr_sync`,
  the Flex token + query id, the scheduler poll, `GET /api/ibkr/status`,
  `PUT /api/ibkr/config`, `POST /api/ibkr/sync`, and the configuring UI.
- **`ibkr_config.json` stays checkpoint-EXCLUDED.** Its comment used to give two reasons: it
  holds a secret token, AND it holds a permission flag a restore must never flip on. Only
  the second goes away. A Flex token is still a credential, and a restore must still never
  resurrect a revoked one. The comment was rewritten to the surviving reason; the exclusion
  itself is unchanged. (Directly mirrors the Alpaca lane's handling of `alpaca_config.json`.)

## Consequences

- **No alias, no migration entry.** Same reasoning as the Alpaca ADR, and it is worth
  restating because the charter rule is emphatic: an alias exists so a tool NAME persisted in
  `agents.json` or a skill's `allowed-tools` keeps resolving. Nothing was renamed here, and
  **no tool was removed — there were none.** `registry._LEGACY_TOOL_ALIASES` and
  `migrate_tool_names.RENAMES` are untouched, and `tool_inventory --lint` stays at 0 defects
  over an unchanged tool count.
- **The removal is pinned by tests, not by absence.** Three assertions fail loudly if any of
  it returns: `test_trading_flag_is_gone` (not a default, not settable, not on `public()`),
  `test_trading_route_is_gone` (the route 404s **and** the surviving config route refuses the
  flag as a smuggled alternative writer), and `test_order_wire_calls_are_gone` (the service
  module exposes no order entrypoint). **Negative control run, because a pin that cannot fail
  pins nothing:** re-adding `"trading_enabled": False` to `DEFAULTS` was verified to turn
  `test_trading_flag_is_gone` red (`AssertionError: assert 'trading_enabled' not in {…}`),
  then reverted and the suite confirmed green again.
- **A stored config with `trading_enabled: true` is INERT but PERSISTS.** Worth stating
  precisely, because the first draft of this ADR asserted it "disappears on the next write"
  and driving the case proved that wrong: `update()` refuses to *add* the key (it is no
  longer in `DEFAULTS`, and unknown keys are dropped), but `get()` merges `{**DEFAULTS,
  **data}`, so a key already on disk is passed through — and `update()` rewrites the merged
  object wholesale, so it survives every subsequent write. What matters is that **nothing
  reads it**: `public()` does not expose it (verified), `is_trading_active()` is gone, and no
  route or tool writes it. So the capability is unreachable, but a dead
  `"trading_enabled": true` line can sit in a legacy `ibkr_config.json` indefinitely.
  **`alpaca_config.json` behaves identically** — verified against `AlpacaConfigStore` rather
  than assumed — so this is a shared residue of both removals, not an IBKR defect, and
  purging it belongs in one change that covers both stores. Logged to
  `docs/launch/BOARD.md` rather than fixed here, since fixing only IBKR would make the two
  connectors diverge for no functional gain.
- **`environment`-style trap did not apply.** Alpaca had to keep a trading-flavoured field
  (`paper|live`) because it derived the read base URL. IBKR's `base_url` is plain config with
  no trading connotation, so nothing needed rewording for that reason.

## Notes for whoever reads this next

- **Verify the finding before executing the brief.** The instruction was conditional ("if a
  real order path exists, this lane is wrong — STOP"), and the grep is what turned a plausible
  claim into an established one. It also upgraded the argument: not "dormant" but
  "structurally impossible".
- **`services/connectors.py::_ibkr_view` was the site a map would miss** — the exact twin of
  the `_alpaca_view` site the Alpaca lane called out as found-by-sweeping-not-by-reading. It
  surfaced `trading_enabled` into the `ConnectorView` config the connector CARD renders; left
  alone it would have silently started emitting `None` rather than failing. The precedent
  said "verify a map, don't trust it" — that generalizes to: after deleting a field, grep for
  its name again, because the dangerous leftovers are *readers*, not writers.
- **The UI string was the model-facing-equivalent lie.** The Alpaca lane's adversarial verify
  found three stale texts its own removal missed, one rendered into a model-facing tool
  description. IBKR has no order tool and therefore no tool description to poison, but the
  user-facing "not available in this release" string was the same category of false promise
  and was reworded, not deleted, so the panel still explains why there is no trading here.
- **The RFC now points at a deleted reference implementation.** `.claude/rfcs/phase3/ibkr-connector.md`
  specs S2 as "mirror Alpaca exactly, byte-for-byte" — and that Alpaca code was removed a day
  earlier. Two retired designs pointing at each other is exactly how a future agent
  reconstructs a capability nobody wants; the banner says so explicitly.

## Agent provenance

Executed by a worktree agent on `lane/ibkr-dead-flag` off `harness/reliability-program`,
from an owner directive that named the finding but required independent verification first.
Scope was held to the flag: no drive-by refactors, and defects noticed outside the item list
went to `docs/launch/BOARD.md` as inbox lines rather than being fixed here.

Gate (all run, output reported, none asserted):

- **Import smoke** — `IMPORT_OK`.
- **IBKR suite 37 → 37 passed** (3 trading cases removed, 3 pinning cases added — the first
  draft of this line said "→ 36, 2 added" and was corrected against the actual run).
- **Live routing table asserted empty of the route**, not just the source: `/api/ibkr/trading`
  is absent from `app.routes`, which lists only `GET /status`, `PUT /config`, `POST /sync`.
- **Read-sync proven by EXERCISING it**, per the brief's "prove it works, don't read it":
  a driven `run_ibkr_sync()` against a stubbed Flex statement (only the HTTP boundary faked,
  all production code above it) booked **1 position** (MSFT qty 7, cost basis 300) and **2
  transactions** (`external_source="ibkr"`, refs `U7654321:DRIVE-ORD-1` / `…:DRIVE-DIV-1`),
  auto-linked the venue account, and a second run left the count at 2 — dedup intact.
- **`make lint` clean**: ruff check + format, **5/5 import-linter contracts KEPT**,
  `tool_inventory --lint` **0 defects over 256 tools** (count unchanged — no tool was
  removed, which is the point).
- **`tsc --noEmit` exit 0** after the `IbkrStatus` interface change.
- **Full suite** `pytest -q -m "not integration"`: **8 failed, 11,494 passed, 11 skipped, 46
  deselected in 487s.** Seven are exactly the briefed known whole-suite-only failures
  (`test_briefing_api` ×3, `test_email_snapshot_api` ×1, `test_price_backfill` ×2,
  `test_recurring_transaction` ×1); each was re-run in isolation and passes (67+1skip / 7 /
  56 / 14). The 8th, `tests/evals/test_tool_surface_harness.py::test_perfect_persona_passes_every_task[read_net_worth]`,
  is **not** on the briefed list — so it was isolated rather than waved through: **60 passed,
  1 skipped** on its own. That file is already named in the BOARD's TEST-ORDER POLLUTION
  entry, which records that *which* test in it fails moves as unrelated files are added — the
  same signature seen here. It also cannot plausibly be this diff: it exercises tool-surface
  personas, this change removed no tool, and `tool_inventory` counts an unchanged 256.
  A separate earlier run additionally failed `test_tools_payees` — also green in isolation
  (125 passed), and traced to four other lanes running pytest against this repo concurrently.
