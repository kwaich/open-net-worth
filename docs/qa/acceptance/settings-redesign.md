# Acceptance — Settings overlay (REDESIGN — supersedes the Settings tab + the Profile tab)

GIVEN/WHEN/THEN criteria for the ported Settings overlay. The **design handoff prototype**
(`frontend/design_handoffs/settings_handoff/prototype/Settings Tab.html`) is the visual +
behavioural reference; every criterion is "matches the prototype". Verify on the RUNNING app,
never by reading code. Reference shots: `frontend/design_handoffs/settings_handoff/shots/`
(`{wide,tablet,phone}-{section}.png`). Deliberate deviations are listed in that folder's
`PARITY_AND_GAPS.md` — a criterion here never contradicts one.

IDs are referenced by the verify gate.

## A. The overlay shell
- **A1** GIVEN any tab, WHEN I open Settings, THEN a dialog opens **over** that tab — the tab is
  still rendered behind the scrim, not replaced by a blank shell. No console errors.
- **A2** The dialog is two panes: a fixed section rail left, one scrolling pane right. Nine
  sections in order: Profile · AI Model · Permissions · Duplicate Detection · Appearance ·
  Currency & format · Security · Data · Developer.
- **A3** Section icons are Material Symbols Rounded glyphs, not text and not hand-drawn paths.
- **A4** WHEN I press Escape, THEN the overlay closes and I am back on the tab underneath.
- **A5** WHEN I mousedown on the scrim, THEN it closes; a drag that STARTS inside the dialog and
  ends outside does **not** close it.
- **A6** Changing section resets the pane's scroll to the top (never `scrollIntoView`).
- **A7** The open section mirrors to `?sub=` (a reload or a shared link lands on it), and
  NOTHING is stored across a close: reopening Settings lands at its top level — Profile on
  desktop, the section list on a phone — unless the URL names one of its sections. A `?sub=`
  that belongs to the tab underneath (`/investments?sub=activity` rides along into
  `/settings`) is not a Settings section and does not drill in (owner, 2026-09-05).
- **A8** GIVEN Settings is already open on one section, WHEN a `?sub=` deep-link arrives for
  another (Overview's "Import a statement" → `?sub=data`), THEN the pane switches to it. No
  update-depth error, and the URL and the visible section agree afterwards.
- **A9** The scrolling pane carries `data-screen-label="Settings — <section>"`.
- **A10** Lucius stays reachable: the dock is clickable AND focusable while the overlay is open.
- **A11** The FIRST open of Settings in a session is as instant as every later one: the overlay
  ships in the main bundle (static import), so no chunk is fetched between the tap and the sheet
  appearing and no `null` Suspense fallback ever shows a blank beat. The SECTIONS inside remain
  lazy chunks with their own hover/press prefetch and skeleton (A-series unchanged). Decision:
  `docs/adr/2026-09-05-settings-ships-in-the-main-bundle.md`.

## B. Section search
- **B1** Typing filters the rail against each section's label **plus its synonym list** —
  "python" finds Developer, "colour" finds Appearance, "token" finds Security.
- **B2** No matches shows `No matching settings`.
- **B3** Search never filters panels *inside* a section.
- **B4** The clear (×) appears only while there is a query and empties it.

## C. Row grammar (every section)
- **C1** Every setting reads label · consequence · control, control right-aligned.
- **C2** A panel renders as a caption above a single rounded card whose direct children are
  hairline-separated rows; nothing is a box inside a box (provider rows, radio groups and
  warning banners are stripped of their own borders and made flush).
- **C3** A trailing `.rd-help` tucks under the block it describes with **no hairline above it** —
  it must not read as a footer strip. A leading one keeps its row treatment.
- **C4** Hover-revealed row actions sit flush to the card edge (not 56px short of it), and are
  reachable by keyboard via `:focus-within`.

## D. Profile (section 1)
- **D1** Four panels: About you · Income & expenses · FIRE settings · Notes for Lucius.
- **D2** About you has eight fields incl. **What should Lucius call you?**; age is derived from
  DOB, never asked.
- **D3** Income & expenses labels carry the **current base currency**; the low-balance threshold
  has a `?` tip explaining cash-flow alerts.
- **D4** FIRE settings is a **read-only two-column mirror** with an Edit button — not editable
  inline. Target retirement age carries a derived `in N yrs` note.
- **D5** WHEN I edit any field, THEN a sticky action bar appears: **Unsaved changes · Discard ·
  Save profile**. Discard restores the baseline; Save persists and the bar goes away.
- **D6** A background profile refresh while I am mid-edit does **not** clobber what I typed.
- **D7** Edit opens the FIRE editor; saving it updates both this mirror and the Plan tab —
  the two can never show different withdrawal rates.

## E. AI Model
- **E1** The providers panel lists only what is **configured**, default first.
- **E2** The **radio on the row IS the default setting** — there is no separate default select.
- **E3** Status badge: `Disabled` / `Default` / `Default · Key invalid` (bad) / CLI `Detected` /
  `Not installed` / `Configured` / `Key invalid` / `Unreachable`.
- **E4** The default provider **cannot be disabled**: its toggle is disabled and toasts
  *Set another provider as default first*. Remove is hidden in the editor for the default.
- **E5** Add and Edit are one modal in two modes. Add filters out already-configured providers
  but **always offers Custom**. Fields follow the catalog entry (key / editable URL / CLI binary
  path / model datalist); only Custom asks for a display name.
- **E6** A saved key renders `•••••••• (saved)` with *leave blank to keep it*; keys are never
  echoed back.
- **E7** Remove is a two-press arm (`Confirm remove`, ~4s) — no modal for a reversible change.
- **E8** Routing profile is a **radio group with a sentence each** (Cheapest / Balanced /
  Fastest), not a select.
- **E9** "Allow local models" help text **changes with its state**.
- **E10** Preferred models is a disclosure with chips + a search that **only offers unpicked
  models**, and an explicit Save with an `Unsaved` marker.
- **E11** Advanced tier pins sit behind a disclosure, both defaulting to Auto.
- **E12** Memory: one master switch (extraction AND injection), plus a fact count and a
  **Review…** modal — searchable, Forget per fact, Forget everything.
- **E13** Task models: **12 routable tasks in three groups** (Assistant · Insights · Statement
  import), matching the backend's `VALID_TASKS`. Chat is NOT among them.
- **E14** **Vision contract.** WHEN a vision task (Image reading / Scanned pages) resolves to a
  model the provider declares text-only — including via the DEFAULT model, not just an explicit
  override — THEN a banner names the task and the model, the offending select takes a warn
  border and a **readable** inline caption (not overlapping its description), text-only options
  are labelled `— no vision`, and the Default option reads `Default (no vision)`.
- **E15** The banner offers **Fix…** which opens the customise view. It is a **warning, not a
  block** — the selection is still accepted.
- **E16** A model the provider says nothing about is treated as **capable**, not warned on.
- **E17** AI spend: Today / 30 days / All time; four stats; three bar breakdowns (by surface, by
  provider, by model) as percentages of the window total; a projected monthly figure.

## F. Permissions
- **F1** Columns Tool · Scope · Granted · action; Scope always reads **Always**.
- **F2** Revoke is a two-press arm (`Confirm revoke?`, ~4s) and toasts *takes effect on the next
  call*.
- **F3** Grants for tools that no longer exist are **hidden**, not shown as a puzzle.
- **F4** Empty state explains how rows get here, since none can be added from this screen.

## G. Duplicate Detection
- **G1** Engine configuration: auto-merge policy, judgment model, max AI checks per run, and the
  AI-judge toggle — each captioned with what it costs you, not what it is.
- **G2** Remembered decisions is a flush list of real decisions with verdict + date and a hover
  **Forget** that lets the engine re-evaluate that pair.

## H. Appearance
- **H1** App name · logo URL · four curated accent swatches (no free colour picker).
- **H2** Text commits on **blur or Enter** (not per keystroke) and toasts; re-blurring an
  unchanged field is silent. A swatch commits **on click**.
- **H3** The selected swatch takes a two-ring box-shadow and stays a 26px circle (a border would
  resize it).
- **H4** The subtitle states this is **instance config, not part of your profile**.

## I. Currency & format
- **I1** Base currency is the **one select that confirms first**.
- **I2** The confirm modal names exactly which hand-typed amounts will NOT convert, **with their
  current values**, and says where each lives.
- **I3** Cancelling leaves the base currency unchanged.
- **I4** Date / number / week-start commit on change and change how the app **writes** amounts
  and dates thereafter.

## J. Security
- **J1** Password: current · new · confirm, then Change password. New-password help states the
  12-character minimum and that **there is no reset email**.
- **J2** API token masked, with Reveal · Copy · Rotate (danger). Rotating toasts that the old
  token is now invalid.
- **J3** Auth behaviour: session duration, Trust loopback, and **Disable auth entirely** — label
  in `--bad`, and turning it on toasts *Careful — auth is now off*.

## K. Data
- **K1** Export produces a single `.zip`; the caption states import **replaces all current data**
  and that a checkpoint is saved first.
- **K2** Automatic backups: daily toggle, retention days, **Back up now**, and a last-backup line.
- **K3** The backup list shows real backups — timestamp, `Automatic`/`Manual` badge, file count,
  size. A run triggered by **Back up now** is badged **Manual**.
- **K4** Row actions are hidden at rest and revealed on hover: **Download** and **Restore…**.
- **K5** Restore confirms in a modal that names what is lost ("anything added since {when}") and
  notes the safety backup. It really restores, and a restore can itself be undone.
- **K6** A restore never rolls back the password / API token.
- **K7** Uploaded files lists real chat attachments; Delete toasts the **consequence**
  ("future chat turns can no longer read this file"), not "Deleted".
- **K8** Danger zone is its own tinted panel and reads as ONE warning, not two rows.
- **K9** Reset requires typing `RESET`; the confirm button is **disabled until it matches**.
- **K10** The TradingView Lightweight Charts™ credit renders as a quiet footer line (licence
  obligation — it must live in the product).

## L. Developer
- **L1** MCP: a scrolling segmented control of six clients, each with where the file lives, a
  `Local (stdio)` / `Needs a public URL` badge, and **its own** snippet. VS Code's uses
  `"servers"` + `"type"`, not `"mcpServers"`.
- **L2** Snippets carry **this instance's** real base URL and API token; Copy config copies them.
- **L3** iPhone Shortcuts: a detected address field (picks up a tunnel) and the three real
  shortcuts, each with what it does and what it asks on install; caption warns that rotating the
  token invalidates installed shortcuts.
- **L4** Application logs: level select, search, Refresh, and a fixed-height (340px) scrolling
  tail of the REAL server log.
- **L5** Code execution: the sandbox paragraph, the switch, and **Always allow** which renders
  only while execution is on and is **force-cleared** when it is turned off.
- **L6** Extensions: name, version, permissions, an **always-visible** enable toggle (a toggle
  that appears on hover is unfindable) and Uninstall. Disabling one really stops it being used.
- **L7** Bundled script skills: two independent switches; unattended runs is disabled until the
  kill switch is on, and its help text becomes the instruction to fix that.

## M. Responsive
Boundaries follow the app's canonical ladder (640 / 768 / 1024), NOT the prototype's own
900/700 — those put the dialog out of step with the shell around it.

- **M1** Desktop (**≥1024px**): dialog centred with a max width; rail left, pane right.
- **M2** Tablet (**640–1023px**): the dialog is **edge to edge** — full width, full height, no
  radius, no border, no scrim margin — and the rail narrows to 178px.
- **M3** Phone (**<640px**, the same query as `useIsMobile`): edge to edge, and the rail becomes
  a horizontal scrolling row of section chips above the pane; form grids collapse to one
  column; **hover-only row actions become permanent** — there is no hover on a phone.
- **M4** **A phone in LANDSCAPE is still full-screen.** Phones are 844–956 CSS px wide in
  landscape, which sits above the prototype's 900px cutoff — it rendered as a centred popup
  floating on a scrim, on a phone. Verify iPhone SE / 12 / 15 / 15 Pro Max, Pixel 7 and
  Galaxy S9+ in BOTH orientations: card at 0,0 filling the viewport.
- **M5** Height uses `dvh`, not `%`: on iOS a fixed `100%` resolves against the layout viewport,
  so the bottom of the pane hides behind the browser chrome.
- **M6** No horizontal page scroll at any width; the MCP client row scrolls inside its own card
  rather than being amputated by the dialog's overflow; a long provider endpoint URL truncates
  rather than running under its status badge.

## N. Navigation
- **N1** Profile is **no longer a tab**: it is absent from the desktop rail and from the mobile
  "More" sheet.
- **N2** `/profile` still works as a direct URL and opens Settings on the Profile section, with
  the Settings entry highlighted in the rail.
