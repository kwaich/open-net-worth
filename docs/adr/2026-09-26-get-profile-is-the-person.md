---
title: get_profile returns the person — other domains stored on the profile row are read through their own tools
status: Accepted
date: 2026-09-26
deciders: orchestrating agent (Claude) + repo owner
---

# 2026-09-26. `get_profile` returns the person, through an allowlist

## Context

`get_profile` — the tool every personalised answer starts from — returned `read_profile()`
verbatim. The profile row has grown into a store for six unrelated domains: the person
(identity, household, residency, income, risk, retirement), Zero-Based budget state
(`category_targets` and `category_notes`, keyed by envelope id since the envelope/category
split despite the name; `budget_config`; `credit_card_mode`), the investment target mix
(`target_allocation`), the Risk card's limits, chat UI toggles, and import/dedup settings.

The owner caught Lucius explaining its "category caps": it had read the raw
`category_targets` map — opaque `env_…` ids with no names — and paired target amounts with the
wrong envelopes (4 of 7 wrong), called them caps, and miscounted them. That is the failure ADR
`2026-07-20-resolve-ids-and-drop-dead-profile-fields` exists to prevent: opaque ids must arrive
with their labels. The labelled read of the same data already exists (`get_budget_month` rows
carry `envelope_id`, name and `target`).

## Decision

We will make `get_profile` return `services.profile.profile_for_model(read_profile())`: an
ALLOWLIST of the person's fields (`_MODEL_FIELDS` — name, nickname, date of birth, household,
nationalities/residency/tax residencies, employment, base currency, monthly net income and
expenses, declared liabilities, risk tolerance, retirement target age, the FIRE assumptions,
notes), a server-computed `age`, and the three preferences `update_profile` can set that are not
about the person under `settings` (`briefing_preferences`, `import_review_default`,
`cashflow_low_balance_threshold`). Everything else on the row is read through its own tool: budget
targets and notes via `get_budget_month` / `get_envelope_note`, the investment target mix via
`get_rebalance_plan`, savings goals via `get_targets`. `read_profile()` itself — the Settings UI,
FIRE, budgets, the system prompt — is unchanged.

## Consequences

### Positive
- No opaque id, and no key added to the profile row later, reaches the model raw — an allowlist
  fails closed where the old dump failed open.
- The tool's payload shrinks to what personalised advice needs, and matches the fields the system
  prompt already selects (`ai/prompts.py`).
- Read/write symmetry is pinned: every field `update_profile` can write reads back through
  `get_profile` or a named tool (`tests/test_get_profile_view.py`).

### Negative
- A second tool call when a turn needs budget targets or the investment mix alongside the person.
- `settings.*` is nested on the read while `update_profile` takes the same keys flat.

### Follow-ups
- The stored names stay stale (`category_targets` holds envelope-keyed Zero-Based targets). Moving
  budget, investment and UI state off the profile row into their own stores is a data migration,
  deliberately not part of this change.

## Alternatives considered
- **Blocklist the offending keys** — rejected: fails open; the next key stored on the row leaks
  the same way.
- **Label the envelope ids inside `get_profile`** — rejected: it would keep budget state in the
  person's tool, beside a labelled read that already exists.
- **Rename/move the storage now** — rejected for this change: an Alembic migration across every
  planning reader for a problem the read boundary solves on its own.

## Agent provenance
- **Decided by:** orchestrating Claude agent, on the owner's "fix and make sure get_profile is
  properly done and clean".
- **Lane / branch:** `main` (direct, owner's standing rule).
- **Alternatives weighed:** blocklist vs allowlist; labelling in place vs pointing at the labelled
  tool; projection vs storage migration.
- **Driving artifacts:** owner screenshot of Lucius mis-pairing Zero-Based targets (2026-09-26);
  ADR `2026-07-20-resolve-ids-and-drop-dead-profile-fields`.
- **Why this way (one line):** the model should get the person from the person's tool, and every
  other domain from the tool that already labels it.
