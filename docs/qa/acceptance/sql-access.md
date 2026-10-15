# Acceptance — SQL access (Settings › Developer)

Spec: `docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md`. ADR:
`docs/adr/2026-08-23-lucius-reads-the-database-directly.md`.
Verify on the running app via `make qa-e2e` / `make qa-cu`, never `:5174`/`:8080` directly.

The panel is the owner's half of a capability whose other half is invisible (a model calling a
tool). Two things have to be true for it to be worth shipping: the switch must actually stop
the capability, and the log must show what happened — including what was refused. A switch with
no visibility is a switch nobody has any basis to flip.

## SQL1 — The panel

- **SQL1.1** GIVEN Settings › Developer WHEN the pane loads THEN a panel titled "SQL access"
  renders below "Code execution", with the sub "Lucius can read this database directly —
  read-only, and never your credentials".
- **SQL1.2** GIVEN the panel THEN it explains, before any control, that writes and schema
  changes are refused *by the database itself* and that credential tables and chat transcripts
  are unreadable — the claim a reader needs in order to decide, not a reassurance after the fact.
- **SQL1.3** GIVEN the panel THEN a "Refresh" action sits in the panel header and re-fetches the
  query list without a page reload.

## SQL2 — The kill switch

- **SQL2.1** GIVEN a fresh instance WHEN the panel loads THEN "Direct SQL reads" is **ON**
  (`role="switch"`, `aria-checked="true"`). Default-on is deliberate — see the ADR §4.
- **SQL2.2** GIVEN the switch is ON WHEN it is clicked THEN a `PUT /api/config/sql-query` carries
  `{"enabled": false}`, the toggle reads `aria-checked="false"`, and a toast says "SQL access
  blocked".
- **SQL2.3** GIVEN the switch is OFF THEN the row's sub-copy states that `run_sql_query` and
  `describe_db_schema` are blocked and Lucius falls back to the ordinary read tools.
- **SQL2.4** GIVEN the switch is OFF WHEN Lucius is asked a question that would use SQL THEN he
  does NOT run a query, and the refusal he receives names Settings › Developer as the place to
  re-enable it.
- **SQL2.5** GIVEN a save fails (server 500) THEN a toast reports the HTTP status. The toggle
  must not silently show a state the server did not accept.
- **SQL2.6** GIVEN the switch is flipped WHEN the pane is closed and reopened THEN the new state
  persists (it is server state, not per-browser).

## SQL3 — The query log

- **SQL3.1** GIVEN Lucius has run a successful query THEN the "Recent queries" list shows that
  query's SQL verbatim in a mono block, an "ok" badge, the row count, the duration in ms, the
  origin, and a relative timestamp.
- **SQL3.2** GIVEN a query returned exactly one row THEN it reads "1 row", not "1 rows".
- **SQL3.3** GIVEN a query hit the row cap THEN it reads "N rows (capped)" — a capped result must
  never be presented as a complete one.
- **SQL3.4** GIVEN a query was REFUSED (a write attempt, or a credential table) THEN the row
  shows a "refused" badge and the refusal reason. Attempts are as interesting to an owner as
  successes and must not be hidden.
- **SQL3.5** GIVEN the list THEN it is newest-first.
- **SQL3.6** GIVEN the list THEN it sits inside a `data-ctx-secret` region — a `WHERE` clause can
  embed real amounts and account names, and must never ride into the Lucius screen snapshot
  (same treatment as the application log and the MCP token snippet).

## SQL4 — Lucius end-to-end (the reason the feature exists)

- **SQL4.1** GIVEN SQL access is ON WHEN Lucius is asked something no fixed tool can express
  ("which payees did I pay in both March and April?") THEN he calls `describe_db_schema` and/or
  `run_sql_query` and answers from the result.
- **SQL4.2** GIVEN a question about a payload-shaped store ("how many positions per account
  type?") THEN the query uses `json_extract(payload, …)` — proving the schema tool gave him the
  shape rather than leaving him to guess at columns that do not exist.
- **SQL4.3** GIVEN a query whose result touches `transactions` money columns THEN Lucius does NOT
  state the figure as fact — he either verifies it against `get_transactions`/`net_worth` or says
  plainly that the SQL total needs confirming. The caveat is in the payload; this criterion is
  whether it changes his behaviour.
- **SQL4.4** GIVEN a query over `transactions` THEN soft-deleted and merged-away rows are excluded
  (or their inclusion is stated). A naive count over that table over-reports.

## States

- **Empty (access ON):** "No queries yet" + "Nothing has been asked of the database directly. Ask
  Lucius something the ordinary tools can't answer and it will show up here."
- **Empty (access OFF):** "No queries yet" + "SQL access is off, so nothing can run." The empty
  state must explain WHICH emptiness this is.
- **Loading:** "Loading…" while the first fetch is in flight; the section-busy skeleton covers the
  config fetch.
- **Error:** "Could not load the query log" + "The server did not answer." + a Retry button that
  re-fetches. A failed log fetch must not blank the kill switch — they are independent fetches
  and the switch is the more important of the two.
