---
title: DNS-rebind-safe outbound fetch — resolve once, PIN the connection to that IP
status: Accepted
date: 2026-07-16
deciders: orchestrating agent (Claude) + INT5 security gate (LOW-1 finding, owner-dispatched)
---

# DNS-rebind-safe outbound fetch — resolve once, PIN the connection to that IP

## Context

`core.net_guard.assert_public_url` validates that a URL's resolved IP is public (rejects
loopback/private/link-local/reserved/multicast/metadata addresses) and returns the URL
**string**. Every caller then hands that string to a normal HTTP client
(`urllib.request.urlopen`, `httpx`), which **re-resolves DNS again** when it actually opens the
connection.

That gap is a classic **DNS-rebinding TOCTOU**: an attacker's domain runs an authoritative
nameserver with a very low TTL. It answers a public IP the first time (the validation lookup, so
`assert_public_url` passes) and a private/internal IP the second time (the connect lookup,
seconds later) — `169.254.169.254` (cloud metadata), `127.0.0.1`, or a host on the operator's LAN.
The guard's own logic never sees the bad address; it validated a *different* answer.

This was flagged by the INT5 security gate (LOW-1, 2026-07-15) as pre-existing codebase posture,
newly **reachable unattended**: INT5 shipped a 90-second hot RSS poll loop over
`custom_feed_urls` (user-settable), so a planted rebinding feed URL gets probed automatically
every 90s with no operator in the loop. INT9 Phase B (parallel lane) is adding user-added source
URLs that funnel through the same `monitoring.collect._fetch_feed` path — fixing the **shared**
mechanism means both inherit the fix without either lane touching the other's files. Exploit
payoff is bounded here (fetched content lands only in the user's own evidence store / outbound
channels — no code execution, no credential access), but the fix should be structural, not a
per-caller patch.

Three call sites carry the guard-then-fetch pattern most exposed to attacker-supplied URLs:
`monitoring.collect._fetch_feed` (RSS/Atom feeds, the hot loop), `ai.search.base.plain_get_fetch`
(the model-reachable `fetch_page`/`web_search` fallback fetch, over `urllib` via a rewrite —
previously `httpx`), and (unmigrated, see Alternatives) `services.favicons`, `services.prices`,
`api.extensions`, `monitoring.asset_brief` — lower-priority callers documented below.

## Decision

**Option A — pin-and-connect.** `core.net_guard.resolve_public_url(url)` now returns
`(clean_url, ip)`: the same validation as `assert_public_url`, plus the **first validated address
from the same `getaddrinfo()` call** used to check every address. `assert_public_url` becomes a
thin wrapper (`clean, _ = resolve_public_url(url); return clean`) — byte-identical behavior for
every existing caller that only wants the validated string.

A new leaf module, `core.pinned_http`, provides `pinned_urlopen(url, *, headers, timeout,
max_redirects)` — pure stdlib (`urllib`, `http.client`, `ssl`, `socket`):

1. Calls `resolve_public_url(url)` — one resolution, one validation.
2. Builds **one-shot** `HTTPConnection`/`HTTPSConnection` subclasses whose `connect()` dials the
   **pinned IP literal** directly (`socket.create_connection((pinned_ip, self.port), ...)`) —
   `self.host` is left untouched as the **original hostname**, so the `Host` header (http) and
   SNI + certificate-hostname verification (https, `self._context.wrap_socket(sock,
   server_hostname=self.host)`) still target the real name. The OS resolver is never asked again
   for this request — a rebinding second answer has no channel to reach the connect step.
3. Builds a one-shot `urllib.request.OpenerDirector` bound to those connection classes, with the
   default `HTTPRedirectHandler` **stripped** — a 3xx response raises `HTTPError` instead of
   being auto-followed.
4. On a 3xx `HTTPError`, resolves the `Location` (joined against the current URL) and repeats the
   ENTIRE cycle (resolve → validate → pin → connect) for that hop, up to `max_redirects` — so a
   redirect to a rebinding host is refused on the hop, and each hop gets its own honest pin
   (matches the pre-existing manual-redirect posture in `collect.py`/`favicons.py`/`base.py`,
   which this centralizes rather than replaces). A redirect status with NO `Location` header has
   nothing to follow — `pinned_urlopen` returns it as the FINAL response (the `HTTPError` object
   is itself a file-like wrapper over that body via `urllib.error.HTTPError`'s `addinfourl`
   ancestry), preserving `plain_get_fetch`'s pre-existing "treat the body as final" behavior
   rather than turning a harmless edge case into a hard failure.

`monitoring.collect._fetch_feed` and `ai.search.base.plain_get_fetch` now call
`pinned_urlopen` instead of building their own `urllib.request.build_opener(...)` /
`httpx.AsyncClient(...)` — `plain_get_fetch` runs the (blocking, stdlib) call via
`asyncio.to_thread` and no longer imports `httpx` at all. Both call sites shrank (the redirect
loop and guard call move into the shared primitive) rather than grew.

## Consequences

### Positive
- The TOCTOU is closed for the two most attacker-reachable paths: the unattended 90s feed-poll
  loop and the model-reachable page fetch. INT9 Phase B's user-added source URLs inherit the fix
  automatically (same `_fetch_feed` choke), with zero changes to that lane's files.
- One mechanism, `core.pinned_http`, for any future urllib-based fetcher to adopt with a one-line
  swap (`build_opener(...)` + inline redirect handling → `pinned_urlopen(...)`).
- `assert_public_url`'s public signature and behavior are unchanged — no ripple through the
  ~10 other call sites that only need the validated string.

### Negative / residual risk
- **Not migrated in this lane**: `services.favicons._safe_fetch` and `services.prices`'s
  CoinGecko/Yahoo fetchers (`_CgGuardedRedirect` + friends) and `api.extensions`'s connector-fetch
  path still call `assert_public_url` then fetch via `httpx`/`urllib` unpinned — the TOCTOU
  remains open there. Rationale for deferring: `favicons.py` already documents this exact gap in
  its own docstring as a known, deliberately-deferred limitation, mitigated in depth by its rate
  limit + auth gate + magic-byte body validation (an attacker who wins the rebind still only gets
  a favicon-shaped image cached, or nothing if the body doesn't look like one); `prices.py`'s
  CoinGecko/Yahoo targets are fixed, operator-independent API hosts, not attacker-influenced
  domains, so the rebind precondition (attacker controls the DNS answer for a URL an operator
  chose) doesn't apply the same way. httpx does not expose a request-level "connect to this IP,
  verify against that hostname" hook the way `http.client` does, so migrating those would mean
  either dropping httpx there too (larger diff, more callers, out of this lane's scope) or a
  custom httpx transport (deferred — real complexity, no attacker-reachable payoff identified for
  those hosts today). Follow-up: migrate `favicons.py` (the other feed/LLM-derived-domain path)
  onto `pinned_urlopen` in a dedicated lane once its httpx `iter_raw` streaming/redirect logic is
  ported.
- **Proxy env vars are a two-sided tradeoff, not a gap** (traced during the consolidated review
  round): the pinned `connect()` override dials `(pinned_ip, self.port)` **unconditionally** and
  never consults `self._tunnel_host` (the state `urllib.request.ProxyHandler` would normally set
  up for CONNECT tunneling from `http_proxy`/`https_proxy`). The safe side: a `http(s)_proxy` env
  var therefore CANNOT reopen the DNS-rebind hole by routing a pinned fetch through an
  attacker-influenced proxy resolution — the pin dominates regardless of proxy config. The cost
  side: an operator who deliberately runs an egress proxy for these specific fetchers (firewall
  policy, outbound audit logging, a captive network) will find `pinned_urlopen` silently bypasses
  it — the request goes straight to the pinned IP, not through the configured proxy. Neither
  fetcher this ADR migrates (RSS feeds, the model's page fetch) currently document proxy support,
  so this is a latent behavior change rather than a regression against a stated contract, but it's
  a real one if an operator adds proxy config later expecting it to apply here.
- `pinned_urlopen`'s per-hop resolution still trusts a single `getaddrinfo()` snapshot; a
  resolver that itself lies within one call (compromised recursive resolver, not a TTL race) is
  out of scope — DNSSEC / DoH hardening is a separate, much larger concern.
- **Decode fidelity**: the stdlib path decodes the body using the response's declared
  `Content-Type` charset, falling back to UTF-8 with `errors="replace"` on the whole body. The
  prior `httpx`-based `plain_get_fetch` used `.text`, which additionally sniffs an undeclared or
  wrong charset (chardet/charset-normalizer-style detection). A page with no charset header and a
  non-UTF-8 encoding may now decode with replacement characters where `httpx` would have guessed
  correctly — an accepted fidelity tradeoff for dropping the httpx dependency from this path, not
  a security concern (worst case is garbled text, not misinterpreted bytes).

### Follow-ups
- Migrate `services.favicons` onto `pinned_urlopen` (dedicated lane; needs the raw-body streaming
  cap ported off `httpx.iter_raw`).
- Consider whether `services.prices`/`api.extensions` warrant the same treatment if their target
  hosts ever become user-configurable.

## Alternatives considered
- **Option B (post-connect peer verification)** — after connecting, read the peer address and
  compare against the validated set. Rejected as the primary mechanism: `http.client`/`urllib`
  don't expose the connected peer address before the request is sent without the same kind of
  `connect()` override Option A already requires, so it buys nothing over pinning and is weaker
  (a race between "verify peer" and "send request" still exists unless the socket itself is
  pinned — pinning the connect target *is* the verification).
- **Option C (resolve-once + patch the global resolver for the request)** — rejected: mutating
  process-global DNS resolution (`socket.getaddrinfo`) for the duration of one request is a
  thread-safety hazard in this async app (concurrent requests would race on the patched
  resolver); a per-connection `connect()` override has no such shared state.
- **Full httpx-transport rewrite (pin at the httpx layer for every caller)** — deferred: httpx's
  pinning story requires a custom `NetworkBackend`/transport reaching into `httpcore`
  internals — a much larger, less-obviously-correct change than overriding `http.client`
  connect(); `urllib` was already the substrate for `collect.py`, so pinning there is a natural
  fit, and `plain_get_fetch` moving off httpx onto the same stdlib primitive keeps ONE mechanism
  instead of two.

## Agent provenance
- **Decided by:** orchestrating agent (Claude Fable 5), lane `lane/sec-dns-rebind-pinning`,
  dispatched from the INT5 security gate's LOW-1 finding (board item, 2026-07-15/16).
- **Driving artifacts:** `docs/launch/BOARD.md` Inbox item (`lane/sec-dns-rebind-pinning
  DISPATCHED 2026-07-16`); INT5 security gate report (`.claude/rfcs/news-ingestion-latency.md`
  lane); this ADR's own regression tests (`tests/test_pinned_http.py`,
  `tests/test_net_guard.py`). A consolidated security-PASS + code-review-PASS round on the same
  lane found and fixed 5 items: an unbounded body read in `plain_get_fetch` (now capped at
  `_MAX_FETCH_BYTES`), a semantics regression on a Location-less 3xx (restored the pre-existing
  "treat body as final" behavior instead of raising), the proxy/TCP_NODELAY comment + ADR
  inaccuracies fixed above, and the collect-test doubles repointed at the real
  `_build_pinned_opener` seam.
- **Why this way:** the codebase already builds redirect-safe fetches on `urllib.request` for
  every non-httpx caller (`collect.py`, `prices.py`, `favicons.py` all share the
  `_GuardedRedirect`/manual-loop idiom) — pinning at the `http.client.HTTPConnection.connect()`
  level is a small, precise override that slots into that existing idiom instead of inventing a
  new one, and it is provably correct for TLS (SNI + hostname verification both keep targeting
  the real hostname, verified by test, not by inspection alone).
