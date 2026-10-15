# Assets — Create/Edit Redesign: Wireframe + Build-Ready UI Spec

Read: `docs/prd/assets-create-edit-redesign.md`, current `frontend/src/components/tabs/assets/{Assets.tsx,modals.tsx,detail.tsx,register.tsx,assets.css,assetsData.ts,icons.tsx,api.ts}`, `frontend/src/types/assets.ts`, `frontend/src/components/tabs/accounts/AccountModal.tsx`, the `ui/` kit (`Modal`, `FormField`, `MoneyField`, `DateField`, `SegmentedControl`, `CollapsibleSection`, `ConfirmDialog`, `PageTabs`, `States`, `ActionMenu`, `Button`, `Text`, `Cluster`), `docs/FRONTEND_CONVENTIONS.md`, `docs/UI_QUALITY_GATE.md`, `docs/adr/2026-09-06-assets-tab-faithful-port.md`, and the QA screenshots/`facts.json` at `/tmp/onw-assets-qa/`. This spec folds in the four owner-side additions the coordinator sent mid-task (value-history Edit/Remove + "Edit purchase" link + rename to "Value history"; inline "Add a loan" in the loan picker and Ownership editor; policy-specific step‑4/Purchase copy; the asset‑page header's share‑aware Value/Net‑equity treatment) — all four are designed in place below, not bolted on.

---

## Verdict

**No new tab, no sub-tab, no `App.tsx` change.** This redesigns surfaces *inside* the existing `assets` tab (already wired in `TABS`/`PRIMARY_TABS`/`TAB_LABELS`/`TAB_SUBTITLES`/`TAB_ICONS` and the render switch) — the Add flow, the asset detail page's editing surfaces, and the register's composition rail. Nav label "Assets", page title "Assets", subtitle "Everything belongs to you" — unchanged.

---

## Conventions established once (apply everywhere below)

> **Owner override (2026-09-26, after seeing the build):** the wizard does NOT use a fixed-height
> frame — each step's dialog fits its content (width stays `size="md"`), because a fixed frame
> left short steps floating in an empty box. The Modal **title is the step's question** ("What is
> it?", "Name and value", "Is it all yours?", "How much did you pay?" / "How much have you paid in?"), the
> **subtitle is "{Category} · Step {n} of 4"** ("Step 1 of 4" on step 1), and a thin four-segment
> progress bar (`.as-wizard-progress`) sits at the top of the body. This supersedes conventions #3
> (title/subtitle) and #4 (fixed frame) below.

**1. Dialog title/subtitle pattern.** Every dialog in this feature uses `<Modal title subtitle>`: `title` = the action ("Update value", "Correct this value", "Edit details"), `subtitle` = the asset's name (or, in the wizard, the step's own question — see #3). One rule, no repetition needed per-dialog.

**2. Every date input is `<DateField>` (or `<TextField type="date">`, which is the same component — `TextField.tsx` delegates `type="date"` straight to `DateField`). Never a bespoke date input.** Root-cause note for the build agent: the WebKit screenshot (`webkit-iphone-add-form.png`) shows "Bought on" rendering full-width with WebKit's own grey today's-date skeleton where no value is set — this is a known WebKit `<input type=date>` rendering quirk (renders today's numerals in a lighter weight when empty, not a true blank), not a missing-wrapper bug (the wrapper is already correct — `.ui-datefield` already exists and already contains it). Do not try to "fix" this by hand-rolling a custom date widget. Do: (a) always set `max={localIso()}` (existing helper, `utils/format.ts`) on every "not in the future" date so the native picker itself can't go past today, and (b) treat this exact field as the **first thing verify-build screenshots at 390×844 WebKit** — if its rendered height still measurably differs from the Name field's, the fix is a height rule on `.ui-datefield input`, not a new component.

**3. The wizard is ONE frame, footer Back (not a title arrow).** AccountModal's title-arrow ("← What kind of account?") is for a *hierarchical drill into a full-screen picker and back* (one branch, one return). This flow is a genuine 4-step **linear** wizard with a persistent step indicator, which is the opposite shape — and it's already this app's own prior art: the *current* `AddAssetModal`'s `cat→form` transition already uses a **footer** Back button, not a title arrow. Decision: **footer Back** (ghost, bottom-left) on steps 2–4; step 1 has no Back (nothing precedes it). Title stays **"New {category}"** (stable across steps 2–4, so the identity doesn't flicker); step 1's title is **"What is it?"**. Progress + the step's question both live in the subtitle: **"Step {n} of 4 · {question}"** (step 1: "Step 1 of 4", no question suffix — it *is* the question).
   Primary buttons: steps 2–3 = **"Continue"**; step 4 = **"Add asset"**. **Enter-to-advance**: in any step-2/3/4 text/number/money field, `Enter` triggers the same action as the step's primary button (blocked by the same validation the button uses — an invalid Enter shows the inline error, exactly like clicking would).

**4. Fixed frame, never resizes.** Desktop: `<Modal size="md">` (520px, same width every step). Wrap the body in a new class **`.as-wizard`**: `height: min(56vh, 440px); overflow-y: auto; overflow-x: hidden; overscroll-behavior: contain;` — the exact pattern `accounts.css`'s `.acct-wizard` already uses (`height: min(62vh, 560px)`), scaled down because Assets' steps are lighter. A step's content that's short just leaves empty space at the bottom of the fixed frame; a step that's tall (step 3 with the loan mini-form open) scrolls *inside* the frame rather than growing the dialog. Phone: the sheet is already 92dvh (shrinks with the keyboard automatically); `.as-wizard` gets **no fixed height** there — `height: auto`, the sheet's own scroll is the safety net. The "no scrolling inside any step" acceptance bar was tested against **a watch** (no loan step at all), so it's satisfied by content budget (≤3 field-rows per step), not by CSS; the loan mini-form case (property/vehicle only) is allowed to scroll within its step on a 390px keyboard-up viewport — call this out explicitly to verify-build so it isn't mis-flagged as a regression.

**5. Discard confirmation.** Any close attempt (✕, Escape, backdrop) while the draft differs from its pristine defaults (name non-empty, value entered, share ≠ 100, share note, loan picked, cost/cost-date entered, or the "Create a category" field non-empty) opens `<ConfirmDialog>` — title **"Discard this asset?"**, body **"You'll lose what you've entered."**, confirmLabel **"Discard"** (danger), cancelLabel **"Keep editing"**. Step 1 alone (nothing picked yet) never triggers it.

**6. Money input — MoneyField already does 90% of this; extend it with two small, additive props.** `MoneyField` (`ui/MoneyField.tsx`) already supports a right-side `suffix` (absolutely-positioned decoration + reserved `padding-right`, used today by the Plan tab's `/month` fields) and free-form `formattedValue` (Gallery already shows `formattedValue={\`S$${...}\`}`, i.e. a symbol *can* already be baked into the string) — so a currency symbol is achievable today, but baking it into `formattedValue` means it vanishes while the field is focused (the raw-digit `draft` state has no symbol), and it can't sit on the *left* without also living inside the editable text. Two small extensions instead, mirroring the existing `suffix` mechanism exactly:
   - **`prefix?: string`** — a `.ui-money-field__prefix` span (mirrors `.ui-money-field__suffix`: absolute, `left: var(--space-5)`, faint, `pointer-events:none`), with `.ui-money-field .ui-input` unconditionally reserving `padding-left` the same way it already unconditionally reserves `padding-right` for `suffix`. Use: `prefix={getCurrencySymbol(baseCurrency)}` — **`getCurrencySymbol()` already exists** in `utils/format.ts` and already returns `"S$"` for SGD (the `UNAMBIGUOUS` map), falling back to the Intl symbol or the ISO code. No new lookup table needed.
   - **`allowEmpty?: boolean`** — widens `value` to `number | null` and `onValueChange` to `(value: number | null) => void`. When set and the trimmed draft is empty on blur/Enter, `commit()` calls `onValueChange(null)` instead of the current behavior (which marks the field invalid with the generic "Enter an amount of 0 or more."). This is the ONLY behavior change, and it's opt-in — the two existing callers (`Plan/outlook.tsx`'s two levers) don't pass it and are byte-for-byte unaffected.
   Every money field in this feature uses `prefix={getCurrencySymbol(baseCurrency)}` and `formattedValue={value == null ? '' : fNum(value)}` (`fNum` = `Math.round(n).toLocaleString(...)`, already in `utils/format.ts` — gives thousands separators, matches the whole-number convention every other Assets figure already uses via `fBaseWhole`). Fields that must never be a bare, accidentally-committed 0 (**Worth today / Cash value today, Bought for / Premiums paid, New value in both value dialogs, the inline loan's Amount owed**) use `allowEmpty`. Fields that always have a concrete starting number (**Sold for** — prefilled with the user's share of the current value; **Your share** — defaults to 50 the moment "Shared" is picked) do not.

   **Final fix round (2026-09-26, overrides any wording below):** MoneyField never commits text it cannot read — it keeps the text on screen, reports it through `onInvalidChange`, and every form refuses to save while any money field is unreadable. Amounts parse in the user's number format (`ui/amountText.ts`, the same locale `fNum` formats in); the field's OWN currency typed around the figure ("S$ 820,000", "sgd820000", "$" for a dollar, "820.000 S$") and k/m shorthand are read, while any other currency ("¥2,000,000", "EUR 14,800") is refused — never read as the field's; a thousands group never starts with 0; text typed after an Enter or Escape is committed by the next blur; a figure set from outside replaces stale unreadable text; a thousandfold-ambiguous entry ("17,000" under the dot format) is refused with **"Did you mean 17.000?"**; anything else unreadable shows **"Enter an amount of 0 or more."** under the field (outside the box the prefix centres on).
   "Your share" reuses the *same* `MoneyField`, with `suffix="%"` instead of a currency `prefix` — this is the in-field "%" suffix the PRD asks for, and it's free (same component, same mechanism, zero new code beyond the prefix extension above). Bounds (0–100) are step-level validation, not a MoneyField feature.
   **This is a `ui/` kit change** — add it to `Gallery.tsx`'s Money field entry in the same PR (a prefixed + an `allowEmpty` example) per `FRONTEND_CONVENTIONS.md`'s "changed a primitive → update the gallery" rule.

**7. Inline error / server message.** `api.ts`'s `request()` helper currently discards the server's actual reason (`throw new Error(\`Assets write failed (${res.status})\`)`) — this is *why* every dialog today can only ever say "That didn't save — try again." **Fix `request()` (and `deleteAsset`'s bespoke branch) to parse the JSON body on a non-ok response and throw `new Error(body.detail ?? generic)`.** Every dialog below then shows that message via a `role="alert"` block under its fields (AccountModal's own `saveErr` pattern, reused verbatim) — dialog stays open, nothing typed is lost, `saving` state disables the primary button and relabels it ("Adding…" / "Saving…").

**8. Focus on step change.** Steps are conditionally rendered (mount/unmount, not CSS-hidden) — so a plain HTML `autoFocus` on each step's first real control (Name on step 2, the active SegmentedControl segment on step 3, "Bought for" on step 4) fires naturally on mount. No custom ref/effect needed.

---

## A. Create flow — 4 steps, one frame

### Step 1 — "What is it?"

```
┌ Modal size=md ───────────────────────────────────────────┐
│ What is it?                                          [X] │
│ Step 1 of 4                                               │
├─────────────────────────────────────────────────────────┤
│  .as-wizard (fixed 440px, scrolls if needed)              │
│  ┌─────────┬─────────┬─────────┬─────────┐                │
│  │ Property│ Vehicle │ Watch & │  Art &  │  ← .as-pick    │
│  │  🏠     │  🚗     │ jewelry │ collect.│    (unchanged) │
│  ├─────────┼─────────┼─────────┼─────────┤                │
│  │Precious │Insurance│  Other  │ {custom}│                │
│  │ metals  │& endow. │         │  cats…  │                │
│  └─────────┴─────────┴─────────┴─────────┘                │
│                                                             │
│  + Create a category                    ← persistent,      │
│                                            NOT inside the   │
│                                            scroll region    │
│  ─────────────────────────────────────────                │
│  ✨ Or describe it to Lucius                                │
│     Lucius asks for anything missing and adds it           │
│     once you confirm.                                       │
└─────────────────────────────────────────────────────────┘
  (no footer — tapping a tile advances immediately)
```

- **Region: tile grid.** `ui/` none native — reuse existing `.as-pick` grid verbatim (unchanged visuals/behavior). Tapping a tile → `setCat(key); setStep('nameValue')`.
- **Region: "+ Create a category".** Reuse existing inline expand (`TextField` + "Create"/"Cancel" `Button`s, `Enter`-to-submit) **verbatim** — the one structural fix: this row (trigger + its expanded form) sits **outside** the `.as-wizard` scrollable region, as a fixed row between the tile grid and the Lucius link (new class **`.as-wizard-pinned`**, `flex: none`, always visible). This is the direct fix for the QA-observed bug (the inline field getting pushed under the iOS keyboard when the tile grid scrolls) — pinning it means it's never more than one screen-height from the top, and the phone sheet's own 92dvh already shrinks with the keyboard, so a pinned row near the top of a shrunk sheet stays visible.
- **Region: Lucius link.** New, quieter than today's full `.as-door` card — new class **`.as-step-lucius`** (a plain link row: small Spark icon + label, no border/background) + **`.as-step-lucius-cap`** (muted caption line beneath, `--text-xs`/`--faint`). Copy: link **"Or describe it to Lucius"**; caption **"Lucius asks for anything missing and adds it once you confirm."** (replaces the old "Lucius fills the form, you check it" — which the PRD flags as untrue). Click: `close(); onAskLucius('Add an asset: ')` — unchanged wiring.
- **Pre-seed rule** (unchanged from PRD): if a category chip is active on the register, "Add asset" opens straight at step 2 with that category; Back from step 2 returns here to change it.
- **Empty/loading/error:** none — this step reads only the already-loaded `categories` prop; category-create failure shows inline under the inline form (existing pattern, keep).
- **Phone (<640):** sheet, same structure; `.as-pick` already reflows to fewer columns per its existing mobile rule — unchanged.

### Step 2 — "Name and value"

```
┌ Modal size=md ───────────────────────────────────────────┐
│ New property                                         [X] │
│ Step 2 of 4 · Name and value                              │
├─────────────────────────────────────────────────────────┤
│  Name *                                                    │
│  ┌───────────────────────────────────────────────────┐   │
│  │ e.g. 88 Marina Rise                    (autoFocus) │   │
│  └───────────────────────────────────────────────────┘   │
│                                                             │
│  Worth today *                                              │
│  ┌───────────────────────────────────────────────────┐   │
│  │ S$ 14,800                                          │   │
│  └───────────────────────────────────────────────────┘   │
│  Your best estimate is fine                                │
├─────────────────────────────────────────────────────────┤
│ [Back]                                          [Continue] │
└─────────────────────────────────────────────────────────┘
```
Policy variant: label → **"Cash value today"**; help → **"The surrender value on your latest statement — not the sum assured."** (replaces "Your best estimate is fine" for this category only).

- **Region: Name.** `FormField label="Name" required` + `TextField` (`autoFocus`), placeholder = existing `namePlaceholder(category)` map. Error (on Continue/Enter with blank): **"Enter a name."**
- **Region: Worth today.** `FormField` + `MoneyField` per convention #6 (`prefix`, `allowEmpty`, `formattedValue=fNum(value)`). Errors: blank → **"Enter what it's worth today."**; present but ≤0 → **"Enter a value greater than 0."**
- **Phone:** identical, 2 fields — comfortably fits 390×844 with keyboard up (this is the step the "no scrolling" acceptance bar is proven against).
- **Empty/loading/error:** N/A (pure client validation); no server round-trip on this step.

### Step 3 — "Is it all yours?"

```
┌ Modal size=md ───────────────────────────────────────────┐
│ New property                                         [X] │
│ Step 3 of 4 · Is it all yours?                             │
├─────────────────────────────────────────────────────────┤
│  ┌───────────┬───────────┐                                │
│  │ All mine  │  Shared   │   ← SegmentedControl            │
│  └───────────┴───────────┘                                │
│                                                             │
│  Your share                    Shared with (optional)      │
│  ┌────────────┐                ┌───────────────────────┐  │
│  │   50   %   │                │ e.g. joint with Alex   │  │
│  └────────────┘                └───────────────────────┘  │
│  % — net worth counts your share only                      │
│                                                             │
│  Any loan against it?                                       │
│  ┌───────────────────────────────────────────────────┐   │
│  │ HDB Mortgage · S$310,000                        ▾  │   │
│  └───────────────────────────────────────────────────┘   │
│  + Add a loan                                               │
├─────────────────────────────────────────────────────────┤
│ [Back]                                          [Continue] │
└─────────────────────────────────────────────────────────┘
```

Only property / vehicle / custom (`key.startsWith('c_')`) categories show the loan block (new helper `canHaveLoan(category)` — mirrors the existing `canHaveStorageLocation` pattern, in the same relocated rules file, see file breakdown).

- **Region: ownership.** `SegmentedControl` options `"All mine"` / `"Shared"`. Picking "Shared" reveals **Your share** (`MoneyField suffix="%"`, defaults **50** the instant "Shared" is chosen — never blank, never 100) and **Shared with** (`TextField`, optional, placeholder "e.g. joint with Alex", → `share_note`). Error on Continue/Enter: share <0 or >100 → **"Enter a share from 0 to 100."**
- **Region: loan picker.** `FormField label="Any loan against it?"` + `Select`: first option **"No loan"**, then `"{name} · {fmt(balance)}"` per loan; a loan already securing another asset renders **disabled**, labeled `"{name} · {fmt(balance)} (secures {other asset name})"` — requires a new wire field on `AssetLoan`, e.g. `secures_asset_name: string | null` (flag to the API lane). **Zero loan accounts:** skip the `Select` entirely, show a one-line note **"No loan accounts yet."** directly above the same add-loan trigger.
- **Region: inline "Add a loan"** (owner addendum #2 — new, reused verbatim in the Ownership section editor on the asset page). Trigger: quiet link/button **"+ Add a loan"**, always present under the Select (or under the "No loan accounts yet." note). Expands a small inset block (new class `.as-loan-add`):
  ```
  ┌ Add a loan account ──────────────────────────────┐
  │ Loan name                                         │
  │ ┌───────────────────────────────────────────┐    │
  │ │ e.g. HDB Mortgage                          │    │
  │ └───────────────────────────────────────────┘    │
  │ Amount owed                                        │
  │ ┌───────────────────────────────────────────┐    │
  │ │ S$ 300,000                                 │    │
  │ └───────────────────────────────────────────┘    │
  │                              [Cancel] [Add loan]   │
  └───────────────────────────────────────────────────┘
  ```
  Fields: **"Loan name"** (`TextField`, error "Enter a name for this loan."), **"Amount owed"** (`MoneyField`, `prefix`+`allowEmpty`, error "Enter an amount greater than 0."). Button **"Add loan"** (primary, `size="sm"`, saving label **"Adding…"**), **"Cancel"** collapses back. Inline error (`role="alert"`) on failure, server message via convention #7. On success: collapses, the loan appears in the `Select`, **already selected** — same "session-local extra entry" technique the code already uses for `extraCat` in `AddAssetModal` (hold it in local state, append to the loans list for this session so it's usable immediately without waiting on a parent refetch).
  **While it is open** (ruling R13): a form holding anything not yet added — a changed name or amount, or an amount it can't read — holds the dialog's primary (wizard Continue and Enter on step 3, Add asset, the Ownership editor's Save changes). Nothing is sent; **"Add or cancel this loan first."** shows under the form (`role="alert"`) and keyboard focus moves to the form (`role="group"`, "New loan account"). An untouched open form closes as Cancel would and lets the primary go. Un-added edits are never applied.
  **Data:** `POST /api/accounts` with `{name, type: 'loan', currency: baseCurrency, initial_balance: -Math.abs(amount)}` (liabilities are raw-negative — same convention `AccountModal.tsx`'s `signedOpening` already documents). New thin wrapper `assets/api.ts#createLoanAccount(name, amount)` reads the wire (snake_case) response and maps to `AssetLoan = {id, name, balance: Math.abs(initial_balance), rate_label: '', term_label: '', secures_asset_name: null}`.
- **Phone:** the base step (no loan question — watch/art/metal/policy/other) is 3 field-rows, fits easily. Property/vehicle/custom **with the loan mini-form expanded** is the one place internal scroll is expected (see convention #4) — flag to verify-build, not a defect.
- **Empty/loading/error:** the loan `Select`'s "no accounts" state is itself an empty state (handled above); the mini-form's own saving/error states as specified.

### Step 4 — "How much did you pay?" (optional)

```
┌ Modal size=md ───────────────────────────────────────────┐
│ New property                                         [X] │
│ Step 4 of 4 · How much did you pay?                            │
├─────────────────────────────────────────────────────────┤
│  Optional — add what you paid to see your gain and mark   │
│  the purchase point on your value chart.                   │
│                                                             │
│  Bought for                    Bought on                   │
│  ┌────────────┐                ┌───────────────────────┐  │
│  │ S$ 9,000   │                │ mm / dd / yyyy         │  │
│  └────────────┘                └───────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│ [Back]                                          [Add asset]│
└─────────────────────────────────────────────────────────┘
```
Policy variant — subtitle **"Step 4 of 4 · How much have you paid in?"**; intro **"Optional. Shows whether the cash value is above what you've paid."** (owner-specified, verbatim, replaces the default intro entirely — do not show both); field labels **"Premiums paid so far"** / **"Policy started on"**.

- **Region: intro line.** Plain `Text size="sm" tone="muted"` — the single explanation of what this step unlocks (see convention above: field-level `help` text is dropped here to avoid repeating the same idea twice).
- **Region: price.** `MoneyField` (`prefix`, `allowEmpty`). Error if present & ≤0: **"Enter a value greater than 0, or leave it blank."**
- **Region: date.** `DateField`, `max={localIso()}`. Error if in the future: **"Pick a date that isn't in the future."** (same string reused by every "not future" date in this feature — see copy table).
- **Save:** primary **"Add asset"**, saving label **"Adding…"**, `disabled` while saving or while Name/Worth-today are invalid (guards against a user who somehow reached step 4 with step 2 broken via back-nav edits). On failure: inline `role="alert"` under the fields with the server's message (convention #7); dialog stays open, all 4 steps' state intact. On success: modal closes, toast **"{name} added"**, **the new asset's page opens**.
- **Phone:** 2 fields, fits without scrolling (the flow's own acceptance-tested case).
- **Empty/loading/error:** loading = saving state above; error = inline alert above; no separate empty state (nothing to be empty).

---

## B. Asset page

### Header

```
Assets / QA Test Condo
┌──────────────────────────────────────────────────────────────────┐
│ [🏠]  QA Test Condo                                          ⋯   │
│       (description, if any)                                       │
│                                                                     │
│  VALUE                    NET EQUITY              UNREALIZED       │
│  S$850,000                S$540,000               +S$230,000       │
│  valued Sep 2026          Whole asset · 64% of val +37.1% · 7.3yr  │
│  Your 50% · S$425,000                                               │
│  [Update value]                                                     │
└──────────────────────────────────────────────────────────────────┘
```

- **Region: title row (`.as-dhead`, unchanged structure).** Thumb (editable, unchanged) + name/description + **kebab (`ActionMenu`, reuse the primitive instead of the current hand-rolled `.acct-menu-wrap` popover — same visual slot)**, now **3 items only**: **"Rename or change category"**, **"Sell or dispose"**, **"Delete asset"**. This is *why* Update Value moves out cleanly: the original code's own comment explains the kebab was made to swallow every action specifically to keep it pinned beside the title on a narrow phone — moving Update Value into the **stats row** (a separate row, already full-width, already `flex-wrap`) doesn't reopen that constraint at all; the title row is untouched.
- **Owner decision (2026-09-28) — a register of many things.** A category with 4+ live assets (`STACK_AT`, `stacks.ts`) is ONE stacked card on the All view (`registerGrid.tsx` `StackCard`): its plural, "N items", its total (Σ `base_share_value`), its gain over the priced items (Σ `base_share_cost`), its three most valuable names "+N more", two card edges drawn below it (box-shadows); placed among the rest by base-currency value (`base_value`). Tapping it sets the category filter. **Owner, later the same day: "the grouping should only happen when we show All"** — any category view (from its stack or its chip) is its own cards, most valuable first, nothing above them (the owner found a summary line and a back button "not necessary" — the All chip and back are the way out); the compact list was removed. The category is URL-backed (`/assets?cat=<key>`, pushed on open, replaced on All) so the back gesture returns to All. The page payload's assets carry `base_value`, `base_share_value`, `base_share_cost` (`owned_assets_summary.base_figures`, the page's own rates).
- **Owner override (2026-09-28): one header layout, photo or not.** "The layout shouldn't change when there is no image. The image size should be the same." With no photo, the photo's place (192×144 beside the name on desktop, the full-width 16:10 cover on a phone) holds the category's icon on its tint with "Add a photo" (`.as-dphoto-empty`); tapping it picks one. The desktop media block and the figures beside it are always on.
- **Owner overrides (2026-09-28, later) — page width and header alignment.** The asset page reads at max 1120px, centred (`.as-dpage`); the register keeps its own width. Desktop with a photo is a media block (`.as-dhead-media`): the name, a two-line description (max 72ch, full text on hover) and the three figures stacked beside the photo from its top. On a phone the header row never wraps — the name wraps inside it and ⋯ stays beside its first line — and the figures and tabs sit on the same 16px edge as the name (the page body already pads them; their own margin doubled it).
- **Owner overrides (2026-09-28) — header, photo, figures, Lucius.** A photo is shown big: 192×144 (4:3) beside the name on desktop, the page's full-width 16:10 cover above the name on a phone; tapping it OPENS it (`PhotoViewer`: the photo at full size, Remove photo — asks first, its file is deleted, `DELETE /api/assets/{id}/photo` — Close, Change photo). With no photo the category icon still adds one (nothing to open). ⋯ gains "Add photo" / "Change photo". Value, Net equity and Unrealized are ONE size (`--text-2xl`, phone `--text-xl`) — mixed sizes read as messy; a zero gain is a plain "S$0", never a green "+S$0". Step 1's Lucius way in is a Lucius-tinted card (`.as-lucius-cta`): "Describe it to Lucius" / "Say it in your own words, like “my 2019 Rolex, bought for {symbol}9,000”. Lucius adds it after you confirm." — an example, not an abstraction.
- **Region: Value stat** (owner addendum #4). Unchanged kicker/value/caption (`Value` / `fmt(a.value)` / `valued {monthYear}`) **plus**, only when `share_pct < 100`, a new line **`.as-dstat-share`**: **"Your {share_pct}% · {fmt(share_value)}"**. `share_value` is a **new server field** on `OwnedAsset` (share-adjusted, base currency) — the owner explicitly wants this server-provided (so it's byte-identical to whatever the register's composition math uses, item C below); until the API lane ships it, the frontend may compute `a.value * a.share_pct / 100` client-side as an exact stopgap (an owned asset's `currency` is always base at create time, so no FX ambiguity) — but treat the server field as the target, not the client formula. Then, new **`.as-dstat-value-cta`**-spaced `<Button variant="secondary" size="sm">Update value</Button>` as the stat cell's last child. Net effect: the Value cell is taller than its two siblings when both the share line and the button are present — accept this (siblings stay bottom-aligned via the existing unchanged `align-items:flex-end` on `.as-dstats`; do **not** change that rule for the other two stats).
- **Region: Net equity stat.** Copy change only — caption becomes **"Whole asset · {pct}% of value"** (was "{pct}% of value") so it can't be misread as the user's own equity share when the asset is jointly owned. No structural change.
- **Empty/loading/error:** the page itself only renders once `open` (the asset) resolves — loading/error are the register's own states (`TabSkeleton variant="assets"` / `ErrorState`), unchanged, already correct.
- **Phone (<640):** `.as-dhead`/`.as-dstats` already reflow (existing media query: 20px title, wrapped stats, reduced font sizes) — the new share line + button just add height to the Value cell in the same wrapped row; no new mobile rule needed beyond what already exists.

### Chart + subtabs

Unchanged (`MarkChart`, `PageTabs`) except the **tab label**: **"Valuation history" → "Value history"** (owner addendum #1). "Attachments" unchanged.

### Value history

> **Owner override (2026-09-26, after seeing the build):** no per-row ✎/🗑 buttons and no "Edit
> purchase" link. Each row is itself the control (a chevron › on the right): a value row opens
> "Correct this value", whose footer carries **Remove value** (confirm; disabled on the last value
> with "An asset needs at least one value."); the purchase row opens the Purchase editor. The
> wireframes and file notes below that show row buttons are superseded.

Two actions per regular row now (owner addendum #1): **Edit** ("Correct this value") and **Remove** (confirm). The purchase-point row gets neither — a text link **"Edit purchase"** instead, opening the Purchase editor. **Remove is disabled** when the row is the only remaining regular (non-purchase) valuation — Edit stays available even then.

```
Desktop (.as-tbl, one column added):
┌────────┬───────────┬──────────────┬────────────┬─────┬─────────┐
│ DATE   │ VALUE     │ NOTE         │ CHANGE     │ BY  │         │
├────────┼───────────┼──────────────┼────────────┼─────┼─────────┤
│Sep 2026│ S$850,000 │ —            │ +S$230,000 │ you │ ✎  🗑   │
│Jun 2019│ S$620,000 │ entered on..*│ —          │ you │ Edit    │
│        │           │              │            │     │purchase │
└────────┴───────────┴──────────────┴────────────┴─────┴─────────┘
  * the Jun 2019 row is the purchase point (kind=purchase) — no ✎/🗑, "Edit purchase" instead
```

```
Phone (<640, new .as-history-list — replaces the squeezed 5-col table):
┌─────────────────────────────────────────────┐
│ Sep 2026                    S$850,000    ✎🗑│
│ —                            +S$230,000      │
├─────────────────────────────────────────────┤
│ Jun 2019                    S$620,000        │
│ entered on add          Edit purchase        │
└─────────────────────────────────────────────┘
```

- **Region: table/list.** New file `ValueHistory.tsx`. Desktop: existing `.as-tbl` + one new trailing column, new class **`.as-history-row-actions`** (a `Cluster gap="--space-2"` of two small icon buttons — reuse `.as-fact-x`'s circular chrome, content = `Icons.pencil`/`Icons.trash` instead of "×"). Phone: new **`.as-history-list`**/**`.as-history-list-row`** — a genuine 2-line list (date+value on line 1, note+change on line 2), actions as a small icon cluster pinned to the row's right edge, vertically centered across both lines. `by` is dropped from the phone layout entirely when `'you'` (the default), shown as a trailing "· automation" only when `by === 'automation'` (rare, worth the one word, not worth a whole column).
- **Region: row actions.** Edit → opens **"Correct this value"** (see below), prefilled with that row's own value/date/note. Remove → `ConfirmDialog`: title **"Remove this value?"**, body **"This removes the {monthYear} entry — {fmt(value)}. This can't be undone."**, confirmLabel **"Remove"**, danger. Purchase row → **"Edit purchase"** text link opens the Purchase section editor (below), not a value dialog.
- **Data:** reads `a.marks` (already on the asset). "Remove" needs the already-PRD-scoped remove-mark endpoint (`DELETE /api/assets/{id}/marks/{mark_id}` — "Remove-a-valuation joins the API," per the PRD's own Data/API section — new `assets/api.ts#removeMark(id, markId)`). "Correct this value" needs one of: (preferred) a real `PATCH /api/assets/{id}/marks/{mark_id}` editing that mark's value/date/note atomically, or (minimal fallback, no new endpoint) client-composed as `removeMark` + `addMark({value, date: <same date>, note})` using the mark endpoints already planned — flag this exact choice to the senior-engineer/API lane; note the fallback is not atomic (a failed re-add after a successful remove would leave the row missing) so the PATCH is the safer target.
- **Empty:** never empty (an asset always has ≥1 mark). **Loading/error:** inherits the page's own (asset already loaded by the time this renders).

### Update value / Correct this value (two dialogs, same shape)

```
┌ Modal size=sm ─────────────────────────────┐
│ Update value                          [X]  │
│ QA Test Condo                               │
├─────────────────────────────────────────────┤
│ Current value: S$850,000 · valued Sep 2026  │  ← plain text, not an input
│                                               │
│ New value                                    │
│ ┌─────────────────────────────────────────┐ │
│ │ S$                                       │ │  ← blank, not prefilled
│ └─────────────────────────────────────────┘ │
│ As of                                        │
│ ┌─────────────────────────────────────────┐ │
│ │ 2026-09-26                       (today) │ │
│ └─────────────────────────────────────────┘ │
│ Note (optional)                              │
│ ┌─────────────────────────────────────────┐ │
│ │ e.g. Chrono24 median                     │ │
│ └─────────────────────────────────────────┘ │
│ 🕐 This is saved as a new dated value — the  │
│    old one stays in history.                 │
├───────────────────────────────────────────────┤
│                              [Cancel] [Update value] │
└─────────────────────────────────────────────┘
```

- **Update value** (header button): title **"Update value"**, subtitle = asset name. Reference line (plain text, not a second input — this REPLACES the old `.as-qmove`/arrow two-input layout, which is exactly the "looks like an editable box" pattern the PRD calls out): **"Current value: {fmt(a.value)} · valued {monthYear(a.valued_on)}"**. Fields: **New value** (`MoneyField`, `allowEmpty`, blank, not prefilled), **As of** (`DateField`, default **today**, `max={localIso()}`), **Note** (optional `TextField`). Hint line rewritten to drop the word "mark": **"This is saved as a new dated value — the old one stays in history."** Button **"Update value"**. Toast **"Value updated."** The note placeholder follows the category (`assetCategoryRules.ts`): property "e.g. Bank valuation", vehicle "e.g. Dealer quote", watch "e.g. Chrono24 median", art "e.g. Auction estimate", metal "e.g. Spot price", policy "e.g. Annual statement", anything else "e.g. Where the figure came from". On a shared asset (share < 100%) the value field's help reads **"The whole asset's value — net worth counts your {share}%."** in both value dialogs.
- **Correct this value** (row-level Edit, owner addendum #1): same component, `mode="correct"` — title **"Correct this value"**, subtitle = asset name, all three fields **prefilled** from the row being corrected (value/date/note), no reference line (there's nothing to compare against — you're fixing *this* row, not adding a new one). Button **"Save correction"**. Toast **"Value corrected."**
- **File:** one `ValueDialogs.tsx` — a shared internal form + two thin exports (`UpdateValueDialog`, `CorrectValueDialog`) differing only in title/prefill/submit target/button/toast.
- **Validation:** blank/≤0 value → **"Enter a value greater than 0."**; future date → **"Pick a date that isn't in the future."** Saving/error per convention #7.
- **Phone:** same 3 fields, `size="sm"` sheet, no scrolling concern.

### Sections rail (Ownership / Purchase / Details / Notes)

**Owner override (2026-09-27): the sections are part of the page, not a side rail.** The asset page is one column — the value and its chart, then the four cards as a two-up grid (`.as-dsections`; cards in a row share its height; stacked on a phone), then Value history / Attachments; the Added caption closes the grid. The fixed 300px sticky rail wrapped a real record's long values (a loan name, a payment plan) and hid its last card while a long history scrolled. The register keeps its own composition rail. Wherever this section says "the rail" below, read "the sections grid".

**Placement decision:** stays the **right rail on desktop** (`aside.as-rail`, unchanged grid: `.as-body { grid-template-columns: minmax(0,1fr) 300px }`, collapses to a single column under the main content below 1180px per the existing rule), because that's already this page's established hierarchy (chart+history is the primary reading column; supporting facts sit beside it) and every rail card already has this exact "labelled block + Edit" shape today (`Secured by`, `Facts`, `Notes`) — this redesign relabels and completes that shape rather than inventing a new one. Order top-to-bottom, per the PRD's own listing: **Ownership → Purchase → Details → Notes**, each a `.as-rcard` with a new **`.as-rcard-head`** (title + small "Edit"/"Add" `Button`, `justify-content: space-between`).

```
┌ Ownership ──────────────────── Edit ┐   ┌ Purchase ─────────────────── Edit ┐
│ Your share: 50% — joint with A. Lim │   │ Bought for   S$620,000            │
│                                       │   │ Bought on    Jun 2019             │
│ Secured by HDB Mortgage               │   └───────────────────────────────┘
│ S$310,000 · 3.1% · to 2044            │   (empty:)
│ [====equity====][==debt==]            │   ┌ Purchase ─────────────────── Add ┐
│ 64% equity · LTV 36%                  │   │ Add what you paid to see your    │
│ The loan is an account. This asset    │   │ gain.                             │
│ points at it; equity is derived.      │   └───────────────────────────────┘
└───────────────────────────────────────┘

┌ Details ───────────────────── Edit ┐   ┌ Notes ─────────────────────── Edit ┐
│ Address or unit   #14-02, 88...    │   │ Anything worth remembering — the    │
│ Tenure            Freehold          │   │ receipt is in the safe deposit box. │
│ Insured           No                │   └───────────────────────────────────┘
│ (Floor area, Tenanted not set)      │   (empty:)
│ + Add floor area, tenanted          │   ┌ Notes ─────────────────────── Add ┐
└─────────────────────────────────────┘   │ Add a note                        │
                                            └───────────────────────────────────┘
              Added Sep 2026   ← plain caption, no card, bottom of rail
```

- **Ownership card.** Content: "Your share" line (`shareOf(a)`, existing helper, unchanged format "100%" / "60% — joint with A. Lim") + the existing "Secured by"/equity-split-bar block **moved here from its own separate card** (today it's a standalone "Secured by" `.as-rcard`; this redesign folds it under Ownership since PRD explicitly groups "your share + who with and the linked loan" as one concept). Empty (no loan): existing copy **"Nothing — owned outright. Link a loan account if that changes."**. **Edit** opens the **Ownership editor** — a `size="sm"` `Modal` (title "Ownership", subtitle = asset name) wrapping the **exact same `OwnershipStep` component** used at wizard step 3 (SegmentedControl + share + shared-with + loan Select + inline "+ Add a loan" — the owner's addendum #2 explicitly calls for this reuse), footer **Cancel / "Save changes"**. Toast **"Ownership updated."**
- **Purchase card.** Title **"Purchase"** (default) / **"Premiums paid"** (policy — owner addendum #3). Filled: **"Bought for {fmt(cost)}"** + **"Bought on {monthYear(cost_date)}"** (default) or **"Premiums paid so far {fmt(cost)}"** + **"Policy started on {monthYear}"** (policy). Empty: **"Add what you paid to see your gain."** (default) / **"Add your premiums to see if the cash value is ahead."** (policy). **Edit/Add** opens a `size="sm"` Modal (title = the card's own title, subtitle = asset name) wrapping the **exact same `PurchaseStep` component** used at wizard step 4. Toast **"Purchase updated."**
- **Details card.** Guided fields per category (`CATEGORY_FACTS`, unchanged mapping) shown as **labelled rows** (never the old anonymous "Facts" list), plus **Insured** (Yes/No — hidden entirely for `category === 'policy'`, where a generic "is this insured" question doesn't apply; new helper `showsInsured(category)`), plus **Matures on** (policy only, from `maturity_date`), plus **Where it's kept** (portable categories only, via the existing `canHaveStorageLocation`), plus an **"Other details"** sub-list of free-form label/value rows. Three fill states: (1) some guided fields filled → show those as rows, and append one small ghost row **"+ Add {names of the still-unset guided fields, comma-joined}"** (e.g. "+ Add mileage") so the rest stay discoverable without opening the editor blind — new class `.as-details-add-more`; (2) nothing at all filled → replace the whole card body with `<EmptyState title="Nothing added yet" body="Add {guided field names for this category}" action={<Button>Add details</Button>}>` (reuse the kit primitive, don't hand-roll); (3) fully filled → plain rows, no ghost prompt. **Edit/Add details** opens the **Details editor** — `size="lg"` Modal (guided fields need 2-column room), title "Edit details", subtitle = asset name: guided `FormField`s first (each with its category placeholder, e.g. "SLA 4417 X"), then an **"Other details"** heading + the existing free-form label/value row editor (`TextField` pairs + circular `.as-fact-x` remove + "+ Add a detail" `Button` — ported from the current `EditAssetModal`'s Facts editor verbatim). Footer Cancel / "Save changes". Toast **"Details saved."**
- **Notes card.** Always renders now (today it's hidden entirely when empty — PRD wants an empty state, not nothing). Filled: the note text (unchanged styling). Empty: **"Add a note"** (link/button). **Edit/Add** opens a tiny `size="sm"` Modal (title "Notes", subtitle = asset name) — one `TextArea` + Cancel/"Save". Toast **"Note saved."**
- **"Rename or change category"** (kebab item, replaces "Edit details"): `size="md"` Modal, title "Rename or change category", subtitle = asset name. Fields: **Name** (required), **Short description** (help: "The line under the name in the register"), **Category** (`Select`). Changing category shows the existing hint (kept, reworded slightly for plain-words consistency): **"Moving this to {new category} changes the suggested details. Its values and documents stay with it."** Button "Save changes". Toast **"Name and category saved."**
- **Added caption — owner override (2026-09-28): removed.** Under the sections grid it read as a stray line "out of nowhere"; the purchase date and the value history carry the dates that matter, and `created_at` stays stored. Original decision, kept for the record: `created_at` gets exactly one, unobtrusive home: a plain `<Text size="xs" tone="faint">Added {monthYear(created_at)}</Text>` (new `.as-added-caption`, no card chrome) as the **last thing in the rail**, below the Notes card. Not a "fact," not editable, not implying it's part of the record — resolves the PRD's "Added: Sep 2026 stored as a user fact" bug by giving it a real, permanent, non-editable home instead of deleting the information outright.
- **Phone (<640):** rail already stacks to a single column below the main content (existing `.as-rail { grid-template-columns: minmax(0,1fr) }` mobile rule) — every card above just stacks in the same order; no new mobile CSS needed beyond what the cards' own internals require (all are simple label/value rows).

### Attachments

```
┌ Attachments ─────────────────────────── [Add file] ┐
│ FILE                          ADDED    LUCIUS  🗑   │
│ 📄 statement.pdf              Sep 2026  [x]    🗑   │
└──────────────────────────────────────────────────────┘
empty:
┌───────────────────────────────────────────────────┐
│  No attachments yet                                 │
│  Keep its documents here — the purchase agreement,   │
│  title deed, receipts and photos.   (per category)   │
│                      [Add file]                       │
└───────────────────────────────────────────────────┘
```
- **Owner override (2026-09-28): attachments open, rename and drop.** A row (or its name) opens `AttachmentViewer` — Documents' own viewers for the same `asset-<ref>` item: an image as itself, a PDF page by page (`DocumentPages`), a spreadsheet as a table (`TablePreview`), else "No preview" — with Download. Images show a thumbnail in the row. Each row's lone trash icon became a ⋯ menu: **Rename** ("Rename file", the whole name selected; blank refused; the doc's display name shared with Documents — the file keeps its own) and **Remove**. Files dropped anywhere on the asset page attach to it (`useFileDrop` + one shared `useDocUpload`; the page switches to Attachments; "Drop to attach to {name}" while hovering; several files at once, a refused one never stops the rest); "or drop files on this page" shows only with a mouse. On a phone the table is a list — one row per file, its date and Lucius status under the name, Rename / Lucius / Remove in its ⋯ menu. The asset API's docs carry `filename`, `media_type` and `size_bytes`.
- New file `Attachments.tsx`. Header action **"Add file"** `Button` (secondary, `size="sm"`) triggering a hidden `<input type=file>` (same label-wraps-hidden-input pattern `Thumb`'s photo picker already uses in `register.tsx`) → `api.uploadDoc` (**already exists**, just never wired to a control — pure frontend gap). Per-row **Remove** icon-button (reuse `.as-fact-x` chrome, `Icons.trash`) → `ConfirmDialog` (title "Remove this file?", body "{name} will be removed from this asset.", confirmLabel "Remove", danger) → `api.removeDoc` (**already exists**, same gap). "Lucius can read" `Switch` — unchanged. Empty state: `<EmptyState title="No attachments yet" body={attachmentsHint(a.category)} action={<Button>Add file</Button>}> (owner override 2026-09-28 — was "…so a value shows what it's based on.")` — copy reworded to drop "mark" per convention. Toasts: **"File added."** / **"File removed."**
- **Phone:** the existing 3-column table (File/Added/Switch) already fits at 390px reasonably (unlike the 5-column history table) — leave its shape alone, just add the header button + remove icon; no new mobile list needed here.

### Sell or dispose

```
┌ Modal size=sm ─────────────────────────────┐
│ Sell or dispose                       [X]  │
│ QA Test Condo                               │
├─────────────────────────────────────────────┤
│ Sold for            Sold on                  │
│ ┌─────────────┐    ┌───────────────────┐    │
│ │ S$850,000   │    │ 2026-09-26 (today)│    │
│ └─────────────┘    └───────────────────┘    │
│ Proceeds into                                │
│ ┌─────────────────────────────────────────┐ │
│ │ HDB Savings · S$42,000                   │ │
│ └─────────────────────────────────────────┘ │
│ ✓ Realized gain +S$230,000 against ...       │
│ 📄 The asset is archived, not deleted. ...    │
├─────────────────────────────────────────────┤
│                        [Cancel] [Record disposal] │
└─────────────────────────────────────────────┘
```
Adds **"Sold on"** (`DateField`, default today, `max={localIso()}`) beside "Sold for" — `DisposeBody.date` already exists on the wire (`api.ts`), pure frontend wiring gap. Inline `role="alert"` error block added below the fields, server message via convention #7 (today a failed dispose only shows the generic toast). Everything else (realized-gain hint, archived-note hint) unchanged. **Final fix round (2026-09-26):** the prefill is the user's share of the current value (`share_value` from the server), never the whole value — the proceeds post verbatim into cash, so a whole-value prefill on a 50% asset put the co-owner's half into net worth. Below 100% the field is labelled **"Your share of the sale"** with help **"Your {share}% — what lands in the account below."**, and the gain hint reads "…against your {share}% of the purchase price, {cost × share}."; at 100% both are unchanged. With no base-currency cash account, **"Pick a cash account in {base}."** shows beside Proceeds into.

### Delete confirmation

Title **"Delete this asset?"**, body: **"Delete {name} and its history? This can't be undone. If you sold it, use Sell or dispose instead — that keeps its history."** (folds in the one useful sentence from the old essay, per PRD). confirmLabel "Delete", danger.

---

## C. Register

- **Composition rail — server percentages (sum to 100).** `Rail`'s current per-category rows are 100%-client-computed (`(r.v/summary.own)*100`) over **unshared** `a.value` — this is the exact source of the "Property 151%" bug (a 50%-owned property counted at its full value). Fix: a **new server field**, e.g. `summary.composition: AssetSummaryRow[]` (`{key, label, value, pct}`, values share-adjusted + base-converted, `pct` summing to 100 across the array) — flag to the backend/RFC lane. `Rail` **stops computing `pct` client-side** for the owned-asset category rows and renders `row.pct` directly; `row.value` still formats via `fmt()`. **Debt Composition stays exactly as-is** (client-computed) — liability accounts have no `share_pct`, so they're unaffected by this bug and don't need the change.
- **A jointly-owned asset's card shows its share.** `AssetCard`'s existing `.as-card-sub` line (today just the category label, e.g. "Property") appends the share when `share_pct < 100`: **"Property · Your 50%"** — reuses the identity row's existing dot-join convention (`assetSublabel` already dot-joins `category`/`valued_on` elsewhere), no new row, no layout change, and it's the highest-visibility spot for "does this card's value belong entirely to me" since it sits right next to the name.

---

## D. Responsive matrix

| Width | Wizard | Detail page | Value history | Chat open |
|---|---|---|---|---|
| **390 (<640)** | Bottom sheet, `.as-wizard` height:auto, content-budgeted to fit with keyboard up (base flow); loan mini-form step may scroll internally | Rail stacks under main column (existing rule); header stats wrap (existing rule) + new share line/button add height only | `.as-history-list` 2-line rows | N/A on phone (chat is a separate tab) |
| **1024 (tablet)** | Unchanged from desktop (dialog width is fixed regardless of viewport) | `.as-body` still 2-column until 1180px, then stacks (existing rule — unaffected by this work) | Table (5+action cols) | Modal already clears `--chat-width`; verify the dialog still centers in the visible (non-chat) region |
| **1440 (desktop)** | `size="md"` centered dialog | Full 2-column layout as wireframed above | Table | Same clearance check, chat panel open AND closed |

No new `position:fixed`/overlay elements are introduced by this feature (every new surface is a `<Modal>` or lives inside the existing page flow), so the chat-panel clearance rule (`FRONTEND_CONVENTIONS.md`'s "#1 thing new UI forgets") is inherited for free from `Modal` — verify-build should still screenshot chat open+closed per the standard checklist, but there is no new risk surface here beyond what `Modal` already handles.

---

## E. Accessibility

- Dialog titles/subtitles: automatic via `Modal`'s `Dialog.Title`/`Dialog.Description` wiring — just pass `title`/`subtitle` (convention #1).
- Focus on step change: native `autoFocus` on each step's first control (convention #8) — no custom code.
- Error announcement: every inline field error goes through `FormField error=` (already renders `role="alert"`); every dialog-level save error is a `<div role="alert">` (AccountModal's pattern, reused verbatim).
- Keyboard: `Enter`-to-advance per convention #3; `Escape`/backdrop routed through the discard-confirm gate (convention #5) rather than closing silently; the loan `Select` and category `Select` are native `<select>` elements (full keyboard support for free); the value-history row actions are real `<button>`s with `aria-label`s ("Correct this value", "Remove this value" — the latter additionally `aria-disabled` + a `title` of "Keep at least one value" when it's the last regular row).

---

## File / component breakdown

`frontend/src/components/tabs/assets/` — the current `modals.tsx` (1047 lines) and `detail.tsx` (788 lines) already exceed the ~400-line guideline; this redesign is the moment to split both, not add to them.

| File | Purpose | Approx. lines |
|---|---|---|
| `AssetWizard.tsx` | Create-flow shell: step state machine, discard-confirm, save, replaces `AddAssetModal` in `modals.tsx` | ~280 |
| `CategoryStep.tsx` | Step 1 body (tiles, create-category, Lucius link) | ~150 |
| `NameValueStep.tsx` | Step 2 body | ~90 |
| `OwnershipStep.tsx` | Step 3 body — **reused** by the Ownership section editor | ~220 |
| `InlineAddLoan.tsx` | The "+ Add a loan" mini-form, child of `OwnershipStep` | ~110 |
| `PurchaseStep.tsx` | Step 4 body — **reused** by the Purchase section editor | ~130 |
| `ValueDialogs.tsx` | `UpdateValueDialog` + `CorrectValueDialog` (shared inner form) | ~180 |
| `DetailsEditor.tsx` | Guided-fields-first + "Other details" editor | ~180 |
| `RenameCategoryEditor.tsx` | Name / description / category editor | ~90 |
| `sections.tsx` | Rail cards (Ownership, Purchase, Details, Notes) + Notes inline editor + "Added" caption | ~260 |
| `ValueHistory.tsx` | Table (desktop) / list (phone) + row actions + remove-confirm | ~200 |
| `Attachments.tsx` | Add-file + per-row remove + empty state | ~140 |
| `modals.tsx` | Trimmed to `DisposeModal` only (+ Sold-on, inline error) | ~140 |
| `detail.tsx` | Trimmed to the page shell: header, chart, subtabs, composes the files above | ~340 |
| `Assets.tsx` | Shell — wires the new dialogs/handlers, surfaces server errors instead of only toasting | ~370 |
| `assetsData.ts` | Unchanged numeric/display helpers, **plus** `regularMarks`/`purchaseMark` split and `canRemoveMark` | ~300 |
| `assetCategoryRules.ts` (**new**) | Relocated from `modals.tsx`: `CATEGORY_FACTS`, `NAME_PLACEHOLDERS`, `canHaveStorageLocation`, `canHaveLoan` (new), `showsInsured` (new), `sanitizeFacts`, purchase-label-per-category helpers | ~150 |
| `api.ts` | Fix `request()`/`deleteAsset` to surface `body.detail`; add `removeMark`, `createLoanAccount`; (backend permitting) `patchMark` | ~160 |

`frontend/src/components/ui/MoneyField.tsx` — add `prefix`/`allowEmpty` (convention #6). `frontend/src/components/ui/Gallery.tsx` — add the new prop examples. `frontend/src/index.css` — the one new kit class, `.ui-money-field__prefix`.

**Build order:** (1) `MoneyField` prefix/allowEmpty + `.ui-money-field__prefix` + Gallery entry, since every later step depends on it; (2) `api.ts`'s error-surfacing fix, since every dialog's inline-error requirement depends on it, plus the new `removeMark`/`createLoanAccount` (and `patchMark` if the backend lane ships it in the same window); (3) `assetCategoryRules.ts` extracted from the current `modals.tsx` (pure relocation, no behavior change, de-risks everything after); (4) the wizard (`AssetWizard.tsx` + its 4 step files + `InlineAddLoan.tsx`), wired into `Assets.tsx` in place of `AddAssetModal`; (5) the detail-page split (`sections.tsx`, `ValueHistory.tsx`, `Attachments.tsx`, trimmed `detail.tsx`) plus the two new editors (`DetailsEditor.tsx`, `RenameCategoryEditor.tsx`) and `ValueDialogs.tsx`, reusing `OwnershipStep`/`PurchaseStep` from step (4) — this is the biggest reuse payoff, so it should land after the step components exist, not before; (6) `register.tsx`'s composition-rail consumption change + `AssetCard`'s share badge (item C), gated on the backend's `summary.composition`/`share_value` fields landing — until then, ship behind the client-side stopgap formula noted above so the UI isn't blocked on the backend lane; (7) trim `modals.tsx` to `DisposeModal` + add "Sold on"/inline error last, since it's the most isolated change.

---

## New CSS classes (add to `assets.css` — the tab's one registered CSS file; no new file)

`.as-wizard`, `.as-wizard-pinned`, `.as-step-lucius`, `.as-step-lucius-cap`, `.as-loan-add`, `.as-loan-add-row`, `.as-dstat-share`, `.as-dstat-value-cta`, `.as-rcard-head`, `.as-history-row-actions`, `.as-history-list`, `.as-history-list-row`, `.as-details-add-more`, `.as-added-caption`. Reused as-is, no new class: `.as-fact-x` (value-history remove, attachments remove, other-details remove — one circular-affordance chrome for all three), `.as-note`, `.as-hint`, `.as-rcard`, `.as-pick`, `.as-newcat*`. Kit-level new class: `.ui-money-field__prefix` (`frontend/src/index.css`).

---

## Full copy table

| Region | Copy |
|---|---|
| Step 1 title / subtitle | "What is it?" / "Step 1 of 4" |
| Step 1 Lucius link / caption | "Or describe it to Lucius" / "Lucius asks for anything missing and adds it once you confirm." |
| Step 2 subtitle | "Step 2 of 4 · Name and value" |
| Step 2 Name error | "Enter a name." |
| Step 2 Worth-today label (default / policy) | "Worth today" / "Cash value today" |
| Step 2 Worth-today help (default / policy) | "Your best estimate is fine" / "The surrender value on your latest statement — not the sum assured." |
| Step 2 value errors (blank / ≤0) | "Enter what it's worth today." / "Enter a value greater than 0." |
| Step 3 subtitle | "Step 3 of 4 · Is it all yours?" |
| Step 3 segmented options | "All mine" / "Shared" |
| Step 3 share help | "% — net worth counts your share only" |
| Step 3 share error | "Enter a share from 0 to 100." |
| Step 3 shared-with placeholder | "e.g. joint with Alex" |
| Step 3 loan label | "Any loan against it?" |
| Step 3 loan empty note | "No loan accounts yet." |
| Step 3 add-loan trigger | "+ Add a loan" |
| Step 3 add-loan fields | "Loan name" / "Amount owed" |
| Step 3 add-loan errors | "Enter a name for this loan." / "Enter an amount greater than 0." |
| Step 3 add-loan buttons / saving | "Add loan" / "Cancel" / "Adding…" |
| Step 4 subtitle (default / policy) | "Step 4 of 4 · How much did you pay?" / "Step 4 of 4 · How much have you paid in?" |
| Step 4 intro (default / policy) | "Optional — add what you paid to see your gain and mark the purchase point on your value chart." / "Optional. Shows whether the cash value is above what you've paid." |
| Step 4 labels (default / policy) | "Bought for" / "Bought on" · "Premiums paid so far" / "Policy started on" |
| Step 4 price error | "Enter a value greater than 0, or leave it blank." |
| Any future-date error | "Pick a date that isn't in the future." |
| Step 4 save button / saving | "Add asset" / "Adding…" |
| Discard confirm | "Discard this asset?" / "You'll lose what you've entered." / "Discard" / "Keep editing" |
| Create success toast | "{name} added" |
| Create failure fallback | "We couldn't add this asset. Check the details and try again." |
| Header Update-value button | "Update value" |
| Value stat share line | "Your {pct}% · {amount}" |
| Net-equity caption | "Whole asset · {pct}% of value" |
| Subtab rename | "Value history" |
| History row-remove confirm | "Remove this value?" / "This removes the {month} entry — {amount}. This can't be undone." / "Remove" |
| Purchase-row link | "Edit purchase" |
| Update-value dialog | "Update value" / "Current value: {amount} · valued {month}" / "This is saved as a new dated value — the old one stays in history." / "Update value" / toast "Value updated." |
| Correct-value dialog | "Correct this value" / "Save correction" / toast "Value corrected." |
| Value-removed toast | "Value removed." |
| Ownership card empty | "Nothing — owned outright. Link a loan account if that changes." |
| Purchase card empty (default / policy) | "Add what you paid to see your gain." / "Add your premiums to see if the cash value is ahead." |
| Details empty | "Nothing added yet" / "Add {guided field names}" / "Add details" |
| Details partial-fill prompt | "+ Add {unset field names}" |
| Notes empty | "Add a note" |
| Added caption | (removed 2026-09-28) |
| Rename/category editor | "Rename or change category" / "Save changes" / category-change hint: "Moving this to {category} changes the suggested details. Marks and documents stay with it." |
| Attachments header button | "Add file" |
| Documents tab empty | "No documents yet" (renamed 2026-09-29 with the tab) / `attachmentsHint(category)`: "Keep its documents here — {the paperwork that category comes with}." (owner 2026-09-28: attachments are the asset's documents, not proof of a price) |
| Attachments remove confirm | "Remove this file?" / "{name} will be removed from this asset." / "Remove" |
| Attachments toasts | "File added." / "File removed." |
| Dispose new field | "Sold on" |
| Delete confirm | "Delete this asset?" / "Delete {name} and its history? This can't be undone. If you sold it, use Sell or dispose instead — that keeps its history." / "Delete" |
| Ownership/Purchase/Details/Notes editor toasts | "Ownership updated." / "Purchase updated." / "Details saved." / "Note saved." |
| Kebab items | "Rename or change category" · "Sell or dispose" · "Delete asset" |

---

## What NOT to do

- Do not hand-roll a second money input with a separate read-only currency `TextField` beside it — that box-that-looks-editable is the exact bug being fixed; use `MoneyField` with `prefix`.
- Do not use a raw `<input type=date>` or re-derive date containment — `DateField`/`TextField[type=date]` already exists and already handles iOS containment; the remaining WebKit quirk is a rendering nuance to verify, not a reason to hand-roll a replacement.
- Do not add a second page heading — the tab's `PageHeader` (title "Assets") is untouched; every dialog title above is a *dialog* title, not a page heading.
- Do not resize the wizard dialog between steps — same `size="md"`, same fixed `.as-wizard` height, every step.
- Do not put "Update value" back in the kebab, and do not add a second header row to fit it. **Owner override (2026-09-28):** on a phone it is one full-width button under all the figures, not inside the Value stat cell — there it read as part of that one number.
- Do not create a new `AssetLoan`-picker component for the Ownership editor — it's the literal same `OwnershipStep` component the wizard uses, not a lookalike copy.
- Do not build a bespoke empty-state box for Details/Attachments — use `ui/States.tsx`'s `EmptyState`.
- Do not invent a new circular remove-button style for Attachments/Value-history/Other-details — reuse `.as-fact-x`.
- Do not keep computing the composition rail's percentages client-side once `summary.composition` exists — that recomputation *is* the 151% bug.
- No raw px/hex in any new CSS — every new class above uses `--space-*`/`--text-*`/color tokens only, matching the rest of `assets.css`.

---

## Verify-build checklist (region by region)

1. **MoneyField** — `prefix`/`allowEmpty` present, Gallery updated, existing Plan-tab levers pixel-unchanged (regression check).
2. **Step 1** — tiles unchanged; "+ Create a category" visibly stays on-screen with the iOS keyboard up (WebKit 390×844, screenshot the keyboard-open state); Lucius link uses the new quiet style, not `.as-door`.
3. **Steps 2–4** — same dialog width/height across all 4 (screenshot each, diff the frame bounding box); footer Back present on 2–4, absent on 1; Enter-to-advance works in every text/money field; every validation string matches the copy table exactly; "Bought on"/DateField height matches sibling inputs at 390×844 WebKit specifically.
4. **Step 3 loan** — disabled option shows the other asset's name; zero-loan note + add-loan trigger both present; inline add-loan appears selected immediately after "Add loan" with no page refetch.
5. **Discard confirm** — fires on ✕/Escape/backdrop with any input present; does not fire on step 1 alone.
6. **Header** — kebab has exactly 4 items (Rename or change category · Add/Change photo · Sell or dispose · Delete asset — owner 2026-09-28: the photo's tap now opens it); visible Update value — beside the kebab on desktop, one full-width button under the figures at 390px; share line appears only when `share_pct < 100` and matches the register card's own percentage.
7. **Value history** — rename to "Value history" landed; Edit/Remove present on regular rows, absent (link only) on the purchase row; Remove disabled on the sole remaining regular row; phone renders the 2-line list, not a squeezed table.
8. **Rail sections** — all four cards present in Ownership→Purchase→Details→Notes order; each has a working Edit/Add opening the correct editor; Details' three fill-states (empty/partial/full) all render distinctly; "Added {date}" caption present, uncarded, at the end of the sections grid.
9. **Attachments** — Add file and per-row Remove both wired and functional; empty state uses `EmptyState`.
10. **Dispose** — "Sold on" present; a forced server rejection shows the real message inline, not the generic toast.
11. **Delete** — confirm body includes the "use Sell or dispose instead" sentence.
12. **Register** — a 50%-owned asset's category no longer overshoots 100% in Asset Composition; its card shows "Your 50%".
13. **Chat open/closed, 640/1024/1440** — standard checklist, all dialogs above (no new fixed-position elements expected to fail this).
