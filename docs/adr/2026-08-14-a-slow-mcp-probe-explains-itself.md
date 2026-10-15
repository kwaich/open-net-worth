---
title: A slow MCP probe explains itself — the child's stderr is captured, quoted, and polled, not streamed
status: Accepted
date: 2026-08-14
deciders: owner (build both halves) + implementing Claude agent (mcp probe-progress lane)
---

# A slow MCP probe explains itself — the child's stderr is captured, quoted, and polled, not streamed

## Context

The owner tried to add the IBKR MCP server using the vendor's own documented config:

```json
{"mcpServers": {"interactive-brokers": {"command": "npx", "args": ["-y", "interactive-brokers-mcp"]}}}
```

It failed. The diagnosis was **not a protocol failure — a cold-install timeout.** The package
unpacks to ~264 MB and its first npm download alone took **61.2s**, so the probe's 60s budget
killed `npx` before the MCP server had ever started. Warm-cache, the identical probe finished in
**3.64s** and discovered all 14 tools.

Raising `PROBE_TIMEOUT_STDIO_SECONDS` (60 → 300) fixes the failure. It does not fix the
**experience**, which is the part that made a working server look broken: for the whole minute
the UI showed a spinner and one sentence, and the failure said `mcp server 'x' timed out after
60s`. There was no way to tell *still downloading* from *this server is broken* — and the two
call for opposite responses (wait vs. fix your command).

The information was there the entire time. `stdio_client` spawned the child with
`stderr=subprocess.DEVNULL`: npm narrates its download on stderr, and we were discarding the only
signal that would have explained the wait.

## Decision

### 1. Capture the child's stderr — with a dedicated reader thread and two independent bounds

`backend/mcp/stdio_stderr.StderrTail`. `stderr=subprocess.PIPE` plus a daemon thread draining
continuously from spawn to EOF.

The thread is not a style choice. **A pipe nobody drains deadlocks the child** once the OS buffer
fills (64 KiB on Linux, less on macOS) — the child blocks in `write`, never answers `initialize`,
and the probe times out for a reason that has nothing to do with the server. A chatty installer
hits that easily. `proc.communicate()` is not an alternative either: it blocks the caller, and the
caller is mid-JSON-RPC on the other two pipes.

Bounded twice over, mirroring the discipline `MAX_LINE_BYTES` already imposes on stdout: a
per-line cap (2 KiB — bytes past it are **dropped as they arrive**, never buffered and trimmed
later, so one endless line costs the cap rather than what was sent) AND a rolling window of the
last 12 lines capped at 8 KiB total. A hostile or broken server can stream forever; memory here
is O(cap).

`\r` counts as a line break alongside `\n`, because an npm/pip download bar redraws in place with
bare carriage returns and may not emit a single newline for a minute — the one line that explains
the wait would otherwise never commit.

**The reader owns closing the pipe**, in its own `finally`. Closing an fd from another thread
while this one sits in `read1` is an fd-reuse hazard. Teardown order is therefore: kill the
process group (which closes the write end, which ends the read), *then* join the reader with a
1s bound, then close stdin/stdout. The thread is a daemon and the join gives up rather than
waiting, so a grandchild that somehow escaped the process group can delay one fd's close but can
never keep the process alive or block a teardown.

### 2. Quote the tail in the error — sanitized, at the one place it enters a message

A timeout now reads `… timed out after 300s — last output: npm http fetch GET 200 … 264MB`, and
`exited before replying` gets the same treatment (that is where `npm ERR! 404 Not Found` lives —
the "your command is wrong" case, previously just as mute).

A child that printed **nothing** says so explicitly rather than getting a fabricated cause.
Silence is itself a fact, and it is precisely the fact that separates a wedged server from a
downloading one.

This is server-chosen text reaching the UI and persisted as `last_error`, so it goes through
`core.untrusted.sanitize_untrusted` + a cap — the standing rule for every server-chosen string
(ADR `2026-08-13-what-a-model-may-see-of-a-connector`): a hostile server must not forge
`«user_msg:hex»`, the one fence meaning the operator is speaking.

### 3. Live progress — POLLED, and the probe stays synchronous

`GET /api/mcp-clients/probe/{probe_id}/progress` returns `{running, elapsed, lines}` and the add
flow polls it once a second while `POST /api/mcp-clients/probe` is still in flight. The token is
client-minted (the POST has not returned yet — there is no earlier moment for the server to hand
one out; the idempotency-key shape) and validated as an opaque `[A-Za-z0-9_-]{8,64}` key.

**The obvious precedent was SSE** — `ai/live_sessions` + `GET /api/sessions/{id}/live`, which is
how every chat turn already pushes in-flight state to the browser. It was evaluated and rejected
on three counts:

* **Mechanically, it would be a poll wearing a costume.** `probe()` is synchronous blocking code
  (subprocess + urllib) running in a threadpool worker; `live_sessions.publish` fans out through
  `asyncio.Queue`, which is not thread-safe from a worker thread. Any push design needs a
  threadsafe bridge over a polled buffer underneath.
* **The precedent's reasons do not apply.** `live_sessions` exists because a turn genuinely
  outlives its request — reconnect, replay, multiple watchers, mid-turn injection. A probe is
  modal-bound, single-watcher, non-resumable, and hard-capped at 300s.
* **Synchronous is a SAFETY property here, not just a simpler one.** The `with
  make_connection(...)` block inside the request thread is what guarantees the process group is
  reaped on every exit path, and "an unreached server is never persisted" is structural because
  `probe()` never touches the store. A 202 + background-job design would add a job registry, an
  expiry sweeper, and a fresh way to orphan a 264 MB `npx` install — to deliver the same handful
  of lines.

The channel is in-process, ephemeral and bounded (≤8 channels, swept on TTL), and every published
line is sanitized + capped at the ingress: **progress output is no more trustworthy than error
output.** An unknown or junk token yields the same uniform "not running" shape — a poll landing
either side of the probe is the normal case, and a bad token must cost the display, never the
connection test the user actually pressed a button for.

### 4. The UI states the distinction rather than implying it

The connecting step carries an elapsed clock (which moves even when the command is silent), the
child's own output (which stops when it stalls), and — only after 10s, so a normal 3.6s connect
never sees an unprompted excuse — the actual root cause in the user's words: *a first connect
downloads the package before the server starts, which can take a few minutes; later connects take
seconds.* When nothing has been printed at all, it says so.

## Consequences

* A cold install now looks like an install. The two failure modes are distinguishable from the
  first line of output, and from the elapsed clock when there is none.
* `stderr` is no longer discarded, so a stdio server's own diagnostics reach `last_error` for the
  saved-server path (`test_connect`) too, not just the probe.
* The probe endpoint's contract is unchanged — still one synchronous POST returning the full
  result, still persisting nothing. Progress is strictly additive; a client that sends no
  `probe_id` behaves exactly as before.
* A stdio child now has a pipe on stderr rather than `/dev/null`. That pipe MUST stay drained;
  `StderrTail` is the only sanctioned way to attach it.

## Alternatives considered

* **SSE via `live_sessions`** — see above. Rejected as complexity with an orphan-process risk
  attached, not as a bad idea in general.
* **A single global "current probe" slot instead of a token** — simpler, but two tabs probing at
  once would cross-contaminate each other's tail.
* **Streaming the tail into the POST response** — would break the one-JSON-shape contract the
  add flow and its tests are built on, for no gain over a 1s poll.
* **Raising the timeout alone** (already done separately) — necessary, and insufficient: it turns
  a 60s failure into a 300s silence.
