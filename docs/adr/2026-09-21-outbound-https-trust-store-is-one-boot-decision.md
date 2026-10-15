---
title: Outbound HTTPS trust store is one boot-time decision; no bare urlopen outside core
status: Accepted
date: 2026-09-21
deciders: orchestrating Claude agent + repo owner
---

# 2026-09-21. Outbound HTTPS trust store is one boot-time decision; no bare urlopen outside core

## Context

**The incident (2026-09-21).** A friend of the owner installed the app on a macOS Python from
python.org and never ran its `Install Certificates.command`. That build has NO CA bundle at
OpenSSL's compiled-in path, so every stdlib `urllib.request.urlopen` HTTPS call builds an EMPTY
trust store and fails `CERTIFICATE_VERIFY_FAILED`. Meanwhile yfinance (curl_cffi) and httpx
carry certifi's bundle and kept working — so quotes and charts looked healthy and only the
things riding bare `urllib` died: the Cmd+K market search (`_yahoo_equity_candidates`,
`search_symbol`, the CoinGecko client), and, latently, Telegram rich messages, webhook
channels, the MCP HTTP transport and MCP OAuth. Every one of those failures was swallowed —
the search ones at DEBUG, below the app's INFO logger — so "palantir" returned an empty list
with HTTP 200 and no evidence anywhere. Two other machines with populated stores could not
reproduce it.

`core/pinned_http.py` already knew this scar: its `_https_context()` fell back to certifi for
pinned fetches (RSS, favicons, imports). The knowledge lived in one private helper that most
call sites never reached. The dependency rule (`CLAUDE.md`) makes `core` the only shared leaf
every layer can import, so a process-wide policy has to live there.

## Decision

We will treat the outbound TLS trust store as ONE process-wide decision made at boot, and
forbid a bare stdlib HTTPS call anywhere else:

1. `core.pinned_http.ensure_default_trust_store()` runs from `backend/main.py` immediately after
   `.env` loads, before any SSL context exists. An operator `SSL_CERT_FILE` that exists (or an
   `SSL_CERT_DIR` directory) wins untouched; a DANGLING `SSL_CERT_FILE` is repaired, not
   respected (OpenSSL honours it over the compiled-in path, yielding exactly the empty store);
   then the platform bundle if present; else certifi's, by setting `SSL_CERT_FILE` for the
   process. Verification is never loosened. The fallback logs one WARNING naming the remediation.
2. Every `urllib.request.urlopen(...)` / `build_opener(...)` outside `backend/core/pinned_http.py`
   passes an explicit verified context from the public `core.pinned_http.https_context()`
   (`context=` or an `HTTPSHandler(context=...)`). `tests/test_outbound_tls_guard.py` parses the
   backend AST and fails on any call without one; loopback-only callers are allow-listed by path.
3. Yahoo search rides yfinance's managed session like every other Yahoo call, and a provider
   failure in search is logged at WARNING with the exception class and message.

## Consequences

### Positive
- One machine-level defect is fixed for every stdlib caller at once, including the four that
  had not yet bitten anyone.
- The belt (per-site context) and the brace (boot hook) fail independently; the guard test
  keeps a new call site from regressing either.
- The failure is now visible in `app.log` at INFO level with its cause.

### Negative
- Setting `SSL_CERT_FILE` mutates process environment, which child processes (stdio MCP
  servers) inherit. That is the intended effect but is a global.
- The guard is AST-shaped, not runtime: a wrapper that hides a bare `urlopen` behind another name
  in `core` would pass it. Reviewers must keep `core/pinned_http.py` the only opener factory.

### Follow-ups
- Surface the hook's decision in the health endpoint or Settings diagnostics so support starts
  with the answer.
- Consider declaring `certifi` as an explicit dependency in `pyproject.toml` (today it arrives
  transitively via httpx and curl_cffi).

## Alternatives considered
- Per-call-site `context=https_context()` only — rejected alone because the next bare call
  repeats the bug; kept as the belt beside the boot hook.
- Boot hook only — rejected alone because a site that builds its own context bypasses it and
  nothing would flag it; the guard needs a per-site shape to check.
- `pip install truststore` / OS-store injection — rejected: another dependency, platform
  branches, and it does not fix an interpreter with no store at all.
- Disabling verification on failure — rejected outright; never.
- Telling users to run `Install Certificates.command` — kept as the remediation in the log line,
  rejected as the fix: users install with `uv tool install` and never see that script.

## Agent provenance

- **Decided by:** orchestrating Claude agent (Fable 5.1), repo owner confirming scope ("fix all
  of these")
- **Lane / branch:** `main`, working tree alongside the owner's uncommitted category work
- **Alternatives weighed:** per-site context only; boot hook only; truststore; verify-off
- **Driving artifacts:** owner's friend's probe output (bare `urlopen` fails
  `CERTIFICATE_VERIFY_FAILED`, yfinance session and certifi-context `urlopen` both succeed on
  the same URL); `core/pinned_http._https_context` docstring naming the same scar
- **Why this way (one line):** the trust store is a property of the process, so decide it once
  where every layer can reach it, and make the per-site shape mechanically checkable.
