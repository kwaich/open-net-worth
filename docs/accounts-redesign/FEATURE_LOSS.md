# Feature-loss & divergence report — Accounts redesign

Reconciliation of the **current app** (legacy `Cash.tsx` + related panels) against the **design
handoff**. The owner asked: "if there are existing features not supported in the design, note them
down." Disposition legend:
- **KEPT** — design has an equivalent (often richer); no loss.
- **KEPT+ENHANCED** — design is a strict superset of the legacy feature.
- **MOVED** — capability survives but its entry point changed per the design.
- **KEPT+FLAGGED → OWNER DECISION** — not in the design; we keep it working but you should decide
  whether to retire it for one canonical flow.
- **DROPPED → OWNER DECISION** — the design's surface supersedes it; capability still reachable
  elsewhere (Lucius / Automations). Confirm this is acceptable.

## Net-new in the design (gains, not losses)
Describe-an-account to Lucius · method-choice add flow · full ~25 account-type picker with aliases ·
type-conditional account fields (APR/principal/payoff/min-payment, APY projection, locked toggle) ·
venue nesting (≥2 same-institution) · needs-review **Approve/Reject** in the drawer · **Provenance +
Activity timeline** · rich inline pickers (account/date/payee/category) · bulk **Move-to-account /
Duplicate / Note / Export** · transaction **CSV export** · YNAB-style search tokens · sidebar
responsive auto-collapse.

## Reconciliation table
| # | Current-app feature | In design? | Disposition | Note |
|---|---|---|---|---|
| 1 | Account sidebar + platform/venue grouping | yes | **KEPT** | design = venue nesting ≥2 + fixed group order |
| 2 | Register inline cell edit | yes | **KEPT+ENHANCED** | design adds rich pickers + sign-aware amount edit |
| 3 | Bulk ops (categorize / cleared / delete) | yes | **KEPT+ENHANCED** | design adds Move-to-account, Duplicate, Note, Export |
| 4 | Split transactions | yes | **KEPT** | view + expand identical |
| 5 | Reconcile popover | yes | **KEPT** | same Yes/No → adjustment flow |
| 6 | Account add/edit modal (kind radio, band, platform, currency, balance, asOf, last4, color, note, credit fields) | yes | **KEPT+ENHANCED** | design derives kind from type (no radio); adds full type set + type-conditional fields + describe-to-Lucius |
| 7 | Transaction add/edit (`TransactionForm.tsx`) incl. FX, transfers, taxable | yes | **KEPT** | design = inline add row + drawer editor; same model |
| 8 | **PayeesPanel as a routed page** (`/accounts/payees`) | as a **modal** | **MOVED** | design = "Manage payees" modal from the header; the `/accounts/payees` route now opens that modal |
| 9 | **CSV/statement import in Settings → Data** (`CsvImportPanel`) | as an **Accounts ImportModal** | **KEPT+FLAGGED → OWNER DECISION** | design surfaces Import from the Accounts header (new `ImportModal`, same backend `/api/import`). The legacy Settings→Data panel still exists. **Decision:** keep it as a secondary entry, or remove for one canonical import flow? |
| 10 | **Inline "Create categorization rule from this transaction"** affordance in the legacy register | **not in the design register** | **DROPPED → OWNER DECISION** | the design's register has inline edit + bulk but no per-row "create rule" button. Rule creation remains available via the Automations/Rules editor and via Lucius (`create_rule`/`get_rule_suggestions`). **Decision:** acceptable to drop from the register? |
| 11 | RulesEditor (Automations tab) | separate surface | **KEPT** | untouched; design only references "apply rules to history" inside Manage payees |
| 12 | CategorySuggestions (Automations) | not in design | **KEPT** | separate surface, unaffected |
| 13 | **Mobile filter drawer** in legacy `Cash.tsx` (narrow-screen filters) | design's responsive story = **sidebar auto-collapse** | **FLAGGED → OWNER DECISION** | the design (a desktop prototype) specifies sidebar auto-collapse + resizable columns, not a dedicated mobile filter drawer. On very narrow viewports the register/filter ergonomics follow the design, which is less mobile-optimized than the legacy drawer. **Decision:** is desktop-first acceptable for v1, or do we need a mobile pass? |
| 14 | Account "kind" explicit asset/liability radio | derived from type | **DROPPED (intentional)** | design derives kind/group/band from the chosen type — fewer choices, matches the design's "only type is picked" principle |

## Design-asks NOT implemented in this pass (engineering follow-ups, not user-facing)
- **Handoff page 05 — promote the bespoke pickers to DS primitives.** To guarantee a pixel-exact
  match we ported the prototype's pickers in-place (in `tabs/accounts/pickers.tsx` + `ds.tsx`) rather
  than rebuilding them as generic `ui/` components. The look/behavior match the design; the internal
  DS-promotion refactor is a follow-up. (Recorded in the ADR.)

## DIVERGENCES from the prototype (should be ~none; filled during verify)
- Lucius dock/side-panel + app nav rail: consumed from the existing app shell (handoff-sanctioned),
  not ported from the prototype.
- Balances are server-authoritative (prototype applied them client-side) — no visible difference.
- **Correction (2026-07-02, owner-flagged)**: row 7 above previously claimed "splits" were part of the
  KEPT transaction add/edit capability — that was wrong. Split-transaction **creation/editing has
  never been a human-operable control**, in this build OR in the original design prototype
  (`design_handoffs/accounts_handoff/prototype/accounts/modals.jsx`'s `TxnModal` seeds a `split: false`
  form field but never wires it to any toggle/multi-line editor — dead scaffolding, not a working
  feature). Row 4's "KEPT" is accurate but narrower than it reads in isolation: only READ-ONLY split
  *display* (the "Split" chip + per-line expand in the register/drawer) is kept; there is no way for a
  human to create or edit a split from the UI. The backend/AI surface fully supports it
  (`add_transaction`/`update_transaction` tools, `POST/PUT /api/transactions`'s `splits` param) — only
  a human-facing control was never built. Tracked in `docs/launch/BOARD.md`'s Accounts-tab
  "Owner-decision items" list for a proper design pass.
- _(verify lane appends any pixel/behavior deltas found)_
