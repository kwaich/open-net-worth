---
title: A propose_* preview must not write — resolve read-only, create at the apply choke
status: Accepted
date: 2026-07-18
deciders: reliability-program agent (Claude) + repo owner (harness reliability sweep, 2026-07-18)
---

# A `propose_*` preview must not write — resolve read-only, create at the apply choke

## Context

The propose/apply split exists so the user sees a diff and consents before anything
changes. Two `propose_*` tools quietly broke that contract by mutating state while
*building* the preview:

1. **`propose_position_update` minted accounts.** An `account_name` with no `account_id`
   resolved through `positions.resolve_account`, which is a find-or-**CREATE**
   (`positions.py`, the `AccountStore().create(...)` fallthrough). So merely *proposing* a
   move to a mistyped or not-yet-existing venue created a real account — and a proposal the
   user rejected left that orphan behind permanently. Typos were the common case: one
   `"Schwabb"` and the account list grows a ghost that never holds anything.

2. **`propose_lot_dispose` inserted lots.** `lots.preview_disposal` called
   `ensure_opening_lot`, which does `LotStore().insert(...)`, to seed the synthetic opening
   lot for a pre-lots holding so the draft had something to price against. Previewing a sale
   you never confirmed wrote a lot into the book.

Both were *known* — each carried a `mutates=(...)` tag and a "Propose-SHAPED but writes"
comment, and `tests/test_tool_domains.py` pinned the tags as intended behavior. Tagging
made the writes observable (the frontend refetched), which is why they survived; but a tag
is a data-refresh signal, not a licence. Notably `lots.preview_disposal`'s own docstring
still claimed "PURE preview of a disposal (no writes)" — the code and its contract had
diverged.

The genuinely hard part is that both previews *need* the resolved value to render the card.
Deleting the resolution is not an option; the preview would be wrong or would refuse.

## Decision

**Split each resolver into a read-only half (preview) and a creating half (apply). The
preview may look things up; only the apply choke point may bring them into existence.**

**Case 1 — resolve read-only, report the intent.** New `positions.find_account(*,
account_id, account_name)` is the pure lookup (id, then platform-or-name), returning `None`
on a miss. `resolve_account` is now layered on top of it and keeps its find-or-create
semantics for every existing apply/aggregation caller — no behavior change there.
`propose_position_update` calls `find_account`; a hit resolves to a concrete `account_id`
exactly as before, and a **miss leaves `account_name` on the change** and flags
`display.account_to_is_new` so the card says the venue would be created. The promise is
kept at apply time by `positions._materialize_account_name`, called from
`apply_position_change`'s update branch, which mints (or reuses) the venue and hands
`update_position` a concrete id — so `_UPDATABLE_FIELDS` never has to learn the name form.

**Case 2 — project the derived entity in memory.** New `lots.projected_open_lots(position)`
is the pure twin of `ensure_opening_lot`: real open lots when the position has any, else the
synthetic opening lot built (not persisted) via the pre-existing
`opening_lot_for_position`, else `[]` for a zero-quantity position (mirroring
`ensure_opening_lot`'s own skip). `preview_disposal` consumes that projection;
`compute_disposal` was already pure and already took `open_lots` as a parameter, so the
math is untouched. The apply path (`_apply_dispose`) still calls `ensure_opening_lot` and is
unchanged.

**Supporting change: the opening lot's id is now deterministic.** `_opening_lot_id` derives
it from the position id (`lot_` + 12 hex of `sha256("opening:<position_id>")` — same shape
as a minted id, and position ids are unique so collisions are impossible) instead of
`new_id("lot")`. Without this the preview would display consumptions naming a lot id that
never comes to exist, since apply mints its own. With it, the id the preview projects is
byte-identical to the one apply persists.

**`propose_alpaca_order` keeps its write — deliberately.** It stages
`{action, args, environment}` in `alpaca_pending_orders.json` under a single-use nonce, and
the proposed change carries only that nonce; `apply_alpaca_order` pops it and fires the
**stored** args, never model-supplied ones, so a card reading `buy 1 AAPL` cannot apply
`buy 100 TSLA`. This is the `McpPendingCallStore` pattern, and the staged file is a capped,
chmod-600, checkpoint-EXCLUDED runtime queue — not user domain data. The write *is* the
security mechanism. It stays tagged and must not be "fixed".

## Consequences

- Previewing is now safe to do speculatively. A rejected or mistyped proposal leaves nothing
  behind, which is what the propose/apply split always claimed.
- `mutates=("accounts",)` / `mutates=("portfolio",)` are removed from the two fixed tools,
  and both move into `READ_ONLY_TOOLS` in `tests/test_tool_domains.py`. The old
  `test_propose_shaped_writers_stay_tagged` (which pinned the bug) is replaced by
  `test_previews_that_used_to_write_stay_pure`, asserting the tags stay **absent** — the
  regression pin now points the other way — plus `test_propose_alpaca_order_stays_tagged`
  documenting why the third one is exempt.
- **The double-book guards are unaffected.** Both propose-time guards
  (`find_double_book_candidate` for acquire, `find_duplicate_disposal_candidate` for
  dispose) were already read-only store scans that never depended on the seeded lot, so they
  still fire at propose time; the apply-side guards in `_apply_acquire`/`_apply_dispose` are
  untouched. `tests/test_lot_double_book_guard.py` passes unchanged, and
  `test_dispose_double_book_guard_still_fires_on_a_pre_lots_position` re-pins the dispose
  guard against the exact pre-lots shape whose preview used to write.
- One accepted behavioral edge: the projected opening lot is not visible to a *concurrent*
  reader during the preview (it does not exist yet). That is the point — and the deterministic
  id means the preview and the eventual apply still agree on identity.
- Not addressed here (out of scope, no `propose_*` involvement, but the same smell): the
  `GET`-shaped `api/lots.py` read endpoint and `services/tlh.py`'s analysis pass both call
  `ensure_opening_lot` and therefore write during a read. `projected_open_lots` is the seam
  that would let them go pure if that is taken up.

## Agent provenance

Audited all 14 `propose_*` handlers across `portfolio.py`, `lots.py`, `equity.py`,
`options.py`, `income.py`, `statements.py` and `alpaca.py` by tracing each into
`services/`/`db/` to a store write. Exactly three wrote; the other eleven are genuinely
pure, and two near-misses are worth recording as prior art for the pattern chosen here:
`equity_import._resolve_instrument` is explicitly fuzzy-match-only ("NEVER auto-create",
returning `unresolved_rows` instead), and `options._resolve_account`/`_resolve_wallet` fail
**closed** with an error rather than minting an account — a deliberate 2026-07-12 security
fix. Option (a) "resolve read-only" was chosen for the account case because an account is a
user-facing entity whose creation deserves consent; option (b) "in-memory projection" for
the lot case because the opening lot is a *derived* artifact of the position, so projecting
it is exact rather than approximate.
