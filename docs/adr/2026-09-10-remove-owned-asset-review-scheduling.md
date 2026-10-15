---
number: 2026-09-10
title: Remove owned-asset review scheduling and type asset facts
status: Accepted
date: 2026-09-10
deciders: Codex orchestrator and repo owner
---

# Remove owned-asset review scheduling and type asset facts

## Context

The original Assets design made review cadence part of every category and asset, derived freshness
from it, and optionally created an unattended re-mark Automation. The owner decided that this
configuration and reminder flow is unnecessary. The same form stores insurance and maturity as
free-text facts even though their intended semantics are boolean and date.

## Decision

We will remove review cadence, freshness projections, review queues, and `asset_remark:*`
Automations end to end. Owned assets will instead store `insured` as a boolean and
`maturity_date` as an optional full ISO date. Valuation marks and `valued_on` remain the only
asset-value freshness evidence. Currency remains server-owned and is displayed read-only.

## Consequences

### Positive

- The form focuses on facts that affect identity, ownership, and net worth.
- REST, Lucius, and UI share validated insurance and maturity semantics.
- No hidden scheduler continues an explicitly removed feature.

### Negative

- Existing re-mark schedules are irreversibly deleted.
- Users no longer receive proactive prompts when an asset valuation becomes old.
- Legacy free-text facts require a best-effort migration.

### Follow-ups

- Rewrite existing owned-asset/category payloads and stop the matching scheduler runtime.
- Update the Assets product and QA contracts.

## Alternatives considered

- Hide the controls while retaining cadence and schedules — rejected because behavior and data
  would survive the owner's removal decision.
- Retain insurance and maturity as facts — rejected because a switch and date picker would only
  disguise an ambiguous underlying contract.
- Delete historical runs and artifacts — rejected because audit history is useful and external
  file deletion is not atomic inside Alembic.

## Agent provenance

- **Decided by:** Codex orchestrator with repo-owner approval
- **Lane / branch:** `codex/asset-create-modal`
- **Alternatives weighed:** hidden defaults, fact-backed controls, full history erasure
- **Driving artifacts:** `docs/prd/asset-modal-simplification.md`, `.Codex/rfcs/asset-modal-and-review-removal.md`
- **Why this way (one line):** Removing the feature must stop its behavior while preserving the
  dated valuation history that still supports net worth.
