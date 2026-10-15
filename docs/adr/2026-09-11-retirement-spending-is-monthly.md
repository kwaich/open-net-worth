---
title: Store retirement spending monthly
status: Accepted
date: 2026-09-11
deciders: Codex orchestrator + repo owner
---

# Store retirement spending monthly

## Context

Monthly saving and retirement spending are paired Plan assumptions, but retirement spending was
stored annually under `fire_annual_expenses` and ambiguously as `spend` in Plan state. The Plan
tool schema already described `spend` as monthly while the client divided it by 12. Currency-based
sliders added another conversion boundary and an arbitrary maximum.

## Decision

We will persist retirement spending only as a monthly amount, using explicit monthly field names
at every boundary. Existing annual Profile and Plan values will be divided by 12 in one Alembic
migration with no runtime compatibility shim. Simulations consume monthly spending directly;
annualization is allowed only to derive the FIRE number and annual explanatory display.

## Consequences

### Positive

- User input, persisted data, Lucius tools, and simulations share one unit.
- Monthly saving and retirement spending can be compared directly.
- Future callers cannot silently treat an ambiguous `spend` value as annual.

### Negative

- The wire contract changes and requires coordinated migration of every caller.
- Repeating monthly decimals from annual values are preserved without display rounding.

### Follow-ups

- Keep migration and end-to-end unit tests in the permanent gate.
- Treat large one-off retirement costs as life events, not recurring monthly spending.

## Alternatives considered

- Keep annual storage with monthly UI conversion — rejected because the hidden conversion caused
  the existing mismatch.
- Store both units — rejected because they can diverge.
- Retain ambiguous Plan `spend` — rejected because the unit cannot be inferred from its name.

## Agent provenance

- **Decided by:** Codex orchestrator with solution-architect and ui-designer review
- **Lane / branch:** `codex/fix-outlook-currency-bugs`
- **Alternatives weighed:** annual storage, dual storage, and monthly-only storage
- **Driving artifacts:** `docs/prd/monthly-retirement-spending.md`; `.codex/rfcs/monthly-retirement-spending.md`; GitHub issues 104 and 105
- **Why this way (one line):** one explicit monthly value eliminates arbitrary slider limits and unit ambiguity at every money-math boundary.
