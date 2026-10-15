---
title: Budget-engine Descriptor splits model_note from description
status: Accepted
date: 2026-08-23
deciders: senior-engineer lane (Claude) + repo owner (mid-task correction)
---

# Budget-engine `Descriptor` splits `model_note` from `description`

## Context

Piece 1 of this task renamed the three budget engines' DISPLAY names/subtitles (engine ids
`zero_based`/`envelope`/`proportional` unchanged) to fix a real incident: every real-world
authority (YNAB, Ramsey, Actual, NerdWallet, Fidelity) defines "envelope budgeting" as a KIND
of zero-based budgeting, so naming this app's strict cash-pot engine "Envelope" collided with
that prior in both the user's and Lucius's heads. Asked to set up the Envelope lens, Lucius
described it as "the classic YNAB-style model with Ready to Assign and carryover" — the OTHER
engine's behavior — and offered to run zero-based write verbs against it, which would have
edited the user's real budget under the wrong paradigm.

The first fix attempt put BOTH audiences in one field: `Descriptor.description` carried a
short user-facing subtitle followed by a long "NOTE FOR MODELS: ..." disambiguation paragraph
naming internal engine ids and verb names (`set_pots`, `envelope_create`, ...). This shipped a
second incident immediately: `description` is rendered **verbatim** as body copy by
`GenericEngineSetup`/`ProportionalSetup` (`budgetLens/GenericEngineSetup.tsx:39`,
`body={descriptor.description}`) — so the model-facing paragraph landed on screen, unreadable,
naming things a user has no reason to know. One field cannot serve both a screen reader and an
LLM reader when their correctness bars are opposite: a user-facing string must be short and
never speak in ids; a model-facing string must be precise and IS allowed to name ids/verbs.

## Decision

We will split `Descriptor` into two fields: `description: str` (strictly user-facing — a
one-line subtitle, safe to render anywhere, asserted to carry no engine id / verb name / "NOTE
FOR MODELS" marker) and `model_note: str | None = None` (Lucius-only disambiguation guidance,
same substance as before, updated to the new display names). The two fields are then wired to
DIFFERENT serialization surfaces on purpose:

- `services/budget_engines/service.py::list_engines()` (the REST surface `GET
  /api/planning/engines` feeds, and the only thing the frontend ever reads) hand-builds its
  response dict field-by-field and **never includes `model_note`** — not omitted by convention,
  structurally absent from the wire, so no frontend code can render it even by accident.
- `ai/tools/budget_engines.py::list_budget_engines` (the AI tool) includes `model_note` per
  engine, but only when set (`**({"model_note": ...} if ... else {})`) — proportional, which
  never had a naming collision, carries no key at all rather than a null.

`tests/test_budget_engines_base.py::test_description_is_strictly_user_facing_no_model_facing_markers`
is the regression test: it asserts, for every registered engine, that `description` contains
none of `"NOTE FOR MODELS"`, `"zero_based"`, `"'envelope'"`, `"set_pots"`, `"envelope_create"`,
`"move_money"`, and reads under 80 chars. A parallel REST-side test
(`tests/test_planning_engines_api.py`) asserts `"model_note" not in` any engine dict from `GET
/api/planning/engines`.

## Consequences

### Positive
- The user-facing setup card is a plain subtitle again — matches every other `EmptyState` body
  in the app, no internal vocabulary leaking onto screen.
- Lucius keeps the FULL disambiguation guidance it needs to not repeat the incident, with no
  compromise on precision (ids/verb names still spelled out, just in the field only it reads).
- The split is enforced structurally (a dict that never includes the key) rather than by
  convention ("don't render this field") — the class of bug this ADR exists to fix cannot
  recur by a frontend dev innocently doing `{descriptor.model_note}` in JSX, because there is
  nothing on that wire to reach for.

### Negative
- `Descriptor` now has an asymmetric field a future engine author must remember: most engines
  need no `model_note` at all (only `zero_based`/`envelope` do, because only they collide with
  a popular external prior); a contributor adding a fourth engine has to know to ask "does my
  engine's id/behavior collide with a well-known naming convention" rather than the field being
  self-evidently required.
- Two hand-built dicts (`service.list_engines()` and the AI tool's own) must independently stay
  correct about which fields cross which boundary — there is no single schema enforcing "REST
  never gets `model_note`" beyond the two regression tests above and the fact that neither
  dict-builder uses `dataclasses.asdict()` (which would leak it structurally into whichever one
  forgot to strip it).

### Follow-ups
- If a future engine (e.g. a community "caps" engine) also needs model-facing disambiguation,
  it sets `model_note` on its own `Descriptor` — no other wiring changes needed, both
  serialization sites already handle "some engines have it, some don't."

## Alternatives considered
- **Keep one field, just shorten the combined string** — rejected: any text long/precise enough
  to actually stop Lucius repeating the incident (naming both engines' ids and verbs) reads as
  a paragraph, not a subtitle, when rendered on screen; the two correctness bars are opposite,
  not just different lengths.
- **Keep `description` combined but have the frontend strip everything after a `NOTE FOR
  MODELS:` marker before rendering** — rejected: fragile (a marker-string convention silently
  breaks the moment someone forgets it), and still ships the model-facing paragraph over the
  wire to a client that has no business receiving it at all.
- **Move the disambiguation guidance into the AI tool's own static schema `description=`
  instead of a per-engine runtime field** — rejected: the guidance is genuinely PER-ENGINE
  (`zero_based`'s note and `envelope`'s note are different), and a static tool description is
  paid on every offer of the tool regardless of which engine is actually in play, where a
  runtime field costs nothing until the engine in question is actually listed.

## Agent provenance

- **Decided by:** senior-engineer lane (Claude), acting on an explicit mid-task correction from
  the task's coordinator after a screenshot confirmed the rendered-on-screen bug.
- **Lane / branch:** working tree on `main` (no dedicated branch for this task).
- **Alternatives weighed:** one combined field with a stripped/shortened string vs. a
  frontend-side marker-strip convention vs. a static per-tool note vs. the chosen per-engine
  `model_note` field split by serialization surface.
- **Driving artifacts:** coordinator's two mid-task correction messages (naming-collision
  incident background; the "Descriptor.description is consumed by TWO audiences" bug report).
- **Why this way (one line):** the fix has to be structural (a field that literally never
  reaches the frontend) because a convention ("don't render this one") is exactly the kind of
  rule that gets forgotten the same way the original incident happened.
