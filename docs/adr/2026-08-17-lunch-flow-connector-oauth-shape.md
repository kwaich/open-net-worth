---
title: The Lunch Flow connector gets its OWN OAuth flow — mirrors the MCP OAuth shape, reuses none of its code
status: Accepted
date: 2026-08-17
deciders: implementing Claude agent (lunch-flow-connector lane)
---

# The Lunch Flow connector gets its OWN OAuth flow — mirrors the MCP OAuth shape, reuses none of its code

## Context

Lunch Flow (lunchflow.app) is a bank/investment-aggregation PLATFORM — OAuth-redirect bank
linking across 30+ countries, thousands of institutions — architecturally closer to Plaid/
Enable Banking than to a single static personal token (SimpleFIN, Lunch Money). This app
already has TWO precedents for an external OAuth-shaped flow:

1. **Plaid's Hosted Link** (`backend/services/aggregation/plaid.py`) — closest structural
   precedent for "an aggregation platform with owner-held developer credentials plus a
   per-instance linked item", but its own consent step is a Plaid-hosted `link_token` +
   public-token exchange, not a redirect-based OAuth 2.0 authorization-code grant.
2. **The MCP OAuth flow** (`backend/mcp/oauth.py`/`oauth_flow.py`/`oauth_pending.py`,
   `backend/api/mcp_oauth_api.py`, RFC `2026-08-14-mcp-oauth`) — a REAL OAuth 2.0
   authorization-code redirect, built earlier this session for external MCP servers. It is the
   closer shape (a browser redirect out, a callback route back in, a "did it finish yet" poll)
   but it is also RFC-8414/9207/Dynamic-Client-Registration/PKCE-shaped: it discovers an
   authorization server via `WWW-Authenticate` + Protected Resource Metadata, registers a
   client dynamically (DCR) when no client_id is already known, and always uses PKCE. Lunch
   Flow's own docs (`GET /api/platform/oauth/authorize?client_id=&redirect_uri=&email=&state=`,
   `POST /api/platform/oauth/token`) show none of that: ONE fixed, already-known authorization
   server, a manually-registered developer app (client_id/client_secret from Lunch Flow's own
   dashboard, configured into this app exactly like Plaid's), and no PKCE parameter anywhere in
   the documented request/response shapes.

So neither precedent fits verbatim. Building a third, independent OAuth implementation from
scratch would have thrown away real, reviewed security work (the single-use TTL-bounded
`state`-as-credential pattern, the auth-exempt-callback reasoning, the popup+poll frontend
dance) that the MCP OAuth lane already earned scrutiny for.

## Decision

Build Lunch Flow's OWN, connector-specific OAuth implementation
(`backend/services/aggregation/lunch_flow_oauth.py` + `lunch_flow_pending.py`,
`backend/api/lunch_flow_api.py`, `frontend/.../lunchFlowOAuth.ts` + `lunchFlowApi.ts`) that
**reuses the MCP OAuth flow's SHAPE, not its code**:

- A server-minted, single-use, TTL-bounded `state` staged in an in-memory map
  (`lunch_flow_pending.py`, a smaller sibling of `oauth_pending.py` — no PKCE verifier, no
  resolved-client/AS-metadata fields to carry, since there is nothing to discover: the
  authorize/token URLs are fixed constants) is the flow's actual credential, exactly like
  `oauth_pending.py`/`services/shortcuts/tickets.py` before it.
- `GET /api/lunch-flow/oauth/callback` is auth-EXEMPT (`api/_deps.py`'s `_EXEMPT`), for the
  identical reason the MCP OAuth callback is (ADR
  `2026-08-16-mcp-oauth-callback-is-auth-exempt-state-is-the-credential.md`): the vendor's
  hosted consent screen can take minutes, a session can expire mid-flow, and the redirect back
  may land on a browser whose cookie has gone stale. It answers the SAME fixed HTML page
  regardless of outcome — no oracle, nothing server/vendor/caller-supplied ever interpolated.
- `frontend/.../lunchFlowOAuth.ts` mirrors `oauthSignIn.ts`'s popup+poll controller almost
  line for line (open a blank popup synchronously before the first `await` so it survives the
  browser's "direct response to a click" heuristic, navigate it to the real `authorize_url`
  once `begin()` answers, race a `postMessage` from the callback page against a `GET
  /api/lunch-flow/oauth/{flow_id}` poll every 1.5s) — the exact same reasons apply, so the
  same dance was worth copying deliberately rather than re-deriving.

What it does NOT reuse: `mcp/oauth.py`'s discovery (`fetch_protected_resource_metadata`,
`fetch_authorization_server_metadata`), its DCR (`register_client`), and its PKCE
(`generate_pkce`) — none of that has anywhere to bind for a fixed, already-known authorization
server with a manually-registered client and no PKCE parameter in the documented flow. Lunch
Flow's `begin()`/`complete()`/`ensure_fresh_token()` are a plain OAuth 2.0 authorization-code
grant, the same shape `services/aggregation/plaid.py`'s comment block already describes for
Plaid's own consent flow, just via a real redirect instead of a hosted-link URL.

## Judgment calls made in this lane (flagged for the owner's review)

1. **No `POST /users` call in the connect flow.** Lunch Flow's docs describe TWO ways to
   obtain a token: `POST /users` (Basic Auth, returns `access_token`/`refresh_token` directly,
   no bank ever linked) and the OAuth authorize/token redirect (which the docs describe as
   handling "sign-in and bank connection" together). This connector uses ONLY the second path
   — `begin()` goes straight to `GET /oauth/authorize`, never calling `POST /users` first.
   Reasoning: the OAuth exchange's own documented response already carries everything
   `POST /users` would have (`access_token`/`refresh_token`/`user_id`), the `email` parameter
   on `/oauth/authorize` is documented as merely "pre-fill the user's email on the login page"
   (not "the email of an already-registered user"), and Lunch Flow's hosted login page is the
   one place a bank actually gets linked — `POST /users` alone would leave nothing to sync.
   **Unverified**: the live docs never explicitly confirm whether Lunch Flow's hosted login
   page also handles brand-new signup inline, or requires an existing account. If it turns out
   a `POST /users` call must run FIRST (to provision an account before `/oauth/authorize` will
   show a login rather than a dead end), the fix is additive — one more service call in
   `begin()`, no change to the staged-state/callback/token-exchange machinery above it.
2. **Single-operator collapsing "per end-user" credentials to one config row.** Lunch Flow's
   API is multi-tenant-shaped (a platform serving many end users), but this app is
   single-operator self-hosted — there is exactly ONE `lunch_flow_config` row, exactly like
   Plaid's own `client_id`/`secret`/`access_token` triple already models "one operator, one
   linked Item" rather than a user registry. `email`/`user_id` are informational fields on that
   one row, not a lookup key into a collection.
3. **The refresh race IS guarded, even though it's a low-probability race in a single-operator
   app.** `ensure_fresh_token()` uses an in-process lock (serializes concurrent callers within
   this one process — the realistic case: a scheduled poll and a manual "Sync now" click
   landing in the same second) PLUS a database-level compare-and-swap
   (`LunchFlowConfigStore.apply_token_refresh`, built on the same
   `_use_serialized_namespace_conn` seam `MarketProviderCircuitStore` already uses for exactly
   this shape) that refuses to overwrite if some OTHER writer already changed the row while the
   network call was in flight. Chosen over skipping the CAS entirely because: (a) the seam
   already existed in this codebase (near-zero marginal cost to use it correctly), (b) a lost
   refresh-token rotation is NOT self-healing — Lunch Flow's refresh grant may rotate the
   refresh token itself, and a naive last-write-wins under a real race would strand the
   connector needing a full manual reconnect, which is a materially worse failure mode than the
   few extra lines this costs.
4. **UNVERIFIED transaction sign convention.** Lunch Flow's docs show `"amount": 0` as a
   placeholder with no signed worked example. `_txn_amount` in
   `services/aggregation/lunch_flow.py` assumes the "standard" Open Banking polarity (POSITIVE
   = money in, NEGATIVE = money out) — the same polarity Enable Banking resolves to via its own
   explicit credit/debit indicator, and the OPPOSITE of Plaid's deliberately-inverted sign. The
   basis for this guess: each account object's `provider` field is documented as e.g.
   `"gocardless"`, and GoCardless/Nordigen's own Open Banking Data API uses this exact standard
   polarity. **This could not be verified against a live account** — no real Lunch Flow
   developer credentials exist in this environment. If the first real sync's income/expense
   classification comes out backwards, the fix is a one-line sign flip in `_txn_amount`,
   flagged prominently in that function's own docstring so it isn't missed.
5. **No investment-holdings sync in this pass.** Lunch Flow's docs also expose
   `GET /accounts/{id}/holdings` (brokerage-style positions), but this connector only wires the
   `_LEDGER` domain (accounts + transactions), matching Plaid/SimpleFIN/Enable Banking's own
   scope exactly — none of them sync holdings either. Wiring holdings would mean a genuinely
   different write choke (`positions_service.apply_position_change`, the shape Alpaca's
   connector uses) and unverified field semantics beyond what the docs gave
   (`security.figi`/`cusp`/`isin`, no clear own-institution symbol-mapping convention to build
   against). Deferred, not silently dropped — flagged here and in the module docstring.
6. **No real brand logo.** `lunchflow.app` declares exactly one icon site-wide: a genuinely
   reachable 96×96 32-bit `favicon.ico` (byte-verified: one embedded frame, no larger size
   inside the ICO container). That is below this codebase's 128px-or-vector bar for every other
   sourced connector mark, and the marketing site's `/brand` and `/press` paths return HTTP 200
   but are byte-identical to a deliberately bogus nonexistent path — a client-rendered SPA
   shell with nothing server-side to scrape, not a real source. Documented as a miss in
   `frontend/public/connector-logos/SOURCES.md`, same treatment as the FRED/SEC EDGAR misses
   already there — the connector falls back to its tinted category glyph rather than shipping
   an under-sized or fabricated mark.

## Consequences

### Positive

- The security properties this session already earned review scrutiny for (single-use
  TTL-bounded state-as-credential, auth-exempt callback with no oracle, popup+poll with a
  same-origin-checked `postMessage`) now protect a SECOND real credential-bearing flow, with
  the reasoning traceable back to the first instead of re-derived (and possibly re-litigated)
  from scratch.
- `lunch_flow_pending.py`/`lunch_flow_oauth.py` are each well under the file-size cap and
  carry no DCR/PKCE/resource-metadata complexity Lunch Flow's actual protocol doesn't need —
  building the MCP-shaped machinery here would have been dead weight, not defense in depth.

### Negative

- A third auth-exempt route now lives in `api/_deps.py._EXEMPT` (after `/api/shortcuts/
  download` and `/api/mcp-oauth/callback`) — every future reviewer of that set reasons about
  three exemptions, not two. Same trade every prior addition to that set made; still worth
  naming.
- Five explicit unverified/deferred judgment calls (above) ride on this connector shipping
  without a live Lunch Flow account to test against. Each is flagged in code (docstrings) and
  here, with a stated one-line-or-additive fix path — but a real first sync IS the only way to
  actually confirm #1 and #4.

## Alternatives considered

- **Literally reuse `mcp/oauth.py`'s functions** (discovery/DCR/PKCE) — rejected. None of that
  protocol machinery has anywhere to bind for a fixed AS with a manually-registered client and
  no PKCE parameter in the documented flow; forcing it through the DCR/discovery code paths
  would mean either faking metadata Lunch Flow never serves or forking that module's internals
  beyond recognition — worse than a small, purpose-built parallel.
- **Skip the refresh-race CAS entirely** (last-write-wins, matching how most of this
  connector's own `update()` calls already do a plain read-modify-write) — rejected for the
  token-refresh path specifically (judgment call #3 above): a lost refresh-token rotation is a
  qualitatively worse failure (full manual reconnect) than losing a stats counter increment,
  and the guarding seam was already sitting in this codebase.
- **Call `POST /users` before every `begin()`, defensively** — rejected as unverified-but-
  unnecessary complexity: the docs' own worked flow for the OAuth path never mentions it as a
  prerequisite, and adding a speculative extra network call+error-handling branch that might
  turn out to be dead code is worse than flagging the open question and keeping the fix
  additive if it's ever confirmed necessary.

## Security review addendum (2026-08-17)

A security review of this connector returned BLOCK on one finding and flagged four more. This
addendum records what changed and the reasoning for the two judgment calls the review left to
this lane (the sign convention's Part B, and the token-exchange content-type).

### Finding 1 — unverified sign convention had zero runtime signal (BLOCK)

**Part A — done in full.** `LunchFlowAdapter.map` now runs `_polarity_smell_test` over every
run's own income/expense split: >= 10 mapped rows with an implausibly high income share (>
80%) or every row landing on one side logs a warning AND writes a new `last_caution` field
(`LunchFlowConfigStore`), rendered in `LunchFlowSection.tsx` **unconditionally**, the same way
`last_error` already is — so it cannot go unseen on a technically-"ok" sync, closing the exact
gap the review named. When it fires, the connector's `enabled` flag is also flipped off in the
same write, so the 6h scheduler cannot keep silently repeating an unattended, unverified guess
— a human has to look at the caution and re-enable it. The sign-convention comment moved from
the module docstring (found "effectively invisible at the point that matters") onto
`_txn_to_draft`'s own branch, and the test fixtures' comment was rewritten to say plainly that
a green suite here does not confirm real-world polarity.

Implementing this also surfaced a real pre-existing bug one layer down: `LunchFlowConfigStore.
update()`'s "a `None` change means leave this field alone" contract (built for the REST config
PATCH surface) meant `record_status`'s old `self.store.update({"last_error": error, ...})`
call could never actually CLEAR `last_error` back to `None` on a clean run following a failed
one — it silently kept the stale value forever, and would have done the identical thing to the
new `last_caution` field. Fixed with a dedicated `LunchFlowConfigStore.record_run(...)` writer
where `None` is a real, settable value, over the same `_use_serialized_namespace_conn` seam
Finding 2 below hardens every other writer onto.

**Part B — deferred, not implemented.** The review's own text pre-authorized this: "if this
turns out to be a significantly bigger lift than Part A ... do Part A fully and report back on
why Part B is deferred." Tracing `MapResult.review_items` / `adapter.commit_review_items`
(`services/ingestion/base.py`/`orchestrator.py`) confirmed no adapter in this codebase
implements it yet — Lunch Flow would be the first real caller, not just a new user of an
existing pattern. Reasoned through what a CORRECT implementation needs: durable storage for a
first sync's draft transactions between "the scheduler ran unattended" and "the operator next
opens Settings" (existing `NamespacedSettingsStore`-style config-row storage was considered and
rejected — `LunchFlowConfigStore` lives in the checkpoint-EXCLUDED, NON-RESTORABLE
`secret_config` table, the wrong home for ordinary transaction drafts, not a place to smuggle
money-adjacent data into); a confirm/discard REST surface; a write path that still goes
through the ledger choke point (`ledger_service.create_transaction`/`bulk_create_transactions`)
on confirm; and a frontend control, which per this codebase's own Definition of Done is
DESIGN-FIRST and VERIFY-MANDATORY (an acceptance file, `make qa-e2e`, `make ui-gate`,
screenshots) — a materially different shape of work than a security patch, closer to this
repo's own `new-feature` lifecycle than a hardening fix. Given the size of the other four
findings in the same pass, building that safely in the same change was judged a worse outcome
than shipping it half-tested. **Interim mitigation**: Part A's caution is not a queue, but it
is no longer optional to notice — it persists, it renders regardless of status, and it
disables further unattended runs until a human looks at it. A follow-up ticket for the actual
review-queue seam (first real caller of `MapResult.review_items`) is the natural next step.

### Finding 2 — every writer but refresh could clobber tokens (MEDIUM, proven live)

Fixed mechanically, as the review predicted: `update`, `set_tokens`, `bump_stats`,
`link_account`, `forget_connection` (`LunchFlowConfigStore`) now each hold ONE
`_use_serialized_namespace_conn` transaction across their own read+write, the same seam
`apply_token_refresh`'s compare-and-swap already used — not a `get()` then a separate `save()`.
Two new regression tests (`test_lunch_flow_config_store.py`) reproduce the reviewer's exact
scenario with real threads (a writer paused mid-transaction via a monkeypatched `get`, a
concurrent `apply_token_refresh` racing it) and prove it can no longer complete while the first
writer holds the lock; both fail against the pre-fix code and pass after. A new
`test_two_concurrent_ensure_fresh_token_calls_make_exactly_one_network_call` mirrors the MCP
OAuth flow's own concurrency test — coverage the review noted was missing (refresh-vs-refresh
itself was already correct).

### Finding 3 — token exchange content-type (verify immediately)

**Switched to form-urlencoded** (`httpx.post(TOKEN_URL, data=payload, ...)`, was `json=`).
Genuine ambiguity, not a confirmed fact either way: Lunch Flow's own docs page
(`lunchflow.app/docs/api/platform-api-overview`) never shows a worked token-exchange request —
asking a fetch tool to summarize it once DID produce a fabricated `curl` example claiming JSON
with the right-looking field names; asking the SAME URL a second time for its content
**verbatim** showed no such example exists on the page at all. A targeted web search for any
independent confirmation of Lunch Flow's own content-type choice also came up empty. Per the
task's own instruction for this exact situation — "if you cannot find a definitive example
either way, SWITCH to ... form-urlencoded as the RFC-conformant safe default" — this resolves
to RFC 6749 §4.1.3's required `application/x-www-form-urlencoded`, not the JSON shape
previously guessed. If a live Lunch Flow account ever confirms the vendor genuinely expects
JSON, that is a one-line revert (`data=` → `json=`) with a note here, not a redesign.

### Finding 4 — stale reactive-401-retry docstring

Implemented the real retry rather than only correcting the docstring: `LunchFlowAdapter.
_reactive_401_retry` (`lunch_flow.py`) mirrors `mcp/client.py`'s `_rpc`/`_OAuthUnauthorized`
pattern — a live 401 (a new `_LunchFlowUnauthorized` marker, distinguished from every other API
failure in `_api_error`) triggers exactly one `ensure_fresh_token(force=True)` + retry, never a
loop; a second straight 401 escalates to a whole-sync `auth_error` rather than being silently
swallowed. Wired at both call sites `fetch()` makes (`fetch_accounts`, and each account's
`fetch_account_transactions`, threading the possibly-refreshed token through the rest of that
run). Two new tests cover it: a persistent-401 case (still ends `auth_error` after exactly one
retry, not a loop) and a transient-401 case (the retry succeeds and the sync completes `ok`) —
both mock the forced refresh's own `httpx.post` too, since without that this lane's existing
"no live network in these tests" invariant would have been silently broken by the very fix that
adds the retry (confirmed live during this session: the unmocked path reached the real
`lunchflow.app` host).

### Finding 5 — Disconnect had no confirmation

`LunchFlowSection.tsx`'s per-institution Disconnect now confirms via `window.confirm` before
firing `DELETE /api/lunch-flow/connections/{id}` — the same lightweight pattern this app's own
`StatementsSection.tsx`/`ImportModal.tsx` already use for a comparable single-button
irreversible action in a flat form section (the richer `ConnectorDetail.tsx` confirm-VIEW
pattern is reserved for that page's own "Remove connector," a different, page-level control).
Two new frontend tests (`LunchFlowSection.test.tsx`) prove the DELETE call is gated on
`window.confirm`'s return value; both fail against the pre-fix component.

### One-sentence notes requested for items left out of scope

- **Credit-card/loan compounding**: added to `lunch_flow.py`'s module docstring, beside the
  sign-convention note (not just here) — hardcoding every account to `kind="asset"`/
  `type="bank_transfer"` means a credit-card or loan account inherits BOTH the raw-negative-
  balance assumption AND the unverified sign guess, so a wrong call on either axis compounds
  rather than cancels out for that account type specifically.
- **`X-Forwarded-Host`-derived `redirect_uri`**: already defended in depth (`detect_base_url`
  never trusts it for an authorization decision — see `api/shortcuts.py`'s own docstring; the
  redirect only ever reaches an operator-visible page). The actual backstop this lane relies
  on but does not control is Lunch Flow's OWN redirect-URI allowlist on their authorization
  server — an assumed vendor-side control, not something verifiable from this codebase.
- **Missing PKCE**: already correctly justified (judgment call #3 above), worth stating
  explicitly for a reader comparing the two OAuth lanes side by side — this flow has ONE
  security layer on the authorization-code grant (the single-use, TTL-bounded `state`), where
  the MCP OAuth work has TWO (`state` + PKCE); do not assume parity between them.

## Agent provenance

- **Decided by:** implementing Claude agent, branch `feature/lunch-flow-connector`
  (worktree `.claude/worktrees/lunch-flow`).
- **Alternatives weighed:** literal MCP OAuth code reuse (rejected — wrong protocol shape);
  skipping the refresh CAS (rejected — token rotation loss is not self-healing); a defensive
  `POST /users` pre-call (rejected — unverified against the live docs, kept as an additive
  follow-up instead of speculative code).
- **Driving artifacts:** the task's own architecture guidance (mirror Plaid's config-store
  shape + reuse the MCP OAuth flow's SHAPE, not its code); Lunch Flow's live docs, fetched and
  cross-checked against this ADR's own summary during this session (base URL, both auth tiers,
  the OAuth authorize/token field names, `GET /accounts`/`GET /accounts/{id}/transactions`/
  `DELETE /connections/{id}` response shapes — all confirmed field-for-field against
  `lunchflow.app/docs/api/platform-api/*`); precedent read in full before writing a line:
  `backend/services/aggregation/plaid.py`, `backend/mcp/oauth.py`/`oauth_flow.py`/
  `oauth_pending.py`, `backend/api/mcp_oauth_api.py`, `docs/adr/
  2026-08-16-mcp-oauth-callback-is-auth-exempt-state-is-the-credential.md`,
  `frontend/.../oauthSignIn.ts`.
- **Why this way (one line):** the flow's only correctness requirement is "complete the ONE
  redirect this unguessable, single-use, TTL-bounded value names" — the exact same requirement
  the MCP OAuth callback already satisfies, so its SHAPE was worth mirroring deliberately;
  Lunch Flow's actual wire protocol underneath that shape is genuinely simpler (no DCR, no
  PKCE, no discovery) and reusing the heavier machinery built for that harder problem would
  have been the wrong kind of consistency.

### Addendum provenance (security review, 2026-08-17)

- **Decided by:** implementing Claude agent, branch `feature/lunch-flow-connector`, responding
  to a security review's BLOCK finding + four supporting findings.
- **Alternatives weighed for Finding 1 Part A's visibility mechanism:** overloading
  `last_status` with a new value (rejected — a new status string ripples into every consumer
  that branches on it: the scheduler, `classify_error`, the frontend badge map, automations'
  `observe_sync_result` — for a signal that is or ought to be ORTHOGONAL to "did the sync
  mechanically succeed") vs. a dedicated `last_caution` field rendered unconditionally, mirroring
  `last_error`'s already-proven always-visible pattern — chosen for the smaller, more precisely
  targeted blast radius.
- **Alternatives weighed for Finding 1 Part B:** build a full pre-commit review queue in this
  pass (rejected — genuinely new data shape + choke-respecting write path + REST + a
  DESIGN-FIRST/VERIFY-MANDATORY frontend control, disproportionate to a hardening pass run
  alongside four other findings) vs. Part A fully plus an auto-disable-on-caution safety net
  using only existing primitives (chosen — real, immediate, bounded risk) vs. doing nothing for
  Part B (rejected — the review explicitly required an explicit, reasoned deferral, not silence).
- **Alternatives weighed for Finding 3:** trust the fetched "verbatim" quote from the docs page
  (rejected once a second, explicitly-verbatim fetch of the SAME url showed no such example
  exists — the first result was a fabrication) vs. the RFC-conformant default the task's own
  instructions named for exactly this ambiguity (chosen).
- **Driving artifacts:** the review's own five findings (verbatim in the launching task); live
  re-fetch of `lunchflow.app/docs/api/platform-api-overview` and `.../api-reference/introduction`
  (both attempts, including the contradicting one, described in Finding 3's writeup above);
  `backend/mcp/client.py`'s `_rpc`/`_OAuthUnauthorized` (the reactive-401 pattern mirrored for
  Finding 4); `tests/test_mcp_oauth_flow.py`'s concurrency tests (the shape mirrored for
  Finding 2's new coverage); `backend/db/payload_store.py`'s
  `NamespacedSettingsStore._use_serialized_namespace_conn` (the seam every Finding-2 writer now
  routes through, already proven correct by `apply_token_refresh`).
