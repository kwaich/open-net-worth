# Backend Architecture

FastAPI backend. Five layers enforced by import-linter in CI (`make imports`).

---

## Layer diagram

```
  ┌─────────────────────────────────────────────────────────┐
  │  api/          Thin routers — HTTP only                 │
  ├─────────────────────────────────────────────────────────┤
  │  ai/           Assistant, tools, prompts, memory        │
  ├─────────────────────────────────────────────────────────┤
  │  services/     All business logic + monetary math       │
  ├─────────────────────────────────────────────────────────┤
  │  db/           PayloadTableStore — data access only     │
  ├─────────────────────────────────────────────────────────┤
  │  core/         Leaf — stdlib + paths only               │
  └─────────────────────────────────────────────────────────┘
         models/   Pydantic schemas — leaf alongside core/
```

Imports flow **downward only**. `api` may import `ai` (it mounts AI routes); `ai` may NOT import `api`.

---

## Import-linter contracts (all CI-blocking)

| # | Contract | Why |
|---|---|---|
| 1 | `api → ai → services → db → core` (layers) | Enforces the spine. `api` can call down into `ai`; `ai` calls only `services`. |
| 2 | `db` must NOT import `ai` | Checkpoint hook inverted via `core.io.fire_write_hook` (db calls core, ai registers the hook). |
| 3 | `services` must NOT import `api` (direct) | Services would gain HTTP concerns. FX cache lives in `core.fx`. |
| 4 | `core` is a leaf | Core must be importable anywhere without pulling in business logic. |
| 5 | `sandbox` is a secret-free leaf | The code-exec runner never touches db/services/ai — a sandbox bug can't leak credentials. |

**Accepted exceptions** (documented, never expand without RFC):
- `ai/tools/` may import `db/` config stores directly — `ModelConfigStore`, `AppConfigStore`, connector config stores (`IbkrConfigStore`, etc.) — because they contain only I/O + serialization, no business logic, and no service layer exists for them.
- `ai/tools/memory.py` may import `ai/` sibling stores (not a cross-layer import).

---

## Layer responsibilities

### `api/` — HTTP boundary
- Validate input → call **one** service → shape response.
- No business logic. No monetary math. No direct db access.
- Pydantic models live here (HTTP request/response shapes). Do NOT import them from services or ai.
- Route handlers should be ≤ 20 lines.

### `ai/` — AI assistant
- Tool handlers in `ai/tools/<group>.py`. One `@tool`-decorated async function per tool. Registry auto-collects.
- Tools call **services**, never `api/` and never `db/` directly (except the config-store exceptions above).
- `ai/assistant.py` owns the tool-dispatch loop, session history, prompt caching.
- `ai/prompts.py` owns system-prompt construction.
- `ai/tools/_shared.py` is the keystone — imported by nearly every tool group. Changes here have wide blast radius.

### `services/` — Business logic
All domain logic lives here. Monetary math, validation, cross-store operations.

| File | Owns |
|---|---|
| `ledger.py` | Account balances, transaction create/update/delete choke points, net worth, `txn_base_amount()` |
| `categories.py` | Category lifecycle (rename = atomic re-key across all stores), `get_all_categories()`, `kind_for()`, `STRUCTURAL_KEYS` |
| `budgets.py` | Budget config, expense-category derivation |
| `planning.py` | Envelope-row budget math (`available()`), `apply_budget_change()` — the Planning write choke |
| `envelopes.py` | Envelope lifecycle (create/merge/split/rename/delete), envelope⊥category linking |
| `profile.py` | `read_profile()`, `write_profile()`, `handle_base_currency_change()`, `normalize_target_allocation()` |
| `positions.py` | Portfolio positions, `hydrated()` (priced rows), `apply_position_change()` |
| `lots.py` | Tax lots, FIFO selection, `apply_lot_change()`, realized P&L |
| `xray.py` | Look-through portfolio exposure, `get_instrument()`, `set_instrument_constituents()` |
| `instruments.py` | `resolve_instrument()` — find-or-create from ticker/query (yfinance + CoinGecko) |
| `prices.py` | `fetch_live()`, `_NAMES_CACHE`, all price-fetching helpers |
| `fire.py` | FIRE projection math (`compute_fire()`, `_current_age()`, `_load_profile()`) |
| `montecarlo.py` | THE retirement simulation (pure numpy engine: fan, insight scans, Rich/Broke/Dead) |
| `retirement_plan.py` | Resolves the saved plan + lever overrides into the engine's inputs; the one answer behind `/api/plan/outlook` and `simulate_retirement` |
| `commitments.py` | Financial commitments CRUD + funding projection |
| `watchlist.py` | `read_watchlist()`, `write_watchlist()`, `_sync_holdings()`, store factories |
| `actions.py` | `read_actions()`, `write_actions()` |
| `automations/` (package) | Unified automations — schedules, event triggers, `apply_automation_change()`; absorbed the former standalone "Agents" and "Scheduled tasks" concepts (2026-08-02, `docs/superpowers/plans/2026-08-02-unified-automations.md`) — `services/agents.py` and `services/scheduling.py` no longer exist |
| `skills.py` | Skill validation/rendering (`validate()`, `render()`) — the actual write choke, `apply_skill_change`, is the AI tool handler in `ai/tools/skills.py`, not a `services/` function |
| `audit.py` | Transaction audit log writer |
| `payees.py` | Payee registry, normalization, rule-based autocategorization |
| `fx_cache.py` | FX rate cache I/O (`load_fx_cache()`, `save_fx_cache()`) |
| `cashflow.py` | Cash-flow projection, `build_projection()` |
| `tax.py` | Tax estimation |
| `returns.py` | Portfolio return calculations |
| `tlh.py` | Tax-loss harvesting suggestions |
| `rebalance.py` | Rebalance plan generation |
| `corrections.py` | Correction signal detection (learn-from-edits) |
| `payee_registry.py` | Payee canonical name registry |
| `reconcile.py` | Statement reconciliation |
| `portability.py` | Export/import (portability) |
| `setup.py` | `is_fresh_setup()` — app bootstrap check |
| `import_*.py` | Format-specific statement importers (csv, xlsx, ofx, qif, pdf, mt940…) |
| `connectors.py` | Financial data connector wrappers |
| `notify.py` | In-app notification dispatch |
| `equity.py` | Equity grants/vests, `apply_equity_change()` |
| `income.py` | Income events, `apply_income_change()` |
| `receipts.py` | Transaction evidence, `apply_receipt_change()` |
| `product_docs.py` | Parses/indexes/searches the `docs/product/` corpus — backs `search_docs`/`read_doc` and the in-app Help route |

This table is a sample, not an index — `backend/services/` has ~140 files today. See
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) for the full, current store/service inventory.

### `db/` — Data access
- `PayloadTableStore` (`db/payload_store.py`) is the base for a new flat-list store: a
  `payload_table(name)` factory row shape (`seq`, `id`, `payload JSON`) over the shared
  `db/tables.py` `MetaData`, created by an Alembic revision under `db/alembic/versions/`.
  `JsonStore` (the file-backed predecessor) is retired — deleted, guarded forever by
  `tests/db/test_no_json_store.py`.
- Subclasses own one table each. No business logic — only CRUD, queries, and field normalization.
- Every mutate fires `core.io.fire_db_write_hook(table_name, rows, conn)` → checkpoint
  `write_log` row capture (the DB-store analogue of the old file write-hook).
- Never call `json.load()` directly, and never hand-roll SQL outside a store — always go
  through a store.

### `core/` — Leaf utilities
No internal imports except `backend.paths`. Safe to import from anywhere.

| Module | Provides |
|---|---|
| `core.fx` | `to_base()`, `base_currency()`, `txn_base_amount()` |
| `core.io` | `atomic_write_text()`, `fire_write_hook()`, `register_write_hook()` |
| `core.errors` | Typed exception hierarchy (`ValidationError`, `NotFoundError`, `ConflictError`, …) |
| `core.audit_context` | ContextVar for transaction audit scope |
| `core.automation_runner` | Seam so `services/automations` runs Lucius without importing `ai/` (renamed from `core.agent_runner`) |
| `core.headless_turn` | Seam so a service (e.g. `services/self_harness`) runs one LLM turn without importing `ai/` |
| `core.llm_seam` | Seam for LLM calls from non-ai layers |
| `core.channels` | `notify()` fan-out registry (no hardcoded connectors) |
| `core.email_consumer` | ContextVar for IMAP cursor per agent/session |
| `core.net_guard` | `assert_public_url()` + SSRF-safe fetch |
| `core.security` | PBKDF2 + HMAC session tokens |
| `core.units` | Physical unit conversion (grams ↔ troy ounces, etc.) |
| ~~`core.headless_scope`~~ | **REMOVED 2026-07-18** — the `HEADLESS_ALLOWED_TOOLS` ceiling and the T0/T1/T2 tiers (`core.agent_caps`) are gone; every caller class sees the same tool surface. See [ADR 2026-07-18](docs/adr/2026-07-18-remove-layered-ai-permission-system.md). |

---

## Write choke points — never bypass

| Domain | Choke point |
|---|---|
| Transactions | `ledger_service.create_transaction()` / `bulk_create_transactions()` |
| Transaction edits | `ledger_service.update_transaction(txn_id, changes: dict)` |
| Positions | `positions_service.apply_position_change(change)` |
| Tax lots | `lots_service.apply_lot_change(change)` |
| Planning | `planning.apply_budget_change(change)` |
| Equity grants/vests | `equity_service.apply_equity_change(change)` |
| Automations (incl. former Agents/Scheduled tasks) | `apply_automation_change(change)` |
| Income | `apply_income_change(change)` |
| Receipts | `receipts_service.apply_receipt_change(change)` |
| All file writes | `core.io.atomic_write_text()` — never `open(..., 'w')` |
| All DB store writes | `core.io.fire_db_write_hook(table_name, rows, conn)` — never a hand-rolled `INSERT`/`UPDATE` outside a store |

`Skills` (`apply_skill_change`) is an AI-tool-level choke, not a `services/` one — see the
`services/` table above.

---

## Core seams (dependency inversion)

These exist so lower layers can trigger upper-layer behaviour without an upward import:

| Seam | File | How it works |
|---|---|---|
| Write hook | `core.io` | `db` calls `fire_write_hook(path)`; `ai/checkpoint` registers the handler at import |
| Automation runner | `core.automation_runner` | `services/automations` calls the runner; `ai/` registers it at startup (renamed from `core.agent_runner` — `services/scheduling` no longer exists, see the `services/` table above) |
| Headless turn | `core.headless_turn` | `services/self_harness` runs one LLM turn; `ai/` registers the runner at import |
| LLM | `core.llm_seam` | `services/import_pdf` calls `complete()`; `ai/` registers the provider at startup |
| Audit context | `core.audit_context` | Router dep sets `actor=user/surface=ui`; ledger choke reads it — no import needed |
| Email consumer | `core.email_consumer` | ContextVar namespaces IMAP cursor per agent/chat session |
| Channel registry | `core.channels` | `notify()` fans out to registered connectors; no hardcoded Telegram (Slack/Discord/WhatsApp removed 2026-08) |

---

## Key invariants

- **`category` is OPTIONAL, not "never blank."** `None` renders "Uncategorized" — there is NO
  `uncategorized` sink, and an unknown category id RAISES `ValidationError` rather than being
  coerced to one. Precedence at the create choke is **explicit > payee-default > `None`** — the
  old *rule* tier is gone (2026-08-02): a categorization rule is now an event-triggered
  Automation that sets the category AFTER the row commits, via `update_transactions`.
- **A stored `base_amount` is TRUSTED, not "never trusted."** It's TRUSTED only while its
  `base_currency` still matches the current base (the historical-rate lock, ADR
  `2026-07-09`) — a completed transaction's base value is a historical fact, not revalued as
  the market drifts. Only on a base-currency change, or a legacy/unstamped row, does
  `txn_base_amount(t, base, rates)` recompute it.
- **`STRUCTURAL_KEYS = {"split"}`** — the ONLY structural category; every other id (including
  `transfer`/`investment`/`adjustment`/`income`) is ordinary user data, renameable/deletable.
  There is no `uncategorized` category at all.
- **`update_transaction`** wraps inner `create_transaction` in `_suppress_inner_create_audit()` → emits one `update` event, never a phantom `create`.
- **`$0 taxable proceeds`** are REFUSED on lot disposals.
- **Unknown cost basis** → `realized_gain=None` (never `0`).
- **Profile writes are serialised by the DB**, not a per-process lock — `ProfileStore.set_key`'s
  single-transaction read-modify-write replaced the old `services/budgets._profile_write_lock`
  once `profile.json` became a DB singleton table (Wave 5A, revisions 0020–0021).
- **FX cache** is invalidated on `base_currency` change (`handle_base_currency_change` unlinks it).
- **Split transactions**: `category="split"` sentinel on parent; `Σ split.amounts == leg amount ±0.01` enforced at choke point — never rescaled.

---

## Data stores

The JSON→database migration (Plans 1–4e plus the Wave 5 follow-on sweep, both 2026-08, COMPLETE)
moved every store off `backend/data/*.json` and onto real tables — `backend/db/json_store.py` is
DELETED, guarded forever by `tests/db/test_no_json_store.py`. There is no live `JsonStore`
subclass left anywhere in this codebase; a `(file)` marker below means genuinely
`INTENTIONALLY_FILE_BACKED` (a blob, cache, or secret sidecar — see `core.paths`), never a
leftover `JsonStore`. This table is a quick index, not the source of truth: see
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) for the full, current per-store/table list, tiering,
and schema.

| Store | Store class | Owns |
|---|---|---|
| `accounts` table | `AccountStore` | Asset + liability accounts |
| `transactions` table (+ `transaction_splits`/`transaction_external_refs`) | `TransactionStore` | Two-leg ledger entries |
| `instruments` table | `InstrumentStore` | Instrument master (ticker, price source, sector) |
| `positions` table | `PositionStore` | Holdings (quantity, cost basis per instrument) |
| `lots` table | `LotStore` | Tax lots (FIFO ordered by acquired_at, created_at) |
| `price_points` table | `PriceHistoryStore` | Daily price series per price_source_id |
| `profile` table | `ProfileStore` | Base currency, FIRE config, budgets, category targets (per-book singleton, Wave 5A) |
| `watchlists` table | `WatchlistStore` | Price/news watchlist + config |
| `automations` table | `AutomationStore` (`services/automations/`) | Unified automations — absorbed the former standalone Agents and Scheduled-tasks concepts (2026-08-02) |
| `commitments` table | `CommitmentsStore` | Financial commitments + schedules |
| `payees` table | `PayeeStore` | Payee registry + aliases |
| `actions` table | `ActionStore` | Scheduled actions |
| `txn_audit` table | (append-only via `services/audit`) | Transaction audit log — checkpoint-EXCLUDED |
| `secret_config` table (namespace `auth`) | `AuthStore` | Session tokens — checkpoint-EXCLUDED, NON_RESTORABLE (folded off `auth.json`, Wave 5D2a) |
| `memory` table | `MemoryStore` | Lucius cross-session memory facts (durable + time_bound) |
| `settings` table (namespace `memory_settings`) | `MemorySettingsStore` | Global memory toggle (`{"enabled": true}`, default on) |

Categorization "rules" (the old `rules.json`/`RuleStore`) and standalone "Agents" (the old
`agents.json`/`AgentStore`) no longer exist as separate concepts — both were absorbed into the
unified Automations model (`services/automations/`, 2026-08-02,
`docs/superpowers/plans/2026-08-02-unified-automations.md`).

Cache files (under `backend/cache/`, controlled by `CACHE_DIR` env var):

| File | Owns |
|---|---|
| `fx_cache.json` | Last-known FX rates (pivot = base currency) |
| `names_cache.json` | Ticker → display name (from yfinance) |
| `sectors_cache.json` | Ticker → GICS sector (from yfinance) |
| `briefing_cache.json` | Daily briefing cache |
| `monitoring_price_cache.json` | Price monitor alert state |

---

## Memory system (`ai/memory.py`)

Cross-session memory persists durable facts and dated intentions across chat sessions in the `memory` table.

**Global toggle** — `MemorySettingsStore` (a row in the shared `settings` table, namespace `memory_settings`) stores `{"enabled": bool}`.  Default when absent: `true`.  REST surface: `GET /api/memory/settings` and `PUT /api/memory/settings` (`{"enabled": bool}`).

**AND-gate helper** — `memory_enabled_for(agent)` lives in `db/memory_settings_store.py` (db layer) so a `db/`-or-below caller can check it without a layering violation (the standalone `services/agents.py` this line used to name is gone — see the `services/` table above; the "agent" concept it gated now lives under `services/automations/`).  Returns `True` only when the global toggle is `ON` AND (if an agent dict is provided) the agent's `memory_enabled` field is not `False`.

**Write gates** — `add_explicit()` (used by `remember_fact` tool) and `remove_fact()` (used by `forget_fact` tool) both raise `MemoryDisabledError` when the global toggle is `OFF`.  The tool handlers catch this and return `{"ok": false, "message": "..."}`.  Background paths (`extract_and_merge`, `consolidate`) are no-ops when `OFF`.

**Incremental extraction cursor** — `extract_and_merge(history, session_id)` writes `{"last_extracted_len": N}` to the session's `extraction_cursor` column (`SessionStore.get_extraction_cursor`/`set_extraction_cursor`, Wave 5F) after each successful extraction — no sidecar file.  On the next call:
- If `last_extracted_len == len(history)` → no new messages; returns immediately without an LLM call.
- If `last_extracted_len < len(history)` → sends `history[last_extracted_len - EXTRACT_OVERLAP:]` to the LLM (4-message overlap for dedup context).
- First run (`last_extracted_len == 0`) or stale cursor (`last_extracted_len > len(history)`) → caps the backfill to the last `EXTRACT_BACKFILL_CAP` (200) messages.

---

## Adding a feature — where does new code go?

1. **New business rule / calculation** → `services/<domain>.py`
2. **New data to persist** → new `PayloadTableStore`-based store in `db/`; add table to data model table above
3. **New REST endpoint** → thin handler in `api/<domain>.py`; call one service
4. **New AI tool** → one `@tool` function in `ai/tools/<group>.py`; import from services
5. **Cross-layer state needed by both api and ai** → seam in `core/` (see `core.channels` or `core.llm_seam` as the pattern)
6. **New service that replaces an api-layer function** → move logic to `services/`, have `api/` re-export for backward compat; update tests to patch the services module

**Never:**
- Put business logic in `api/` route handlers
- Import from `api/` in `ai/tools/` — this is caught by import-linter contract #1
- Import `ai/` from `db/` — caught by contract #2
- Import `api/` from `services/` — caught by contract #3
- Use `json.load()` directly — always go through a store
- Bypass write choke points (use `transactions.replace_all()` instead of `create_transaction`)
