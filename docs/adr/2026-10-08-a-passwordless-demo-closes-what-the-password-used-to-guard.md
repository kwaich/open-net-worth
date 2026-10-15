---
title: A passwordless demo closes what the password used to guard
status: Accepted
date: 2026-10-08
deciders: Repo owner, Claude
---

# A passwordless demo closes what the password used to guard

Amends `2026-10-05-a-public-demo-visitor-is-an-operator-who-cannot-reach-past-the-book` and
`2026-10-07-the-public-demo-needs-no-password`.

## Context

The 2026-10-05 ADR listed the handful of things an operator can do that reach past the demo's
book, and locked those. It was written when a visitor still needed the published password, and
it was a **denylist**: everything not named stayed open. 2026-10-07 removed the password. A
security review of that branch (MERGE AFTER FIXES) found what the denylist never named, now
reachable by anyone on the internet with no credential at all:

- `POST /api/builder/runs` starts Codex or Claude Code **with a shell on the host**. The only
  gate in `services/builder.py` was `desktop_mode`.
- `POST /api/reset` wipes the book every visitor shares; backup restore and data import replace
  it; backup download and data export carry it off.
- Automations (create, edit, run) spend tokens unattended and can notify outward; extensions,
  skills, and every provider/connector/channel config either run code or send data out.
- File ingestion (uploads, statements) put a stranger's real documents into a shared public book
  and run the app's largest unmetered model spend.
- The quota guarded only `POST /api/chat`, keyed on the **leftmost** `X-Forwarded-For` entry (the
  one a client writes), and charged tokens only to sessions the chat route had bound. Every other
  model call — a narration, a suggestion, an import, a scheduled Automation — was uncounted.
- `GET /api/sessions` listed every visitor's conversations to every other visitor, and Lucius's
  memory distilled one visitor's chat into facts shown to the next.

## Decision

### 1. Writes are an allowlist; `_LOCKED` is the explicit denylist on top

`api/demo_lockdown.py` now refuses a POST/PUT/PATCH/DELETE unless its path is under `_WRITABLE`
(the book's own domains and a visitor's own conversations), and refuses anything in `_LOCKED`
whatever its method (the reads a visitor must not have, and the named dangerous families). A
router nobody classified is closed, not open. `tests/test_demo_route_classification.py` is the
review gate on top: it walks the real route table and fails on any mutating route that is
neither locked nor in its reviewed snapshot — so even a new route under an allowed prefix is
decided in the diff, not discovered later.

Classification of every mutating route (318: 180 allowed, 138 refused):

| Family | Routes | Decision | Why |
|---|---|---|---|
| Accounts, categories, institutions, transactions, payees, matches | 49 | allowed | the book itself |
| Planning, plan state, targets, commitments, recurring, income | 48 | allowed | the book itself |
| Positions, lots, options, instruments, equity, rebalance, Monte Carlo, watchlists, alerts, intel assets/signals | 40 | allowed | the book itself |
| Assets (marks, disposal), vault items, profile, dashboard layout, actions, inbox, notification log, mentions | 32 | allowed | the book itself |
| Chat, sessions, capture | 9 | allowed, **owner-scoped and metered** | §2, §4 |
| `auth/login`, `auth/logout` | 2 | allowed | inert on an open demo |
| Builder | 2 (+ reads) | **locked** + service refusal | a coding agent with a shell |
| MCP clients/OAuth, `/mcp`, extensions, skills, desktop, QA (Terminal is a WebSocket, locked for any method) | 24 (+ reads) | **locked** | runs code or a process on the host |
| `reset`, backup, data import/export, move, demo seed | 17 (+ reads) | **locked** | replaces or carries off the shared book |
| Automations (create/edit/delete/run/stop/infer) | 6 | **locked** | unattended spend and outbound reach |
| Memory, channels, chat pairing, cost budget/log, intel scan/sources, watchlist config, match-judge config, setup complete | 17 | **locked** | spend, outbound reach, scheduler control |
| model-config, preferred models, connectors, tool permissions, search/market-data/Telegram/email config, Plaid, Enable Banking, Lunch Flow, Lunch Money, SimpleFIN, Yodlee, YNAB, IBKR, Alpaca, app config | 41 | **locked** | credentials and where they are sent |
| Uploads, statements, equity 8b import, vault file add, asset doc/photo, receipt attach/upload | 23 | **locked** | file ingestion: a shared public book must not receive a stranger's documents, and parsing them is model spend |
| Password/auth settings, API token, Shortcuts | 5 | **locked** | auth state (2026-10-07) |
| Checkpoints, logs, setup-status path probes, artifacts/uploads lists | 3 (+ reads) | **locked** | the host, or other visitors' conversations |

The Builder is also refused **in the service** (`builder._execution_refusal`, shared by
`start`, `_execute_subprocess`, `status` and `engine_statuses`): the route lock is one layer, and
a caller that skips it still cannot start a run.

### 2. Every model call is counted, where the provider is built

`ai.assistant._provider_from_resolved` — the one function every provider passes through, for
chat, narrations, imports, jobs and tools — calls `demo_quota.assert_model_available()`, which
raises when the visitor (bound for the request by `bind_visitor`) or the demo is out of tokens.
`demo_quota.note_usage` now charges **every** recorded call: to the session's visitor, else to the
visitor whose request caused it, else to an unattributed bucket that counts toward the instance
ceiling only. The burst window (turns per 300 s) additionally meters the **deliberate** model
actions — chat, a resolved approval, suggestions, capture, account describe, match judge, instrument
enrichment, an asset-brief refresh. Reads that merely may narrate (digests, technicals) are not
"turns": they spend through the token budgets, which the choke point checks.

### 3. The visitor key is the address the trusted proxy appended

`DEMO_TRUSTED_PROXY_HOPS` (default **0**: ignore `X-Forwarded-For`, key on the network peer). With
N, the visitor is the Nth entry from the right; a header shorter than N falls back to the peer.

- **AWS demo** (`deploy/aws-demo`): the Cloudflare edge → `cloudflared` on the VM → the app
  container on `127.0.0.1:5175`. The edge appends the connecting client's address to whatever the
  client sent, so the rightmost entry is the real visitor and N = 1 (`compose.yml`).
- **`run_demo.sh`**: the same tunnel → `vite preview`, whose proxy does not touch
  `X-Forwarded-For` → uvicorn. N = 1 as well.

The previous rule (leftmost entry) let a client mint a new quota bucket per request. Default 0
means a misconfigured host fails to *share* one bucket (visible, safe) rather than to *trust a
spoofable header*. Unverified: that cloudflared adds no entry of its own — see Follow-ups.

### 4. Conversations are private to the visitor who started them

A demo issues an anonymous `onw_demo_visitor` cookie (random, HttpOnly, SameSite=Lax; it
authenticates nothing). A conversation is owned by the visitor who first writes to it
(`services/demo_visitors`, in memory like the quota ledger). A visitor lists, reads, watches,
writes to and deletes only their own; every `/api/sessions/{id}…` and `/api/chat/{id}…` route
answers 404 for anyone else's, `POST /api/chat` refuses another visitor's id before anything is
loaded, and the list, unread marker and live list are filtered. Seeded Automation run
conversations are shared and read-only. A restart orphans earlier conversations: an orphan is
readable by nobody. Consequences accepted: a client that refuses cookies loses its history (it
never borrows a neighbour's); history is per browser, not per address, so two people behind one
NAT do not see each other's chats.

Lucius's memory is read-only on a demo (`memory_writes_enabled`): facts distilled from one
visitor's chat would otherwise reach the next, and extraction is an uncharged model call.
Checkpoints and the artifacts/uploads lists, which carry other visitors' conversation text, are
locked.

### 5. Nothing fires unattended

The Automations scheduler tick (due runs, event observation, event drain) does not run, and its
job is not registered, while `DEMO_MODE` is on. Disabled rather than counted: a scheduled run has
no visitor to charge and nobody watching; counting it would let background work starve the
visitors the ceiling serves; and since visitors cannot create or run Automations (§1), the seeded
rows are fabricated history that needs no execution. The monitoring jobs (price check, RSS, intel
scans) keep running — they are the live-looking half of the demo — and are covered by §2.

## Consequences

### Positive

- A new route cannot silently widen the demo: it is closed at runtime and fails the review test.
- Spoofing `X-Forwarded-For` no longer resets any quota; every model call is bounded by the same
  budgets; no scheduled spend; no cross-visitor conversation reads.
- Every behaviour above is inert off a demo and tested as such.

### Negative

- A demo visitor cannot upload a file, create an Automation, undo a Lucius turn from a checkpoint,
  or change Lucius's memory. The seeded Automations tab shows history but cannot run it.
- The Builder, `/mcp`, the Terminal and all configuration are unavailable on a demo even to its
  host; the host changes them from a shell with `DEMO_MODE=0` for that one process
  (`demo-aws.sh lucius`).
- Conversation ownership and the quota ledger are in memory: a restart resets quotas and hides old
  conversations. A busy demo that wants durable history needs a persisted owner column.
- A chosen model/effort is still the visitor's to pick within the configured provider; the budgets
  bound tokens, not model price.

### Follow-ups

- Verify on the live host that the rightmost `X-Forwarded-For` entry is the visitor
  (`curl -H 'X-Forwarded-For: 1.2.3.4' https://demo…` twice from different networks and compare the
  `tokens_by_ip` keys in `demo_quota.snapshot()`); if `cloudflared` appends its own hop, set
  `DEMO_TRUSTED_PROXY_HOPS=2`.
- `run_demo.sh` binds `vite preview` on `0.0.0.0:5175`; a LAN client that bypasses the tunnel can
  write the whole `X-Forwarded-For` header. The instance-wide ceiling is what holds then.

## Alternatives considered

- **Keep the denylist and extend it.** Rejected: it is exactly the shape that failed — the next
  router is open until someone remembers. The review test alone would catch it in CI but not at
  runtime; the allowlist does both.
- **Scope conversations by IP.** Rejected: the same key the quota uses, but a NAT shares it, and a
  visitor who rotates networks loses their own chat. The cookie is per browser and carries no
  privilege.
- **Make chat history ephemeral (never persist visitor conversations).** Rejected: the transcript
  store is the app's one path and Lucius's resolve/approval flow reads it back; disabling
  persistence would fork it for the demo.
- **Count scheduled Automations against the instance ceiling.** Rejected, §5.
- **Trust `CF-Connecting-IP`.** Rejected: only true behind Cloudflare, and a LAN client of
  `run_demo.sh` could write it. The hop count is explicit and testable.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), fixing a security review's findings.
- **Lane / branch:** `feat/demo-passwordless`, worktree `agent-aa7a86f6f04db98bc`.
- **Alternatives weighed:** extended denylist, IP-scoped conversations, ephemeral history,
  counted Automations, `CF-Connecting-IP`.
- **Driving artifacts:** the security review of `df7154ddf`, `tests/test_demo_route_classification.py`,
  `tests/test_demo_visitors.py`, `docs/DEMO.md`.
- **Why this way (one line):** a stranger holds the operator's API, so the default for any route,
  model call and conversation must be closed, with the exceptions written down and tested.

## Amendment (2026-10-08): what the second review found

A second security review of this branch found three doors the route lockdown did not cover,
closed before the demo went live:

- **Any model on the operator's key.** A visitor could name any provider/model (or a local CLI
  provider) per turn, and token budgets bound tokens, not price. On a demo,
  `ai.assistant._provider_from_resolved` — the one place every model call builds its provider —
  now ignores the requested provider and wraps the instance's in `_DemoPinnedProvider`, which
  runs every call on the instance model at no more than `medium` effort.
- **Lucius's tools reached what the routes lock.** `ai.tools.registry._DEMO_REFUSED_TOOLS`
  (checkpoints, logs, cost summary, automations, skills, code/scripts, memory writes, outbound
  email/Telegram/webhook, model/provider/connector/monitoring config, intel scan) is refused at
  dispatch on a demo, BEFORE the permission gate's fail-open `try`, so it cannot degrade.
- **Chat suggestions read any conversation by id.** The follow-up path now requires
  `demo_visitors.may_access(..., write=False)`; anyone else's id falls back to starters.
  `cost_log`, `cost_log_daily` and `model_telemetry` (which carry session ids) joined the demo
  SQL deny list.

Deliberately NOT changed: the per-id routes (`PATCH /api/sessions/{id}` etc.) still claim an
unknown id. Refusing to claim there locks a visitor out of a chat the UI configured before its
first message. The flood it allows only evicts ownership (fail closed, nothing leaks); a bound
per visitor is a follow-up. Keying IPv6 visitors by /64 is a follow-up too.

## Amendment (2026-10-08): Automations run on the demo

Owner decision: the demo exists to show the full product, and Automations are a headline
capability, so seeded scheduled and event-driven Automations RUN on a demo again (the scheduler
gate is removed). Their spend is bounded without switching them off: every call is pinned to the
instance model at ≤ medium effort, and their usage is unattributed, so it counts toward the
instance-wide daily token ceiling and no visitor's budget. Visitors still cannot create, edit or
run an Automation (the routes and the tools stay locked) — a shared Automation is shared state.
Run conversations are listed for every visitor with `read_only: true`; the chat never opens a
read-only conversation by default (`useConversations` boot), which is what dropped every
first-time visitor into a run that refused their first message.

## Amendment (2026-10-08): file ingestion is open on the demo

Owner decision: the demo is public and every visitor knows it, and statement import, receipts and
Documents are the core of the product's story. Uploads, statement preview/import/commit, receipt
attachments, chat attachments, Documents files, asset docs/photos and the equity 8B import are
moved from `_LOCKED` to `_WRITABLE`. What stays locked is what reaches past the fabricated book:
code on the host, secrets and AI-credential routing, auth state and the API token, whole-book
replace/export/move, and outbound channels. Parsing cost stays bounded by the model pin and the
per-visitor and instance token budgets; size limits are the app's own.
