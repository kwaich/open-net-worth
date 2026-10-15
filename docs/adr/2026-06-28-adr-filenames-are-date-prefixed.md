---
title: New ADRs use date-prefixed filenames (YYYY-MM-DD-slug)
status: Accepted
date: 2026-06-28
deciders: Repo owner + Claude (docs-hygiene task DH3)
---

# 2026-06-28. New ADRs use date-prefixed filenames (YYYY-MM-DD-slug)

## Context

Sequential four-digit numbering (`0001`, `0002`, …) collided three times under parallel/multi-agent
authoring: numbers `0011`, `0014`, and `0020` were each used by two independent agents working on
different branches simultaneously. All three pairs exist in the repo today. The convention has no
collision-avoidance mechanism — whichever agent lands first "wins" the number, and the second
agent's file is simply also committed with the same prefix.

Date-prefixed filenames (`YYYY-MM-DD-slug.md`) already appear in two adjacent doc types in this
repo — `docs/superpowers/specs/` and `.claude/rfcs/` — and have experienced zero collisions in the
same parallel-authoring environment. Two agents writing ADRs on the same calendar day both get a
valid, non-colliding filename (they share the date prefix, but the slug disambiguates).

## Decision

We will use **`YYYY-MM-DD-kebab-title.md`** as the filename convention for all new ADRs authored
from 2026-06-28 onwards.

Existing numbered ADRs (`0001`–`0029`) keep their four-digit filenames; no renaming of the
historical corpus (except the three collision pairs resolved in the same DH3 commit: `0011-prefixed-
hex-entity-ids`, `0014-decouple-price-alerts-into-a-standalone-store`, and
`0020-dividend-calendar-data-source-and-projection` are each renamed to their add-date prefix, while
their collision partners keep the original numbers).

The `0000-template.md` file is the template and is never counted.

## Consequences

### Positive

- Parallel/multi-agent authoring can no longer produce filename collisions.
- Date makes the authoring timeline self-evident in a directory listing without needing git log.
- Consistent with the existing `specs/` and `rfcs/` conventions — one mental model for all durable
  doc types.

### Negative

- The corpus uses two schemes: numbered (`0001`–`0029`) + date-prefixed (new). A reader searching
  for "ADR 0030" will find nothing — they must look for a date-prefixed file.
- "ADR NNNN" shorthands in code comments and docs become non-uniform going forward (new ADRs can
  only be cited by date or slug, not a short number).

### Follow-ups

- Update `.claude/skills/adr/SKILL.md` to reflect the new procedure (done in this commit).
- Add a convention note to `docs/adr/README.md` (done in this commit).
- Resolve the three existing collision pairs (done in this commit).

## Alternatives considered

- **Keep sequential numbers, resolve collisions manually each time** — rejected: the mechanism that
  caused three collisions in the first place is unchanged; a future parallel build would collide
  again.
- **Use commit hash prefix** — rejected: opaque, hard to type in a reference; loses the human
  timeline signal entirely.
- **Centralize number allocation via a registry file** — rejected: introduces a merge conflict point
  on the registry file itself whenever two branches add an ADR simultaneously.
- **Adopt date-prefix for the entire corpus** (renumber all existing ADRs) — rejected: high churn,
  breaks all existing code comments and doc references; historical numbered ADRs are well-understood
  and not causing ongoing confusion. Forward-only is the minimal correct change.

## Agent provenance

- **Decided by:** Claude (implementer agent, docs-hygiene task DH3) + repo owner.
- **Lane / branch:** `main` (docs-hygiene, no worktree; clean state at task start).
- **Alternatives weighed:** sequential with manual resolution; commit-hash prefix; registry file;
  full corpus renaming — see above.
- **Driving artifacts:** three observed collision pairs (`0011`/`0014`/`0020`); existing date-prefix
  pattern in `docs/superpowers/specs/` and `.claude/rfcs/`; `docs/launch/BOARD.md` DH3 scope.
- **Why this way (one line):** date-prefix is already proven collision-free in this codebase's
  parallel-authoring environment, adopting it for ADRs closes the only remaining numbering gap.
