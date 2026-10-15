# Autonomous-run guardrails (READ FIRST)

Read this before and while running any unattended/agent build (Phase work, workflows).
It exists because real problems bit us — each rule below is a scar, not a theory.
Companion to docs/review/TRIAGE.md (what to build) and
[CLAUDE.md](../CLAUDE.md) (how to build). The user **runs the live app while agents work** —
their data and uncommitted edits must survive every agent action.

## §1 — Git & data safety (the bug that wiped live data)

**What happened:** the user added/edited transactions in the running app while agents worked;
a gated-workflow **auto-revert ran `git reset --hard HEAD`**, which reverted the *tracked*
`backend/data/*.json` files and **destroyed the live edits** (reflog: two `reset: moving to HEAD`).

**Now fixed two ways:** (1) personal data is gitignored (commit `2d054f8`); (2) the user's live
data lives at an **external `DATA_DIR` outside the repo** (`~/.open-net-worth/data`, set in `.env`),
so **no git operation on any branch can touch it** — `backend/data/` in the repo is NOT the live
source of truth anymore. The process rules below are still mandatory: they protect the user's
uncommitted **code** WIP and keep `backend/data` from ever being re-tracked.

- **NEVER `git checkout <branch>` / `git switch` in the user's MAIN checkout.** `main` and
  `feature/go-public` still **track** `backend/data/*.json`; switching the main working tree to
  them would **overwrite the live (now-ignored) data files** — git clobbers *ignored* files on
  checkout without the "untracked file would be overwritten" guard. The main checkout stays pinned
  to `auto/phase-2-fundamentals` (where data is untracked). **All other-branch work happens in a
  separate `git worktree`** (its own directory; its checkouts/resets can't touch the main tree).
- **NEVER run repo-wide destructive git ops** in an agent/workflow: no `git reset --hard`,
  no `git checkout .` / `git checkout -- .`, no `git stash` (its drop loses work),
  no `git clean -fd`, no `git restore .`. They erase uncommitted work indiscriminately.
- **Auto-revert on a failed gate = revert ONLY the paths this task itself changed**, by explicit
  path (`git restore --source=HEAD --staged --worktree -- <file1> <file2>` or
  `git checkout -- <those exact files>`). Compute the path set from *this task's own* additions/
  edits — never a wildcard, never "everything dirty."
- **Never stage, commit, revert, or clean `backend/data/**`.** It's the live app's state. It's
  gitignored now — keep it that way; do not `git add -f` it back.
- **`git add <explicit paths>` only.** Never `git add -A`, `git add -u`, or `git add .`.
- **Back up first:** at the start of a run, snapshot `backend/data/` → `backend/data/backups/<ts>/`
  (gitignored). Cheap insurance if anything slips.
- **Preserve user WIP:** uncommitted changes you didn't make may be the user's (e.g. `targets.py`,
  the review checklist). Don't revert/stash them; if they block you, commit them *separately*
  with no co-author line, or work around them. See [[feedback_dont_discard_user_wip]].
- **Prefer worktree isolation** (`isolation: "worktree"`) for parallel file-mutating agents so they
  can't collide in — or with — the user's main checkout where the app runs.
- **HANG RULES (carry forward):** never start a foreground server/dev/build in an agent loop;
  alarm-guard import smoke (25s `signal.alarm`); fast gate is `pytest -m "not integration"`;
  macOS has no `timeout`.

## §2 — Don't re-introduce the review findings

The Phase-2 review (docs/review/TRIAGE.md) found recurring mistakes.
When you build, honor these so agents don't repeat them:

**Lucius / AI behavior (the biggest theme — MEM-1, SP1-2, INV1-1):**
- The **data stores are ground truth** (ledger, lots, realized events, positions, rules, accounts).
  On any conflict with memory or a narrative, the records win — say so, never invent a reconciliation.
- **Never memorize a proposed/discussed action as completed.** A plan is "user *intends* to…", not history.
- **Re-read state before explaining surprising behavior** (`list_rules`, `get_*`) — don't theorize a mechanism.
- Rules run on **every** transaction-create path incl. manual `add_transaction` — never claim otherwise.
- **The layered permission system is GONE — do not restore it** (owner decision, 2026-07-18;
  [`docs/adr/2026-07-18-remove-layered-ai-permission-system.md`](../docs/adr/2026-07-18-remove-layered-ai-permission-system.md)).
  This bullet used to read *"money tools stay propose→confirm→apply; a headless/scheduled turn can
  never auto-write money (`HEADLESS_ALLOWED_TOOLS`) — keep that structural guard."* **That guard no
  longer exists and must not be rebuilt.** Removed: the T0/T1/T2 tiers (`core/agent_caps.py`), the
  headless ceiling (`core/headless_scope.py`), the three per-turn `_skill_scope` narrowings
  (headless/skill/responder), the dispatch-time default-deny block, propose→wait→apply *as an
  enforcement mechanism*, per-agent auto-apply grants, and the inbound MCP tier ladder. A scheduled
  task, an agent run, and an inbound MCP call all see the same tool surface as an interactive turn,
  and may write directly. The reasoning: the app **records** money rather than moving it (no trades,
  no transfers, no payment rails), every write is checkpoint-reversible, and the tier lines were
  guessed rather than measured — twice at the cost of real capability (see
  `.claude/rfcs/headless-proposal-pipeline.md`'s 2026-06-12 incident, and
  `docs/adr/2026-07-12-payee-config-writes-grantable-t1.md`).
- **What to do instead of a permission check.** Safety on a write is now *reversibility +
  attributability*, not *reachability*: (1) never bypass a write choke point — every
  `JsonStore._save_raw()` fires the `core.io` write hook → checkpoint snapshot, and that is the
  recovery path; (2) keep the write attributable (`txn_audit.json` via `services/audit.py`,
  `run_history.json` via `services/run_ledger.record_run()`); (3) keep it legible — a money-shaped
  change still renders a `ChangePreview` confirm card showing **every** field it will set (the FE-4
  rule below still binds), because the card is now a UX affordance rather than a gate.
- **These are NOT permission tiering — do not sweep them while "finishing the teardown":** Bearer
  auth on the inbound MCP server (authentication, not authorization); the nonce-staged confirm that
  fires **server-STORED** args (`db/mcp_pending_call_store.py` — anti-tampering: it makes
  approve-then-substitute structurally impossible); the IDOR proposer↔tapper binding in the
  chat-connector confirm path (a *different principal* on a shared channel); all 66 untrusted-text
  sanitization/fencing call sites, **including the 3 in `ai/tools/catalog.py` that use a private
  `_sanitize_external` and will not show up in a grep for `core/untrusted`**; and the entire
  OUTBOUND external-MCP posture (fail-closed auto-fire, money-movement-always-confirms, read-only
  allowlist) which defends against untrusted third-party servers — the opposite trust polarity.
- **If you think a gate is needed, you need an incident first.** Do not propose a tier table. Name
  the observed failure, its trace in the audit/run ledger, and the narrowest mechanism that would
  have caught it — then record it as an ADR. Re-deriving a taxonomy without a cited incident is the
  exact behavior this decision removed.

**Money-confirm completeness (FE-4, INV1-2):**
- A confirm card MUST show **every field that will be set** — especially the **date**, amount, account,
  and total. Selling must not silently make proceeds vanish from net worth (offer a cash-account picker;
  never silently auto-create a leg).

**Frontend consistency (FE-1/2/3):**
- Every overlay uses the shared `.modal-overlay` (clears the chat via `--chat-width`) — never a hand-rolled
  `position:fixed` overlay.
- Every create/edit form uses `modal-card` + `btn-primary`/`btn-secondary` (green primary) + the shared
  label class — don't invent a new card or a blue button.
- Numeric table columns are **right-aligned** (match `HoldingsTable`), incl. chat-rendered tables.

**Data model (DATA-1, the money invariant):**
- When you add a field to a model, **migrate the existing data file** in the same change — no
  read-both-formats shims ([[feedback_no_compat_shims]]).
- Never trust a stored `base_amount` / `realized_gain_base` — **recompute from the native leg**.

**Parity ([[feedback_ui_lucius_parity]]):** anything doable in the UI is doable via Lucius and vice-versa.

## §3 — Keep the register honest
As you close an item, flip its status to ✅ Closed (`<commit>`) in both its area file and
docs/review/TRIAGE.md, and add any *new* finding you discover
(area file + README index + a TRIAGE cluster/bucket).
