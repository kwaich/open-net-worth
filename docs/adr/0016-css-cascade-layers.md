# ADR 0016 — CSS cascade layers (@layer) for the global stylesheet

- Status: Accepted
- Date: 2026-06-20
- Relates to: RFC `.claude/rfcs/frontend-styling-pwa-ideal.md` (this is **Phase C**); ADR 0014 (token codegen)
- Provenance: Agent build choice (autonomous), code-reviewed (verdict: concerns → addressed) before commit.

## Context

The global stylesheet (`frontend/src/index.css`, ~18k lines, plus `chat.css`/`skills.css`)
had **no cascade layering**. Precedence was pure source-order, propped up by **26 `!important`
declarations** and duplicate bare selectors. That made overrides fragile: new rules had to be
placed *after* the rule they meant to beat, or escalate specificity / `!important`. It also made
the file resistant to safe refactoring (move a block → silently change the cascade).

Separately, the one-directional class linter (`check-css-classes.mjs`) only checked TSX→CSS, so
**dead CSS accumulated unchecked** (~289 verified-dead classes + a fully-dead `App.css`).

## Decisions

1. **Adopt CSS cascade layers** with a fixed order:
   `@layer reset, tokens, base, primitives, legacy, app;`
   - `reset` / `tokens` — element reset + the generated `:root` token block.
   - `base` — element defaults.
   - `primitives` — the `ui/` house classes (declared now, **populated in Phase F**).
   - `legacy` — the existing ~18k lines, wrapped verbatim. The transitional shrink target.
   - `app` — new/migrated work; overrides `legacy` by **layer order**, with no `!important` or
     specificity hacks.

2. **Declare the layer order in a separate `src/index.layers.css`, imported first in `main.tsx`**
   (before `index.tokens.generated.css` and `index.css`). Rationale: the Google-Fonts `@import` must
   remain the first statement in `index.css` (CSS requires `@import` before normal rules), and
   interleaving an `@layer` statement there is brittle. A dedicated first-imported file declares the
   order cleanly and deterministically. The built bundle was verified to carry the font `@import`
   first and the layer order `reset < tokens < base < primitives < legacy < app`.

3. **The generated `:root` moves into `@layer tokens`** via a one-line change to `build-tokens.mjs`'s
   `generateCss()` (the byte-identical drift guard auto-tracks it).

4. **The wrap is PURE** — only `@layer X { … }` braces were added; **no selector or property changed**,
   so the render is byte-identical (confirmed by code review + build). The 26+3 `!important` are now
   structurally redundant but were left in place; their removal is deferred to Phase F to keep this
   change a pure wrap.

5. **Dead-code removal rode along** (Phase C-3): `App.css` deleted, ~289 verified-dead classes removed
   (`index.css` 17,907 → ~15,690 lines), and `check-css-classes.mjs` made **bidirectional + dynamic-
   stem-aware** with a non-growth ratchet (`.css-debt.json`). Two linter bugs found in review were
   fixed: the ratchet wrote its floor unconditionally (self-clearing after a violation → now ratchets
   *down* only via `Math.min`), and the stem collector captured English prose from template literals
   (e.g. `${n} signal${…}` → stem `signal`, masking the dead `signal-*` family → now requires a stem to
   look like a CSS prefix: contain `-` or end `_`).

## Consequences

- Overrides are now **declared, not positional**: `@layer app` beats `@layer legacy` regardless of
  source order or specificity. New work goes in `app`.
- `primitives` and `app` are declared but currently empty — Phase F populates `primitives` (moving the
  `ui/` house classes there) and migrates feature CSS into `app`.
- The `!important` count is unchanged this phase (redundant-but-harmless); Phase F prunes them.
- `App.css` is gone; the dead-class floor is tracked by the ratchet and shrinks over time.
- Pure wrap → zero visual change, verified; no QA-gate regression expected.
