---
name: adr
description: Author an Architecture / Agent Decision Record under docs/adr/. Use when a decision has been MADE that is cross-cutting, hard to reverse, deviates from a CLAUDE.md convention/invariant, or is a non-obvious build choice an agent made mid-task — so the choice and its reasoning are recorded append-only instead of evaporating into the diff.
---

# Author an ADR

An ADR records a decision **already made**: the choice, the forces, the rejected alternatives, and
**which agent made it and why** (the Agent provenance block). It is short, numbered, and
append-only — never edited away. Lives in `docs/adr/`. See `docs/adr/README.md` for the full
conventions; this skill is the authoring procedure.

## When an ADR is REQUIRED (the trigger)

Write one when the decision is any of:
- **Cross-cutting** — touches multiple layers/modules or every caller of something.
- **Hard to reverse** — a data-model shape, a write choke point, an external contract.
- **Convention-deviating** — it bends/breaks a stated convention or invariant in `CLAUDE.md`. If
  you have to justify *why the rule didn't apply here*, that justification is the ADR.
- **A non-obvious build choice an agent made mid-task** — the approach isn't what a reader would
  assume from the code alone.

Routine, easily-reversible choices (a local refactor, a rename, an obvious fix) do **not** need an
ADR. In doubt: would a future agent waste time relitigating this, or be surprised by it?

This is **not** an RFC (forward-looking design *proposal*, pre-build, `.claude/rfcs/`) or a PRD
(product spec, `docs/prd/`). An ADR is the decision *of record*.

## Procedure

1. **Choose a filename.** New ADRs use **date-prefixed filenames**: `YYYY-MM-DD-kebab-title.md`
   where `YYYY-MM-DD` is today (e.g. `2026-06-28-store-fx-rates-per-day.md`). Sequential `NNNN-`
   numbers caused collisions under parallel/multi-agent authoring; date-prefix matches
   `docs/superpowers/specs/` and `.claude/rfcs/`. Existing `0001`–`0029` ADRs keep their numbers.
2. **Copy the template.** `cp docs/adr/0000-template.md docs/adr/YYYY-MM-DD-kebab-title.md` — use
   a short imperative kebab-case title.
3. **Fill it in:**
   - **Front-matter** — `title`, `status` (usually `Accepted` for a decision already made;
     `Proposed` only if not yet ratified), `date` (today), `deciders` (agent/role + human).
   - **Context** — the load-bearing forces, constraints, and the invariant/convention in play.
   - **Decision** — active voice, "We will …", one decision per ADR.
   - **Consequences** — Positive / Negative / Follow-ups (name a real downside).
   - **Alternatives considered** — each option weighed and why it lost.
   - **Agent provenance** — decided-by (agent/role), lane/branch, alternatives weighed, driving
     artifacts (RFC/PRD/review-finding/issue links), and the one-line "why this way".
4. **Update the index.** Append a row to the `## Index` table in `docs/adr/README.md`:
   `| [YYYY-MM-DD-slug](YYYY-MM-DD-slug.md) | <Title> | <Status> | <Date> |`.
5. **To supersede an earlier ADR** instead of writing a fresh decision: author the new ADR as
   above, then **flip the old ADR's `status:` to `Superseded`** and link the two together (new ADR
   references what it replaces; old ADR links forward). Never delete or rewrite the superseded
   record; update its row's Status in the index too.

## Verify

- File name matches `YYYY-MM-DD-kebab-title.md` with today's date.
- Every section is filled, including **Agent provenance**.
- The Index row exists and points at the new file.
- No existing ADR was edited except a status flip when superseding.
