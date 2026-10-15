---
title: Lucius reads the database directly
status: Accepted
date: 2026-08-23
deciders: repo owner + implementing agent (Claude)
---

# Lucius reads the database directly

## Context

Lucius has ~200 tools and still routinely cannot answer a question the data can answer. Every
tool is a FIXED projection over a FIXED filter, so anything that needs an unanticipated
combination has no route at all:

* "which payees did I pay in both March and April"
* "how many transactions carry a tag no category uses"
* "what's the distribution of `source` on rows imported last month"

Each is one line of SQL and zero tools. The failure mode is not an empty result — it is Lucius
assembling a partial answer from three bounded reads and stating it confidently, or reporting
that the data cannot be found while it sits in `onw.db`.

The owner asked for a SQL tool. Four decisions were put to them and all four were taken as
recommended: read everything except secrets; run like any other read rather than behind a
confirm card; ship a kill switch plus a query log; warn loudly on money rather than blocking
aggregates.

## Decision

Two tools — `run_sql_query(sql, limit?)` and `describe_db_schema(table?)` — executing
**read-only** statements against the live database, with the enforcement boundary at the
CONNECTION rather than in a SQL parser.

### 1. The boundary is the connection, not the string

A SQL-string parser is a blocklist. Blocklists lose — to CTEs, scalar subqueries, views,
`UNION`, aliases, comments spliced mid-keyword, unicode confusables, and whatever is published
next month. Any design whose safety rests on "we checked the text for the word DELETE" is one
clever string away from being wrong.

So the text gate (`assert_statement_is_read_only`) is explicitly **not** the security boundary.
It exists so the common mistake produces a clear, actionable error instead of a cryptic
authorizer denial. The real boundary sits beneath the parser:

**SQLite (shipped):** `sqlite3.Connection.set_authorizer()`. SQLite invokes it during statement
PREPARATION, after parsing, once per action and once per table/column access — so it sees the
RESOLVED access, not the text. A credential table reached through a three-level CTE inside a
`UNION` arm of a view still arrives as a plain `SQLITE_READ` on that table's name. The callback
is an **allowlist** of four actions (`SELECT`, `READ`, `FUNCTION`, `RECURSIVE`); an action code
the module has never heard of is refused, so a future SQLite capability fails closed rather
than falling through. `PRAGMA query_only=ON` is a second, independent control, set BEFORE the
authorizer is installed (the authorizer denies `SQLITE_PRAGMA`, so the order is load-bearing).

**Postgres (CI-tested):** no authorizer exists. It gets a genuine read-only transaction
(`postgresql_readonly`), `statement_timeout`, an unconditional rollback, and a **fail-closed
identifier scan** for denied table names. That scan OVER-refuses — a query whose text merely
mentions `secret_config` inside a `LIKE` pattern is rejected. Over-refusal is the safe
direction and the error message says exactly why.

The asymmetry is deliberate and disclosed: SQLite gets a structural guarantee, Postgres a
conservative textual one. If Postgres ever becomes the shipped default, the honest upgrade is a
dedicated `REVOKE`-d database role, not a better regex.

**`mode=ro` was considered and rejected.** A third control looked attractive until the WAL
interaction: a read-only URI open cannot create or recover the `-shm` sidecar a WAL database
needs, so on a cold instance it fails with a bare "unable to open database file" that reads as
data corruption to the operator. Two controls that always work beat three where the third turns
a working feature into a confusing outage.

### 2. What may not be read

`DENIED_TABLES = core.sensitive.SECRET_TABLES ∪ {sessions}`.

`SECRET_TABLES` is REUSED, never re-listed. It is already this codebase's canonical answer to
*"may this table's rows leave the instance?"* — `secret_config` (password hash, session-signing
HMAC, API bearer token, every LLM/bank/broker credential) and `mcp_clients` (per-server
tokens). A tool result IS those rows leaving the instance, into a model context that may be
logged, shared, or steered by untrusted text. Reusing the set means a newly-classified
credential table becomes unreadable here automatically, with no second list to forget.

`sessions` is added for a different reason and is NOT a secret: it holds every chat transcript
as multi-MB JSON columns. Reading it through SQL is a bad idea for size alone, and there are
purpose-built tools for session history.

`tests/db/test_sql_console.py::test_every_table_is_classified_allowed_or_denied` fails if any
table on `db.tables.metadata` is neither readable nor explicitly denied — mirroring
`tests/test_sensitive_paths.py`'s own drift guard. A new table cannot be born unclassified.

### 3. The invariant this deviates from, and why it still stands

`CLAUDE.md`: *"NEVER add a caller-chosen `fields`/projection parameter to a money-bearing read.
It lets the model pick `amount` over `base_amount` and defeat the historical-rate lock."*

Raw SQL is the maximal caller-chosen projection. The deviation is real and this section is why
it is acceptable rather than why it is not a deviation.

**The invariant stands. This tool is never a source for a reported figure.** That is enforced
in the model's context on EVERY result, not stated once in a tool description that scrolls
away — a description is read at offer time; a caveat is read at the moment the wrong conclusion
would be drawn. Two caveats, attached structurally by `services/sql_query.py::caveats_for`:

* **The historical-rate lock** (when the statement touches `transactions` money columns): a
  stored `base_amount` is trustworthy only while its `base_currency` matches the current base;
  a row stamped otherwise needs `txn_base_amount()`. Amounts are exact decimal TEXT, so summing
  them in SQL may not do what the model expects. *Do not report a figure from this tool —
  verify with `get_transactions` / `net_worth`.*
* **Soft deletes** (whenever `transactions` is referenced at all): the table retains
  `deleted = 1` rows and rows merged away via `merged_into`, and neither filter is applied
  automatically.

The first caveat was not theoretical even in the first end-to-end run against real data:
`SELECT SUM(base_amount) … GROUP BY month` returned floats, because SQLite silently coerced the
exact decimal TEXT to sum it. The caveat fired on that exact query.

The second is the single most likely way this tool produces a wrong answer, since every naive
`SELECT … FROM transactions` over-counts without it.

**Rejected: blocking aggregates over money columns.** It would be the stronger correctness
guarantee, and it would also block a large amount of legitimate ad-hoc analysis for a hazard
the caveat already names precisely. The owner chose warn-and-steer.

### 4. It runs like any other read

`core`-owned, NOT in `GATED_TOOL_NAMES` — no confirm card per query. The reasoning: it cannot
write, cannot reach a credential, and reads data Lucius can already reach through ~200 existing
tools, just in combinations they cannot express. It still sits in `tool_permissions` like every
tool in the app, so Settings › Permissions can move it to `needs_approval` or `blocked` at any
time.

The kill switch (`app_config.sql_query_enabled`) **defaults ON**, unlike `code_execution_enabled`
and `script_skills_enabled`. The asymmetry is the point, not an inconsistency: those execute
code, so "does not exist until you say so" is right. A read-only, credential-free query that
defaults OFF is a capability that silently does nothing for every owner who never finds the
toggle — the outage-that-calls-itself-security shape `_confirm_gate`'s own arming note warns
about. The switch is a genuine off-ramp, not an opt-in gate. Human-only: no Lucius tool writes
it, so the model can never restore its own access.

### 5. Two tools, not one

`describe_db_schema` is not a convenience. Most stores here are a single `payload` JSON column
(`payload_table()` / `book_payload_table()`), so `SELECT quantity FROM positions` — the obvious
guess — errors, and the real query is `json_extract(payload, '$.quantity')`. Without a live
schema map, every query the model writes is a guess.

Rejected alternatives: folding it into `run_sql_query` as a no-argument mode (a mode-switching
tool is a worse schema for a model to reason about) and putting a static schema in the tool
description (~4KB of prompt on every offer, stale the moment a migration lands). Introspection
cannot go stale and costs nothing on turns that never need it.

The name deliberately avoids `describe_database`: the registry already has `describe` (domain
introspection — what a write choke accepts) and `describe_account` (which interprets free text
and is unrelated to either). `describe_db_schema` says STORAGE shape without joining a confused
prefix family.

### 6. Bounds, measured rather than guessed

Both caps were set against a real 401 MB instance, not chosen by taste.

* **Rows**: 200 default, 1000 max.
* **Cells**: 2000 chars. Across the core money/planning tables the p99.9 cell is 984 chars and
  the largest is 1,401 — so 2000 clips **0.00%** of what a real question reads, where the
  originally-chosen 500 clipped 3.89% for nothing.

A cell cap is nonetheless non-negotiable: the widest cell on that instance is a single
`statements_meta.payload` of **1,063,534 characters** (~265K tokens in ONE value), and
`SELECT payload FROM transactions LIMIT 200` — comfortably inside the ROW cap — is ~28,600
tokens uncapped. Row-capping alone does not bound a result; both caps are load-bearing.

Both report themselves honestly, per `ai/tools/_bounded.py`'s rule that *a bounded read must
never look like a complete one*. `total` is a STRING: when the row cap bites it becomes
`"unknown — re-run with COUNT(*) for the true total"` rather than a number we would be
inventing. A clipped cell sets `cells_truncated` + a note naming the recovery path — an
ellipsis alone is not a machine-readable signal, and a model reading a clipped `payload` sees
plausible JSON, never notices the tail is gone, and concludes the field does not exist.

### 7. The audit trail keeps the SQL verbatim

`sql_query_log` (revision 0050) records every attempt, refusals included. It stores the
statement **verbatim** (capped at 4000 chars) — a deliberate departure from `tool_call_log`'s
pinned "SIZES AND SHAPES ONLY — never raw arguments".

The distinction is not a relaxation of that rule; it is a different question. For a
high-volume telemetry ring sized to answer "which tools earn their prompt tokens", arguments
are incidental and storing them would turn a log into a financial-record spill. Here the
statement IS the auditable action: a log saying "a 412-character query ran and returned 9 rows"
tells the owner nothing about whether the capability is behaving. `mcp_write_log` made the same
call for the same reason.

Consequences: the endpoint is auth-gated with `Cache-Control: no-store`, the Settings list is
`data-ctx-secret` (a `WHERE` clause can embed real amounts and account names, and must not ride
into the Lucius screen snapshot), and nothing under `ai/` or `mcp/` reads the log back.

Tier-2, checkpoint-EXCLUDED, and — mirroring `tool_call_log` — with NEITHER a
`portable_bridge` BRIDGED entry NOR a `_WIPE_FNS` one: a runtime log has no export or reset
role.

## Consequences

**Good.** Lucius can answer questions he previously could not, or could only half-answer. The
owner gets a visible record of what the model asks of the database, which is new: no comparable
record existed for any other read path. `describe_db_schema` also gives the model an accurate
picture of a storage layout it previously had to infer.

**Risk, on the record.** This is not a security-neutral change. Read-only and credential-free is
a real bound, but it widens what a prompt injection — in an imported statement, a fetched news
article, an external MCP tool result — can READ in a single shot. The denylist, the log and the
kill switch are mitigations, not a proof of safety. The trade is judged right for a
self-hosted, single-owner application where the model can already reach most of this data
through existing tools; it is recorded here so the trade is a decision rather than an oversight.

**Residual.** The Postgres table boundary is textual, not structural (see §1). The
`GATED_TOOL_NAMES` lever remains available at zero cost if the owner later wants a confirm card
per query.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| A structured JSON query DSL (table/filter/aggregate) | Re-invents SQL badly — no joins, no window functions, no `json_extract`. A worse version of the tools that already exist, which is the problem being solved. |
| Give the sandbox (`run_code`) a DB handle | `sandbox` is a secret-free leaf enforced by the 5th import-linter contract. Handing it the database destroys that property permanently. |
| Allowlist of money/book tables only | Safest, and it re-creates the "Lucius can't reach the data" problem for everything not on the list. The list also rots. |
| Whole database, no denylist | One prompt injection turns into every credential on the box. |
| Read-only enforced by regex alone | The blocklist problem. See §1. |
