---
title: Payee config writes are grantable T1 (manage_payees capability); merge/delete/bulk stay T2
status: Superseded
date: 2026-07-12
superseded_by: docs/adr/2026-07-18-remove-layered-ai-permission-system.md
deciders: orchestrating Claude agent + repo owner (explicit owner decision, 2026-07-12)
---

> **SUPERSEDED (owner decision, 2026-07-18) — see
> [`2026-07-18-remove-layered-ai-permission-system.md`](./2026-07-18-remove-layered-ai-permission-system.md).**
> The entire thesis of this ADR is reversed. The layered AI permission system — the
> T0/T1/T2 tier taxonomy, the `CAPABILITIES` bundles, `_UNTRUSTED_T1_FLOOR`, the headless
> ceiling, and per-agent auto-apply grants — has been removed. There is no T1 to be
> classified down INTO and no T2 to stay IN: all fifteen payee tools (the ten config writes
> and the five bulk/destructive ones) are simply tools, callable by any caller. Kept from
> the same era, and NOT part of this reversal: Bearer auth on the inbound MCP server,
> the nonce-staged stored-args confirm mechanism, the IDOR proposer↔tapper binding, all
> untrusted-text fencing sites, and the outbound external-MCP posture. Read below for the
> historical reasoning only.

# Payee config writes are grantable T1 (manage_payees capability)

## Context

The agent permission model (`backend/core/agent_caps.py`, P3-AG4 RFC §3) is closed-world:
a tool that is neither T0 (read/notify/propose) nor T1 (listed in a `CAPABILITIES` bundle)
is T2 — never usable by an unattended agent, and rejected outright by
`services.agents.validate_agent`'s allowlist ceiling (`HEADLESS_ALLOWED_TOOLS ∪ _T1`).

The entire payee write family had fallen through to T2 **by omission**, not by judgment:
no capability bundle listed any payee tool. In practice this blocked a natural agent job —
an email/transaction-logging agent that wants to mint a payee, set its default category,
or add an alias hit `"Tool 'create_payee' is not allowed for an agent"` when the owner put
it in an agent's `allowed_tools`. The owner ruled this wrong: creating/configuring payees
should be agent-allowed.

The T1 bar (RFC §3): reversible, single-record, low-blast-radius, checkpoint-covered
writes. Payee config writes meet it — they move no money, touch one registry row, and every
`JsonStore._save_raw` fires the checkpoint write-hook. But the family is not uniform:
`apply_payee_rules` is a GLOBAL ledger rescan, `apply_payee_rules_to_payee` /
`apply_payee_category` are bulk historical relabels, and `merge_payees` / `delete_payee`
are entity merge/delete — the same shapes CLAUDE.md and the tier table already pin T2
elsewhere (`apply_rules_to_history`, entity deletes).

## Decision

We will add a **`manage_payees`** capability to `agent_caps.CAPABILITIES`, classifying
these ten single-record config/label writes down to grantable T1:
`create_payee`, `rename_payee`, `set_payee_default_category`, `set_payee_autocategorize`,
`set_payee_hidden`, `add_payee_alias`, `remove_payee_alias`, `add_payee_rule`,
`remove_payee_rule`, `set_payee_rules`.

The bulk/destructive payee tools **stay T2** (closed-world, deliberately not classified
down): `apply_payee_rules`, `apply_payee_rules_to_payee`, `apply_payee_category`,
`merge_payees`, `delete_payee`.

`manage_payees` is **excluded from `_UNTRUSTED_T1_FLOOR`** (the hardened floor for agents
that ingest web/email content): payee defaults and per-payee rules steer FUTURE
categorization, making this a config-shaping capability in the same class as
`manage_rules`, which the floor already excludes. An untrusted-fed agent granted
`manage_payees` is narrowed to propose for these tools.

## Consequences

### Positive

- An agent (e.g. the email bookkeeper) can be granted payee registry management and run
  unattended within the auto-apply envelope — no human round-trip to mint a payee or set
  its default category.
- The tier table now reflects a deliberate judgment for the payee family instead of the
  closed-world fallback; the T1/T2 line inside the family mirrors the existing
  `manage_rules` vs `apply_rules_to_history` precedent.
- Zero new enforcement code: the allowlist ceiling, `auto_apply_decision`, the agents-API
  capability picker, and the MCP grant catalog (per-tool derived `money` flag) all consume
  `CAPABILITIES` generically.

### Negative

- A granted agent can now grow the payee registry and shape future auto-categorization
  (defaults, aliases, per-payee rules) without per-write confirmation — misconfigured
  agents can create noisy payees or wrong defaults. Mitigations: per-run write caps,
  checkpoint reversibility, the untrusted-feed narrowing above, and the grant being
  opt-in per agent.
- `set_payee_rules` is a whole-list replace on one payee — still single-record, but the
  coarsest tool in the bundle.

### Follow-ups

- Update the CLAUDE.md AI-tools table's `†` (NOT headless) markers for these ten payee
  tools when that section is next touched (the runtime source of truth is `agent_caps`).

## Alternatives considered

- **Leave the family T2 (status quo)** — rejected by owner decision: blocks a legitimate
  unattended payee-bookkeeping job for writes that clearly meet the T1 bar.
- **Classify the whole payee family down, including merge/delete/bulk** — rejected: entity
  merge/delete and bulk historical relabels violate the single-record T1 bar and the
  existing line-in-the-sand (`apply_rules_to_history`, entity deletes are T2).
- **Fold payee tools into the existing `categorize` capability** — rejected: `categorize`
  is transaction-label scoped; payee registry management is a distinct job a user should
  be able to grant (or not) independently.
- **Include `manage_payees` in `_UNTRUSTED_T1_FLOOR`** — rejected: payee rules/defaults
  steer future categorization, exactly the config-shaping class the floor excludes to
  blunt prompt-injection steering.

## Agent provenance

- **Decided by:** orchestrating Claude agent, ratified explicitly by the repo owner
  ("creating payees or stuff should be allowed!", 2026-07-12)
- **Lane / branch:** `fix/lucius-response-calibration` (main working tree)
- **Alternatives weighed:** status quo T2 / whole-family T1 / fold into `categorize` /
  add to untrusted floor
- **Driving artifacts:** `.claude/rfcs/phase3/agents-permissions.md` (tier model §3–4);
  owner chat report of the `create_payee` agent-validation refusal
- **Why this way (one line):** payee config writes meet the T1 bar (single-record,
  reversible, no money), so the closed-world fallback was the only thing keeping them T2 —
  classify them down deliberately and keep merge/delete/bulk at the existing hard line.
