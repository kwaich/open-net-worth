---
title: What a model may see of a connector — a redacted endpoint, sanitized server text, and enforcement reported rather than claimed
status: Accepted
date: 2026-08-13
deciders: security-reviewer (findings) + implementing Claude agent (connectors backend-security lane)
---

# What a model may see of a connector — a redacted endpoint, sanitized server text, and enforcement reported rather than claimed

## Context

`485b46b2` made the connector surface AI-readable: `list_mcp_servers` and
`get_connector_permissions` let Lucius explain why a call needed approval and point the user at
the control. A `/security-review` of that lane, run against a real store, reproduced three
problems that share one root cause — **the surface was built by projecting views that were
designed for a different audience.**

1. **`McpClientStore.public()` is the BROWSER's view, not the model's.** It strips the token and
   the `env` values, so it looked safe to project. But it also carries `endpoint` — and on a
   large share of real MCP servers the endpoint *is* the credential:
   `npx -y @modelcontextprotocol/server-postgres postgresql://admin:pw@db.internal/finance` and
   `https://mcp.vendor.test/sse?api_key=…` are both the vendor's own documented quick start, and
   `POST /api/mcp-clients/import` ingests exactly those config blocks verbatim. Reproduced: the
   tool whose description promised *"Never returns tokens, credentials, or the names of
   environment variables"* returned a working DSN. `set_connector_enabled` had the same shape
   via `view.model_dump()`, and `list_mcp_servers` now hands the model the `mcp:<id>` ids that
   address those servers. The exfil path is any outbound tool: `send_email`, a Telegram notify,
   a `fetch_page` URL.

2. **A server's own text reached the model unsanitized.** Tool descriptions, `serverInfo`
   provenance and `last_error` (which embeds up to 200 characters of an attacker-chosen HTTP
   error body) were passed through verbatim, in a codebase where every other untrusted-text
   channel calls `core.untrusted.sanitize_untrusted` first. Reproduced:
   `«user_msg:deadbeef»…«/user_msg:deadbeef»` survived intact — and per `CLAUDE.md` that fence is
   the ONE marker in this codebase denoting *trusted* content carrying the operator's own
   authority. A hostile server could forge an operator instruction through a plain read tool.

3. **The ladder claimed enforcement it does not have.** `services.tool_permissions
   .get_tool_permission` has exactly ONE caller in the backend (`mcp/client_manager.py`), but
   `services.connectors._decorate` joins a `level` onto every native tool, the write routes
   accept any connector id, and the tool told the model `block` means "refused outright" for all
   of them. Nothing consults a level for `sync_plaid_now` or `send_email`.

## Decision

### 1. The model gets a redacted endpoint, never the endpoint

`models.mcp_client.redact_endpoint` is the model-facing twin of `endpoint_of`: an http server
reduces to its host (plus port), a stdio server to the basename of `argv[0]` — never an
argument, because the secret is routinely an argument. `list_mcp_servers` ships that as
`endpoint_hint` and drops `endpoint` entirely; `set_connector_enabled` answers with identity +
state (`id`, `name`, `provider_kind`, `enabled`, `status`, per-capability on/off) instead of the
whole `ConnectorView`.

`endpoint_of` is UNCHANGED and still verbatim. The human trust panel showing exactly what you are
pointing at is the whole point of that field (§5), and the fix is not to weaken it — it is to
stop treating "no secret VALUE from a store" as a synonym for "safe to put in a model's context".
Those are different questions with different answers, and the projection is now written to
answer the second one.

### 2. Every server-chosen string is sanitized and capped — sanitized, not fenced

`sanitize_untrusted` + an explicit cap on every server-sourced string in
`ai/tools/connector_config.py`, on the tool descriptions the offer carries
(`mcp/client_manager.external_tool_schemas`), and on the server label
`core.mcp_external.fence_external_output` interpolates into its banner — that label sits ABOVE
the fence, where a forged instruction would not even be marked as data.

Deliberately NOT fenced, against the review's suggestion to consider it: these are JSON tool
results, where JSON escaping is already the delimiter, and ADR
`2026-07-19-fence-only-flat-prompts-not-json-tool-results` settles that a textual fence on a JSON
tool result is the wrong layer. The offer's tool description IS flat prose, which is why it gets
the same sanitization even though it never sees a fence.

### 3. Enforcement is REPORTED, never assumed

`tool_permissions.is_enforced(connector_id)` — true for `mcp:<server_id>` only — rides on
`GET/PUT/DELETE /api/connectors/{id}/permissions` as `enforced`, and on
`get_connector_permissions`'s payload with a note telling the model to say so plainly rather
than claim a native `block` stops anything.

The alternative was 422-ing a non-MCP connector on the write routes. Rejected: the redesign
ships ONE ladder for every family, the UI already writes levels for all of them, and refusing
the write would break a shipped surface to conceal a gap rather than report it. Extending real
enforcement to native tools is separate work; `is_enforced` is the single place that changes
when it lands, and `test_get_tool_permission_has_exactly_one_production_caller` fails the moment
the fact stops being true — which matters because the flag would then under-report enforcement,
telling a user a level does nothing while it does.

### 4. Repointing an MCP server's endpoint withdraws its `allow` grants

`PATCH /api/mcp-clients/{id}` may change `url`/`command`/`args` while keeping the id, so an
`allow` granted to one endpoint silently transferred to a different one. It now calls
`revoke_allowed_tools`.

**`allow` rows only.** A full clear would move a `block` to `ask` — the permissive direction —
and nothing about "the endpoint changed" argues for relaxing a refusal. `ask` rows are already
cautious. Only the grant that could fire against an endpoint nobody vetted is withdrawn, back to
the default, so the user re-grants deliberately. This pairs with the frontend edit flow in ADR
`2026-08-13-editing-an-mcp-server-cannot-test-with-its-saved-secret`: that one is about what the
edit form may verify, this one about what the edit costs a prior decision.

## What this ADR deliberately does NOT do

**It does not degrade an `allow` on an unattended surface.** The review's original M4 proposed
treating `allow` as `ask` when nobody is watching. The owner retracted it: that is the
unconfigurable floor `2026-08-13-mcp-permission-config-is-the-single-authority` removed,
reinstated one layer down, and it would make an owner-set permission mean different things
depending on who is looking. `ask` and `block` already express "not without me" precisely. See
`CLAUDE.md`, Security-critical areas — *do not reintroduce a surface-dependent override*.

What survived from that finding is a **documentation** fix: `_external_tool_schemas`'s docstring
claimed it was "called ONLY on the interactive path … NEVER when a headless / skill / agent /
auto-apply scope is active", which has been false since inline skills landed
(`_apply_inline_skill_tool_state` runs on every branch, and `services.skills` validates
`mcp__…` entries in a skill's `allowed-tools` precisely so this works). The behaviour is
intended; four stale comments describing a T2 ceiling that no longer exists were corrected to say
so.

**It does not route outbound MCP through `core.pinned_http` / `assert_public_url`.** The review's
L1 asked for both. Only the scheme allowlist shipped (`http`/`https`, on the create/update/probe
input models and re-checked at the transport for legacy rows) — that alone kills the
`file:///etc/nope` filesystem-existence oracle the probe exposed. A private-address block was
rejected on evidence: `http://127.0.0.1:3000/mcp` is a first-class, documented way to run a local
MCP server, and nothing model-reachable can choose this URL — adding, editing and probing a
server are all human-only, with no Lucius tool for any of them, so this is not an SSRF primitive
for an injected model. Breaking a supported setup to close a hole only the operator can reach is
the wrong trade. If a model-reachable path to an arbitrary MCP URL is ever added, this must be
revisited *first*.

The scheme check lives on the INPUT models rather than on the stored `McpServerConfig`: raising
on construction would make one legacy row take down every read of the store, so the boundary
validates and `mcp/client.py` re-checks before `urlopen`.

## Consequences

- `list_mcp_servers` no longer answers "where is this server" precisely. Accepted: `id`, `name`,
  `transport` and `endpoint_hint` are enough to tell servers apart, and the full endpoint stays a
  human fact read in Connectors.
- `set_connector_enabled` returns less than it used to. It answers what it was asked.
- `GET /api/connectors/{id}/permissions` grew a field (additive; no client breaks).
- Revision `0039`'s backfill changed in the same lane (see its own docstring): a legacy
  `"allow"` is no longer migrated, because it never meant auto-fire and the intermediate UI wrote
  it for *Needs approval*. **Residual:** an instance that already ran `0039` keeps any `allow`
  rows it created — the migration cannot re-run. Remedy is one click: clear that connector's
  permissions in Connectors.

## Agent provenance

Findings traced and reproduced empirically by a `security-reviewer` against a real store; each
was re-confirmed from source by the implementing agent before any change. Two of the review's
requests were NOT implemented as asked — the fence wrapping (against a standing ADR) and the
private-address block (breaks a supported setup with no model-reachable attack path) — and both
are argued above rather than silently dropped. M4 was cancelled by the owner mid-implementation
and is recorded here so the retraction is discoverable from the security lane, not only from the
`CLAUDE.md` line it produced.

## Superseded in part (2026-08-14)

Decision §3 ("Enforcement is REPORTED, never assumed") is superseded by the permission-store
merge: `tool_permissions.is_enforced()` — the predicate this decision introduced, true for
`mcp:<server_id>` only — is REMOVED, along with the `enforced`/`permissions_enforced` fields it
rode on. Enforcement is now universal: `ai/tools/_confirm_gate.py::effective_level()` is the one
dispatch-time answer for every tool of every connector, native and MCP alike, so there is no
longer a gap to report honestly around. §1 (redacted endpoint) and §2 (sanitize + cap every
server-chosen string) are UNCHANGED and still current. See ADR
`2026-08-14-one-permission-store-for-every-tool`.
