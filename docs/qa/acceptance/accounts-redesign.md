# Acceptance — Accounts tab (REDESIGN — supersedes accounts.md on ship)

GIVEN/WHEN/THEN criteria for the ported Accounts tab. The **design handoff prototype**
(`frontend/design_handoffs/accounts_handoff/prototype/index.html`) is the visual + behavioral
reference; every criterion is "matches the prototype." Verify on the RUNNING app
(`make qa-e2e`), not by reading code. IDs are referenced by the verify gate.

## A. Tab load & layout
- **A1** GIVEN the app, WHEN I open Accounts (`/accounts`), THEN I see the app nav rail (existing),
  the **accounts sidebar** (net-worth header + grouped accounts), and a **main column** showing the
  all-accounts **Overview** above the **register**. No console errors.
- **A2** GIVEN Overview, WHEN nothing is selected, THEN the balance card shows
  **Cleared + Uncleared = Working** and an "N to review" affordance; the register shows all accounts.
- **A3** Loading state: skeleton/placeholder while accounts+txns fetch; never a flash of "empty".
- **A4** Error state: if the accounts/txns fetch fails, an inline error with a retry — not a blank screen.

## B. Sidebar
- **B1** Net-worth header shows net worth (masked-capable), an assets/debts split bar, and
  Assets/Debts compact legend.
- **B2** Accounts grouped in fixed order: Cash, Investments, Retirement, Property & other,
  Credit cards, Loans & mortgages — each with a masked subtotal; empty groups hidden.
- **B3** Venue nesting: ≥2 accounts sharing an institution collapse into an expandable venue row
  with its own subtotal; expand/collapse toggles the nested accounts.
- **B4** Each account row: colour dot, name, type (+ ···lastFour), sync/manual badge, and a
  **holdings** figure (securities, ◈) OR native **balance**.
- **B5** "All accounts" row selects Overview; clicking an account row selects single-account view.
- **B6** Sidebar is resizable (drag edge, width persists) and collapsible (chevron); collapsing
  shows a reopen affordance in the main header.
- **B7** Responsive: when the register's available width drops below ~700px (window narrows / Lucius
  panel opens), the sidebar **auto-collapses**; re-expands past the hysteresis threshold; a manual
  collapse/expand pins the choice.
- **B8** "Add account" and "Manage payees" affordances present in the sidebar.

## C. Single-account view
- **C1** Header: account name, sync badge, actions (Manage payees, Import, Reconcile, overflow menu:
  Edit / Connect / Archive / Export). Balance card: Cleared + Uncleared = Working + "N to review".
- **C2** Credit card: shows statement/due affordance (days until due/close) when configured.
- **C3** Reconcile popover: "Is the real balance X?" → Yes (in sync) or No → enter real balance →
  shows adjustment delta → "Add adjustment" books a reconcile adjustment; toast confirms.

## D. Register (table)
- **D1** Columns: checkbox · cleared · (account, Overview only) · date · payee · category · note ·
  source · amount. Labels + order match the prototype.
- **D2** Columns **resizable** (drag, persists; double-click handle resets all) and **sortable**.
- **D3** Infinite scroll: first 60 rows; scroll extends; resets on filter/sort/account change; a
  "shown of total" sentinel past 60.
- **D4** Single click → detail drawer; **double-click a cell** → inline edit (date/account/payee/
  category/note/amount). Esc cancels, Enter commits.
- **D5** Inline amount edit **sign-aware**: positive→income, negative→expense; balances update server-side.
- **D6** Cleared toggle flips cleared on click; needs-review rows show a "Review" pill.
- **D7** Amount display: single account = native primary; Overview = base primary + native/original
  beneath; FX fact on hover.
- **D8** Split rows show "Split · N ▾" expanding the split lines.
- **D9** Transfer rows render "From → To", "Category not needed", signed per the scoped account.
- **D10** Empty states: "No transactions yet" (Add/Import) vs "No transactions match your filters" (Clear filters).

## E. Inline Add row
- **E1** "Add transaction" reveals a sticky add row with rich pickers + inflow⇄outflow toggle;
  keyboard-first (Tab/Enter advance; pickers open with ArrowDown/Space).
- **E2** "To/From {account}" in payee → **transfer** (category disabled).
- **E3** Saved payee w/ default category auto-fills category (flips inflow if income).
- **E4** "Save" closes; "Save and add another" keeps date+account, refocuses; invalid rejected.
- **E5** Posts via API; register + balances reflect it; toast confirms.

## F. Search & filter
- **F1** Grouped, keyboard-navigable suggestions (text/payee/category/note, Category:, Payee:,
  Account:, #tag, amount comparisons, Source:, Type:, Status:).
- **F2** Commit → removable chip; Backspace on empty removes last; "Clear all" clears.
- **F3** Same-field OR, cross-field AND.
- **F4** Date-range presets + custom month range; active styling.

## G. Bulk bar
- **G1** Header checkbox (no selection) offers "These N on screen" vs "All N matching"; selecting
  shows the bar with count + signed total.
- **G2** Actions: Categorize, Mark cleared/uncleared, Duplicate, Note, Move to account, Export, Delete;
  destructive route through the shared Confirm modal.
- **G3** Each calls the API + reflects; toast confirms.

## H. Detail drawer (View ⇄ Edit)
- **H1** Single-click → View: hero amount, account/category/note, original-charge FX, **Provenance**
  (source, added-by, created/edited) + an **Activity** timeline.
- **H2** needs-review rows: **Approve** (clears review) / **Reject** (soft-deletes).
- **H3** "Edit transaction" → full editor (kind segmented, account, date, payee, category/split,
  amount, tags, taxable, FX) → Save patches via API.
- **H4** Drawer resizable (persists) + closes on ✕/outside/Esc.

## I. Add account modal
- **I1** Add opens on a **method choice**: Connect a bank/broker (→ connectors/coming-soon),
  Add manually, or **Describe to Lucius**.
- **I2** Describe to Lucius: free text → structured fields **pre-fill the form for review** (never auto-create).
- **I3** Type picker: searchable, one of seven values (`cash, fixed_deposit, investment,
  retirement, receivable, credit, loan`); only **type** is picked — kind and sidebar group
  derive from it; there is no band, Locked, or on-budget field anywhere on the form.
- **I4** Details: Name, type, Starting balance ("Amount you owe" for liabilities → stored negative),
  Balance-as-of, Currency. Type-conditional: credit→limit+statement/due+reminder; loan→APR+principal+
  payoff+min payment; cash/fixed_deposit→APY. Always: Institution (create-on-fly), last-4, colour, note.
- **I5** Save creates/updates via API (signs liabilities, derives group/kind). Edit jumps to form;
  warns if a holdings account switches to cash-like type.

## J. Import modal
- **J1** Two paths: **Read the statement** (Lucius) / **Map columns myself**; accounts are chips
  picked from a popover. SUPERSEDED by the Statement Import v3 handoff — see `statement-import.md`
  SI-87…SI-92; the `imp-` Choose/Read chrome this line described is gone.
- **J2** Source: drag/drop or browse (PDF, CSV, XLSX, OFX, QIF, MT940, CAMT) or hosted/Sheets URL
  behind the dropzone's own "or paste a URL / Google Sheet link" control.
- **J3** Processing: Lucius returns a compact **schema**; applied to **every** row locally (nothing
  truncated; signs correct); deterministic extractor is the no-LLM fallback.
- **J4** Assign step only if the file spans multiple of the user's accounts.
- **J5** Preview & correct: counts (import/already-imported/skipped/read), anomaly flags, inline
  corrections (flip sign, change cat/account), skipped rows + reasons.
- **J6** Confirm imports via `/api/statements/import` (`source:'import'`, `needs_review:true`), updates balances, summary.

## K. Manage payees modal
- **K1** Master-detail: search, segments (All/Uncategorized/Hidden), sort (Most used/A–Z/Recent),
  "+ New", "Apply rules to history".
- **K2** Editor: display name (live-saved, dupe-guarded), default category, toggles, one-shot
  "apply category to N existing", auto-matched descriptions (read-only), renaming rules
  (contains/is/regex), danger zone (Merge, Delete reassign-first).
- **K3** Every irreversible/bulk payee action confirms its blast radius first.

## L. Add category · Confirm · Toast (shared)
- **L1** Add category: name + group (income vs spending → kind) + colour + (income) taxable; returns
  the new key to the requesting field.
- **L2** Confirm modal = the single shared destructive/bulk dialog.
- **L3** Toast on every successful mutation; auto-dismiss.

## M. Lucius parity (HARD)
- **M1** Every UI op is achievable via Lucius tools: txn add/update/delete incl. transfer, adjustment,
  split, original-FX, tags, taxable, cleared, **needs-review approve/reject**; bulk categorize/cleared/
  note/duplicate/**move-to-account**/delete/export; account CRUD incl. ALL new fields + full type set;
  reconcile; **describe an account**; payee + category + rules + import.
- **M2** Asking Lucius (in-app) to do any of the above completes through the normal confirm flow and
  reflects in the Accounts UI without a full reload.

## Verify gate (blocking)
`pytest -m "not integration"` green · `make lint` clean · `npm run build` green ·
`make qa-e2e` green (touched IDs) · `make ui-gate` clean · screenshots · empty/loading/error shown.
