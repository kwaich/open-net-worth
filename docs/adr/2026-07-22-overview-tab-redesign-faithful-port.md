---
number: 2026-07-22
title: Overview tab redesign — unified widget dashboard, faithful port
status: Accepted
date: 2026-07-22
deciders: orchestrating agent (Claude) + repo owner (task directive)
---

# Overview tab redesign — unified widget dashboard, faithful port

## Context

The handoff (`frontend/design_handoffs/overview_handoff/`) replaces the static, single-column
Overview tab with a Datadog-style customizable widget dashboard: a 12-column grid, 40+ widget
types across seven groups, drag/resize/compaction, multiple named views, and three ways "Lucius
builds a widget" (a fixed-prompt Q&A card, a spec-driven chart, a sandboxed custom-HTML escape
hatch). The prototype (`prototype/overview_v2b/*.jsx`, ~4,300 lines) is a fully interactive
reference implementation over demo data; `01`–`05-*.html` are the prose specs.

The task directive adds three hard requirements on top of "port the design":
1. Unify the widget foundation so Lucius-built ("custom") widgets and predefined widgets share one
   base implementation — predefined widgets are preset instances of the same shape, not a parallel
   system.
2. Eliminate duplicate widget identifiers (the prototype's `uid` vs `id`, and any other
   duplicate-purpose field) — one consistent naming pattern, with better names where two fields
   serve genuinely different purposes.
3. Fully responsive/functional on mobile.
4. Never touch `LuciusDock`/the chat side panel implementation — trigger the real one instead of
   reimplementing it.

An Explore pass over the current codebase found: **no existing widget/dashboard system** (green
field — no naming collisions to reconcile, no legacy saved layouts to migrate); the handoff's own
claim that `OvCharts`/`OvData` are "reused from production" does not hold (no such modules exist —
`Overview.tsx` hand-rolls its Chart.js lifecycles inline); a **mature, hardened artifact/sandbox
pipeline already exists** (`create_artifact` tool → `backend/services/artifacts.py` →
`frontend/src/components/chat/ArtifactCard.tsx` + `utils/artifactFrame.ts`, null-origin sandboxed
iframe, CSP, height-probe) that is structurally identical to what `OvLuxHtml` needs; and two live
Lucius-trigger mechanisms already exist (`onChatSuggest`/`onAskLucius` free-text prefill, and
`useLuciusMention()`/`<LuciusAskButton>` entity-mention staging) that any new "Ask Lucius" /
"Share with Lucius" affordance should call into rather than duplicate.

## Decision

We will build this as a from-scratch, faithful port of the prototype's widget-instance shape, grid
engine, and widget catalog, with these specific choices:

1. **One instance shape, one registry, for every widget — predefined and Lucius-built alike.**
   `WidgetInstance` (grid position/size, `config`, title override, etc.) is the single shape every
   card on the grid uses; `WidgetDef` (`WIDGET_DEFS` → `WIDGET_DEFINITIONS`) is the single registry
   every instance's `defId` resolves against, whether the def is a hand-built widget (net worth,
   allocation, spending pulse, …) or one of the three Lucius-generated def rows (`lux-ask`,
   `lux-chart`, `lux-html`). A Lucius-generated widget is not a special case in the data model at
   all — it differs only in which component its def resolves to and what its `config` carries
   (a prompt vs. a chart spec vs. an artifact reference). This is what "predefined widgets as preset
   instances" means concretely: every default/preset view is just `normalize()` seeding ordinary
   instances from ordinary defs.

2. **Rename the duplicate-purpose identifier pair, and audit the rest of the shape for the same
   smell.** The prototype's `uid` (stable per-instance key, `id + '-' + timestamp36`) and `id` (key
   into `WIDGET_DEFS`, decides component/shape/settings) are different concepts wearing
   confusingly similar names. Renamed:
   - `uid` → **`instanceId`** — the one stable identity of a placed widget (survives duplicate,
     drag, resize; two "Movers" widgets on the same view get two different `instanceId`s).
   - `id` (on both the instance and `WIDGET_DEFS` entries) → **`defId`** — the registry lookup key.
     Instance and definition now use the *same* field name for the *same* concept (a lookup key),
     instead of the instance calling it `id` and meaning "which def."
   - Prototype's per-def `source: [tabKey, tabLabel]` tuple → **`source: { tab, label }`** — a
     positional 2-tuple silently doing the same "two names for two purposes" thing at a smaller
     scale; named fields remove the ambiguity at every call site.
   - `title` stays on both `WidgetDef` (the def's own default rendered title) and `WidgetInstance`
     (the user's rename override) — kept as-is because the foundation doc is explicit that these
     are deliberately separate props precisely so a rename can never eat the def's title, and
     giving them different names would obscure that "override wins over base" relationship rather
     than clarify it; the ambiguity risk here is about merge order, not identity, and is resolved by
     a code comment at the one merge site (`WidgetShell`), not a rename.
   - View identifiers (`DashboardView.id`) and def-def identifiers are unrelated single-purpose
     ids and are left as plain `id` — the rename is scoped to the one place two *different*
     concepts collided under near-identical names, not applied reflexively everywhere the word
     "id" appears.

3. **Drop the prototype's legacy-layout migration branches.** `normalize()`'s handling of
   pre-existing saved-layout formats (`string[]`, `{size,col,h}`, the retired combined `balance`
   widget, row-unit rescale heuristics) exists because the *prototype itself* has shipped multiple
   times before. This is a green-field feature in this app — there is no prior saved layout to
   migrate from — so that branch is dead weight here and is not ported (CLAUDE.md: no speculative
   backwards-compatibility code for scenarios that can't happen in this codebase). `normalize()`
   keeps only: fill-missing-coordinates + `seedConfig()` + `compact()`.

4. **Chart wrapper and delegated tooltip are new shared modules, not a port of something that
   already existed** — the handoff's "reused from production" claim doesn't hold (see Context).
   `charts.jsx`'s theme/axes/legend/gauge/area-fill logic is extracted into a new
   `frontend/src/components/tabs/overview/charts.tsx`, reusing the existing `cssToken()` helper
   (`utils/chartTheme.ts`) for CSS-var resolution instead of re-deriving one. The three chart
   lifecycles hand-rolled today inside `Overview.tsx` are ported to use this wrapper as part of the
   Net-worth-group lane, so there is exactly one Chart.js wrapper in the app going forward, not two.

5. **Lucius-generated widgets reuse real production infrastructure — no new mocked generation
   pipeline, no touching `LuciusDock`/the side panel implementation.**
   - **Ask Lucius** (`lux-ask`): an editable-prompt card; its "Ask"/regenerate action calls the
     existing `onChatSuggest`/free-text-prefill path to open the *real* side panel/dock with that
     prompt. The answer is a real assistant turn in the real panel, not a fabricated inline string —
     this is an intentional, disclosed divergence from the prototype's mocked
     `window.claude.complete()` call (documented in `PARITY_AND_GAPS.md`).
   - **Lucius custom** (`lux-html`): pins a real artifact (created via `create_artifact` in a normal
     chat turn) into the grid, rendered through the *existing* `ArtifactCard`/`artifactFrame.ts`
     sandboxed-iframe pipeline verbatim — zero new sandboxing code. "Create with Lucius" opens the
     real panel prefilled asking Lucius to build a dashboard artifact; the widget's `config` stores
     the artifact id, resolved at render time via the existing `fetchArtifactContent`.
   - **Lucius widget** (`lux-chart`, the spec-driven auto-chart builder): **deferred**. It requires
     a real spec-planning AI tool (`planSpec`'s constrained-JSON contract) that does not exist yet —
     building one is a separate AI-tool project, not a UI-porting task, and out of scope here per
     the "don't touch the Lucius/assistant surface" constraint (a new tool is assistant surface, not
     UI). The `defId` stays registered (so the catalog/picker is complete and honest about what
     exists) but renders a clear "coming soon" state, never a fake or silently-broken chart. Filed as
     a follow-up task (see Consequences).
   - "Build a view with Lucius" (empty-dashboard entry point) opens the real panel with a
     build-a-view prompt; the user still adds the resulting suggestions via the normal picker — no
     auto-insert pipeline, for the same reason as `lux-chart`.
   - **Share with Lucius** (per-widget kebab action) and the needs-attention row's "Ask Lucius"
     action both call the existing `useLuciusMention()` and the entity-mention chip machinery,
     exactly mirroring how `SummaryBar`'s cards already do it — not a new mention mechanism.

6. **Alerts stay UI-only**, exactly matching the prototype's own documented scope (`{op, value}`
   stored on the instance, no evaluation engine) — the handoff itself flags this as unbuilt backend
   work, not a gap this port introduces.

7. **Widget-layout persistence is new backend surface** (the one genuine gap the Explore pass
   found): a dedicated `JsonStore` (`dashboard_layout_store.py`, per the `add-store` convention) +
   `GET`/`PUT /api/dashboard/layout` (per `add-endpoint`), holding `{ activeViewId, views: [{
   viewId, name, widgets: [...] }] }` — position/size/config/title/limit/alert only, never a
   derived figure, per the handoff's own "keep the schema minimal" guidance.

8. **Widget content is implemented per-group in parallel worktree lanes**, each producing its own
   new files (`widgets/<group>.tsx` + `widgets/<group>.css`) so lanes never edit the same file — a
   pre-authored `registry.ts` (foundation) imports and merges all seven group modules. Each widget
   wires real data via existing endpoints wherever one exists (the majority, per the Explore pass);
   a genuine backend gap renders an honest empty/"needs more data" state rather than a fabricated
   number, and is logged in `PARITY_AND_GAPS.md` rather than silently shipped.

9. **Responsive strategy**: the grid collapses to a single stacked column below the `sm` (640px)
   breakpoint (drag reduces to reorder-only via pointer events, which already unify mouse/touch;
   resize handles hide), following the Plan-tab precedent of `container-type: inline-size` +
   `@container` queries on each widget card (a card's own box width, not just the viewport, since
   independently-resizable cards are exactly the case container queries exist for) layered under
   the existing `@media (max-width: 640px)` convention for page-level chrome (header, tab bar,
   picker modal).

## Consequences

### Positive
- One data model and one grid engine for the entire dashboard means a future widget type (Lucius or
  hand-built) is *always* "add a `WidgetDef` + a component," never a fork.
- `instanceId`/`defId` naming makes "which id is this" answerable by name alone at every call site
  (register lookup, drag key, duplicate-with-new-identity) without reading surrounding code.
- Reusing `ArtifactCard`/`useLuciusMention`/`onChatSuggest` means zero new sandboxing or
  mention-context code, and zero risk of the two-Lucius-pipelines problem the task explicitly warned
  against.

### Negative
- The spec-driven Lucius chart builder (`lux-chart`) ships as a registered-but-"coming soon" widget
  type rather than working generation — a real design-spec gap vs. the handoff, disclosed in
  `PARITY_AND_GAPS.md` rather than silently dropped.
- Some Investing/Reports/Tracking widgets (Sankey, treemap sector grouping, allocation drift vs.
  target, contributions-vs-market decomposition) have thinner backend support than net worth/cash
  flow; where a lane hits a genuine gap it ships an honest partial/empty state instead of the full
  visualization — tracked per-widget in `PARITY_AND_GAPS.md`, not silently downgraded.
- Dropping the legacy-layout migration branch (Decision 3) means if this same widget system is ever
  reintroduced with a different shape in a *future* redesign, that future work needs its own
  migration — acceptable since there is no such data today.

### Follow-ups
- File a follow-up task for a real `planSpec`-equivalent AI tool (spec-driven chart generation) once
  the Lucius/AI-tool surface is ready to take on new generative capability — out of scope here.
- `PARITY_AND_GAPS.md` (this redesign) lists every per-widget backend gap for future hardening-loop
  passes.

## Alternatives considered

- **Keep `uid`/`id` as-is, just document them better** — rejected: the task explicitly requires
  eliminating the duplicate-identifier smell, not documenting around it, and the names actively
  invite the "which one do I read here" bug the prototype itself never had to answer twice.
- **Build a real spec-planner tool now so `lux-chart` fully works** — rejected for this pass: it is
  a new AI-tool/assistant-surface capability, not a UI port, and doing it well (facet enumeration
  across every data domain, not just the demo's two sources) is a project-sized effort on its own;
  shipping a fake/heuristic version would violate the app's "never lie with a broken/fake feature"
  posture more than disclosing "coming soon."
- **Migrate legacy saved-layout formats defensively** — rejected: no such data exists in this
  green-field feature; the code would be untested dead weight (CLAUDE.md: no speculative
  backwards-compat).
- **One shared `registry.ts` widgets map hand-edited by every parallel lane** — rejected: high
  merge-conflict surface for no benefit; per-group files + a foundation-owned merge point give the
  same registry with zero lane-to-lane collisions.

## Agent provenance

- **Decided by:** orchestrating Claude agent (foundation + lane dispatch for this task)
- **Lane / branch:** `mc/98-reimplement-the-overview-component-follo`
- **Alternatives weighed:** see above — kept-uid-documented vs. renamed; live spec-planner vs.
  deferred; defensive legacy migration vs. dropped; shared registry file vs. per-group files
- **Driving artifacts:** `frontend/design_handoffs/overview_handoff/{01..05}-*.html`,
  `prototype/overview_v2b/*.jsx`; task directive (unify widget foundation, eliminate duplicate ids,
  full responsiveness, don't touch Lucius panel)
- **Why this way (one line):** one instance shape + one registry makes "predefined widgets are
  preset instances" literally true in the type system, and reusing the artifact/mention
  infrastructure verbatim satisfies "don't reimplement Lucius" while still shipping a working
  custom-widget escape hatch.

## Amendment 2026-10-14: the "Lucius widget" (`lux-chart`) is removed

**Decided by:** repo owner (2026-10-14).

Decision 5 kept `lux-chart` registered with a "coming soon" state until a real spec-driven chart
builder existed. Commit `ae671d33e` (2026-10-13) removed it — the picker's "Generate widget" path,
the stub and the registry entry — without amending this record. The owner reviewed that and chose
to keep it removed rather than restore the stub: a picker entry that only ever says "coming soon"
is not worth carrying. Decision 5's other two Lucius widgets (`lux-ask`, `lux-html`) and "Build a
view with Lucius" are unchanged. If spec-driven chart generation is built later, it comes back as a
new widget with its own decision, not by reviving this stub.
