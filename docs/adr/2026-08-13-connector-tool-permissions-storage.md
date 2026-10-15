---
title: Connector tool permissions are stored per (connector, tool) and nowhere else — the group aggregate is derived, and the table is checkpoint-excluded, non-restorable and non-portable like tool_grants
status: Accepted
date: 2026-08-13
deciders: repo owner (the three-level ladder, the uniform `ask` default, no money-movement exception — RFC `2026-08-10-connectors-architecture` §B4), Claude (implementer agent, connectors-redesign persistence lane — storage shape, classification, migration)
---

# 2026-08-13. Connector tool permissions: the storage shape and its four policy flags

## Context

The Connectors redesign (`.claude/rfcs/2026-08-10-connectors-architecture.md` §B4;
`frontend/design_handoffs/connectors_handoff/index.html` §4 and §10) gives EVERY connector —
Channels, Data sources, Web and MCP alike — the same per-tool permission ladder at one of
three levels: **Always allow / Needs approval / Blocked** (`allow` / `ask` / `block`), every
tool defaulting to `ask`, with no tiering by tool type and no tool structurally locked out of
`allow`. Group headings ("Read-only tools" / "Write & delete tools") are a labelling device;
a group control shows a fourth item, **Custom**, whenever the tools inside it disagree.

The prototype held all of this in component state. The handoff's §10 names the open item
precisely: *"The storage shape (per connector, per tool, with the group aggregate DERIVED and
never written) is the thing to get right."*

Two prior mechanisms exist and neither fits:

* `ToolGrantStore` (`tool_grants`) is GLOBAL and name-keyed — one row per tool name, two
  states (granted / not). It cannot express "allow this tool on Plaid but not on the MCP
  server that happens to advertise the same name", and it has no `block`.
* `McpServerConfig.tool_permissions` is MCP-only, two-valued (`allow`/`deny`), and opt-in
  DENY: absent meant allow, and a `trusted` server auto-fired everything not denied. Both
  `trusted` and the `is_money_movement` hard floor above it are removed by the sibling ADR
  `2026-08-13-mcp-permission-config-is-the-single-authority`.

## Decision

### 1. One row per (connector, tool). There is nowhere to write a group level.

New table `connector_tool_permissions` (revision `0039`), the generic `payload_table()`
shape, Tier-3 (no `book_id` — a permission is this INSTALLATION's trust posture, not a
book's ledger fact, matching `tool_grants`' own tenancy):

```
id          f"{connector_id}:{tool}"      # deterministic surrogate
payload     {id, connector_id, tool, level, updated_at}
```

`id` follows `budget_assignments`' precedent (`f"{book_id}:{month}:{category}"`): a keyed
relation whose pk is composed of the things that identify it, so "set this pair's level" is a
plain upsert and two rows for one pair are structurally impossible.

**The group aggregate is not stored, and cannot be.** "Custom" is computed where it is
displayed — uniform group -> that level, non-uniform -> Custom —
`services.tool_permissions.derive_group_level()` being the one shared definition so the
backend and the UI cannot drift into two. Making this an invariant of the SHAPE rather than a
rule to remember is the point: a stored group level is a second, coarser record that can
disagree with the tools underneath it, and the disagreement is unresolvable (which one wins?).

A group-level pick is therefore a deliberately **lossy stamp** (`set_group_permission`) of one
level onto every tool named, in ONE transaction. Lossy is specified behaviour — it is the
"make this whole group behave" control, and the one-click way out of Custom. Atomic because a
half-applied stamp leaves the group non-uniform, i.e. reading *Custom*: precisely the state the
user just acted to leave.

### 2. Absent means default. Only a real decision becomes a row.

`DEFAULT_LEVEL = "ask"` for every tool of every connector. A fresh install stores NOTHING; no
row set is minted on connect, and no tool type is seeded differently. `get_tool_permission`
never raises on an unknown connector or tool — "I have never heard of this" and "the user has
not decided" must be the same cautious answer, because that read runs on every dispatch.

`clear_connector_permissions` deletes rows rather than stamping `ask`, so "forget my
decisions" and "decide on ask" stay distinguishable — a re-added connector with a recycled id
starts cautious instead of inheriting its predecessor's trust.

### 3. Four policy flags — `tool_grants`' classification, verbatim

| Flag | Value | Why |
|---|---|---|
| Checkpoint | **EXCLUDED** (`ai.checkpoint._policy._EXCLUDED_TABLES`) | `restore_checkpoint` is a MODEL-CALLABLE tool with `mutates=("*",)`. Included, the sequence *user blocks a tool -> the model restores any earlier checkpoint -> the block is gone* is privilege escalation through an unrelated tool, no malice required. A checkpoint undoes what Lucius did to your DATA; a permission is what the owner decided. |
| DR restore | **NON_RESTORABLE** (`core.sensitive.NON_RESTORABLE_TABLES`) | The same argument one layer out: revoke `allow` -> restore a backup taken before the revocation -> the tool auto-fires again. |
| Secret | **NO** (absent from `SECRET_TABLES`) | It holds decisions, not credentials. Security-relevant but not confidential — exactly where `tool_grants` sits ("no chmod 600. It holds no secret"). |
| Portable | **NO** (absent from `PORTABLE_FILES` / `portable_bridge.BRIDGED`; wipe-only on `/api/reset`) | "I trust THIS install to let Lucius place orders unattended" is instance-local trust and must not travel to another machine inside a backup. Reset still reaches it — "reset means reset", and the direction of that wipe is back to the cautious default. |

The accepted cost is stated plainly, as it is for `tool_grants`: a level SURVIVES a `/rewind`,
so undoing a rash session leaves a permission changed during it standing. That is correct —
permissions change when the owner changes them, not as a side effect — and the Connectors tab
is the only place to change one back.

### 4. The migration carries `deny` across, and refuses to carry absence

Revision `0039` backfills from `mcp_clients.payload -> tool_permissions`:

* a stored `"deny"` becomes `level="block"` under connector id `mcp:<server_id>` — an
  explicit user decision, preserved across the redesign;
* a stored `"allow"` becomes `allow` — also explicit, and it means the same thing in both
  models;
* an **ABSENT** entry is deliberately NOT backfilled as `allow`, including every tool of a
  `trusted` server. Absent now means `ask`.

That last bullet is the load-bearing one. Backfilling the old implicit allow would silently
keep money-movement tools firing unattended past a redesign whose stated rule is that every
tool starts at Needs approval with no grandfathering. A permission migration may only move in
the cautious direction; the worst case here is a user re-granting `allow` on a tool they had
effectively auto-allowed, which costs one click and is visible.

## Alternatives considered

* **A per-connector blob** (`{connector_id: {tool: level}}`, the `dashboard_layouts`
  singleton shape). Rejected: it makes a group level *expressible* — a sibling key beside the
  tool map is one careless commit away — and the whole design rests on that being impossible.
  It also loses per-decision `updated_at`.
* **Extending `ToolGrantStore` with a level column.** Rejected: grants are global and
  name-keyed by design (RFC `2026-07-20` §13.3b — no argument-pattern grants), and connector
  scoping is exactly the argument-pattern distinction that store refuses to carry. Two
  vocabularies ("always"/absent vs. allow/ask/block) in one table would also make the confirm
  gate's read ambiguous.
* **Reusing `McpServerConfig.tool_permissions` for every connector.** Rejected: it is
  MCP-shaped (nested inside a server's config row), two-valued, and inverted (opt-in deny) —
  and the redesign's whole claim is that MCP is not a special case.
* **Seeding an explicit `ask` row per discovered tool on connect.** Rejected: an MCP server's
  tool list is the server's to advertise and changes between reads, so the seed would be
  perpetually stale; and it destroys the distinction between "unset" and "decided on ask" that
  `clear_connector_permissions` depends on.

## Consequences

* The service contract (`backend/services/tool_permissions.py`) is what other lanes build
  against — the external-MCP dispatcher reads `get_tool_permission`, the Connectors detail
  page reads/writes through `/api/connectors/{id}/permissions`.
* `McpServerConfig.tool_permissions` still EXISTS and is still what `mcp/client_manager.py`
  reads at the time of this ADR. Retiring the field belongs to the dispatch lane (sibling ADR
  above); this revision only guarantees the decisions inside it survive that rewiring. Until
  the rewire lands, an MCP tool's level is stored here but enforced there — a real, temporary
  split, flagged rather than papered over.
* `tests/test_sensitive_paths.py`'s file->table drift guard gained a third category,
  `db_native_non_restorable`: this is the first non-restorable dataset that never had a JSON
  file to migrate from, so the map could not express it and the guard would otherwise have had
  to be weakened to a non-exhaustive check.

## Superseded in part (2026-08-14)

Revision `0040` renamed this table `tool_permissions` and absorbed `ToolGrantStore`/
`tool_grants` into it — the OTHER "may this tool run" mechanism this ADR's Context section
names as one of the two prior stores that didn't fit (§ "Two prior mechanisms exist"). It
stopped being connector-only: `connector_id` (column name unchanged) now holds the reserved
bucket `core` for every unscoped tool too, so the `connector_` prefix in the original name was
no longer accurate. The three levels are also renamed in the same revision: `allow` →
`always_allow`, `ask` → `needs_approval`, `block` → `blocked`. Everything else decided here —
the shape, the derived-not-stored group aggregate, the four classification flags, the
cautious-absent-means-undecided rule — is UNCHANGED. See ADR
`2026-08-14-one-permission-store-for-every-tool`, and `docs/DATA_MODEL.md`'s `tool_permissions`
entry for the merged shape.
