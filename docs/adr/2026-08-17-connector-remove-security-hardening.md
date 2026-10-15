---
title: Connector Remove security hardening — stop-before-reset, session forgetting, identity-map scoping
status: Accepted
date: 2026-08-17
deciders: security-reviewer (Claude) + senior-engineer implementation lane (Claude)
---

# Connector Remove security hardening — stop-before-reset, session forgetting, identity-map scoping

## Context

A security review of the Pause/Resume/Remove unification
([2026-08-17-connector-remove-covers-every-connector-family](2026-08-17-connector-remove-covers-every-connector-family.md)
and its predecessor) found 13 issues against the full diff and call graph, 2 of them
blocking. All 13 were fixed in the same pass; this ADR records the handful of decisions
that are genuinely hard to reverse or non-obvious, rather than plain bug fixes.

## Decisions

### 1. Telegram `DELETE` stops the live bot BEFORE erasing its credentials, and refuses (502) if the stop can't be confirmed

Before this fix, `DELETE /api/connectors/telegram` reset `TelegramConfigStore` first and
best-effort reloaded the bot afterward (mirroring `PATCH`'s enable/disable order). If the
reload silently failed, the response still read 200 — a live process, started with the OLD
token, could keep running and answering Telegram indefinitely with no operator-visible sign
anything was wrong; the UI showed the connector as fully removed.

We inverted the order for `DELETE` specifically: `telegram_bot.stop()` is awaited first and
its result checked. Only once the stop is confirmed does the credential reset proceed. If
`stop()` returns `False` (teardown raised, or PTB's `Application.stop()`/`.shutdown()`
threw), the route raises `502` and the credentials are **not** touched — the operator can
retry, or restart the app to force a clean stop. This required `bot.stop()` to change its own
contract from `-> None` (silently swallowing a teardown exception) to `-> bool` (reporting it),
and `bot.reload()`/`bot.start()`/`bot.stop()` to share one `asyncio.Lock()` held across the
whole stop-then-start sequence, so a `PATCH` racing a `DELETE` can no longer interleave their
teardown/startup steps.

`TelegramConfigStore.reset()` also independently lands `access_policy="disabled"` (not
`DEFAULTS`' own `allowlist`), which is fail-open with an empty `allowed_chat_ids` — belt and
suspenders, not a substitute for the ordering fix, since a still-live process holding the old
in-memory config wouldn't observe a config change it never re-read.

**Alternative rejected:** keep resetting first and rely solely on the `access_policy` fix.
Rejected — a live bot process holds its own credentials/policy in memory; erasing the DB row
does not touch a process already running, so only confirming the process is actually down
closes the window.

### 2. A connector's `reset()` preserves its `account_map` (a Plaid/SimpleFIN/etc. account-id identity mapping) but NOT its sync cursor

Seven aggregation connectors (Plaid, SimpleFIN, Lunch Money, Enable Banking, Yodlee, Alpaca,
IBKR) plus YNAB and Lunch Flow store an `account_map` — provider account id → ledger account
id — alongside their credentials. The original `reset()` wrote a flat `DEFAULTS` copy,
wiping this map along with the credential. Re-adding the same provider after a Remove would
then create a full set of DUPLICATE ledger accounts, because the sync code's
`_resolve_account`-shaped logic has no other way to recognize "I've seen this provider
account before."

We preserve `account_map` (read before the reset write, spliced back in) across `reset()`,
but deliberately do **not** preserve cursor-shaped fields (Plaid's `cursor`, YNAB's
`server_knowledge`/`budget_id`, Lunch Flow's raw sync state) — those are re-derivable and a
stale cursor against a possibly-different underlying Item/budget is a correctness risk,
whereas a missing cursor only costs a slower first resync (transactions dedupe by
`external_ref` regardless). YNAB's `budget_id` is excluded on the same reasoning as its
sibling `server_knowledge`: they are a linked pair, and keeping one without the other would
recreate the inconsistency `update()` already guards against.

**Alternative considered:** preserve the whole config object except the secret fields.
Rejected — broader than the actual bug (duplicate accounts), and would re-introduce exactly
the "was this cursor really re-validated against the reconnected credential" risk the
narrower fix avoids.

### 3. `remove_connector` now also clears every `tool_permissions` grant for that connector

A `tool_permissions` row is keyed by `(owner_connector_id, tool_name)`. Nothing previously
cleared these on Remove, so an `always_allow` grant set while a connector was configured
would silently survive a full credential wipe and apply again the moment the same connector
id was reconfigured — an unattended-fire permission surviving what looked, and was
advertised, as a full reset. `remove_connector` now calls
`tool_permissions.clear_connector_permissions(connector_id)` as the last step of both the
native (`_RESETTABLE`) and `web_search:` branches. This is a plain correctness fix, not a
close call, but is called out here because it changes what "Remove" promises: a removed
connector now starts genuinely blank, permissions included.

### 4. `services.connectors` reaches `backend.connectors.session_map` directly, and `session_map.py` was split to stay `ai`-free to make that legal

Forgetting a removed Telegram connector's `chat_id → session-uuid` mappings
(`connectors.session_map`) is necessary so a freshly re-added bot doesn't silently resume a
stranger's old conversation the moment that chat id talks to it again. But
`backend.connectors` sits outside import-linter's 5-layer chain the same way `backend.mcp`
does (see the existing "Remove/MCP-dedup" exception in `services/connectors.py`'s own
docstring) — and most of it, including `session_map.py` as it existed before this change,
freely imports `backend.ai` (`reset_session` called `assistant.clear_session`). Importing it
from `services/connectors.py` therefore created a real, transitive `services → ai` edge that
import-linter's layers contract catches (it checks transitively, not just direct imports).

We split `session_map.py`: the one function needing `ai` (`reset_session`, used only by
Telegram's `/new` command handler) moved to a new sibling module,
`backend/connectors/session_reset.py`; everything else (`get_or_create_session`,
`list_user_ids`, `get_effort`/`set_effort`, and the new `clear_connector`) stays in
`session_map.py`, which is now genuinely `ai`-free and safe for `services` to import. The
module docstrings on both sides call out this constraint explicitly so a future addition
doesn't reintroduce the edge.

**Alternative considered:** a `core`-level registration seam (matching `core.automation_runner`'s
pattern) instead of splitting the module. Rejected as disproportionate — the whole problem was
ONE function's ONE import; moving that one function is a smaller, more legible fix than adding
a new standing seam for a single call site.

### 5. Round-2 re-verification (PoC-driven): `reload()`'s discarded result, Pause's missing data-level gate, and the stop-retry bypass

A follow-up re-verification pass ran runnable PoC scripts against decision #1's fix and found
three more real issues — the first fix closed the *intended* window but left two related ones
open, and (a) below was actually never reaching the primary caller at all.

**(a) `reload()` discarded both `_stop_locked()`/`_start_locked()`'s results.** Decision #1
above fixed `bot.stop()`'s own contract, and `DELETE`'s route calls `stop()` directly — but
`PATCH /api/connectors/telegram` (enable/disable) and the AI's `set_connector_enabled` tool go
through `reload_best_effort()` → `reload()`, which called `_stop_locked()`/`_start_locked()`
and then returned `None` regardless of either result. Since `_stop_locked()` reports a failure
via its RETURN VALUE rather than an exception, `reload_best_effort()`'s own `try/except`
never caught anything — it reported `reloaded: true` even on a genuine teardown failure. Fixed
by making `reload()` return `stopped and started` (both `_stop_locked`/`_start_locked` now
share the same best-effort/non-raising/bool-returning contract) and `reload_best_effort`
return that value directly instead of a bare `True`.

**(b) Pause's `enabled=False` was never read on the inbound-message gate at all** — only the
stored `access_policy` was, and Pause never touches `access_policy`. So Pause's entire
security value rested on the live process actually being dead, which (a) showed wasn't
reliably reported. Fixed at the ONE read path both gates share:
`TelegramConfigStore.access_policy()` now returns `POLICY_DISABLED` whenever
`cfg.get("enabled")` is false, regardless of the stored value — the same fail-closed
short-circuit `reset()` already gave Remove, now also covering Pause, and enforced at the data
layer independent of whether the process teardown actually completed.

**(c) The `DELETE` 502's own retry advice defeated the ordering fix from decision #1.**
`_stop_locked()`'s compare-and-clear (`_app = None`) ran on the FAILURE path too, so after a
failed teardown the module believed itself stopped. A second `DELETE` call (exactly what the
502 message told the operator to do) then hit `current_app is None` and short-circuited to
`True` without ever re-attempting real teardown — the credential wipe would proceed on retry
against a bot never actually confirmed down. Fixed by only clearing `_app` on the SUCCESS path;
a retry now genuinely re-attempts teardown against the same object, and `is_running()` stays
honest (still reports live) in between.

All three were confirmed against the reviewer's own PoC scripts before and after the fix
(`poc_reload.py`, `poc_retry.py`) — both reproduced the bug pre-fix and no longer do post-fix.

## Known gaps (documented, not fixed this pass)

Three more LOW-severity findings from the round-2 re-verification were judged fine to document
rather than fix in this pass — each is a real, narrow residual, not a nitpick:

- **CLOSED for Email (2026-08-17, follow-up pass, `fix/email-config-unify`):** `send_email`,
  `search_messages` and `get_messages` now all carry `@tool(connector="email")` (they didn't
  when this ADR was written). `permission_owner()` resolves all three to `email`, so a decision
  made through EITHER the Email connector's own ladder OR the app-wide Settings › Permissions
  route lands under the same owner, and `clear_connector_permissions("email")` on Remove
  genuinely clears all three — see `tests/test_connectors_service.py::
  test_remove_connector_clears_all_three_email_tools_via_real_owner_resolution` and
  `tests/test_tool_permissions_api.py::test_put_resolves_send_email_to_the_email_connector_not_core`.
  This also fixed a SEPARATE, more severe defect the tagging surfaced: because the connector-
  scoped ladder (`PUT /api/connectors/{id}/permissions/{tool}`) always wrote under the URL's
  `connector_id` verbatim while dispatch (`_confirm_gate.effective_level`) resolved the untagged
  tools' owner as `core`, a decision set from Email's OWN detail page — the natural place to set
  one — was silently NEVER CONSULTED at dispatch. An operator who clicked "Always allow" on
  `send_email` from Connectors › Email kept seeing a confirm card on every real call; a "Blocked"
  set the same way did not actually block it. Tagging makes the write and read paths agree.
  `search_messages`/`get_messages` needed one more fix to tag safely: they are members of the
  read-shaped exemption (`GATE_EXEMPT_READ_SHAPED`, owner incident 2026-07-21 — "why does
  reading my own inbox need permission?"), which before this pass only applied to the reserved
  `core` bucket's own default computation. Tagging them alone would have flipped their default
  from `always_allow` to `needs_approval` (the uniform connector default), reopening that exact
  incident. `ai/tools/_confirm_gate.py::_default_level` now consults the exemption BEFORE the
  owner check, for any owner — a narrow widening, not a reopening of "no tiering by tool type
  inside a connector, no exceptions" (that settled rule is about a hard floor for a DANGEROUS
  action never being exempted; this is an ALREADY-reviewed bookkeeping-write staying exempt when
  its owner tag changes for an unrelated reason). Still open, unchanged from the original
  finding: **Webhook and Telegram have no AI tool of their own to tag at all** (webhook/Telegram
  are reachable only through the generic `notify` tool, which spans every channel and is
  deliberately untagged for the same reason `web_search`/`fetch_page` are — see
  `ai/tools/_connector_gate.py`'s module docstring) — there is nothing a Remove of either could
  clear beyond what decision #3 already covers.
- **`removeProviderRevokeNote` (the "you may also want to revoke access at the provider" copy)
  only covers Plaid/Enable Banking/Yodlee.** SimpleFIN, Lunch Money, YNAB, Alpaca, and IBKR
  credentials are equally still-live at the provider after a Remove, with no such note shown.
  Worth widening to every provider-backed connector later; scoped narrowly here because the
  original finding that prompted the note only named those three.
- **8 of the 9 native `reset()` implementations do a non-transactional read-then-write** (read
  `account_map()`, then `_write_raw` the fresh copy) — a concurrent sync's `link_account` landing
  in that exact window is lost (no security impact; a possible lost identity-mapping entry, not
  a credential leak or duplicate account). `LunchFlowConfigStore.reset` already does this
  correctly inside `_use_serialized_namespace_conn`'s transaction; the other 8 don't need to
  match it for this pass, but the gap is real and the pattern to close it already exists in the
  codebase if it's ever prioritized.

## Consequences

### Positive
- A Remove can no longer leave a live, credential-holding process running while the UI reports
  it fully removed.
- Re-adding a previously-removed aggregation connector no longer creates duplicate ledger
  accounts.
- A removed connector's tool permissions and session state genuinely reset, matching what the
  Remove confirmation copy already promised.
- `services/connectors.py` stays provably `ai`-free per import-linter, with the constraint that
  makes it so documented at both ends of the new edge.

### Negative
- `TelegramConfigStore.reset()`, and the DELETE route's Telegram branch, now encode ordering
  invariants (stop-then-reset) that are easy to regress if either side is touched in isolation —
  mitigated by `test_telegram_bot_lifecycle.py`'s lock-serialization/stop-failure tests and
  `test_connectors_api.py`'s stop-confirmation tests, but there is no compiler-enforced
  guarantee here.
- `account_map` preservation is now a per-store, hand-written exception to "reset means
  DEFAULTS" — a ninth aggregation connector added later needs the same treatment applied
  deliberately, not inherited for free.
- `session_map.py`/`session_reset.py` is one more module split driven by a layering
  constraint rather than a domain boundary — mildly less discoverable than a single file,
  offset by the docstring cross-references on both files.

## Agent provenance

- **Decided by:** security-reviewer (Claude) raised the findings; senior-engineer
  implementation lane (Claude) made the specific scoping/ordering calls documented above and
  implemented them.
- **Lane / branch:** `fix/connector-disconnect-unify`, worktree
  `.claude/worktrees/connector-disconnect-unify`.
- **Driving artifact:** security review of commits `4613b61c` (Pause/Resume/Remove unification)
  and `ce0bb43a` (Remove extended to Email/Webhook/Web Search), 13 findings, 2 blocking; then a
  round-2 PoC-driven re-verification of the fix itself (`poc_reload.py`, `poc_retry.py`), 3 more
  real findings plus 3 documented-only LOW items (decision #5 and "Known gaps" above).
- **Why this way (one line):** close every window a security review found real, using the
  narrowest fix that actually closes it — stop-then-reset instead of reset-then-hope,
  preserve only the identity mapping instead of the whole config, and split one function
  instead of building a new seam.
