# Codebase Cleanup Register — 2026-06-20

Source: a 7-dimension read-only audit (legacy/back-compat, backend dead code, frontend
dead code, docs health, structure/size, deps) + a backend coverage baseline. **67 findings**.
Single-user context (one owner) makes back-compat/migration shims strong removal candidates.

**Tiers:** `safe` = provably unused/mechanical · `care` = needs a data/runtime check or a test ·
`human` = needs an owner decision (intentional design, or risky).

**Execution rule:** small commits, each gated by `make check` (or targeted tests + import
smoke), pushed. VERIFY every "unused" claim with a fresh grep before deleting.

**Coverage baseline:** backend **81.7%** (6144 tests). Target 90%+ ⇒ ~3,600 more covered
statements across 58 modules under 75%. This is a multi-session effort; see §Coverage.

Status legend: ☐ pending · ⏳ in progress · ✅ done (commit) · ⏸ deferred (owner decision)

---

## Tier 1 — SAFE removals (execute first)

> ⚠️ **Audit false-positive rate on backend "dead code" was ~60%.** Every "unused" claim
> below was re-verified by grep before acting. Items struck through were FALSE POSITIVES —
> the code is live; **do not remove**.

### Backend dead code
- ❌ ~~`_gocardless_has_creds()`~~ — FALSE POSITIVE: live via `_HAS_CREDS` dict. KEEP.
- ❌ ~~`_enable_banking_has_creds()`~~ — FALSE POSITIVE: live via `_HAS_CREDS`. KEEP.
- ❌ ~~`_provider_configured()`~~ — FALSE POSITIVE: called 4× (account_linking.py:142/154/176/182). KEEP.
- ❌ ~~`_search_per_bank()`~~ — FALSE POSITIVE: called at account_linking.py:228. KEEP.
- ✅ `playbook_tool_names()` — `services/_playbook.py` — removed (commit `0a0a794f`, verified zero callers).

### Backend legacy shims
- ❌ ~~`request_tools` shim~~ — KEEP: `tests/test_cost_scoping.py` enforces it stays a searchable shim.
- ❌ ~~`version_dir` + `list_versions`~~ — FALSE POSITIVE: used by `services/skills.py:106` + ~6 test files. KEEP.
- ❌ ~~skill_bundles re-export shim~~ — FALSE POSITIVE: feeds `api/skills.py:383/451`. KEEP.
- ☐ scheduler `reschedule_<provider>()` wrappers ×4 — `scheduler.py:283-304` — USED by 4 per-provider API routes; a call-site refactor to `reschedule_source`, not a deletion → CARE (deferred).
- ✅ `MENTION_TOKEN_RE` unused @deprecated const — `frontend/src/types/mentions.ts` — removed (commit `7bf3bc94`).

### Frontend dead components (zero importers — verified)
- ✅ `Targets.tsx` — removed (commit `0a0a794f`).
- ✅ `StoryTimeline.tsx` — removed (commit `0a0a794f`).
- ✅ `NetWorthHistoryChart.tsx` — removed (commit `0a0a794f`).

### Unused dependencies (verified)
- ✅ `@dnd-kit/modifiers` — removed from `frontend/package.json` (commit `7bf3bc94`).
- ✅ `tavily-python` — removed from `requirements.txt` (commit `d92f888e`; httpx client used instead).

### Docs (safe)
- ☐ Root `KNOWN_ISSUES.md` (4.6KB, Jun 15) vs `docs/archive/run-logs/known_issues.md` (10.8KB, Jun 20; archived DH2) — CARE: content-merge first (root has 5 issues, docs has 3 active + 3 archived; confirm no issue is lost before deleting root).
- ☐ Add cross-refs: `skill-format.md` ↔ CLAUDE.md §AI tools; `FRONTEND_CONVENTIONS.md` → CLAUDE.md Docs map

### Observations surfaced during the coverage pass (not auto-changed)
- ☐ `services/reconcile.py:86-87` — unreachable `except ValueError` (the regex pre-strips all non-numeric chars). Dead defensive branch; safe to drop.
- ☐ `get_yodlee_status` is absent from `HEADLESS_ALLOWED_TOOLS` while the 5 sibling `get_*_status` connector tools are present — likely an accidental omission (owner decision; behavioral).

---

## Tier 2 — CARE (needs a data/runtime check before removing)

### Store migrations (one-time, persist-once; check the owner's live data has no legacy shape)
- ☐ `ModelConfigStore._migrate_legacy_api_key` — `db/model_config_store.py:189-244` (top-level `api_key` → providers map)
- ☐ `SearchConfigStore._migrate_legacy` — `db/search_config_store.py:220-251` (single→multi-provider)
- ☐ `EmailConfigStore._migrate_legacy_cursor` — `db/email_config_store.py:122-145` (flat IMAP cursor → per-consumer)
- ☐ `'opening'` synthetic lot kind — `models/lot.py:25` (keep enum; remove creator only if no `opening` rows exist)
- ☐ legacy `etf` asset-class collapse — `db/instrument_store.py:129-130` (remove only if no `asset_class='etf'` rows)
- ☐ legacy source-format coercion — `services/asset_detail.py:458-494` (remove only if all briefs are new-shape)

### Frontend localStorage / format migrations (check owner data, then drop)
- ☐ chat-panel-width px→fraction fallback — `components/chat/ChatPanel.tsx:44-50`
- ☐ `migrateLocalStorageHidden` (plan-hidden-cats → backend) — `hooks/useBudget.ts:190-214`
- ☐ agent bare-id → `provider:id` map — `components/agents/AgentEditor.tsx:133-139`
- ☐ tax `rate_source` fallback — `components/tabs/tax/TaxRateModal.tsx:79-86`

### Docs moves / reconciliation
- ✅ Archived `docs/parity-audit.md` → `docs/archive/reviews/` (DH2)
- ☐ Archive `docs/ENGINEERING_GUIDE.md` (planning artifact superseded by CLAUDE.md)
- ☐ Move `docs/superpowers/specs/*` → `docs/design/` + add to CLAUDE.md Docs map
- ☐ Verify `docs/agentskills-compliance.md` still current; annotate
- ☐ Add `docs/architecture/README.md` hub; relocate orphaned visual companions

### Structure / size refactors (split candidates; behavior-preserving, tests gate)
- ☐ `assistant.py` `run()` 791L → extract session/stream/tool-loop/metrics — `ai/assistant.py:1412-2202` (L)
- ☐ `Cash.tsx` 3197L → TransactionFilters / ReconcileUI / CustodianFields (L)
- ☐ `ChangePreview.tsx` 1504L → PreviewFrame + per-type registry (L)
- ☐ `tax.estimate_taxes()` 308L → rate-source + components helpers (M)
- ☐ `ledger.merge_transactions()` 195L / `create_transaction()` 133L → step helpers (M)
- ☐ `Settings.tsx` 1246L → per-section files (M, low risk)
- ☐ `BudgetTable.tsx` 1217L → MonthNav / BudgetCellEditor / BudgetGrid (M)
- ☐ `RulesEditor.tsx` 1232L → RuleTestUI / RuleForm / RulesList (M)
- ☐ `asset_brief._do_refresh()` 184L → search/fetch modules (M)
- ☐ `monitoring/intel_agent.py` orchestration vs synthesis split (M)

---

## Tier 3 — HUMAN (owner decision; do NOT auto-change)

- ⏸ ChatPanel plain-string prefill path — intentional (chips additive). Keep.
- ⏸ Router/Investments/CountryPicker fail-safe fallbacks — intentional guardrails. Keep.
- ⏸ `TransactionForm` "back-compat by absence" splits — intentional REST idiom. Keep.
- ⏸ `python-multipart`, `tzdata` — runtime-only (file upload, zoneinfo); REMOVING BREAKS THINGS. Keep.
- ⏸ `stylelint-config-standard` — config-only dep. Keep.
- ⏸ TODO markers: brand-kit logos (`connectors/logos.tsx`), scenarios/inflation (`RetirementBranch.tsx`) — future work.
- ⏸ Docs map: place `LAUNCH_PLAN.md` / `FAMILY_OFFICE_READINESS_AUDIT.md` / `USAGE_AUDIT_2026-06.md`; finish truncated `LAUNCH_PLAN §2`; reconcile USAGE_AUDIT "REFUTED/FIXED" claims vs `known_issues.md`.
- ⏸ `connectors.py` (1245L) / `ledger.py` (1685L) registry/boundary refactors — large, architectural; owner call.
- ⏸ `ORCHESTRATION_PLAYBOOK.md` — template vs local-ref decision.

---

## Coverage (§ toward 90%)

Baseline **81.7%** → **83.8%** after this pass (+2.1 pts; ~578 new tests, all hermetic).

**Done (10 modules raised, 2 batches):**

| Module | Before → After |
|---|---|
| `services/fire.py` | 15% → 100% |
| `services/reconcile.py` | 26% → 82% |
| `api/instruments.py` | 25% → 100% |
| `ai/tools/aggregation.py` | 46% → 100% |
| `services/payee_registry.py` | 49% → 97% |
| `ai/tools/payees.py` | 33% → 100% |
| `ai/tools/memory.py` | 39% → 100% |
| `api/payees.py` | 47% → 98% |
| `api/briefing.py` | 35% → 96% |
| `ai/tools/equity.py` | 60% → 100% |

**Remaining path to 90%** (≈ +3,000 covered statements over ~83.8%). The next pure/API
targets (good ROI): `services/instruments.py` (62%), `services/planning.py`, `api/actions_api.py`,
`ai/tools/profile.py`, `core/logging.py`, `api/email_ingest_api.py`. Repeat the same
parallel test-writer pattern.

**Not unit-test targets** (need integration tests or a coverage omit — these dominate the
remaining gap and should NOT be brute-force unit-mocked):
- `scripts/*` — one-off migration/eval tools. **Recommend `[tool.coverage.run] omit` for `backend/scripts/`** so the metric reflects shippable code (raises the effective % materially).
- `connectors/telegram|slack|chat/*`, `ingest/*` — network I/O; belong in `@pytest.mark.integration`.
- `ai/batch/*` — Anthropic/OpenAI batch APIs; integration-tier.
- `main.py`, `scheduler.py` — bootstrap/scheduler wiring; partial via app-startup smoke.

> Reaching a literal 90% on the FULL tree is a multi-session continuation. The high-value
> core-logic portion is the 10 modules above + the "remaining path" list; the I/O layers
> are an integration-test effort, not a unit-mock vanity push.

---

## Follow-up — 2026-09-07

- ⏸ **Font-size tokens not adopted in per-tab "verbatim port" stylesheets** — the app has a
  real type-scale token set (`--text-2xs` 10px … `--text-display-xl` 44px,
  `frontend/src/design/tokens.source.mjs`), but `frontend/src/components/tabs/assets/assets.css`
  (52 raw `font-size` px values, 0 token usages) and
  `frontend/src/components/tabs/investments/design.css` (189 raw, 10 token usages) — both
  faithful ports of their own standalone design handoffs, predating token adoption for type
  scale — use hardcoded pixel values almost everywhere instead. Noticed while reconciling a
  visual-sizing inconsistency between the two tabs' card titles/rows (fixed in
  `fix/assets-tab-polish`-adjacent commits). Migrating either file to tokens is a real,
  separate cleanup — dozens of values each — owner call on priority; not done as part of
  that fix.

## Follow-up — 2026-10-06

- ⏸ **Duplicate-review queue looks unused — candidate for removal.** The owner's real book
  (cloud agent, 2,453 transactions) and the local book both have 0 `match_reviews` rows and
  0 `match_decisions` ever. Yet every transaction create still runs
  `match_review.review_created_rows` (UI, Lucius ledger tools, imports, statements), and the
  Inbox shows a "Duplicates" section + badge. Already cut: the badge's 30s poll (it reloaded
  the whole ledger for an empty queue — ~1.5 s CPU/min on a 2 GB Lightsail box) and
  `list_reviews` now returns early when nothing is pending. Full removal would cover:
  `services/match_review.py` queue paths, `db/match_review_store.py` (+ a migration dropping
  `match_reviews`), `api/matches.py` review routes, `frontend/.../tabs/dedup/` + the Inbox
  section/badge, and the queue-acting Lucius tools in `ai/tools/matching.py` (tool names are
  FINAL — removal goes through `backend/scripts/tool_renames.py` and an ADR). Keep the manual
  merge / `scan_duplicates` paths unless they are also judged unused. Owner call.
