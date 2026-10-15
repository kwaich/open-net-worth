# Statement Import — Comprehensive Feature Spec (for UI Design)

**Audience:** a designer (Claude Design) who will design a proper, comprehensive UI for statement import. Assume no prior knowledge of the codebase. This document defines *what the feature must do and why*, the *states and interactions*, the *data available to render*, and the *design principles that are non‑negotiable*. It does not prescribe pixels — that's your job — but it constrains meaning.

**App context:** Open Net Worth is a self‑hosted personal‑finance app (FastAPI + React). The AI assistant is **Lucius**. Users track accounts, a transaction ledger, budgets, and investments. **Statement import** is how a user gets bank/card/wallet/broker activity into the ledger from a file (or URL). It is one of the highest‑stakes flows in the app: a mistake here silently corrupts someone's real money records.

---

## 0. TL;DR

A user uploads a statement (CSV, XLSX, PDF, OFX/QFX, QIF, MT940, CAMT). Lucius reads it (or a deterministic parser does), figures out which account it belongs to, drafts each row as a transaction, and — crucially — **checks every drafted row against what's already in the ledger** so the user never double‑books and never loses a transaction. The user reviews a **reconciliation table** that answers two questions at a glance: *"Will every statement transaction end up in my ledger?"* and *"Will the amounts match?"* Then they commit. Commit is the **only** moment money is written, and it's guarded.

The design challenge is **trust at a glance across a potentially large, messy, multi‑currency list** — making "everything's accounted for" obvious, and making the few rows that genuinely need a human decision fast to resolve.

---

## 1. Who & why

**Primary user:** the self‑hoster reconciling their own finances. Often power‑user‑ish but time‑poor. Frequently non‑US (multi‑currency, non‑Latin descriptions, e.g. Vietnamese bank exports).

**The job to be done:** *"Get this statement into my ledger, correctly, without creating duplicates or losing anything — and let me trust that it's right without auditing every row."*

**The emotional stakes (real, from the product owner):** the owner quit YNAB after a **single reconciliation mismatch cost him ~5 hours to find**. That trauma is the design north star. A mismatch must cost **seconds**, not hours. A green checkmark that is *false* is the worst possible outcome — worse than showing a scary‑but‑true warning.

**Volume reality:** a statement can be 1 row or hundreds. The ledger it's checked against can be thousands of rows. The UI must stay legible and fast at both ends.

---

## 2. Design principles (non‑negotiable)

These emerged from many rounds of live feedback. Treat them as constraints.

1. **The statement is the source of truth.** If the bank statement says a charge was ₫150,000, that's the real number. The UI should make it easy to adopt the statement's figures over an earlier estimate.
2. **"Needs you" = effort = cost.** Every row that requires a human decision is a tax on the user. Minimize the count of such rows; make the ones that remain trivially fast. Auto‑resolve everything you *safely* can.
3. **Honesty over false completeness.** Never show "all covered ✓" when it isn't. Surface discrepancies plainly rather than papering over them or over‑detecting matches that might be wrong. A visible, true warning beats a hidden, false success.
4. **Two questions anchor everything.** The whole review reduces to: (a) *Would every statement transaction end up in my ledger* (either newly imported, or already there)? (b) *Would the final amounts match?* Every element should ladder up to answering these.
5. **Commit is the one gate.** Nothing writes money until the user commits. What they see is exactly what will be written. The user can override any auto‑decision before committing.
6. **Transfers must look like transfers.** A bank‑to‑wallet movement is a two‑account transfer, not an expense. It must render with direction and both account names ("Transfer · Meridian Bank → MoMo Wallet"), never as "Unsorted."
7. **Descriptions are AI/bank noise; don't over‑rely on them.** The bank "description" is a raw, often garbled string (`MBVCB.14776...thanh toan banh mochi.CT tu...`). Matching leans on amounts/dates/references, not wording. The UI should show these strings but not force the user to parse them.
8. **AI‑native: Lucius drafts, the human reviews a diff.** The default posture is that Lucius does the work (reads the file, routes transfers, categorizes, checks duplicates) and the human confirms. The manual path is the fallback, not the primary.
9. **Memory beats repetition.** If the user teaches Lucius something about a source once ("MoMo rows are transfers to my wallet"), it should be remembered for that source and applied automatically next time.

---

## 3. End‑to‑end flow (the map)

```
Entry point
   │  (Import button · drag file into Lucius · paste URL/Google Sheet)
   ▼
① CHOOSE SOURCE ── pick a file or URL; format auto‑detected
   ▼
② CONFIGURE ── which account? (auto‑detected or pick) · optional Instructions for Lucius
   │            (for CSV/XLSX only: optional manual column mapping as a fallback)
   ▼
③ PARSE (loading) ── Lucius reads the file (or a deterministic parser runs);
   │                  extracts rows, statement metadata, and reconcile status
   ▼
④ REVIEW ── the reconciliation table (the heart of the feature) + the two‑question verdict
   │          user resolves the few rows that need it; everything else is pre‑decided
   ▼
⑤ COMMIT ── the one guarded write. "Import N · link M · skip K"
   ▼
⑥ DONE ── summary; option to import another
```

Steps ②–④ can collapse: for a **remembered** source with a clean machine format, the user might land almost straight on Review with nothing to decide.

---

## 4. Entry points

- **Primary:** an **Import** action in the Accounts area header (opens the import modal/flow).
- **Drag‑and‑drop into Lucius:** a user can drag a statement file into the Lucius chat dock and ask it to import (chat‑driven path — same engine, conversational shell).
- **From a URL / Google Sheet:** paste a link instead of uploading a file.

Design implication: the flow must work both as a **focused modal/full‑screen task** (primary) and be *conceptually* reachable from a conversational surface. Design the focused surface first; it's where the reconciliation lives.

---

## 5. Stage ① — Choose source

- **Accepts:** `.csv, .xlsx, .ofx/.qfx/.qbo, .qif, .mt940/.sta, .camt/.xml, .pdf`. Format is auto‑detected from the filename/content and shown as a badge (e.g. `XLSX`, `PDF`, `OFX / QFX`).
- **Two input modes:** *Upload a file* (drag‑drop zone + picker) and *From a URL / Google Sheet* (text field).
- **Per‑format hint copy** (helps the user understand what will happen):
  - **PDF:** "A PDF has no columns to map, so Lucius reads the document itself — every transaction, foreign amounts, references — and checks the total against the statement's own balances. Scanned/photographed statements work too. You review every row before anything is saved."
  - **CSV/XLSX (mappable):** "Recommended. Lucius reads the columns for you — any bank, any language — skips failed rows, flags what you already have, and remembers this format so the next import is instant with no AI at all." + a secondary "Prefer to map the columns yourself?" affordance.
  - **Structured (OFX/QIF/MT940/CAMT):** "This is a structured format — it imports deterministically, no AI reading needed. Lucius just runs the standard parser and shows you the preview to confirm."

**States:** empty (no file yet, show drop zone), file‑selected (show filename + format badge + a "Change" affordance), URL‑entry, error (unreadable/unsupported).

**Important nuance:** if the picked file is from a **recognized source** (seen before), its **saved instructions pre‑fill** later and a subtle "✦ Saved for this source" hint appears (see §10). Recognition happens instantly on file pick (no AI cost).

---

## 6. Stage ② — Configure (account + instructions)

### 6.1 Import into account
- A dropdown, default **"✦ Detect from the statement (recommended)."**
- **Auto‑detection** works two ways, both zero‑AI and label‑free (so they work for any bank in any country): (a) the app scans the statement's printed **account number** and intersects the last‑4 digits with the user's own accounts; (b) after the first commit, the source **remembers** which account it maps to. If neither resolves, the UI must **ask** (never guess): the account selector becomes the required action.
- **Multi‑currency statements** (e.g. a consolidated broker/wallet statement with USD + SGD + EUR sections against one account number) route **each row to the wallet holding its own currency**. An unroutable currency is offered but never default‑booked. The UI must be able to show per‑currency routing.

### 6.2 Instructions for Lucius (optional)
- A free‑text field: *"Instructions for Lucius (optional)."* Example instruction a user might give: *"Rows with MOMO.0398765432 (CashIn/CashOut) are internal transfers between this Meridian Bank account and my MoMo Wallet — never expenses or income."*
- A **"Remember for this source"** checkbox — **not all instructions should be remembered**; the user chooses. Remembered instructions re‑feed automatically on every future import of that source.
- These instructions are threaded into how Lucius reads the statement AND into a **Lucius draft‑operations pass** (see §8.9) that can reshape rows (e.g. flip CashIn rows to transfers) with visible, undoable provenance.
- If the file was recognized, this field **pre‑fills** with the saved text (editable) + the "Saved for this source" hint.

### 6.3 Manual column mapping (CSV/XLSX only, fallback)
- For spreadsheet formats, an optional "map the columns yourself" panel: Date column, Description column, Amount column (single column, or Inflow/Outflow split), Category column. This is the fallback when Lucius's auto‑read isn't wanted. Mapping is **also remembered** as format memory.

**Design implication:** ② should feel light for the common case (account auto‑detected, no instructions) and progressively reveal the manual mapping only when asked.

---

## 7. Stage ③ — Parsing (loading)

- A processing state while Lucius reads (for AI‑read formats) or the deterministic parser runs. Show the file name and a sense of progress ("Lucius is reading <file>…" with rotating phase copy).
- **Cost transparency:** an AI read has a small cost (shown after, e.g. "AI read ≈ $0.05"). A **remembered** format re‑imports with **zero** AI (format memory) — the UI should convey "instant, no AI" when that happens.
- Output of this stage feeds Review: the **drafted rows**, **statement metadata** (period, opening/closing balances, issuer, due date, minimum payment, rewards…), and a **reconcile verdict** (does the statement's own math check out — see §11).

**States:** in‑progress, success (→ Review), error (parse failed — offer retry / manual mapping / different file).

---

## 8. Stage ④ — Review (the core)

This is where 90% of the design value is. The user must, at a glance, trust that importing is safe and correct, and quickly handle the few rows that need them.

### 8.1 The mental model: two questions
At the top, a **verdict** answers the two anchor questions plainly:
- **Coverage:** *"✓ Every statement transaction will be in your ledger — N linked to entries you already had · M imported new."* (Or a warning if some rows can't be accounted for.)
- **Amounts:** *"✓ Amounts match"* — or *"⚠ Off by <amount> — K kept at your original figures (choose Merge to adopt settled)."*
- ~~A **"Show the math"** fold reveals the per‑currency waterfall behind these.~~ **REMOVED
  2026-07-31** — the waterfall's `drift` term is a residual solved for server-side, so its
  "✓ adds up" tick could never fail. Do not design it back in without an independently-computed
  drift to check against it: ADR `2026-07-31-remove-tautological-tie-out-waterfall`.
- Above the verdict, a completeness badge when the statement's own balances reconcile: *"✓ Statement parses clean — reconciles with its own balances."*

Design these as the **emotional payoff**: the user should feel "yes, I can trust this" without reading every row. The two questions must never contradict the rows below them (counts must reconcile exactly — this was a real bug class; the numbers have to tie).

### 8.2 The statement‑order list + pair rows
- Rows are shown in **statement order** (top‑to‑bottom as they appear on the statement), so the user can cross‑reference against the paper/PDF.
- Every row is either a simple row (a clean New import) or a **pair‑reconciliation row** with **three columns** when it relates to something already in the ledger:

```
┌ STATEMENT ─────────────┬ YOUR LEDGER NOW ────────────┬ AFTER IMPORT ─────────────┐
│ what the statement says │ what you have right now      │ what you'll have after     │
│ 06-01 Ecom…MOMO…CashIn  │ "MoMo top-up"                │ MoMo top-up                │
│ Transfer → MoMo Wallet  │ — 06-01 · Transfer · MRD →   │ 06-01 Transfer · MRD →     │
│ −₫98,000                │   MoMo · via Lucius · ₫98,000│   MoMo Wallet  ₫98,000     │
│                         │                              │ linked ✓                   │
└─────────────────────────┴──────────────────────────────┴────────────────────────────┘
   headline: "Already booked as a transfer"   actions: [Skip] [Merge•] [Import new]
```

- **Column meanings:** STATEMENT = the incoming row (the bank's version). YOUR LEDGER NOW = the existing transaction it matched (with a real, clickable link into the transaction detail drawer). AFTER IMPORT = a **live projection** of exactly what the ledger row will look like once you commit, given the currently‑selected action.
- The **AFTER IMPORT** projection must be truthful per action: *Merge* → one record (existing labels kept, statement's settled amount + date adopted, reference attached, cleared ✓); *Skip* → one record unchanged ("yours stays as‑is"); *Import new* → **two** records (the existing one + the statement row as its own), honestly shown as "2 records."

### 8.3 Bands (match confidence) — and what each means
Every row falls into a band. The band drives the default action and the filter chips.

| Band | Meaning | Default action | Headline example |
|---|---|---|---|
| **Matched** (strong) | Near‑certain it's the same transaction (exact/near‑exact amount, close date, or a shared bank reference). | **Merge** (link) | "✓ Already recorded — will link" |
| **Needs you** (review) | A probable match, but not certain. | **Merge** (the factory default; user can switch) | "Already in your account?" |
| **Weak match** | A possible twin with a bigger amount drift. | **Import new** (cautious — never silently link) | "≈ Possibly already recorded — settled amount differs" |
| **New** | No match; a genuinely new transaction to import. | **Import new** | (plain row) |
| **Not on statement** | A ledger entry the statement's period covers but its rows don't account for. (Separate panel — see §8.10.) | — | — |

- The **default for the "Needs you" band is a user setting** (`import_review_default`), **factory‑set to Merge** (the owner's decision: a probable match should pre‑select linking, because "Needs you means effort"). The user can change it globally in Profile → App settings ("Statement import — probable matches: Merge / Skip"), or per‑row.
- **Books‑close promotion:** when the statement is *provably complete* (its balances reconcile — §11), the engine can auto‑upgrade a unique, exact, same‑day match to "Matched" **without needing word overlap** — because completeness proves nothing is missing. This is the mechanism that shrinks "Needs you" dramatically for clean statements.

### 8.4 Filter chips
- A chip row lets the user filter the list: **All · Matched · Needs you · Weak match · New · Not on statement.** Each chip shows a **band‑true count**. Chips **toggle off** (click the active chip to clear).
- Counts across chips must reconcile with the verdict and with each other (a hard invariant — no chip should claim a count that another surface contradicts).

### 8.5 Per‑row actions
- Each matched/pair row exposes **Skip · Merge · Import new** as a segmented control (Weak rows show Skip · Merge; strong rows show Skip · Import new with Merge implied by the calm green headline). The selected action is reflected live in AFTER IMPORT.
- A user can toggle any action; toggling the active one **off** reverts to the band default.
- **Merge** = update the existing entry with the statement's settled amount/date + attach the reference; **keep its category & notes** (labels are never overwritten). **Skip** = leave the existing entry untouched. **Import new** = book the statement row as its own transaction (accepts the risk of a duplicate; honest "2 records" projection).

### 8.6 The match picker (choose *which* entry to link)
- When a row has **more than one** eligible existing entry it could link to, a quiet **"⇄ or match: N other entries ▾"** affordance appears on the YOUR LEDGER NOW column. It opens a small searchable picker listing the candidate entries; picking one re‑points the merge.
- Includes honest states: an entry already taken by another row shows "— matched to another row"; picking it swaps (a mutual‑swap is allowed, and the UI must not deadlock — two rows can trade partners).

### 8.7 Manual link (link a New row to *any* uncovered entry)
- The engine only auto‑matches near‑misses (±1 day, small drift). A genuinely same‑charge pair that drifted further (e.g. an agent booked "TD subscription renewal" −₫250,000 on the 11th; the statement says −₫150,000 on the 14th) sits split: the statement row is in **New**, the ledger entry is in **Not on statement**. There must be a way to hand‑assert the match.
- On an unmatched **New** row: a **"⇄ Link to an existing entry…"** control opens a searchable picker of the **uncovered "Not on statement" entries** (the safe set). The note is shown so entries are recognizable ("TD subscription renewal — 06‑11 · via agent · −₫250,000"). Picking one turns the row into a **Merge** row — full 3‑column projection, the two‑question verdict recomputes, and the **Off‑by** marker shows the real difference (the ₫9,900). Unlink reverts to New.
- **Reverse direction:** the "Not on statement" panel entry also offers a **"⇄ Link…"** that picks from the New statement rows. Same result.
- A linked entry leaves the "Not on statement" panel immediately (pre‑commit), so counts stay honest.

### 8.8 Off‑by / drift markers (the anti‑YNAB feature)
- When a linked row will **keep your original figure** rather than adopt the statement's (i.e. the amounts differ), the row wears an amber **"Off by <amount>"** chip beside its actions, with a tooltip showing both figures: *"Statement settled ₫148,300 · yours says ₫150,000 — Merge adopts the settled figure."* (Skipped rows say the truth: *"Skipped — yours stays ₫150,000; the statement's ₫148,300 is not adopted."*)
- The amounts‑verdict line ("Off by ₫X — K kept at your original figures") is **clickable** — it filters the table to exactly those K drifted rows via a transient "Off by ₫X" chip. So a mismatch is: click the number → see the rows → each shows both figures → Merge the ones the bank is right about.
- **One source of truth:** the per‑row markers, the pill count, and the verdict number all derive from the same predicate — they can never disagree.

### 8.9 Lucius provenance (the draft‑operations pass)
- If the user gave instructions, Lucius runs a **draft‑operations pass** that can reshape rows: **route_transfer** (turn a CashIn/CashOut row into a real two‑leg transfer), **set_category**, **skip**. Each op is applied to the *draft* (never the ledger) with a visible **✦ provenance chip** stating Lucius's reason ("✦ Books as transfer: Meridian Bank → MoMo Wallet"), an **applied strip** above the table ("✦ Lucius applied N changes from your instructions · Undo all"), and **per‑row undo**. Commit books exactly what the user last saw.
- Design implication: the ✦ chips are the *AI‑did‑this, here's why, you can undo* surface. They must be legible and trustworthy, not noise.

### 8.10 The "Not on statement" panel (ledger‑check)
- Below the statement rows, a section: **"In your ledger, not on this statement — N"** with the net amount. Lists ledger transactions inside the statement's period that its own rows don't account for.
- Each entry is inspectable (opens the real transaction drawer), and offers safe actions: **Delete** (if it's a phantom/duplicate), **Link…** (manual link to a New row, §8.7). Entries with a "possible match already linked above" show that note (and a "Show match ↑" jump).
- A **boundary bucket** ("N entries sit near the cut‑off — likely the previous/next statement") is separated out and collapsible, so cross‑period noise never cries wolf.
- This panel is the other half of the "would everything be accounted for" question — it surfaces *ledger* rows the statement doesn't cover, so the user can decide if they're real, phantom, or just from an adjacent period.

### 8.11 Bulk actions
- ONE contextual bulk bar, rendered above the list while the `Will link` chip is active: "Link all" / "Import all" / "Leave all out". (Review and weak never grew a bar of their own; a needs-you row is decided on its queue card.)
- Its scope is not "a band" but **the rows the list beneath it is rendering** — its buttons move exactly that one array, so the bar can never act on a population other than the one on screen. See ADR `2026-07-29-bulk-bar-counts-what-it-mutates`.
- It is **buttons only** — no headline, no count of its own (owner 2026-07-31). The `Will link` section header directly beneath already states the count, and a second number up here is what drifted from the action in the first place. See ADR `2026-07-31-bulk-bar-is-buttons-only`.

### 8.12 Row anatomy (what a single row shows)
A statement row can carry: date, payee (recognizable merchant, when resolved), category (with color dot; "Unsorted" if none; **transfer identity** instead of category for transfers), the raw bank **description**, a personal **memo** (usually empty), the signed **amount** (+ a secondary line with the original foreign amount if the charge was in another currency, e.g. `−₫150,000` with `S$…` below), a **source** icon (imported/agent/Lucius/etc.), and any **flags/chips** (match band headline, ✦ Lucius provenance, ⚠ Off‑by, "Needs your eyes" low‑confidence, routing chip, etc.).

---

## 9. Stage ⑤ — Commit & ⑥ Done

- **Commit** is the single guarded write. The footer summarizes the net effect in the two‑question grammar: e.g. **"Link 1 · import 1"** (a merge counts toward *links*, not imports) or **"Import 23"** or **"Nothing to import"** (all skipped).
- **Guard:** the server re‑derives every match verdict at commit; a merge can only target an entry the engine proposed, an eligible twin, or an uncovered "Not on statement" entry — a fabricated/foreign target is refused. (Design implication: the user can trust that what they approved is exactly what books; there's no silent divergence.)
- **Done state:** a summary ("Imported N · linked M · skipped K"), and an "Import another" affordance.

---

## 10. The teach lane (instructions & memory)

Three memory mechanisms, all designed to make the *next* import effortless:
1. **Per‑source instructions** (§6.2): free text, optionally "Remember for this source." Threaded into parsing AND the Lucius draft pass.
2. **Format memory:** for CSV/XLSX, the learned column mapping and the source's account are remembered — a repeat import runs with **zero AI** and pre‑resolved account.
3. **Force re‑read:** if the user changed instructions but the app served a cached parse, a **"· Re‑read"** affordance forces a fresh parse (and instructions changes bust the cache automatically).

The teach surfaces the user interacts with: the instructions field + "Remember" checkbox at configure time; the "Saved for this source" hint on a recognized file; the "· Re‑read" control; and (historically) a per‑row "✦ Always…" teach popover — **note: the per‑row teach popover was removed**; instructions + the Lucius pass are now the single teaching mechanism.

---

## 11. Completeness & reconciliation

- Many statements carry a **running balance** column (or printed opening/closing balances). The app verifies the **balance chain**: each row's balance must equal the previous ± the row's amount. A fully‑linked chain is *proof the statement is gapless* between its first and last row.
- When the chain checks out (or a machine format declares a clean period), the statement is marked **complete/reconciled** — shown as the green *"reconciles with its own balances"* badge — which unlocks **books‑close promotion** (§8.3) and makes the coverage verdict authoritative.
- If the chain breaks or there's no balance data, the statement is *not* claimed complete, and the UI stays appropriately cautious (more rows land in "Needs you"; the completeness badge is absent).

Design implication: completeness is a **trust amplifier** — when present, lean into "we've verified this statement is whole." When absent, don't fake it.

---

## 12. States to design (every one matters)

For **each** stage:
- **Empty:** no file yet; a statement with zero rows; a ledger with nothing to check against.
- **Loading:** parsing (with cost/AI hints); committing.
- **Error:** unreadable file; unsupported format; parse failure; account not detected (must‑ask state); commit failure (nothing partially written).
- **Partial / degraded:** low‑confidence rows ("Needs your eyes · 72%"); an unroutable currency; a statement that doesn't reconcile; a boundary bucket of cross‑period entries.
- **Success:** the calm all‑green verdict; the done summary.

The **"all clean, nothing to decide"** state deserves special love — for a remembered, reconciling machine import, Review might be *"✓ 45 transactions, all accounted for, amounts match — Import 45."* Make that feel like a one‑tap win.

The **"messy real statement"** state (dozens of rows, several bands, a few off‑by, a Not‑on‑statement panel, non‑Latin descriptions) is the stress test — it must stay scannable.

---

## 13. Edge cases & special shapes (must be representable)

- **Transfers** (bank ↔ wallet, internal moves): render as "Transfer · A → B" with direction and both account names, never "Unsorted." A transfer may *also* carry a category.
- **Cross‑currency FX pairs:** two legs of one conversion (e.g. NovaPay SGD → Meridian Bank VND) — one row books the transfer, the other folds in. A linked transfer must compare/display the leg **on the statement's own account** (a ₫2,000,000 credit linked to a NovaPay SGD→MRD transfer must read ₫2,000,000, not the SGD leg).
- **Foreign‑charged rows:** a card billed in a foreign currency shows the settled amount + the original foreign amount as a secondary line.
- **Duplicates within the file** (a statement listing the same row twice) and **re‑imports** (importing the same file again) must both be caught and shown as "already imported," not re‑booked.
- **Split transactions**, **adjustments/opening balances**, **investment cash legs** — these exist in the ledger and may appear in the "Not on statement" panel; they have their own rendering rules (don't force them into the expense/income template).
- **Non‑Latin / very long descriptions** (Vietnamese bank strings, dotted reference ids): must wrap/truncate gracefully and never overflow into neighboring columns.
- **Multi‑account / consolidated statements:** per‑currency routing to wallet accounts.

---

## 14. Data reference (what's available to render)

So you know what fields exist. A **drafted statement row** carries (roughly): `date`, `payee`, `description` (bank string), `memo` (personal, usually empty), `amount` (signed) + `currency`, `original_amount`/`original_currency` (foreign charge), `kind` (income/expense/transfer), `category`, a `source`, a `reference` (bank ref id), match **flags** (band, matched existing entry summary, score), `routed`/`lucius` provenance (transfer routing + reason), `needsEyes`/`confidence`, and `dup`/`preRecon` markers.

A **matched existing entry** summary carries: `date`, `note`/description, `amount` + `currency`, `kind`, `category` + `has_category`, `cleared`, `source`, `account_id`, and (for transfers) **both legs** (`from`/`to` account + amount + currency) so the anchor‑side leg can be shown.

**Statement metadata:** `currency`, `period_start`/`period_end`, `statement_date`, `payment_due_date`, `opening_balance`/`closing_balance`, `amount_due`, `minimum_payment`, `rewards`, `issuer` — plus a **reconcile verdict**. Design a header/summary that can surface the relevant ones (period, balances, due date, minimum payment for cards).

---

## 15. Formats supported (and how they differ for the user)

| Format | How it's read | User‑facing difference |
|---|---|---|
| **PDF** | Lucius reads the document (incl. scans/photos) | No column mapping; "reads every row + checks the balance total" |
| **CSV / XLSX** | Lucius auto‑reads columns (or manual mapping) | Format is **remembered**; repeat imports are instant/zero‑AI |
| **OFX / QFX / QBO** | Deterministic parser | "Structured — imports deterministically, no AI" |
| **QIF** | Deterministic parser | Same |
| **MT940 / STA** | Deterministic parser (has balances/period) | Same; carries reconcile data |
| **CAMT.053 / XML** | Deterministic parser (has balances/period) | Same; carries reconcile data |

All formats funnel into the **same** review/reconciliation surface — the design should be format‑agnostic downstream of parsing (only stages ①–③ differ by format).

---

## 16. What's built vs. open (your canvas)

**Built and live** (you're redesigning/upgrading, not inventing): the full pipeline (upload → account‑detect → instructions/memory → parse → the pair‑reconciliation review with bands, filter chips, per‑row actions, match picker, manual link, off‑by markers, Lucius provenance, the two‑question verdict, the "Not on statement" panel, balance‑chain completeness) → guarded commit. It works, but it grew feature‑by‑feature from live feedback and deserves a **cohesive, comprehensive visual/IA redesign** — that's the ask.

**Known rough edges to improve (design opportunities):**
- The review surface accreted many chips/lines/panels; it needs a coherent **information hierarchy** so the two‑question verdict dominates and the detail recedes until needed.
- The **large‑list** experience (hundreds of rows, thousands in the ledger) needs deliberate density, grouping, virtualization‑friendly layout, and fast filtering.
- The **three‑column pair row** is information‑dense; make the STATEMENT vs LEDGER‑NOW vs AFTER‑IMPORT comparison instantly legible, especially the "what changes" delta.
- **Empty/loading/error/all‑clean** states are under‑designed relative to the busy state.
- The **conversational entry** (drag‑into‑Lucius, chat import) is plumbed but its review surface is the same modal — consider how the reconciliation reads inside a chat context.
- Mobile/responsive is not yet considered.

**Explicitly out of scope for the engine (don't design around these):** the *matching logic* itself (deterministic, backend) is fixed; you're designing how its verdicts are *presented and acted on*. You don't need to design a rules editor here (that lives in Automations).

---

## 17. Glossary

- **Draft / drafted row:** a statement row turned into a proposed transaction, not yet committed.
- **Band:** match‑confidence bucket (Matched / Needs you / Weak / New).
- **Merge / link:** update an existing ledger entry with the statement's row (adopt settled figures, attach reference, keep labels).
- **Books‑close promotion:** auto‑upgrading a match to "Matched" because the statement provably reconciles (nothing missing).
- **Reconcile / balance chain:** verifying the statement's running balances are internally consistent → proof it's gapless.
- **Off‑by / drift:** a linked row whose amount differs from the statement's settled figure.
- **Not on statement:** ledger entries in the statement's period that its rows don't account for.
- **Lucius pass:** the AI draft‑operations step that reshapes rows from the user's instructions (route transfer / categorize / skip), fully visible + undoable.
- **Source memory / format memory:** remembering a source's account, column mapping, and instructions so repeat imports are effortless.
- **The two questions:** *(1) Will every statement transaction be in my ledger?* *(2) Will the amounts match?* — the anchor of the whole review.

---

### One‑line brief for the designer

> Design a statement‑import experience where, no matter how messy the file, a user can **trust in seconds that everything will be correctly accounted for** — the two‑question verdict dominates, the few rows that truly need a decision are effortless to resolve, transfers look like transfers, mismatches are one click from being seen and fixed, and a green checkmark is *never* a lie.
