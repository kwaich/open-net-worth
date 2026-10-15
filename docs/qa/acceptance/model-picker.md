# Acceptance — the unified model picker (`ui/ModelPicker`)

One component now serves **every** surface that lets a human choose a model. It replaced
six divergent controls (owner request 2026-08-06: *"unify them into one customized picker,
with search, and switch among providers … atm it looks very ugly"*).

**Adopters (all six):**

| Site | File | Mode |
|---|---|---|
| Chat side panel | `components/chat/ChatPanel.tsx` | single |
| Lucius dock | `components/chat/lucius/ModelEffortRow.tsx` | single |
| Settings → Task models (×13) | `settings/sections/ModelSection.tsx` | single, `annotate` for vision |
| Settings → Preferred models | `settings/sections/ModelSection.tsx` | **multiple** |
| Settings → Dedup judgment model | `settings/sections/DedupSection.tsx` | single |
| Automation editor | `tabs/automations/AutomationEditorModal.tsx` | single |

Verify on the running app via `make qa-e2e` / `make qa-cu`, never `:5174`/`:8080` directly.

## MP-1 One control, one look

- **MP-1.1** Every site above renders a `.mp-trigger` button, never a native `<select>`.
  The trigger shows the current model AND its provider, or the site's default label when unset.
- **MP-1.2** The three retired option builders are gone: no site builds its own
  `<optgroup>` list. `buildChatModelGroups` no longer exists.

## MP-2 Search — the reason this exists

- **MP-2.1** GIVEN a configured OpenRouter account (300+ models) WHEN the picker opens
  THEN a search field is focused and typing filters the list live.
- **MP-2.2** Search matches the model id **AND** the provider name — typing `openai`
  surfaces that provider's models.
- **MP-2.3** A search matching nothing shows `No model matches "<q>"`, not an empty box.

## MP-3 Provider switching + grouping

- **MP-3.1** Options are grouped by provider, with **Preferred** pinned first when the
  shortlist is non-empty.
- **MP-3.2** A preferred model ALSO appears under its own provider — the group narrows
  nothing.
- **MP-3.3** The provider name is rendered on a row **only inside Preferred** (the one
  group that mixes providers). Repeating it under a provider header is noise.
- **MP-3.4** Selecting emits a provider-pinned `"<provider_key>:<model_id>"` ref, so the
  choice is immune to a later change of instance default.
- **MP-3.5** The trigger names **both** the model and its provider — `grok-4.5` `xAI`, not
  `grok-4.5` alone. A bare id is ambiguous: `sonnet` is a Claude Code CLI alias, an
  Anthropic model AND an OpenRouter model, billed three different ways.
- **MP-3.7** The instance default is a **group** (`DEFAULT`) holding one row that names the
  real model + provider, exactly like every other row — never a bare `Default (sonnet)`
  pseudo-option. The trigger likewise shows that model + provider, not the word "Default":
  following the default still runs a specific model, and the trigger's job is to say what
  this turn will run on. Which of the two it is stays visible in the list (the tick sits
  under the `DEFAULT` header). Selecting that row still stores `''`, not the resolved id.
- **MP-3.8** A ref with NO provider prefix (the instance default is stored bare, e.g.
  `grok-4.5`) still resolves to its provider — but ONLY when exactly one provider offers
  that id. `sonnet` on three providers resolves to no provider rather than guessing wrong.
- **MP-3.6** A selection resolves through the typed `ModelSelection`
  (`resolveModelSelection(ref, models)` → `{ref, modelId, providerKey, providerLabel, model,
  isDefault}`), so no caller re-derives the provider/model split from the ref string. A ref
  whose provider is no longer configured still reports its provider KEY rather than blank.

## MP-4 Both reported bugs are fixed

- **MP-4.1** (Automations) GIVEN a provider whose models no Automation currently uses
  WHEN the Automation editor's picker opens THEN those models ARE listed and selectable.
  *Was:* `/api/automations/options` returned only the instance default, per-task
  overrides, and models another Automation already stored — anything else was unreachable.
- **MP-4.2** (Dedup) GIVEN a second configured provider WHEN the Dedup "Judgment model"
  picker opens THEN its models are listed. *Was:* `useModels()` without `multi`, so the
  active provider only, with bare ids and no provider prefix.

## MP-5 Interaction — the dismissal defect

- **MP-5.1** GIVEN the picker is open in the CHAT panel or the Lucius dock WHEN the user
  clicks the search field, a group header, or scrolls the list THEN it stays open.
  *Was:* an outside-click check (`document` `pointerdown` + `contains()`) that could not
  tell "clicked outside" from "clicked an overlay stacked above the popover" — and
  `.lx-dock` sits at `z-index: calc(var(--z-toast) + 10)` (710) while the popover was at
  `--z-modal` (500), so the dock swallowed every click and each one closed the picker.
- **MP-5.2** The list SCROLLS when it overflows. *Was:* `.mp-list` is a flex child, whose
  default `min-height: auto` refuses to shrink below its content, so it grew past
  `.mp-pop`'s `max-height` and never scrolled.
- **MP-5.3** Clicking anywhere outside closes it; so does `Escape`. There is **no scrim on
  either form factor** — a full-screen layer, even a transparent one, eats the first click
  on whatever the user was actually aiming at.
- **MP-5.4** ArrowUp/ArrowDown move the highlight and `Enter` selects. The highlight is
  NOT reset while the user hovers or arrows.
- **MP-5.5** GIVEN the picker is opened from inside a modal (Settings → AI) WHEN it is used
  or dismissed THEN the host modal STAYS OPEN. *Was:* a Radix modal dialog sets
  `pointer-events: none` body-wide and only re-enables them inside `Dialog.Content`, so the
  portaled panel was inert AND the click fell through to the dialog overlay, closing
  Settings along with the picker. Fixed by marking every portaled node `data-portal-layer`,
  which `ui/Modal.tsx`'s `isOutsideExempt()` now honours (it also yields the focus trap).


## MP-6 Responsive — popover on desktop, bottom sheet on phone

- **MP-6.1** ≥640px: an anchored `.mp-pop` positioned under the trigger, flipping ABOVE it
  when there is no room below, and clamped into the viewport horizontally.
- **MP-6.2** <640px: a `.mp-sheet` bottom sheet with a grab handle, title and close button;
  the body does not scroll behind it.
- **MP-6.3** The popover never flashes at the top-left before it is positioned.

## MP-7 Multi-select (Preferred models)

- **MP-7.1** Picks render as removable chips; the picker STAYS OPEN between picks (a
  shortlist is built a few models at a time).
- **MP-7.2** No "Default" row — the instance default is not a shortlist entry.
- **MP-7.3** Saving is EXPLICIT (a Save button, disabled until the draft differs). Eager
  per-click saving would fire one toast per pick.
- **MP-7.4** The draft re-syncs when the saved list lands from the server; a landed fetch
  is not an edit, so Save stays disabled.

## MP-8 Settings changes reach every picker without a reload

- **MP-8.1** GIVEN a picker is open (or mounted) anywhere WHEN the preferred-model shortlist
  is saved in Settings THEN that picker's `Preferred` group reflects the new list without a
  page refresh. *Was:* `preferred` is a flag the backend stamps onto `GET /api/models`, and
  `useModels` caches that response at MODULE level — saving the shortlist refreshed only the
  Settings panel's own state, so the chat panel, dock, task-model rows and Automation editor
  all kept the old group until a full reload. `putPreferred` now dispatches
  `MODEL_CONFIG_CHANGED_EVENT`, which clears that cache and refetches.
- **MP-8.2** The same holds for adding, editing, enabling/disabling or defaulting a provider
  (those paths already announced themselves).

## Empty / loading / error states

- No providers configured → the list is empty and the search reports no matches; the
  "AI providers" panel above already says what to do about it.
- A model whose provider was since removed → the trigger still names it (`z-ai/glm-5.2`),
  never silently the first option in the list.
- A site marking a model unavailable (`isUnavailable`) renders it disabled and unselectable.

## Automated coverage

`frontend/src/components/ui/ModelPicker.test.tsx` (40) ·
`frontend/src/components/tabs/settings/sections/PreferredModels.test.tsx` (8) ·
`frontend/src/components/chat/lucius/ModelEffortRow.test.tsx` (8).

**Not yet discharged:** MP-1/-4/-5/-6 need `make qa-e2e` + screenshot evidence against the
RUNNING app. MP-5 and MP-6 in particular are geometry/stacking criteria that jsdom cannot
prove — MP-5.1 and MP-5.2 were both found by the owner in the browser AFTER the unit tests
were green.
