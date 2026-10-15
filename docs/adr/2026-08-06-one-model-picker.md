# 2026-08-06 — One model picker: six controls collapse into `ui/ModelPicker`

**Status:** Accepted
**Owner:** the maintainer (in-session: *"list down all places that we are showing ai model selection
picker, and unify them into one customized picker, with search, and switch among providers …
atm, it's look very ugly, and also, on automation tab, i can't choose some other models at all
from other providers"*; then *"for mobile, maybe we should show it as bottom sheet"*)
**Touches:** NEW `frontend/src/components/ui/ModelPicker.tsx` (+ `.mp-*` CSS in `index.css`).
CHANGED `components/chat/ChatPanel.tsx`, `components/chat/lucius/ModelEffortRow.tsx`,
`settings/sections/ModelSection.tsx`, `settings/sections/DedupSection.tsx`,
`tabs/automations/AutomationEditorModal.tsx`, `hooks/useModels.ts` (deleted
`buildChatModelGroups`), `backend/services/automations/options.py`

## Context

Six live surfaces let a human choose a model, and every one of them sourced and rendered its
own list — three option builders, four markup skins (`ui-select`, `rd-select`,
`a3-mini-select`, the `<Select>` primitive), and one bespoke search combo. Only one had
search. Three were not even provider-grouped, against a list that a configured OpenRouter
account alone pushes past **300 entries**.

Divergence had produced two real defects, both reported by the owner:

1. **The Automation editor could not reach most models.** `automation_options._model_options()`
   returned the instance default + per-task overrides + models *another Automation already
   stored*. The provider catalog was never consulted, so a model not already in use somewhere
   else was unreachable — the picker looked complete and was not.
2. **The Dedup judgment model was provider-locked.** `useModels()` without `multi`, listing the
   active provider only and emitting bare ids with no `provider_key:` prefix.

Neither is a coding mistake so much as a structural one: six independent list sources means
six chances to get the list wrong, and no single place to fix it.

## Decision

**One component, `ui/ModelPicker`, is the only model chooser.** A trigger button showing the
current model; a searchable, provider-grouped panel; `Preferred` pinned first.

- **Desktop → anchored popover. Phone (<640px) → bottom sheet.** The same split
  `ui/ActionMenu` already makes, for the same reason: a popover anchored to a 24px trigger is
  not usable with a thumb.
- **One component serves single AND multi select** (`multiple` prop) so the Preferred-models
  shortlist stops carrying a second implementation of search + grouping.
- **The list always comes from `useModels(true)`** — the cross-provider union. That is what
  structurally fixes both bugs; the picker cannot show a narrower list than the app has.
- **`/api/automations/options` loses its `models` field** (and `_model_options()` is deleted).
  Its only consumer was the broken select. Keeping a second, narrower model list around is
  how the bug happened.

### Deliberate sub-decisions

- **The provider name renders on a row ONLY inside `Preferred`.** That group is the one that
  mixes providers, where a bare `sonnet` could be the Claude Code CLI alias, an Anthropic
  model or an OpenRouter one — billed three different ways. Every other group has a provider
  header doing that job; repeating it on ~300 rows is noise. (This is the rule the deleted
  `buildChatModelGroups` had reasoned its way to; it is preserved, not reinvented.)
- **Multi-select saves EXPLICITLY**, via a Save button disabled until the draft differs.
  Eager per-click saving fires one toast per pick.
- **The selection is a typed object, not a bare string.** `resolveModelSelection(ref,
  models)` returns `{ref, modelId, providerKey, providerLabel, model, isDefault}` and every
  display path goes through it. The stored value stays the compact ref, but nothing
  re-derives the provider/model split by hand — and the trigger shows BOTH halves, because
  a bare id does not identify what will run or what it will cost.
- **No scrim, on either form factor.** A full-screen layer — even a transparent one — eats
  the first click on whatever the user was aiming at. Outside-click is a document
  `pointerdown` listener, which is only safe given the two stacking/pointer-events fixes
  below.

## Three defects the unit tests could not have caught

All three were found by the owner in the browser with the vitest suite fully green. Worth
recording because they are one class: **jsdom has no layout, no stacking and no pointer-events
model.** The unit tests were not wrong — they were blind, and the acceptance file now says so.

1. **Every click inside the open popover dismissed it.** The outside-click check was a
   `document` `pointerdown` listener that closed unless `panelRef.contains(target)`. That
   cannot distinguish "clicked outside" from "clicked an overlay stacked *above* the popover"
   — and `.lx-dock` sits at `z-index: calc(var(--z-toast) + 10)` = **710** while the popover
   was at `--z-modal` = **500**. The dock swallowed every click; each one read as "outside".
   Fixed by raising the panel to `calc(var(--z-toast) + 20)`, above every surface it can be
   opened from. A transparent scrim was tried as a second belt here and REVERTED: it fixed the
   hit-testing but ate the first click on whatever the user was actually aiming at.
2. **The list would not scroll.** `.mp-list` is a flex child, and a flex child's default
   `min-height: auto` refuses to shrink below its content — so it grew past `.mp-pop`'s
   `max-height` and simply clipped. `min-height: 0` is load-bearing, not tidying.
3. **Opened from inside Settings, the picker was inert AND took Settings down with it.**
   A Radix modal dialog sets `pointer-events: none` on the body and re-enables them only
   inside `Dialog.Content`. The portaled panel is a sibling of that, so it received no
   pointer events at all — unscrollable, unclickable — and the click fell through to the
   dialog overlay, dismissing Settings. `ui/Modal.tsx` already carried an
   `isOutsideExempt()` escape hatch for precisely this (`.lx-side`, `.lx-dock`,
   `[data-lucius-portal]`); the fix was to add a generic `[data-portal-layer]` marker to it
   plus `pointer-events: auto`, not to invent a new mechanism.

A fourth, milder one: `rows` had the whole `props` object in its `useMemo` deps, so it was
rebuilt every render, which churned `selectableIdx`, which re-ran the effect that resets the
keyboard highlight — stomping hover/arrow selection back to row 0 on every render.

## Consequences

**Good.** Search everywhere. Provider grouping everywhere. Both reported bugs are fixed
structurally rather than patched. Three option builders and four markup skins become one.
A model ref is provider-pinned from every site, so a choice survives a change of instance
default.

**Costs, accepted.**

- `GET /api/automations/options` no longer returns `models`. Breaking for any external caller;
  in-tree there was exactly one, now migrated.
- The picker is a custom control, so it inherits none of the native `<select>`'s built-in
  mobile affordances — hence the explicit bottom sheet, and hence the keyboard handling
  (arrows/Enter/Escape) having to be written and tested rather than assumed.
- `.mp-pop` at `--z-toast + 20` is now the highest thing in the app. That is a real ordering
  claim: any future overlay that must sit above a model picker has to say so explicitly.

Acceptance criteria: [`docs/qa/acceptance/model-picker.md`](../qa/acceptance/model-picker.md).
MP-5 (interaction) and MP-6 (responsive) are NOT yet discharged against the running app — they
are exactly the criteria jsdom cannot prove, and exactly where all three browser defects lived.

## Agent provenance

Built by a Claude agent in the same session that removed automatic model switching (ADR
`2026-08-06-auto-model-switching-removed`) — the owner asked for the picker inventory
immediately after, and the two changes share the `preferred` flag and the model-ref format.
Scope (all six sites), the single-vs-multi question, and the trigger style were put to the
owner rather than assumed; the mobile bottom sheet was the owner's addition mid-build. The two
browser defects above were reported by the owner against a green test suite, which is the
evidence for this ADR's closing claim about what unit tests can and cannot establish here.
