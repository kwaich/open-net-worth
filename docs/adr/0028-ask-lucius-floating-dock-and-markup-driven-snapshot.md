# 0028 — Ask Lucius: floating dock everywhere + markup-driven screen snapshot

- Status: Accepted
- Date: 2026-06-25
- Deciders: user (provided the Claude Design handoff + prototype, drove it AFK) + implementing agent
- Related: design handoff `frontend/design_handoffs/ask_lucius_handoff/` (prototype `mentions/app.jsx` + `mentions.css`, `screen-awareness-decisions.md`, `ask-lucius-handoff.html`); build plan `docs/superpowers/plans/2026-06-25-ask-lucius-everywhere.md`; acceptance `docs/qa/acceptance/ask-lucius-dock.md`; **supersedes ADR 0027 decision #1** (snapshot source of truth); builds on ADR 0026 (server-authoritative mention registry)
- New/changed: FE `frontend/src/lucius/*`, `frontend/src/components/chat/lucius/*`, `frontend/src/lucius.css`, `RouterShell.tsx`, `ChatPanel.tsx`, `MessageBubble.tsx`, `state/screenSnapshot.tsx`; BE `services/screen_context.py`, `ai/tools/screen.py`, `services/mentions/{search,providers}.py`. Removes `components/tabs/investments/LuciusDock.tsx` + the partial `.lx-dock*` block in `index.css`.

## Context

ADR 0027 shipped a screen-aware MVP: a per-screen `ScreenSnapshot` published from each view-model, attached to the turn, with lazy `get_screen_detail` handles. The Claude Design team then delivered a fuller design (the interactive prototype) that the user instructed us to implement E2E with **no design tweaks**. It changes two things materially:

1. **Surface:** Lucius becomes a persistent **floating dock present on every screen by default** (collapsed pill ↔ expanded composer card ↔ resizable side panel), not a side-panel-only destination. Plus right-click/drag-in/attachments and an on-screen-sources Peek.
2. **Snapshot source:** the prototype's engine is **generic and markup-driven** — `getScreenSnapshot(visibleIds)` reads a declarative `data-ctx-*` contract off the **live DOM at send time** (two-layer visible-now/also-on-screen, form-harvest, tab-state, modal occlusion), rather than each screen serializing a bespoke view-model object. Structured per-row fields still originate in the view-model, surfaced via `data-ctx-fields='{…}'`.

ADR 0027 decision #1 said "structured from each screen's view-model, NOT DOM-scraped." The new design overrides this.

## Decision

**1. Rebuild the surface, keep the plumbing.** Port the prototype's component family (`Dock`, `SidePanel`, `Composer`, `Autocomplete`, `ScreenContextBar`, `Chip`, `Identity`, `MentionDetail`, `CitationChips`) into real TSX under `frontend/src/{lucius,components/chat/lucius}/`, mounted once at the app shell. **Keep** the POC plumbing verbatim: `useChat`/`useConversations`/streaming, the `/api/chat` contract (`mentions`/`responding_agent`/`skill`/`screen` already present), `MessageBubble` tool-call/`ChangePreview`/`ArtifactCard` rendering (tool-pill style unchanged per the user), `MentionComposer`'s DOM-as-source-of-truth contenteditable, and the backend mention/screen contracts. The dock and side panel are two forms of one surface sharing the new Composer/Autocomplete/ScreenContextBar.

**2. Markup-driven snapshot (supersedes ADR 0027 #1).** The snapshot is captured at send time by a generic DOM read of the `data-ctx-*` contract (`screen-awareness-decisions.md`): `data-ctx-region`, `data-ctx-title`, `data-ctx-fact`, `data-ctx-state`, rows with `data-ctx`+`data-entity-id`+`data-ctx-type`+`data-ctx-fields`+`draggable`; modals `role="dialog"`; opt-outs `data-ctx-ignore`; hooks `setHandles`/`setScreenFacts`/`setScreenPrompts`. Right-click, drag, active-tab, form-values, and modal occlusion are automatic.

Why markup-driven beats per-view-model:
- **Genericity / everywhere-by-default:** an un-instrumented screen still yields title + visible text; instrumentation only raises *quality*. One engine, every screen, zero per-screen surface wiring.
- **Captures what a view-model cannot:** actual viewport visibility, dock/modal occlusion (the two-layer visible-vs-off-screen split), and live form/slider values that aren't in `innerText`.
- **No drift:** `data-ctx-fields` is emitted from the same `objectFields()` source that feeds the detail popover, so on-screen context and the popover can never disagree.

The wire shape (`ScreenSnapshotWire` snake_case, `handles[].pinned`) and the backend `format_screen_context` / `get_screen_detail` / session-meta manifest / per-turn fetch budget (cap 3) from ADR 0027 are **retained and extended** (two-layer body text; more hydrators).

**3. Backend extensions (within the dependency rule).** Two-layer snapshot text in `services/screen_context` (a pure formatter — no `ai` import); additional handle hydrators in `ai/tools/screen.py` (the network/SSRF layer stays in `ai/tools`, never in `services`); type/category-synonym search in `services/mentions`.

## Consequences

- Lucius is one keystroke away on every tab; "what am I looking at / is this concentrated / summarize this / why is my FIRE age X" become answerable in place.
- Screens must adopt the `data-ctx-*` contract to give high-quality context; this is incremental (highest-value screens first) and degrades gracefully.
- Trust/transparency model from ADR 0027 is unchanged: a visible "<recipient> can see this screen" bar + a default-on toggle + the Peek showing exactly what's passed vs. fetched. Per-turn automatic sending stays gated behind the toggle; handle fetches stay server-side + SSRF-guarded + capped + user-visible.
- The investments-only `LuciusDock` and its partial `.lx-dock*` CSS are removed to avoid class/behavior collision with the global dock.

## Alternatives considered

- **Keep ADR 0027's view-model snapshot + add a dock shell only.** Rejected: can't express viewport visibility, occlusion, or live form state, and re-imposes per-screen serialization on every tab — the opposite of "everywhere by default."
- **Greenfield rewrite of the chat stack.** Rejected: the POC plumbing (streaming, confirmations, artifacts, sessions) is correct and security-reviewed; the handoff explicitly says keep it.
