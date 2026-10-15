# ADR — Accounts tab redesign: faithful port of the design handoff

- **Status**: Accepted
- **Date**: 2026-06-28
- **Agent provenance**: Decided by the orchestrator driving the Accounts-tab redesign from
  `frontend/design_handoffs/accounts_handoff/`, AFK-authorized by the owner ("re-implement the
  Accounts tab to match the design exactly, E2E, incl. backend + Lucius parity").

## Context

The handoff ships a complete, interactive **prototype** (its own React/babel app + the real
production `accounts.css`, 1397 lines) plus prose specs. The current app already has an Accounts
("Cash") tab — a 3,315-line monolithic `Cash.tsx` with its own sidebar/register/modals — backed by
a strong two-leg ledger (computed balances, cleared/working/reconciled, splits, FX, payees,
categories, rules, multi-format import w/ LLM schema). The owner's hard requirement: the shipped
look + behavior **exactly** match the design; do not "compromise" by claiming the current app
already covers it.

## Decisions

1. **Faithful port, not re-derive.** The prototype is the reference implementation. Translate its
   JSX → app React/TS, bring `accounts.css` across verbatim (re-registered under the CSS-class
   discipline), keep DOM/class structure identical so the result is pixel-identical. This is the
   only approach that guarantees zero design drift, and it is cheaper + lower-risk than rebuilding
   from prose on top of new generic primitives.
2. **Replace, in place.** New self-contained feature dir `frontend/src/components/tabs/accounts/`
   (mirrors `tabs/investments/`); the `/accounts` (+ `/accounts/payees`) route swaps from `Cash` to
   the new `AccountsTab`. Tab id `cash`/label "Accounts" kept to avoid nav churn. Legacy `Cash.tsx`
   stays until the new tab passes the verify gate, then is deleted (cleanup).
3. **Consume, don't rebuild** the app nav rail and the Ask-Lucius dock/side-panel — the handoff
   freezes these as out-of-scope. The prototype's `AppNav`/`LuciusBar`/`LuciusSidePanel` and the
   Tweaks panel are NOT ported; the existing `RouterShell` + `LuciusDock` host the tab.
4. **`institution` ≡ backend `platform`.** The design's institution (drives venue nesting) is the
   same concept as the custodian `platform` key already used by the Investments custodian model.
   Reuse `platform`; label it "Institution" in the UI; venue-nest by it. No new field.
5. **`band` ≡ `asset_class`.** The design's net-worth band reuses the existing `asset_class` override.
6. **New stored fields.** Account gains `connected`, `group` (sidebar group; client-derives default
   from type), `apr`, `principal`, `payoff_date`, `min_payment`, `apy`, `locked` — all metadata,
   default-empty, factory-coerced, no migration; they do not touch ledger math. Transaction gains
   `needs_review` (import/AI set true; approve clears, reject soft-deletes). Account `TYPES` extends
   to the design's full ~25-type set.
7. **Provenance/activity as a read endpoint.** `GET /api/transactions/{id}/activity` maps the
   existing `txn_audit.json` onto the drawer's Provenance + Activity timeline (best-effort; minimal
   synthesized trail when audit is sparse) — no new write surface, no new source of truth.
8. **Describe-an-account.** `POST /api/accounts/describe` + a `describe_account` Lucius tool turn free
   text into a structured **editable** form draft — populate-for-review, never auto-create.
9. **Lucius parity is a HARD gate.** Every UI mutation maps to a tool; the few gaps (needs-review
   approve/reject, move-to-account in bulk, describe-account, the new account fields/types) are added.
10. **Balances stay server-authoritative.** The prototype's client-side `applyBalance` is dropped;
    after every write the UI re-reads computed balances. Recompute base via `txn_base_amount()`.

## Consequences

- The page-05 "promote the bespoke pickers to DS primitives" ask is **not** done as separate DS
  components in this pass — the pickers are ported in-place to guarantee the exact match. Recorded as
  a follow-up in `FEATURE_LOSS.md`/`DIVERGENCES.md`, not a user-facing change.
- Legacy `Cash.tsx`, the Settings→Data CSV import panel, and the `/accounts/payees` route are
  reconciled against the design and dispositioned in `FEATURE_LOSS.md` (kept-and-flagged vs moved).
- Net worth, FX, and the ledger choke points are unchanged — the redesign is presentation + a thin
  set of additive backend fields/endpoints.
