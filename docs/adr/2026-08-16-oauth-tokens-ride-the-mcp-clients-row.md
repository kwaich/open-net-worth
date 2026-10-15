---
title: OAuth tokens ride the existing `mcp_clients` row, not `secret_config`
status: Superseded by 2026-10-10-connectors-live-in-a-connectors-table-and-secrets-in-connector-secrets
date: 2026-08-16
deciders: implementing Claude agent (mcp-oauth-p3-flow lane), design ratified in RFC 2026-08-14-mcp-oauth §4.4
---

# OAuth tokens ride the existing `mcp_clients` row, not `secret_config`

> **Superseded 2026-10-10** by [`2026-10-10-connectors-live-in-a-connectors-table-and-secrets-in-connector-secrets`](2026-10-10-connectors-live-in-a-connectors-table-and-secrets-in-connector-secrets.md):
> `mcp_clients` no longer exists. An MCP server is an `mcp:<id>` row of `connectors`; its OAuth
> client secret and tokens are `connector_secrets` fields, the rest of the block is `state.oauth`.

## Context

An OAuth-authed external MCP server (RFC `2026-08-14-mcp-oauth`) needs to persist a DCR
registration (`client_id`, optionally a `client_secret`) plus a live PKCE token set
(`access_token`, `refresh_token`, `expires_at`) per server. Two places in the storage engine
already hold credential-shaped data: `secret_config` (a namespace-singleton table used by every
connector's own config — Plaid, Alpaca, IBKR, etc.) and `mcp_clients` (already list-shaped, one
row per configured server, already `SECRET_TABLES` + `NON_RESTORABLE_TABLES` + checkpoint-excluded
for its existing write-only `token` field).

## Decision

We will add one nested optional `McpOAuth` block, `McpServerConfig.oauth`, to the **existing**
`mcp_clients` row — not a new `secret_config` namespace, not a new table.

`set_oauth`/`clear_oauth`/`record_oauth_status` (`db/mcp_client_store.py`) are its only writers;
`update()`'s patchable set deliberately excludes `oauth` (`test_update_cannot_patch_oauth`), the
same treatment `discovered_tools` already gets — it is a record of what a real exchange produced,
not caller-editable config. `public()`/`public_one()` project a token-free derived summary
(`_oauth_summary`) instead of the raw block; `tests/db/test_mcp_client_oauth_migration.py`'s
property-style scan proves no secret field leaks through that projection under any key name.

## Consequences

### Positive
- **Zero new plumbing.** `mcp_clients` is already `SECRET_TABLES` (never streamed by an export,
  DELETEd from the sanitised backup's `onw.db` member by
  `services.backup.write_sanitised_copy`/`_sanitise_db_bytes`) and already
  `NON_RESTORABLE_TABLES` (never reverted by a restore) and already checkpoint-excluded (never
  rolled back by `/rewind`). The new `oauth` field inherits every one of those for free — verified
  empirically, not assumed, in `tests/services/test_mcp_oauth_backup_sensitive.py` (a seeded
  refresh token is provably absent from a sanitised download's raw bytes, and a restore never
  un-clears/un-rotates a token that changed after the backup was taken).
- **No new Alembic revision.** `mcp_clients` is a `payload_table` (whole-row JSON `payload`
  column); widening the Python model is purely additive. Pinned directly:
  `tests/db/test_mcp_client_oauth_migration.py::test_no_new_alembic_revision_needed_for_the_oauth_field`
  asserts the schema head is unchanged at revision `0040`.
- **One credential, one lifecycle.** The server's endpoint config and its OAuth state live and
  die together — delete the server, the token goes with it in the same write; repoint the
  endpoint, the token is cleared in the same PATCH (`api/mcp_client_api.py`'s
  `_endpoint_changed` branch). Splitting the token into `secret_config` while the endpoint config
  stayed in `mcp_clients` would give one credential two homes and two independent lifecycles to
  keep in sync by hand.

### Negative
- `mcp_clients` was already the one table in `SECRET_TABLES` that is list-shaped rather than a
  namespace-singleton row — this decision adds a second concern (OAuth state) to that same
  irregular shape rather than growing `secret_config`'s otherwise-uniform namespace-row pattern.
  A future reader of `core.sensitive.SECRET_TABLES`'s docstring has one more reason `mcp_clients`
  is called out as its own table instead of folding into `secret_config`.

### Follow-ups
- None — the storage shape is intentionally final; RFC §4.5 already designed CIMD's future arrival
  as a resolver-only change (a different `client_id` string in the same field), not a storage
  change.

## Alternatives considered

- **Store tokens in `secret_config`** — rejected. Would need a new namespace row keyed somehow
  back to the owning `mcp_clients` row, and would split one credential's lifecycle across two
  tables with no atomic way to keep "delete the server" and "delete its token" as one operation.
  `mcp_clients` already carries the exact same three protections `secret_config` would provide,
  for zero additional entries in `core/sensitive.py`.
- **A dedicated `mcp_oauth` table, one row per server** — rejected as unnecessary indirection:
  every field a separate table would add (issuer, endpoints, token set) is already scoped 1:1 to
  a `mcp_clients` row, and `mcp_clients` is already list-shaped (unlike every `secret_config`
  tenant), so nesting the block costs nothing a join would otherwise buy.

## Agent provenance

- **Decided by:** implementing Claude agent, `lane/mcp-oauth-p3-flow`.
- **Lane / branch:** `.claude/worktrees/mcp-oauth-p3`, branch `lane/mcp-oauth-p3-flow`.
- **Alternatives weighed:** `secret_config` namespace row (rejected in RFC §6's alternatives
  table and reaffirmed here after implementation); a dedicated `mcp_oauth` table (rejected as
  unneeded indirection).
- **Driving artifacts:** `.claude/rfcs/2026-08-14-mcp-oauth.md` §4.4, §6; `core/sensitive.py`'s
  `SECRET_TABLES`/`NON_RESTORABLE_TABLES` docstrings; `tests/db/test_mcp_client_oauth_migration.py`
  (Phase 2, pre-existing); `tests/services/test_mcp_oauth_backup_sensitive.py` (this phase, the
  empirical proof).
- **Why this way (one line):** the token and the endpoint it authenticates to are one fact with
  one lifecycle, and `mcp_clients` already had every protection a credential needs — splitting
  them would only create a second thing to keep in sync.
