---
title: The in-browser terminal is a gated shell that shares the CLI providers' environment filter
status: Accepted
date: 2026-10-03
deciders: W1 agent-app workstream (Claude) + repo owner
---

# The in-browser terminal is a gated shell that shares the CLI providers' environment filter

## Context

A Private Cloud server has no computer of the customer's to run `claude` / `codex login` on, so
the subscription path needs a terminal in the app. A WebSocket PTY is the most powerful thing the
app can hand a browser: cross-site WebSocket hijacking, a stolen API token, a shell inheriting the
app's secrets, and an unbounded number of shells are each a full compromise. The Claude Code and
Codex providers already filter the environment of their children, but each kept its own copy of the
list, in `ai/`, where `services/` cannot import it.

## Decision

1. `/api/terminal` exists only in gateway mode or with `ONW_TERMINAL_ENABLED=true` (off by default),
   on POSIX only. Every gate lives in `services/terminal.py` and is enforced on the WebSocket itself:
   an exact same-origin `Origin` check (a missing one is refused), a signed-in identity that is **not**
   an API token (gateway mode: the gateway-signed session; self-host: a login-session cookie of a
   configured login, never loopback trust or a disabled login), a gateway session minted in the
   last 15 minutes (`X-Onw-Session-Started`), one terminal at a time, a 15-minute input-idle timeout,
   a 64 KiB message cap and flow control on output.
2. The child's environment is `core.child_env.scrubbed_environ()`, an ALLOWLIST (PATH, HOME, locale,
   terminal, proxy and certificate variables) shared by the Claude CLI, the Codex CLI, the Codex app
   server, the Antigravity CLI and the terminal; each caller adds explicit keys (a stored provider key).
   A blocklist missed `AUTH_*` and anything the operator exported.
   The app process itself is made unreadable to the shell: at boot, when gateway mode or the
   terminal is on, `core.process_hardening` sets `PR_SET_DUMPABLE=0` (Linux; `/proc/<pid>/environ`,
   `/proc/<pid>/mem` and ptrace are refused to the same uid) and pops the gateway token and login
   variables from `os.environ` after Settings has read them. The kernel's original environ block is
   not rewritten by unsetenv, so non-dumpable is the real protection and the pop is belt-and-braces.
   The shell CAN still read the app's data directory (database, connector credentials, API keys) — the
   owner's own data, accepted and stated in the docs.
   The shell's process group is recorded at spawn and signalled before the shell is reaped.
3. The pty lives behind a `core/pty_session.py` seam (POSIX imports are lazy; `available()` is false
   on Windows), the shell is the root of its own session and is hung up (SIGHUP, so an interactive
   shell passes it on to its jobs) then killed on close.
4. `require_auth` takes `HTTPConnection` so the app-wide gate also covers WebSocket routes.
5. "Terminal opened" is appended to the run history (kind `system_job`) and the log.

## Consequences

### Positive
- One list of what a child process must never inherit; adding a secret variable protects all four.
- A WebSocket cannot be added to the app without the same auth gate as every HTTP route.

### Negative
- A stale gateway session cannot be sent through re-authentication by the agent alone: it does not
  know the gateway's `reauth` URL, so the status endpoint answers 401 with `code:
  fresh_session_required` and the gateway has to turn that into its `reauth` response.
- The gateway must forward `Origin` untouched and either preserve `Host` or set `X-Forwarded-Host`.

### Follow-ups
- Per-process resource limits (cgroup) are the host's job; the app does not set `RLIMIT_NPROC`.

## Alternatives considered

- A device-code sign-in for subscriptions instead of a terminal — rejected for launch (owner
  decision 6 in the design: terminal first).
- Keeping each provider's own env list and copying it into the terminal — rejected: they had
  already drifted (none stripped `AUTH_*`).
- Bearer-token access to the terminal for scripts — rejected: the token is given to Shortcuts/MCP
  clients that must never get a shell.

## Agent provenance

- **Decided by:** W1 agent-app workstream (Claude)
- **Lane / branch:** `build/w1-agent-cloud`
- **Alternatives weighed:** device-code sign-in; per-provider env copies; token-authenticated shell
- **Driving artifacts:** Private Cloud contracts C10, gaps A4
- **Why this way (one line):** a shell must be refused by default and prove who, where from, how recently and how many on the socket itself.
