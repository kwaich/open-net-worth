# 2026-07-09 — Extensibility substrate: strict typed core, open extension bags, declared participation

**Status:** accepted (owner-directed principle; **implementation deferred** to the P1-A storage rebuild — this ADR records the invariant that rebuild must honor, not a shipped mechanism). Complements the graded-trust ruling (`PLATFORM_MASTER_PLAN.md` §8.8) and the domain-packs / pluggable-engine decision (§8.3).
**Where (forward-looking):** constrains the P1-A SQLite migration + P2-B registries + P3 custom-fields work. Full discussion: `docs/design/design-notes.md` Thread D. Program of record: `docs/launch/PLATFORM_MASTER_PLAN.md`.

## Context

The domain-model audit (2026-07-06) found the data model is NOT extensible: entities are fixed Pydantic schemas, so any new field is a core PR, and every persona's extra data (a watch's condition, a property's address, a policy number) gets crammed into `tags[]` or free-text `note`. The owner's target is the "xlsx model" — strict where correctness needs it, freely expandable everywhere else — so users and community plugins can add their own data (and have it *participate* in computations) without a core change. The tension: a structureless blob (a plain text file / spreadsheet) is infinitely extensible **because** it has zero correctness — which is exactly what a wealth manager cannot be, since net worth / tax / budget correctness is the whole value.

## Decision

The extensibility substrate is: **strict typed core + open typed extension bags + plugin-owned entity types + declared participation hooks.** Four rules:

1. **Correctness-bearing core fields stay strict/typed** — amounts, currencies, dates, account links, the two-leg structure. Money math depends on them; they are never loosened. (The xlsx cell-in-a-formula.)
2. **Every entity gets an open, namespaced `custom_fields` bag of *typed* values** — "add a column." A user or plugin declares a field; core stores and passes it through untouched; renderers show it; it's filterable. (The freeform cell.)
3. **Plugins may own whole new entity types** (`property`, `insurance_policy`, `loan_given`) in their own namespaced store, CRUD'd through a generic API — "add a sheet." Novel things become first-class data, not shoehorned into accounts/transactions.
4. **Extension data participates in core computations only through *declared typed hooks*, never by writing core outputs.** A custom asset declares a "current value" → core's net-worth fold reads it like a manual-priced holding (same conversion / plausibility / staleness checks, same code path); a custom field declares a role → it's queryable/aggregatable. **Plugins declare FACTS; core computes TRUTHS** — this is `recompute-not-trust` (never trust stored `base_amount`) generalized platform-wide.

Corollaries:
- **Participation is opt-in per field, and the ceiling is set by how much the data is typed + role-declared, not by a restriction we impose.** Untyped text → stored+shown; typed value → filter/sort; number+role → aggregate/report; base-ccy value+role → net worth+allocation; +basis+value-over-time → returns; +typed taxable events → tax. Go as far as you type.
- **Additive, not destructive, on canonical outputs.** Inputs are fully extensible; a plugin can ADD a metric/lens beside net worth but can never silently REDEFINE the canonical net-worth number everyone's taxes/decisions depend on (same one-canonical-home / infinite-additive-views rule as multi-lens budgeting, §8.3).

## Consequences

- **This must be designed ALONGSIDE the P1-A storage rebuild, not bolted on after.** Whether `custom_fields` is a JSON column and plugin entities are plugin-owned tables is a P1-A design-time call; deciding the *shape* after the migration means re-migrating. The concrete schema is deliberately left open here — only the invariant is fixed.
- What makes "let everything participate" safe rather than reckless is the same thing everywhere: **core owns the math, plugins own the inputs**, and declared typed inputs get the same validation as native data. Strict where correctness lives; open everywhere else.
- Bigger than the P3 "custom fields" line item — it is the extensibility *substrate* (custom fields + plugin entities + participation hooks), and it underpins the exotic-asset flagship (a watch/cask collection folding into net worth) and community domain packs.
- Open question carried forward: how much extension data must be genuinely *computable/queryable* (participate in net worth/returns/tax) vs merely *stored-and-shown* — the more it participates, the more structure the typed bag needs. Resolve at P1-A design time.

## Agent provenance

Produced during an owner-led design conversation (2026-07-08/09) that ran a 15-report industrial audit + competitor studies (OpenClaw, hermes-agent, Wealthfolio) and derived this substrate from the owner's text-file-vs-xlsx framing and the "plugins declare inputs, core computes" resolution to the participation question. Recorded now (ahead of implementation) so the principle constrains P1-A instead of evaporating; the schema decision is explicitly the implementer's at rebuild time.
