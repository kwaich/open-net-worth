---
title: Connectors live in a `connectors` table; their secrets in `connector_secrets`
status: Accepted
date: 2026-10-10
deciders: Repo owner, Claude
---

# Connectors live in a `connectors` table; their secrets in `connector_secrets`

Supersedes `2026-08-16-oauth-tokens-ride-the-mcp-clients-row`. RFC: `.claude/rfcs/connectors-table.md`.

## Context

There was no table of connectors. `services/connectors.list_connectors()` assembled every connector
on each call from fifteen differently-shaped `secret_config` namespaces (`plaid_config`,
`telegram_config`, `email_ingest`, `channels_config`, `search_config`, `market_data_config`, …), three
`runtime_state` health namespaces and the `mcp_clients` table, driven by a hard-coded list — and five
more lists mirrored it (`connector_secrets._NATIVE_FIELDS`, `move_secrets.CONNECTOR_NAMESPACES`,
`aggregation/providers.PROVIDERS`, `_RESETTABLE`, `_DISPLAY_NAMES`). The 2026-08 connectors redesign
unified the *view* and deferred storage; it never shipped. The costs were visible: Email was split
across two rows with two on/off flags (the source of the `read`/`send` "capabilities" and a
half-failing two-request save); `capabilities` and `managed_by` existed to paper over per-store
shapes; twelve of fifteen stores did an unlocked read-modify-write; secret handling differed per store
(keep-on-blank vs clear-on-blank, env read once vs every call); adding a connector meant a new store
class plus edits to six lists.

The constraint that shaped the answer is the secret model. Protections are by *table name*
(`core.sensitive.SECRET_TABLES`, the checkpoint exclusion list, the SQL console's denied tables,
sanitized backups, restore). A connector's `url`/`command`/`args`/`env` routinely embed a credential
(`postgresql://u:pw@…`, `?api_key=`) — the leak ADR `2026-08-13-what-a-model-may-see-of-a-connector`
closed — so "which fields are secret" is not "which fields are called password".

## Decision

We will store one row per connector in an ordinary `connectors` table
(`id, kind, name, enabled, config, state, created_at, updated_at`) and one row per connector that has
credentials in a locked-down `connector_secrets` table (`connector_id → {field: value}`).

- **Only `connector_secrets` is a SECRET table.** `connectors` is kept in a sanitized backup, so it holds
  nothing credential-bearing: anything that can carry a credential — including an MCP server's endpoint
  and OAuth tokens, Plaid's minted access token, Telegram's chat map (the hidden secret `sessions`) — is
  a secret. `state.last_error` is redacted at write time (`core.redact_error`, which also masks bearer
  tokens, JSON credential fields, signature parameters and opaque path tokens; transports additionally
  never put a credential-bearing URL in a message). `connectors` is SQL-denied to Lucius all the same
  (`db/sql_console`, the schema tool, the Postgres REVOKE list): it holds raw server-chosen MCP text and
  personal settings, and `get_connector_status` / `get_connector_tools` are the model's way in. Both
  tables are checkpoint-excluded and non-restorable, every facade store is forbidden to mention
  descriptors, and a backup taken before the upgrade still carries the retired `mcp_clients` table, so
  `core.sensitive.RETIRED_SECRET_TABLES` is stripped from a sanitized download and a QA clone.
- **The catalog is code; instances are rows.** `services/connector_catalog.py` declares one
  `ConnectorKind` per kind — presentation copy, native tool bundle, which secrets it keeps (each
  revealable, machine-minted, relink-only or hidden), its settings keys, whether it is pausable, its
  "is it set up?" rule, its notify channel, its public-config and removal hooks. `services/connectors`
  (the view), `connector_secrets` (operator reveal), `move_secrets` (Move) and
  `aggregation/providers` read it and keep no list of their own. A connector that has never been saved
  renders from the catalog's defaults as `not_configured`; the first write creates its row.
- **`ConnectorStore` is the one data-access class**, with one secret rule everywhere (a blank string
  keeps, `None` clears) and every mutation in one transaction over both tables, serialized across
  processes (`serialized_unit_of_work`: SQLite's writer lock before the first read, the connector's
  Postgres advisory lock before a compare-and-swap reads). The sixteen settings-store classes remain as
  thin facades over it so their ~50 callers did not move; per-kind validation and defaults stay in them.
- **Price-source telemetry stays in `runtime_state`.** Quota and circuit state are small and live in the
  two price-source rows' `state`; the event log (hundreds of events, rewritten on every provider call)
  keeps its own `market_data_observability` namespace — in a `connectors` row it would rewrite and parse
  ~600 KB on every call and every list read.
- **A restore refuses a backup from another version.** A backup whose database is stamped with a
  different Alembic revision than this code's head is refused before anything destructive; older
  backups are not migrated on restore.
- **Email is one connector** with one row, one address, one password, one on/off, one save. The IMAP and
  SMTP legs share an identity; any pre-2026-08-17 divergent SMTP login or recipient is dropped by the
  migration.
- **The view loses `capabilities`, `managed_by`, `transport`, `provider_kind`.** `GET /api/connectors`
  returns `{id, kind, name, enabled, status, error?, pausable, config, secrets_public, short_description,
  long_description, tools}`; `PATCH` takes `enabled` only. Lucius's `get_connector_status` returns
  `{id, name, enabled, status, error?}` and `set_connector_enabled` loses its `capability` parameter
  (and the comms-capability refusal, which has nothing left to refuse).
- **Pause, resume and remove reconcile the notify channel.** A connector whose kind registers a
  `core.channels` channel (Email, Webhook) registers or unregisters it the moment it changes; before,
  a paused webhook kept delivering alerts until the next restart.
- **Move speaks rows.** It exports every connector row's settings, the state a moved link needs and its
  non-hidden secrets, and recreates them switched off; a payload in the old per-store format is refused
  with an "update both copies of the app" error. Lunch Flow's OAuth tokens (minted by the sender's
  authorization) are not carried.
- **Connectors read no environment variables.** Rows are the only source of connector settings.
- **Connector ids do not change** — they are the join key for `tool_permissions`, `chat_pairing`, alert
  routing and `accounts.external_ids`, none of which hold a foreign key.
- **Upgrading migrates data and nothing more.** One Alembic revision copies every old row, verifies a
  round trip (any mismatch raises, and `ensure_migrated` restores its pre-migrate backup), deletes the
  old namespaces and drops `mcp_clients`. There is no downgrade and no compatibility layer.

## Consequences

### Positive
- Adding a connector is one catalog entry (plus its tools), not a store class and six list edits.
- A secret can only come out of one narrow table; every other serializer of a row (view, Move, tool
  output, SQL) is safe by construction. Keep-on-blank, reset, reveal and Move are one implementation.
- `enabled_connector_ids()` — consulted on every AI tool catalog build — is one table read.
- Email's two-request half-failing save is gone; the 12 unlocked read-modify-writes are serialized.

### Negative
- `connectors` is kept in a sanitized backup, so the "nothing credential-bearing here" rule is a
  review-time invariant on every new `config`/`state` key, enforced only by the catalog's secret field
  list and `tests/test_connector_secrets_model_isolation.py`.
- An MCP server's endpoint (`url`/`command`/`args`) is still shown to the operator in the connector
  view's `config`, exactly as before: the Connectors trust panel and the edit form need it. It never
  reaches a model — every tool projection omits `config` — but it is the one place the operator-facing
  payload carries a possibly credential-bearing string.
- Restoring a backup taken before the migration is refused (a different Alembic revision), not migrated.
- A Move between app versions on either side of this change is refused.

### Follow-ups
- One generic `PUT /api/connectors/{id}/config` could replace the per-provider settings routes, once
  per-kind validation moves out of the facade stores; they remain because their forms still call them.
  (`get_connector_permissions` is already `get_connector_tools`; `tool_renames.RENAMES`.)

## Alternatives considered

- **Merge only Email's two rows** — rejected: fixes the visible split, leaves the six lists,
  `capabilities`, the inconsistent secret rules and the unlocked writes.
- **One table with secrets in a JSON column** — rejected: every reader of a row would hold the secrets
  and have to remember to strip them, the pattern that produced the MCP endpoint leak.
- **A Lucius-readable table that includes credentials-by-another-name** — rejected: an MCP server's
  config embeds credentials, and Lucius already has `get_connector_status`.
- **A table per connector kind** — rejected: typed columns, but fifteen-plus tables and migrations
  for every new connector, the opposite of the goal.
- **Keep OAuth tokens on the `mcp_clients` row** (ADR `2026-08-16-oauth-tokens-ride-the-mcp-clients-row`)
  — superseded: `mcp_clients` is gone; the token set splits into secrets (`oauth_client_secret`,
  `oauth_access_token`, `oauth_refresh_token`) and a non-secret `state.oauth` block, with the same
  protections by table name.

## Agent provenance

- **Decided by:** orchestrating Claude agent with the repo owner (decisions relayed in-session, 2026-10-10).
- **Lane / branch:** `claude/connectors-table`.
- **Alternatives weighed:** merge Email only; one table with a secrets column; a fully readable table; a table per kind.
- **Driving artifacts:** `.claude/rfcs/connectors-table.md`; ADRs `2026-08-13-what-a-model-may-see-of-a-connector`, `2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not`, `2026-09-06-database-ahead-of-code-is-refused-untouched`.
- **Why this way (one line):** a connector is a row and a kind is code, and the only thing that must be locked down is the narrow table that holds credentials.
