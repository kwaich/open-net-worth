---
title: Per-tool-call telemetry — hook the dispatch seam, keep it a separate ring, log sizes not values
status: Accepted
date: 2026-07-19
deciders: harness/reliability-program lane (Claude) + owner directive (owner AFK dispatch)
---

# Per-tool-call telemetry — hook the dispatch seam, keep it a separate ring, log sizes not values

## Context

Several standing decisions about the AI tool surface are currently made from judgement
because no instrument exists to make them from evidence:

* **`CORE_TOOL_NAMES` membership.** ~19 tools are offered on EVERY turn at roughly 6,900
  prompt tokens. The list is hand-maintained and unmeasured — nobody knows which members
  are actually called, or which non-core tool is repeatedly searched for and has earned a
  seat. This is the same "hand-maintained and unverified" pattern that has rotted other
  lists in this codebase (the tool-rename incident of 2026-07-18 being the sharpest case).
* **Round-trips per task.** A measured mean of 4.1 API calls per turn, each re-reading full
  context, with no visibility into which tool *sequences* produce the long turns.
* **Result-size cost.** A `get_transactions` at its default limit is ~15,000 tokens that
  rides in context for the remainder of the session; 58 of 79 read tools have never been
  size-reviewed, and there is no data on which to prioritise.
* **Discovery waste.** `tool_discovery_log.json` records `find_tools` queries and their
  hits, but not what happened next — so a query that was answered by reaching for a tool it
  never returned is invisible.
* **"Did it work."** Tool errors, model recovery, task completion.

The repo already carries four partially-overlapping trails, and the explicit brief was to
extend rather than add a fifth: `mcp_write_log.json` (mutating-inbound-MCP audit),
`core/run_steps.py` → `run_history.json` (per-agent-run narrative), `services/audit.py`
(transaction provenance), `cost_log.json` (per-API-call cost), and `tool_discovery_log.json`.

## Decision

### 1. Hook `ai/tools/registry.py::dispatch`, not the assistant loop

`dispatch` is the single function every internal tool call crosses. `execute_tool` is
literally an alias for it (`ai/tools/__init__.py`), and all five call sites route through
it: `ai/assistant.py` (interactive chat, agent runs, scheduled tasks and skills all recurse
into `assistant.run`), `mcp/adapter.py` (inbound MCP), `connectors/chat/confirm.py`
(connector confirm/apply) and `api/agents.py` (REST finding apply).

The assistant loop was rejected as the seam precisely because it misses three of those:
inbound MCP, connector confirms, and the REST apply path never pass through it. A record
that omits MCP or agent runs cannot answer the questions it exists for.

The registry's standing contract — "adds **no** global try/except so the per-tool
catch-vs-raise behaviour stays byte-identical (assistant.py remains the sole global
catcher)" — is preserved: the handler call is wrapped `try/except/**raise**`, recording the
exception and re-raising it unchanged. Both early-return paths (unknown tool, schema
validation failure) are recorded with hardcoded empty domains, since the handler never ran
and calling `mutated_domains` there could read the *previous* dispatch's narrowing, which
`_active_call` still holds by design.

### 2. A SEPARATE ring, not an extension of `mcp_write_log.json`

The brief flagged `McpWriteLogStore` as the likely foundation, and its shape is indeed
close to right. It was still kept separate, on retention grounds:

`mcp_write_log.json` is a money-write **audit** — which mutating MCP call moved what
amount, and from which checkpoint it is undoable — bounded at `MAX_WRITE_LOG = 500`. That
cap is well-sized for its traffic (a handful of money writes a week). Per-tool-call
telemetry is roughly two orders of magnitude higher volume: at the measured ~4 calls/turn,
500 rows is about 120 turns. Merging the two would evict the money-write audit trail within
minutes of ordinary chat use — trading a forensic record with a real retention guarantee
for a performance metric. Different retention requirements, different consumers, therefore
different rings.

What was done instead of merging: the new ring is a strict **superset** of the MCP write
log's non-money fields (surface, tool, domains, checkpoint id, ok/error), so the MCP audit
becomes a queryable *projection* of telemetry (`origin == "mcp" and mutated`), while the
dedicated store keeps its guaranteed retention for money writes. The cap is
`MAX_TOOL_CALLS = 5000` (~1.2 MB), sized off the question rather than the file: it must
cover enough turns for "which CORE tool was never called over the last N days" to be
trustworthy, which 500 does not.

`run_steps`/`run_history.json` was likewise left alone — it is a human-readable narrative of
ONE agent run (keeping 2,000-char result snippets, retained per entity), not a cross-run
aggregate.

### 3. `tool_discovery_log.json` WAS extended

The one trail genuinely extended rather than paralleled. Its events gained a `session_id`,
which lets the report join a `find_tools` query to the calls that actually followed it
**within the same session**. Without it the join degrades to a time window that
cross-credits concurrent sessions — the difference was visible immediately in the first real
run, where a time-window join attributed an unrelated session's tool call to a query.

### 4. Sizes and shapes only — never argument values, result bodies, or error messages

This app holds real financial records, and this ring is high-volume, long-lived and read by
scripts. Rather than redact, the design makes a leak structurally impossible: the record
carries `input_chars` and `result_chars`, never the values; a closed-vocabulary
`error_class` (`validation` / `unknown_tool` / `tool_error` / `exception:<Name>`), never the
error *message*.

Dropping the error message is a deliberate departure from the brief's allowance for capped,
redacted values. `core/redact.py` masks secret-shaped *keys*, which does nothing for the
actual risk here: a tool error legitimately quotes account names, balances and payee
strings in its message body, none of which `redact_secrets` would mask. The full message is
already logged to `app.log` (which rotates) by the assistant loop for live debugging, so
nothing is lost for the "did it work" question — the class is what aggregates. A regression
test asserts a payload of realistic financial values and a raising handler whose exception
message quotes a balance leave nothing recognisable in the ring.

### 5. Zero added latency; a telemetry fault never fails a tool call

`record_call` appends to a bounded in-memory deque and returns; one daemon thread batches
into a single store write every 2s or 64 rows. A per-call read-modify-write of a 5,000-row
JSON file would otherwise put an fsync on every dispatch. The buffer is capped
(`_MAX_BUFFERED = 2000`) so a store that cannot be written cannot grow the process without
bound, and a failed flush re-buffers rather than discarding. Every entry point swallows
everything, mirroring `mcp/adapter._audit_write`.

### 6. Checkpoint-EXCLUDED, not portable

`TOOL_CALL_LOG_JSON` is registered in `ai/checkpoint._EXCLUDED_PATHS` — required, not
optional, because rows are written *inside* the CheckpointContext every chat turn opens
(it fires on every dispatch), exactly the reason `MCP_WRITE_LOG_JSON` needs an explicit
entry. It is deliberately absent from `core/portable.py`'s `PORTABLE_FILES` allowlist: it
is instrument output, not user data. Both are pinned by test.

## Consequences

**Positive.** The five listed questions are answerable from a script
(`backend/scripts/tool_telemetry_report.py`, sections `usage` / `core` / `results` /
`turns` / `discovery`). `CORE_TOOL_NAMES` membership in particular gets the honest
denominator: *share of distinct turns in which the tool was called at least once*, since a
tool called five times in one turn was still only useful once.

**A gap that was closed on the way.** Inbound MCP **reads** returned from
`adapter.call_tool` before any audit scope was set (only the mutating branch set one), so
every MCP read would have recorded `origin = "unknown"` — blinding the instrument on the
surface the brief singled out as disqualifying. `call_tool`'s read branch now sets and
clears the same `mcp:<surface>` scope. This is inert for the ledger (a read performs no
write to stamp) and is covered by a test that drives the real adapter rather than setting
the scope by hand — setting it by hand is what hid the gap in the first place.

**A gap deliberately left open.** `connectors/chat/confirm.py` and `api/agents.py` set no
audit scope at all, so proposal-apply calls from those two paths record `origin =
"unknown"`. Adding one would change *ledger provenance* for those writes, which belongs to
the transaction-provenance concern and to lanes currently in flight, not to a telemetry
change. The report renders `unknown` as an explicit origin bucket, so the gap is measured
and visible rather than silent. Follow-up, not shipped here.

**Known limitation.** Origin resolution reuses `core.audit_context`'s surface rather than
introducing a parallel signal. Non-script skills narrow the tool scope without publishing a
skill id, so they record a bare `skill` where script skills record `skill:<id>`. Adding a
6th ContextVar to `_skill_scope.py` would have changed `ScopeSnapshot`'s arity while six
lanes are live; not worth the collision risk for one label.

**Not counted here.** Model round-trips per turn. `cost_log.json` already instruments every
API call well; joining the two by session id answers "4.1 API calls per turn" without a
second `assistant.py` hook or a duplicated counter. One hook was added to `assistant.py`
(`call_log.begin_turn`), which yields both turn and call ordinals.

**Deliberately not shipped: a read TOOL.** The query surface is a script only. Adding a
telemetry tool would place a new entry in the very tool catalog this instrument exists to
measure — the instrument should not perturb the thing it measures, and the first data it
produces would be about itself.
