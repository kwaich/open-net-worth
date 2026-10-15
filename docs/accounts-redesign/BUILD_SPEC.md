# Accounts Tab — Redesign Build Spec (authoritative)

> **Mission.** Re-implement the **Accounts tab** end-to-end so its look, layout, states and
> interactions are an **exact match** of the design handoff. The design is law. We do **not**
> compromise the design by claiming the current app "already supports it." Where the current
> app has a feature the design omits, we **keep it working but record it** in
> `FEATURE_LOSS.md` and surface it to the owner — we never silently drop or silently keep
> something that contradicts the design.

## 0. Source of truth (read these first, every lane)

The **prototype is the reference implementation**. Port it faithfully.

```
frontend/design_handoffs/accounts_handoff/
├── 01-foundation.html .. 05-components.html   ← prose spec (data model, flows, the DS ask)
├── prototype/accounts/
│   ├── data.jsx     ← data model, money/FX helpers, categories, TYPE_LABELS, TYPE_GROUP, seed
│   ├── main.jsx     ← App state + the 4 mutations + Overview + AccountHeader + BulkBar + Toast
│   ├── app.jsx      ← Sidebar, Register (table), inline AddRow, the 4 field pickers, filters
│   ├── modals.jsx   ← AccountModal, AddCategoryModal, ConfirmModal, PayeesModal, ColorPicker,
│   │                  BandPicker, InstitutionPicker, CurrencyPicker, PMSelect
│   ├── detail.jsx   ← TxnDetail drawer (View ⇄ Edit), provenance + activity timeline, CcyPicker
│   ├── import.jsx   ← ImportModal (Lucius path + manual column-map path), ImpSelect
│   └── accounts.css ← 1397 lines of the ACTUAL production styles — PORT THIS VERBATIM
└── components/{register.html, transaction-drawer.html}   ← standalone markup+CSS references
```

**Porting rule:** translate babel-JSX (`window.ONW`, `window.AcctData`, `window.AcctParts`) →
the app's React+TS, swap the mock mutations for real API calls, and bring `accounts.css`
across (re-prefixed and registered per the CSS-class discipline). Keep class names and DOM
structure **identical** wherever possible so the rendered result is pixel-identical. When in
doubt, open the prototype in a browser and match it.

## 1. Scope & non-negotiables

- The whole Accounts tab: **sidebar** (net-worth header + grouped accounts + venue nesting +
  resize/collapse), **Overview vs single-account** main column, the **register** (resizable +
  sortable columns, infinite scroll, inline cell edit, inline Add row, selection + header
  select-all menu), **search tokens**, **bulk bar**, the **detail drawer** (View ⇄ Edit,
  provenance, activity), and the three modal flows **Add account · Import · Manage payees**,
  plus **Add category** and the shared **Confirm** + **Toast**.
- **Responsive/progressive behavior** is in scope: sidebar auto-collapse with hysteresis
  (`collapseAt` 700 + 80), resize-persist widths, infinite-scroll windowing.
- **Backend**: implement the API + model changes the new flow needs (§4).
- **Lucius parity (HARD)**: every operation the UI can do, Lucius can do (§5).
- **Empty / loading / error** states for every surface (design shows: zero-accounts hero,
  "no transactions yet" vs "no transactions match filters", payees-empty, import states).

### Explicitly OUT (sanctioned by the handoff scope callouts)
- The **app navigation rail** and **Ask Lucius dock/side-panel** are "built and frozen —
  consume, don't rebuild." → We mount inside the existing `RouterShell` and keep the existing
  `LuciusDock`. **Do NOT port** the prototype's `AppNav`, `LuciusBar`, `LuciusSidePanel`.
- **Bank connect** (aggregator linking, 24-month backfill) is the connector workstream — the
  "Connect a bank" entry points exist but route to the existing connectors flow / "coming soon".
- The **Tweaks panel** is a prototype-only dev tool — not shipped.

## 2. Where it lives (placement)

- New feature dir: **`frontend/src/components/tabs/accounts/`** (mirrors `tabs/investments/`).
  - `AccountsTab.tsx` — orchestrator: owns state, the 4 mutations wired to API, renders
    sidebar + main + register + overlays. (≈ prototype `main.jsx`'s `App`.)
  - `Sidebar.tsx`, `Overview.tsx`, `AccountHeader.tsx`, `Register.tsx`, `AddRow.tsx`,
    `pickers.tsx` (AddAccountSelect/AddDateField/AddPayeeField/AddCategoryField/FilterCombo/
    EditCombo/AdvCheck), `SearchFilter.tsx`, `BulkBar.tsx`, `TxnDetail.tsx`,
    `AccountModal.tsx`, `ImportModal.tsx`, `PayeesModal.tsx`, `AddCategoryModal.tsx`,
    `ConfirmModal.tsx`, `Toast.tsx`, plus `ds.tsx` (ColorPicker/BandPicker/InstitutionPicker/
    CurrencyPicker/PMSelect/CcyPicker), `data.ts` (helpers/format/types), `useAccountsLedger.ts`
    (data hook), `accounts.css`.
  - Keep the split close to the prototype's file boundaries so review is a 1:1 diff.
- **Router**: swap the `/accounts` (and `/accounts/payees`) route component from the legacy
  `Cash` to `AccountsTab` in `frontend/src/router.tsx` / `RouterShell`. Keep tab id `cash` /
  label "Accounts" to avoid nav churn (revisit in cleanup). Leave `Cash.tsx` in the tree until
  the new tab passes the verify gate, then delete it (cleanup lane).
- **CSS discipline**: every new `className` token must be defined in CSS in the same change.
  Put accounts styles in `tabs/accounts/accounts.css` (imported by `AccountsTab.tsx`), mirror
  the Investments `design.css` precedent. Run `npm run lint:classes`. Genuinely dynamic tokens
  go in the check script ALLOWLIST with a comment. **No Tailwind/utility invention.**

## 3. Data-model mapping (design ↔ backend)

The backend already has a strong two-leg ledger (balances computed-not-stored, cleared vs
working vs reconciled, splits, FX `original_amount/original_currency`, payees, categories,
import w/ LLM schema). Reuse it. Map design fields onto it; add only what's missing.

### Account
| design field | backend | action |
|---|---|---|
| `type` (~25 types) | `Account.type` (≈13) | **extend** TYPES + `AccountCreate/Update` enum to the design's full set (see `data.jsx TYPE_LABELS`/`TYPE_GROUP`) |
| `currency` | `currency` | reuse |
| `balance`/`cleared` | computed `balance`/`cleared_balance` (+ `_base`) | reuse (server-authoritative; never store) |
| `holdings` | from `/api/portfolio` positions, mapped by account | reuse the existing holdings-map approach |
| `institution` (venue grouping) | `platform` | **reuse `platform`** as institution; label "Institution"; venue-nest by it |
| `connected` (synced vs manual) | — | **add** `connected: bool = False` |
| `group` (sidebar group; override) | derive from type; optional override | **add** optional `group` (cash·investments·retirement·property·credit·loans); default from `TYPE_GROUP[type]` on the client |
| `kind` (asset/liability) | `kind` | reuse (derive from group for liabilities) |
| `role` (securities/cash) | derived: `holdings>0` | client-derive |
| `color`,`last_four`,`note` | `color`,`last_four`,`note` | reuse |
| credit: `creditLimit`,`statementCloseDay`,`paymentDueDay` | `credit_limit`,`statement_close_day`,`payment_due_day` | reuse; reminders live in Automations |
| loans: `apr`,`principal`,`payoffDate`,`minPayment` | — | **add** `apr`,`principal`,`payoff_date`,`min_payment` (metadata; not ledger math) |
| savings/MMF: `apy` | — | **add** `apy` (metadata) |
| `band` (net-worth band) | `asset_class` | reuse `asset_class` |
| `locked` | — | **add** `locked: bool = False` |
| `lastReconciled` | `last_reconciled_at` | reuse |
| `archived` | `archived` | reuse |

### Transaction
| design field | backend | action |
|---|---|---|
| `kind`,`amount`,legs,`toAmount/toCurrency` | same two-leg model | reuse |
| `baseAmount` | recompute `txn_base_amount()` | reuse (never trust stored) |
| `category`,`splits` | same | reuse |
| `origAmount/origCurrency` | `original_amount/original_currency` | reuse |
| `cleared` | `cleared` | reuse |
| `source` (manual·bank·import·ai·agent·automation·reconcile) | `source` (manual·ai·email·telegram·csv_import…) | **align** the source vocab so the register's Source icon matches the design set; map existing → the design's 7 |
| `needsReview` | — | **add** `needs_review: bool = False`; unattended/heuristic creates set true (AI, import, agent, email, recurring); bank syncs + manual stay false; approve/reject toggles |
| `taxable` | `taxable` (tri-state) | reuse |
| `tags` | `tags` | reuse |
| `activity[]` / provenance (`addedById`,`via`,actor timeline) | `txn_audit.json` via `services/audit.py` + `created_at`/`updated_at`/`source` | **add** read endpoint that maps audit → the design's activity timeline shape (best-effort; minimal trail if audit is sparse) |

## 4. Backend work (Lane BE owns `backend/` except `ai/tools/` which Lane BE also covers)

All monetary math stays in `services/`; never store `base_amount` as truth. Respect the
dependency rule (`api→ai→services→db→core`), the write choke points, and the test policy
(every new endpoint = TestClient smoke; every service method = unit test; every model field =
factory/coercion test).

1. **Account model** (`models/account.py`, `db/account_store.py`): extend `TYPES` to the full
   design set; add fields `connected`, `group`, `apr`, `principal`, `payoff_date`,
   `min_payment`, `apy`, `locked`. Factory-coerce, default-empty, no migration. Update
   `AccountCreate`/`AccountUpdate` + the `POST/PUT /api/accounts` routers.
2. **Transaction `needs_review`** (`models/transaction.py`, `services/ledger.py`): add field;
   set `True` on import + AI-sourced creates; expose in the `/api/transactions` read; add a
   write path to clear/set it (extend `bulk_update_transactions` with `needs_review` + a
   `PATCH /api/transactions/{id}/review` or fold into the existing cleared patch). Approve =
   clear review; Reject = soft-delete (matches the drawer's Approve/Reject).
3. **Source vocab alignment**: ensure the API emits a `source` the register can render as one
   of `manual·bank·import·ai·agent·automation·reconcile`. Map `csv_import→import`,
   `email/telegram→` their actor, etc. Keep a single mapping helper.
4. **Provenance / activity**: `GET /api/transactions/{id}/activity` → `{source, addedBy,
   createdAt, updatedAt, activity:[{action, actor, via?, at, note?}]}` derived from
   `txn_audit.json`. Read-only. Used by the drawer's Provenance + Activity blocks.
5. **Describe-an-account (Lucius)**: `POST /api/accounts/describe` → free text in, structured
   **editable** account-form fields out (type, name, currency, balance, institution, …). Service
   in `services/` calling `core.llm_seam.complete`; **populate the form for review, never
   auto-create.** (Handoff §04 "Describe to Lucius".)
6. **Import**: reuse `/api/import/preview` + `/api/import` + the LLM schema extraction
   (`services/reconcile.py`, `services/import_*`). The new ImportModal UI calls these. Confirm
   the **schema-then-apply-locally** contract is intact (nothing truncated; signs correct;
   multi-account split via the assign step). Add anything the preview needs: counts (to import
   / already imported / skipped / read), anomaly flags, skipped-reasons — most already exist.
7. **Reconcile / adjustment / transfer / cross-currency / splits**: already in `services/ledger.py`
   and the API — verify the new UI's flows hit them unchanged (reconcile popover →
   `POST /api/accounts/{id}/reconcile`; transfer = two-leg create; adjustment = one-leg create
   or reconcile-emitted; split = `splits[]`).
8. **Export**: the bulk bar + overflow "Export" download a CSV. Prefer a backend
   `GET /api/transactions/export?…` (server-authoritative CSV) over client assembly, but a
   client CSV (as the prototype does) is acceptable if simpler — decide and note it.

## 5. Lucius parity checklist (Lane BE; HARD requirement)

Whatever the UI can do, Lucius can do. Most tools exist — confirm/extend each:
- add/update/delete transaction incl. **transfer** (two-leg), **adjustment**, **split**,
  **original-charge FX**, **tags**, **taxable**, **cleared** — `add_transaction`/`update_transaction`
  already cover; verify adjustment + needs_review.
- **needs_review approve/reject** — add to `bulk_update_transactions` (and a single-txn path).
- bulk: categorize / cleared / note / duplicate / move-to-account / delete / export —
  `bulk_update_transactions`, `bulk_delete_transactions` cover most; **add "move to account"**
  (re-point legs) and **duplicate** if missing.
- account CRUD incl. **all new fields** (connected, group, apr, principal, payoff_date,
  min_payment, apy, locked, institution, band, color, last_four, credit fields) — extend
  `add_account`/`update_account`.
- **reconcile** — `reconcile_account` exists.
- **describe an account** — add `describe_account` tool wrapping the new service (interactive).
- payees: create/rename/default-category/autocategorize/hidden/aliases/rules/merge/delete/
  apply-rules — all exist.
- categories: list/add/rename/remove/hidden incl. color/group/taxable — exist; verify `group`.
- import schema extraction — exists (`import_transactions`, `diff_statement`).
- Update `entityRegistry`/mentions if new entity fields surface (`account`, `transaction`).
- Every new tool follows the `@tool` convention; respects headless allowlist (writes NOT
  headless). Add a unit test per new/changed tool.

## 6. Lanes & DAG

```
BE  (backend models/services/api/db + ai/tools parity + tests)      ── parallel, independent
FE0 (port accounts.css + data.ts types/helpers + useAccountsLedger) ── parallel start
   └─ FE1 sidebar/overview/header   ┐
   └─ FE2 register/addrow/pickers   ├─ after FE0 (own distinct .tsx; read shared css/hooks)
   └─ FE3 drawer + modals + import  ┘
        └─ FE-INT AccountsTab orchestrator + router swap   ── after FE1/2/3 + BE contract
            └─ VERIFY (BLOCKING): code-review · qa-engineer(running app) · ui-designer(conformance)
               · security-review(import/money/describe) · pytest -m "not integration" · make lint
               · npm run build · make qa-e2e (touched IDs) · make ui-gate · screenshots
                  └─ REPORT: FEATURE_LOSS.md + divergence summary to owner
```

Work root for all lanes: **`/Users/owner/code/mnw-accounts`** (git worktree, branch
`feat/accounts-redesign`). Reference handoff at
`/Users/owner/code/mnw-accounts/frontend/design_handoffs/accounts_handoff/`.

## 7. Definition of Done (per the repo)

Lint + type + tests + import-linter green. New behavior tested. Docs + ADRs updated. No
secrets/PII/layering violations. UI clears the **UI Quality Gate** (`docs/UI_QUALITY_GATE.md`):
acceptance file in `docs/qa/acceptance/accounts.md`, `make qa-e2e` green for touched criteria,
`make ui-gate` clean, screenshots attached, empty/loading/error verified. `/code-review` +
`/security-review` (money/auth/external-I/O). Significant decisions → ADRs in `docs/adr/`.

## 8. Tracking docs (this folder)
- `FEATURE_LOSS.md` — every current-app feature the design omits (kept-but-flagged, or dropped).
- `DIVERGENCES.md` — any unavoidable deviation from the design + why (should be ~empty).
- `CONTRACT.md` — the frozen FE↔BE API/field contract (so FE & BE can build in parallel).
