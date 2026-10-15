# Launch Plan — Open Net Worth (Personal Wealth Manager v1)

> ⚠️ **SUPERSEDED — historical / reference only, NOT current truth (as of 2026-06-28).**
> The live source of truth is **[`docs/launch/BOARD.md`](launch/BOARD.md)** (the Hardening Loop).
> This plan optimistically marks several flows "launch-grade" that are **not yet trusted** — do not
> act on its statuses. Kept for its competitor-migration scope (§6) and feature inventory, which remain useful.

**Date:** 2026-06-15
**Goal:** Ship a polished, trustworthy, end-to-end **personal wealth manager** a non-technical user can onboard, load their data into, use daily, and export from — publicly.
**Method:** Grounded in a 6-lane codebase audit (onboarding, import/migration, export, every screen, stub/issue sweep, connectors). Each item cites its source.
**Definition of "launched":** A new user can (1) onboard + connect an AI key without hitting a dead end, (2) get their existing data in (connector OR import OR competitor migration), (3) use every advertised screen without silent failures, (4) export everything — and no PII ships in the public repo.

---

## 1. Headline: where we actually stand

**Strong / launch-grade today** (do not touch): the analytical engine — ledger, accounts, net worth, portfolio + FIFO lots + realized P&L, TWR/XIRR returns with benchmark, equity-comp vesting, YNAB planning, FIRE/Monte Carlo, the **file-import + dedup engine**, the **export/restore/checkpoint system**, all **8 bank/broker connectors** (sync depth verified), all **6 chat connectors**, and the AI/agent "staff" layer.

**The launch work is NOT new features** — it's: 2 safety blockers, first-run robustness, making data-in *discoverable*, and a trust pass on silent UI failures.

---

## 2. Complete inventory — features, screens & flows (with state)

Legend: ✅ launch-grade · 🟡 functional-but-needs-work · 🔴 blocker / broken / missing

### A. Onboarding & first-run
| Flow / screen | State | Note |
|---|---|---|
| Auth gate, dormant first-run, Login | ✅ | Loopback-open, remote-refused; clean |
| Wizard: Welcome / Password / Currency / Demo-or-empty / First account / Done | ✅ | Real 7-step wizard, skippable, demo branch wired |
| Wizard: **Connect AI** step | 🔴 | `submitAi()` ignores `res.ok`, never calls existing `/validate` → a bad API key silently "succeeds"; user's first Lucius message fails with no cause (`OnboardingWizard.tsx` ~L201-231) |
| **First-run missing-key detection** (if wizard skipped) | 🔴 | No banner/modal routes a keyless user to AI setup; they hit "no models available" with no path (`Settings.tsx` AI section exists but undiscoverable) |
| FIRE / retirement surfaced in onboarding | 🟡 | The headline differentiator is never nudged on first run |
| Demo mode + "clear & start fresh" | ✅ | Idempotent, reversible |
| Empty states (Overview, Cash, Commitments, Intel, etc.) | ✅ | Polished, with CTAs |
| **Investments empty state** | 🔴 | Zero-holdings user sees "No holdings match your filters" (misleading) + no "add first holding" CTA (`Investments.tsx:390`) |
| Empty-state polish: Planning / CashFlow / Tax | 🟡 | Bare tables/spinners before guidance |

### B. Getting data IN — connectors, import, migration
| Flow | State | Note |
|---|---|---|
| 8 bank/broker connectors (SimpleFIN, Plaid, Enable Banking, Pluggy, Yodlee, IBKR, Alpaca, GoCardless) | ✅* | Full sync depth verified; *3 caveats below |
| Enable Banking / Pluggy / Yodlee built against **unconfirmed API shapes** | 🔴 | `# TODO(provider)` on field names/pagination; could silently mis-sync. Gate as "experimental" or hold from launch set |
| GoCardless | 🟡 | Provider paused new signups in 2025 — hide or label "Legacy" |
| File import: CSV, OFX/QFX, QIF, MT940, CAMT, XLSX | ✅ | Robust parse → preview → dedup → commit; the real strength |
| File import: PDF statement | 🟡 | Silent 14k-char truncation; no OCR for scanned PDFs; chat-only |
| **Import discoverability** | 🔴 | The working upload UI is buried 3 clicks deep in Settings→Data, no nav entry, no CTA — a normal user won't find it |
| **Guided competitor migration** (YNAB / Monarch / Actual Budget) | 🔴 | None today. All three are CSV-exportable and map onto the existing engine; two share our YNAB-style budget model — see §6 |
| Statement reconcile / Email-to-txn / Equity import | 🟡 | All **chat-only**; no non-chat upload+review UI. Email ingest commits without a review queue + skips attachments |
| Dedup / match-review engine | ✅ | Won't double-book; conservative; full UI. Gap: import result doesn't link to the pending-review count |
| Manual account add + institution picker | ✅ | Search-first, never dead-ends |

### C. Core app screens (15 tabs)
| Tab | State | Note |
|---|---|---|
| Cash Flow, Tax, Agents, Inbox, Connectors, Plan | ✅ | All three states + retry; reference-grade |
| Overview, Cash (Accounts), Investments, Intel, Commitments, Profile | 🟡 | **Silent fetch failures** — `.catch(()=>{})` / no error UI → a failed load looks like "no data" (trust-breaking for a wealth app) |
| Skills | 🟡 | No empty state at 0 skills (blank tree) |
| Settings (Data + Dedup panels) | 🟡 | Export/import no loading state; raw error text not `ErrorState` |
| `intel/AnalystConsensus`, `intel/QuoteStats`, `AssetPriceChart` | 🔴 | No loading/error states — slow/failed fetch shows nothing |
| `tabs/Targets.tsx` (649 lines) | 🔴 | **Dead code** — rendered nowhere. Wire in or delete |
| Saved-scenarios "+ Save current" button | 🔴 | **Dead `console.log` stub**, no `/api/scenarios` endpoint — looks functional, isn't (`ProjectionsBranch.tsx:239`, `RetirementBranch.tsx:486`) |

### D. Getting data OUT — export & portability
| Flow | State | Note |
|---|---|---|
| Full `.zip` bundle export (19 stores + manifest + SHA256) | ✅ | Production-ready, CSRF-guarded, in Settings |
| Import / restore with undo checkpoint | ✅ | Atomic, byte-identical round-trip tested |
| Turn checkpoints (rollback any Lucius edit) | ✅ | 50-deep, data/conversation/both |
| Report downloads (5 types, HTML/JSON) | ✅ | Printable |
| Help text for intentionally-excluded data (keys, run history) | 🟡 | Add a one-line tooltip so self-hosters understand restore scope |

### E. Cross-cutting / safety
| Item | State | Note |
|---|---|---|
| **Git history PII scrub** | 🔴 | Real personal data + bank statements in **prior commits** (HEAD is clean). MUST scrub before public (DATA-4) |
| **Nonce-binding on interactive `apply_*`** | 🔴 | Propose→apply trusts conversational confirmation, not a server nonce — money-adjacent re-apply hole (`KNOWN_ISSUES.md:9`) |
| Secret discipline (write-only, chmod 600, gitignored) | ✅ | Excellent across all connectors |
| Light mode | 🟡 | Dark-only by design — a **product decision** to confirm, not a defect |
| Oversized files (Cash 3179, Intel 1338, Settings 1242…) | 🟡 | Maintainability; post-launch |

---

## 3. The 7-day plan

> Assumes parallel execution (this repo is built for multi-agent lanes). Each UI lane clears the UI Quality Gate (acceptance file → `make qa-e2e` → `make ui-gate` → screenshots). The error-state sweep is scoped to the **highest-trust screens first**, not every screen — full polish continues post-launch.
>
> **Sequencing note (safety blockers go last, deliberately):** Build/polish work runs first; the safety bucket lands at the end. The **git history scrub is irreversible and must be the final step** — you rewrite history *once*, after every commit exists, right before publishing. The **nonce-binding** is a code change, so it sits on Day 6 *with* the QA gate that verifies it. Only the scrub + publish are truly dead-last (Day 7).

### Day 1 — First-run you can't fail
- **Fix the AI-key step**: check `res.ok`, call `/validate`, show a clear error + retry. *[M]*
- **Keyless-user detection**: a banner/modal that routes anyone without a working AI key to setup (covers wizard-skip). *[M]*
- **FIRE/retirement nudge** on the Done step (optional, dismissible). *[S]*

### Day 2 — Make "get my data in" obvious
- **Surface Import** as a first-class entry (top-level or prominent empty-state CTAs on Overview/Accounts/Investments). *[S–M]*
- **Competitor migration (top 3: YNAB · Monarch · Actual Budget)**: named CSV presets that deterministically map each app's export (transactions + accounts + categories) onto the existing import engine, plus a "switch from your old app" onboarding branch. Full **envelope-budget assignment** migration (YNAB/Actual monthly *assigned* amounts → the planning model) is a fast-follow, not launch-blocking. See §6 for per-app scope. *[M]*
- **Link import result → pending dedup-review count** so users actually clean duplicates. *[S]*

### Day 3 — Non-chat import surfaces + Investments
- **Statement upload + reconcile UI** (non-chat) reusing the deterministic cycle/balance math. *[M]*
- **Equity import UI** (non-chat) for the 8B path + manual-entry polish. *[M]*
- **Investments empty state**: branch true-zero vs filtered-zero + "add your first holding / ask Lucius" CTA. *[S]*

### Day 4 — Trust pass: kill silent failures
- **Wrap every silent `.catch(()=>{})`** with `ErrorState` + retry across Overview, Cash, Investments, Intel, Commitments, Profile. *[batch of S]*
- **Fix the 🔴 intel cards** (AnalystConsensus, QuoteStats, AssetPriceChart) — loading + error states. *[S]*
- **Profile loading state; Skills empty state; Planning/CashFlow/Tax** empty-state polish. *[S]*

### Day 5 — Remove the lies (dead/stub code) + export polish
- **Saved-scenarios**: hide the dead "+ Save current" button + Retirement "plan B" (or build `/api/scenarios` if Day-5 capacity allows). *[S hide / M build]*
- **`Targets.tsx`**: wire in or delete. *[S]*
- **Export**: excluded-data help tooltip + verify export→restore round-trip in the running app. *[S]*
- **Connector polish**: official brand SVGs + credential-help hints for Yodlee/Alpaca. *[S]*

### Day 6 — Safety code changes + full QA gate
- **Nonce-bind interactive `apply_*`** (positions/lots/equity/income propose→apply) — extend the connector-confirm nonce so a stale payload can't re-apply. *[M]*
- **Gate the 3 unverified aggregators** (Enable Banking, Pluggy, Yodlee) behind an "experimental — verify before relying" label, or hold from the launch set. **Hide/label GoCardless** "Legacy." *[S]*
- `make check` (lint/type/test/build) green + **`make qa-e2e`** for all touched acceptance IDs + **`make ui-gate`** clean + screenshot evidence. *[M]*
- **End-to-end smoke**: onboarding → AI key → import (each format + one competitor preset) → use every core tab → export → restore. *[M]*

### Day 7 — Irreversible safety step + publish
- **Scrub git history** of PII/bank statements (filter-repo/BFG), verify clean (no PII in any commit). *[M]*
- **Re-run `make check`** post-scrub (history rewrite shouldn't change the tree, but confirm), final **responsive pass** (640/1024/1440) per tab. *[S]*
- **Tag the release and publish.** Buffer for fallout. *[S]*

---

## 4. Post-launch backlog (the "remaining")

**Personal-family-office vision** (from `docs/archive/planning-2026-06/FAMILY_OFFICE_GAP_AUDIT.md`): household/shared finances → document vault → insurance tracking → estate basics → goals-based framing → the "staff" experience.

**Import depth:** email-ingest review queue + attachment handling; OCR for scanned PDFs; investment-transaction CSV/QIF (buys/sells/dividends); US broker/equity-comp importers (Schwab, Fidelity, E*Trade, Carta, Shareworks); EU semicolon/tab CSV + multi-sheet XLSX.

**Planning/analytics:** full `/api/scenarios` persistence; retirement inflation indexing; tax bracket currency conversion (`tax.py:1181`); performance attribution + risk stats (Sharpe/vol/drawdown).

**Platform:** CSV row-level export; scheduled auto-backups; AI tools for export/import; light mode (product decision); oversized-file refactors (Cash → split first); Lucius↔UI parity gaps (intel scan trigger, IMAP config, asset_class set); per-connector error hints; validate the 3 experimental aggregators against sandbox and graduate them.

---

## 5. Realism note

"Complete everything in 7 days" is achievable **for the launch-blocking + high-impact set above**, run as parallel lanes. The one item that can overrun is the **silent-failure error-state sweep** (a full pass across all screens was estimated at 3–5 days alone) — this plan deliberately scopes Day 4 to the highest-trust screens and pushes exhaustive polish to post-launch.

---

## 6. Competitor migration scope — YNAB · Monarch · Actual Budget

All three export CSV, so all three ride the existing `import_csv.py` engine (auto-mapping, inflow/outflow, dedup, rules). The work is a **deterministic per-app preset** (recognize the exact header set → map without guessing) plus three shared mapping concerns. Two of the three (YNAB, Actual) use the *same envelope-budget model we already have*, so they migrate with unusually high fidelity.

| App | Export shape | Migrates at launch | Fidelity | Notes |
|---|---|---|---|---|
| **YNAB** | "Export budget" → ZIP with **Register.csv** (Account, Date, Payee, Category Group/Category, Memo, Outflow, Inflow, Cleared) + **Budget.csv** (Month, Category, Budgeted/Activity/Available) | Transactions + accounts + categories/groups | ★★★ Highest | Same envelope model as us. Transfers are payee `Transfer : <account>` → must become **two-leg transfers**. "Starting Balance" inflows → account `initial_balance`. *Budget.csv → monthly assignments = the fast-follow.* |
| **Monarch** | Transactions CSV (Date, Merchant, Category, Account, Original Statement, Notes, **signed Amount**, Tags) | Transactions + accounts + categories + tags | ★★★ High | Maps cleanly (Merchant→payee, signed amount, Tags→tags, Notes→memo). Monarch budgets flexibly (no envelopes) → **no budget assignments to migrate**, so txns are the whole story. |
| **Actual Budget** | Per-account **CSV export** (Date, Payee, Notes, Category, Amount) *or* full native **ZIP backup (SQLite)** | Transactions + accounts + categories (via CSV) | ★★★ High (CSV) | Open-source, YNAB-style envelopes. Launch via per-account CSV. The SQLite backup (full categories + budgets + rules in one shot) is a **post-launch deep importer**. |

**Three shared mapping concerns (build once, reuse across all presets):**
1. **Account mapping** — the `Account` column → map to an existing ON account or auto-create it (don't dump everything into one account).
2. **Category mapping** — competitor category/group → ON category/group, auto-creating missing ones (clean for YNAB/Actual's hierarchy; flatter for Monarch).
3. **Transfer detection** — recognize transfer rows and build ON's required two-leg `from`/`to` transfer (most relevant for YNAB).

**Recommendation:** Ship **transactions + accounts + categories** presets for all three on Day 2 (S–M each on the existing engine — fits the plan). Ship **YNAB envelope-budget assignment migration** as the **first fast-follow** — YNAB switchers are the highest-intent audience and it's where "it feels like home" pays off most. Actual's full SQLite importer and Monarch (no budget to port) need nothing further for budgets. If the AI-key fix, the git scrub, the nonce-binding, import discoverability, and the trust pass land, the product is genuinely ship-ready as a personal wealth manager.
