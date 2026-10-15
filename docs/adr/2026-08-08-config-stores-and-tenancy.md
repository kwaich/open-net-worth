---
title: Config stores on payload tables — the settings namespaced-singleton pattern, the tenancy ruling (book_id vs neither), the tool_grants/signals checkpoint exclusions, and the signals prepend-order cutover decision
status: Accepted
date: 2026-08-08
deciders: Claude (implementer agent, storage plan 4d tasks 1/2/5/6 — schema, store rewrites, absorb, portability/reset/seed + rehearsal); controller ruling (task 6 brief) deferring connector secrets (D8) and the ~17 connector stores to a separate post-terminal security track
---

# 2026-08-08. Config stores: the `settings` pattern, the tenancy ruling, two checkpoint exclusions, and the `signals` order fix

## Context

Storage plan 4d (Wave D) moves the last twelve non-connector `JsonStore`s onto
`backend.db.payload_store` — `payees`, `automations`, `alerts`, `dashboard_layouts`, `memory`,
`news_sources`, `statement_formats`, `tool_grants`, `signals`, `intel_signals`, plus
`memory_settings`/`preferred_models` (two stores sharing ONE `settings` table). This is the
last Wave-D task before the security gate (task 7): it makes the twelve first-class in
portability (`/api/export`/`/api/import`), `/api/reset`, and first-boot seeds, then proves the
wiring on an isolated clone.

**Scope note (controller ruling, task 6 brief):** the design spec's Plan 4D originally bundled
connector secrets (the D8 Fernet encryption seam) and the ~17 connector config/credential
stores into this same wave. Both are DEFERRED to a separate, post-terminal security track — see
the "Deferred: connector secrets" section below. This ADR covers ONLY the twelve config stores
named above plus the `settings` table.

Two design questions this wave answers that no prior wave (money, planning, investments, logs)
needed to:

1. **Two stores sharing one physical table.** `memory_settings` and `preferred_models` are each
   a single-document config blob (`{"enabled": true}`, `{"models": [...]}`) — too small to
   justify a dedicated table each, but each still needs its own identity for import/export/wipe
   purposes.
2. **A tenancy split within one wave.** Design spec `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md:139-140`
   lists `payees`/`automations`/`alerts`/`dashboard_layouts`/`memory` together in the Tier-1
   `book_id` list — but spec:276 ("Partition asymmetry to decide deliberately, not discover")
   separately states that "in family mode... chat history and Lucius's memory are `user_id`-scoped
   (private)," not `book_id`-scoped. Those two statements name `memory` on both sides — this ADR
   is the record of how that tension resolved for THIS wave's actual schema.

## Decision

### 1. The `settings` namespaced-singleton pattern

`backend/db/tables.py` adds one small table:

```python
settings = Table(
    "settings",
    metadata,
    Column("namespace", Text, primary_key=True),
    Column("payload", JSON, nullable=False),
)
```

One row per config namespace, `payload` holding the whole document verbatim — no `book_id`
(Tier-3 installation settings, not book- or user-scoped). `backend.db.payload_store
.NamespacedSettingsStore` is the reusable base: `get() -> dict` (namespace's payload, or `{}` if
absent), `save(doc)` (whole-document replace, firing `fire_db_write_hook` with the before-image
so a checkpoint-included namespace stays undoable). `MemorySettingsStore`/`PreferredModelsStore`
each subclass it with one fixed `namespace` string and layer their own domain methods
(`enabled()`/`set_enabled()`, `resolved()`/`replace()`) on top. A LATER task may add every
connector/model config namespace as more rows in this SAME table (the docstring in `tables.py`
already anticipates this) — that is explicitly the deferred D8 track, not this one.

This mirrors `plan_state`'s/`watchlists`' per-book-singleton shape exactly, keyed by `namespace`
instead of `book_id` — the THIRD "one row per fixed key, not a flat list" shape this codebase
has now built (`plan_state`/`watchlists` keyed by `book_id`, `settings` keyed by `namespace`),
confirming it as a real, repeatable pattern rather than a one-off.

**Portability consequence:** `memory_settings.json`/`preferred_models.json` are wipe-only (see
point 3 below) — each gets its own filename-keyed wipe function
(`portable_bridge._wipe_settings_namespace(conn, "memory_settings" | "preferred_models")`) that
deletes ONLY its own row from the shared table, never a whole-table truncate. Proven directly,
not just asserted: `tests/db/test_configs_portable.py::test_memory_settings_and_preferred_
models_wipe_is_scoped_by_namespace` seeds both namespaces, wipes one, and asserts the sibling's
row survives byte-exact.

### 2. The tenancy ruling

| Store | `book_id`? | Reasoning |
|---|---|---|
| `payees` | YES — `DEFAULT_BOOK_ID` | Tier-1 per spec:139-140; a payee is ledger-adjacent config (learned category defaults consumed by the ledger choke) |
| `automations` | YES — `DEFAULT_BOOK_ID` | Tier-1 per spec:139-140; an Automation's `allowed`/writes are scoped to what it can touch in a book |
| `alerts` | YES — `DEFAULT_BOOK_ID` | Tier-1 per spec:139-140; a price alert is user-authored watch config |
| `dashboard_layouts` | YES — PK **is** `book_id` (per-book singleton) | Tier-1 per spec:139-140; mirrors `plan_state`/`watchlists`' own per-book-singleton shape |
| `memory` | **NO** | spec:276's partition asymmetry: a memory fact is a per-USER relationship with Lucius, not a per-book ledger fact. Single-user install today (no `user_id` column exists anywhere in this schema yet) — `payload_table("memory")`, plain, no book scoping. Family mode adds a `user_id` column in a LATER migration, **never** `book_id` — carrying a `book_id` now would be actively wrong to retrofit away from, not merely incomplete |
| `memory_settings` | **NO** | Same reasoning as `memory` — a `settings` row, no book/user column at all yet (single-user) |
| `news_sources`, `statement_formats`, `tool_grants`, `signals`, `intel_signals`, `preferred_models` | NO | Tier-2/3 installation-wide config/reference/cache — not book-scoped user data by any reading (a news source registry, a learned import format, a tool permission grant, a monitoring cache, an analyst signal feed, and a curated model shortlist are all install-wide, not per-book) |

**Why `memory` resolves to "no `book_id`" despite spec:139-140 naming it in the Tier-1 list**:
spec:139-140 is the SCHEMA-TIER list (does this go on `book_payload_table()` or plain
`payload_table()`), written before the partition-asymmetry note at spec:276 was reasoned through
in detail. spec:276 is the more specific, more recently reasoned statement about what
`memory`/`memory_settings` actually need — a person, not a book. Tier-1 in the spec's D4/D5
sense means "relational, typed, carries SOME real-world partition key" more than it strictly
means "carries `book_id` specifically" — `automation_runs` already established in Wave C (ADR
`2026-08-09-logs-tier2-classification`) that Tier and the specific partition-column question are
independent axes; this wave's `memory`/`memory_settings` are the SAME independence proven from
the opposite direction — Tier-1-adjacent config, deliberately still carrying NEITHER `book_id`
NOR (yet) `user_id`, because the latter doesn't exist in this schema and inventing it now, ahead
of family mode's actual design, would be exactly the kind of premature column spec:276 itself
warns against ("Recorded here; resolved when family mode is designed"). `book_id` is also
carried on every Tier-1 table specifically because spec:445's own risk table accepts the cost
("`book_id` is YAGNI if hosting never ships... carrying it costs one column and one base-class
filter; retrofitting costs a migration across the whole ledger plus an open leak-bug class") —
the identical logic argues AGAINST speculatively adding `user_id` to `memory` today, before
family mode's actual shape is known, rather than for it.

### 3. Portability split — seven bridged, five wipe-only

Unlike every prior wave (one uniform undoable/no-FK combination across the whole wave), config
splits per-store along the portable axis:

**SEVEN join `portable_bridge.BRIDGED`** (undoable, no cross-file FK, ride the generic
`_apply_bridged_import` loop) — `payees`, `automations`, `alerts`, `dashboard_layout` (the
dict-shaped singleton), `memory`, `news_sources`, `intel_signals`. All seven filenames were
ALREADY live in `core.portable.PORTABLE_FILES` before this task touched anything — user-authored
config that was already exported/imported/backed-up under the JsonStore era, no
`income_events.json`-shaped silent-backup-gap existed here (verified directly:
`tests/db/test_configs_portable.py::test_seven_config_files_are_live_in_portable_files`, and
re-proven live on the rehearsal clone below).

**FIVE are wipe-only, never bridged** — `memory_settings`, `preferred_models`,
`statement_formats`, `tool_grants`, `signals`. `/api/reset` clears all five (config/cache —
"reset means reset"), but none carries an export/import role: correctly ABSENT from
`PORTABLE_FILES`, no backup, no re-import. This EXTENDS `/api/reset`'s reach as a DELIBERATE
change, not a silent side effect: in the JsonStore era none of these five were wiped by reset
(absent from `PORTABLE_FILES` and from any wipe path); giving them DB tables with `_WIPE_FNS`
entries brings them in. `statement_formats` is the one with teeth — LEARNED, not re-derivable
without re-running LLM inference — so this is named explicitly here, per Wave C's precedent that
extending "reset means reset" to a previously-unreached store is an ADR-worthy decision. The
reasoning is NOT `asset_brief`'s ("a derived
cache with nothing coherent to export") — it is instance-local trust/tuning that must never
travel to another machine on an export, stated most explicitly by `tool_grant_store.py`'s own
pre-existing "NOT portable" section ("`I trust THIS install to book transactions unattended`
must not travel to another machine inside a backup"), which this task extends to its four
siblings: a signal is a disposable notification ring, a statement format / memory toggle /
preferred-model shortlist is this install's own tuning, not a portable fact.

### 4. The `tool_grants`/`signals` checkpoint exclusions

Of the twelve, exactly TWO — `tool_grants` and `signals` — join `ai.checkpoint._EXCLUDED_TABLES`
(added in task 1, before this task's own portability wiring). The other ten (`settings`,
`payees`, `automations`, `alerts`, `dashboard_layouts`, `memory`, `news_sources`,
`statement_formats`, `intel_signals`) are ordinary checkpoint-INCLUDED user data/config,
unchanged from their JsonStore-era behavior — portable-status and checkpoint-status are
independent questions (the same precedent `match_reviews`/`match_decisions` established for
planning): `statement_formats`/`memory_settings`/`preferred_models` are non-portable YET
checkpoint-included, exactly like those two planning stores were checkpoint-included yet
non-portable, just with the two axes flipped.

**Why these two specifically**, per each store's own docstring (reproduced here so this ADR is
the one place a future reader finds the full reasoning without cross-referencing a store file):

- **`tool_grants`**: `restore_checkpoint` is a MODEL-CALLABLE tool carrying `mutates=("*",)`.
  Were this table checkpoint-included, `owner revokes a grant -> model calls restore_checkpoint
  to any earlier point -> the grant is back` — privilege escalation through an unrelated tool,
  no malice required. A checkpoint undoes what Lucius did to your DATA; a grant is not something
  Lucius did, it is something the owner decided. Accepted cost: a grant SURVIVES a CHECKPOINT
  restore, so undoing a rash session leaves the grant made in it standing — correct, because
  permissions change when the owner changes them, never as a side effect of an unrelated undo.
  A full DR *backup* restore (`services.backup`) is a DIFFERENT surface, and it ALSO preserves
  this table now: `core.sensitive.NON_RESTORABLE_TABLES` (`tool_grants` + the program-wide
  `txn_audit`/`mcp_write_log`/`automation_runtime`, which moved to the DB in earlier plans) is
  the DB counterpart of `NON_RESTORABLE_FILES`, and `restore_backup` captures those tables' live
  rows and re-applies them across the `onw.db` swap — so neither a checkpoint restore nor a DR
  restore un-revokes a grant. (The DR restore additionally refuses a pre-migration backup that,
  having no `onw.db` member, would otherwise delete the live database.)
- **`signals`**: a runtime monitoring/notification cache — self-heals on corruption, disposable
  like `price_points`/`asset_brief`. A `/rewind` must not resurrect a signal the user already
  dismissed, or erase one a poll produced mid-turn for an unrelated reason.

Both wipe functions (`_wipe_tool_grants`/`_wipe_signals`) still fire `fire_db_write_hook`
UNCONDITIONALLY — uniform with every sibling wipe function, never a special case in the wipe
machinery itself — the `_EXCLUDED_TABLES` membership alone is what makes the capture a
structural no-op, mirroring `corrections`' own precedent from Wave A exactly. Proven live, not
only by `_EXCLUDED_TABLES` membership: `tests/db/test_configs_portable.py::test_tool_grants_
and_signals_wipe_fires_hook_but_checkpoint_exclusion_makes_it_a_structural_no_op` and the
rehearsal's own `/rewind` proof below (a NEW tool grant made mid-turn survives a restore that
correctly reverts two ordinary, non-excluded writes made in the SAME turn).

### 5. The `signals` prepend-order / `reverse=True` cutover decision

`signals.json` is the one prepend/ring source among the twelve — its pre-rewrite `SignalsStore`
always `items.insert(0, new)`, so the file is newest-first on disk, while the DB-era
`SignalsStore.query_signals` reads via an explicit `ORDER BY seq DESC` (ascending `seq` means
oldest→newest, matching `_next_seq`'s `max(seq)+1` convention for a post-migration append — the
newest row must land on the HIGHEST seq). `backend.db.config_io.import_payload_list`/
`export_payload_list` therefore carry an explicit `reverse: bool = False` flag — `False` for
every other store (their files are already append-order, oldest-first on disk), `True` only for
`signals` (task 5's own module docstring has the full mechanics). Because `signals` is wipe-only
(point 3 above), this flag is exercised ONLY at cutover-absorb time and by direct
`config_io`/`portable_bridge` calls in tests and this ADR's own rehearsal — never through the
bundle export/import pipeline, since `signals.json` never reaches it. Recorded here explicitly
so a future flip of `signals` to portable does not silently import it in file order (oldest-
first) instead of the newest-first its real read path requires — the exact "Wave C logs-cutover
scar" task 5's own docstring names as the trap this flag exists to avoid repeating.

Re-proven END TO END on the rehearsal clone below (not merely by the unit-level
`test_signals_prepend_order_survives_cutover` in `tests/db/test_configs_cutover.py`): a
newest-first fixture, absorbed through the REAL `ensure_migrated()` boot path, read back through
the REAL `SignalsStore.query_signals()` production read path, in the correct order.

### Deferred: connector secrets (D8) — a separate security track

Connector-secret encryption (the D8 Fernet seam) and the ~17 connector config/credential stores
(Plaid, Telegram, IBKR, Alpaca, email, etc.) are a DEFERRED, separate, post-terminal security
track — NOT decided by this ADR. None of the ~17 connector stores are `JsonStore` subclasses
today (most are already their own bespoke config-file stores, pre-dating this wave), so none of
them block the terminal `json_store.py` deletion this program is working toward. This wave's
schema task (task 1) created NO `connector_settings` table — only the shared `settings` table
(the two non-secret singleton configs) plus the 12 config tables. When the D8 track migrates
connector secrets it creates its OWN table for them, kept checkpoint-EXCLUDED and out of
`PORTABLE_FILES` — deliberately NOT more rows in the checkpoint-INCLUDED `settings` table, since
`_EXCLUDED_TABLES` is whole-table granularity and secret payloads in `settings` would land as
`write_log` before-images (the design fork this wave's plan resolved, confirmed by the task-7
security gate). So there is nothing to wipe, leak, or prove a bundle-no-leak guarantee for in
THIS task. Its own ADR lands with that future track, when a connector secret's encryption-at-rest
and bundle-redaction story is actually designed.

## Verified on an isolated dress rehearsal

**2026-08-09, plan 4d task 6 — a scratchpad clone, the live `~/.open-net-worth` tree NEVER READ
OR WRITTEN.** Unlike Waves A/B/C's rehearsals (which cloned a representative book), this
rehearsal deliberately does not — config is behavioral, not numeric, so there is no dollar
figure a real-data clone would add proof for that a small, disclosed, hand-authored fixture
does not already prove just as rigorously. The fixture (2 payees — one with a learned
expense-category default, one without; 2 automations — one due, one not; 1 alert; 1 dashboard
layout; 2 memory facts — one durable, one expired `time_bound`; 1 news source; 1 intel signal;
1 tool grant; 3 signals in newest-first order; 1 statement format; the two `settings`
namespaces) was written directly to a throwaway `DATA_DIR`, never copied from anywhere.

**Migration**: the real `backend.db.migrate.ensure_migrated()` boot entrypoint absorbed all 12
sources to head `0016` in 0.32s; every one of the 12 `json_cutovers` rows confirmed
`renamed=1`; every source file renamed to `<name>.json.migrated-<ts>`, never deleted, none left
in place (re-verified by this rehearsal, not merely inferred from the migration completing
without raising).

**OLD-vs-NEW parity — EXACT on all eight config-derived behavioral predicates** (sha256 of the
canonical predicate dict, computed independently on each side, compared equal — not merely
"looks equal"): `619c4913...dc18f4f` both sides.

| Predicate | OLD (computed from the raw fixture JSON) | NEW (through the real, current, DB-backed surface) | Match |
|---|---|---|---|
| `payee_registry.default_category_for` (learned default) | `cat_software` | `cat_software` | EXACT |
| `payee_registry.default_category_for` (no default) | `None` | `None` | EXACT |
| `AutomationStore.due()` — a due automation | `True` | `True` | EXACT |
| `AutomationStore.due()` — a not-yet-due automation | `False` | `False` | EXACT |
| `ToolGrantStore.effective()` — a granted tool | `"always"` | `"always"` | EXACT |
| `ToolGrantStore.effective()` — a never-granted tool | `None` | `None` | EXACT |
| `MemoryStore.active_facts()` — excludes the expired `time_bound` fact | `["mem_durable"]` | `["mem_durable"]` | EXACT |
| `SignalsStore.query_signals()` — newest-first | `[sig_3, sig_2, sig_1]` | `[sig_3, sig_2, sig_1]` | EXACT |

**PORTABLE_FILES no-silent-loss check, live**: all seven bridged config filenames confirmed
members of the REAL, unnarrowed `core.portable.PORTABLE_FILES` on the migrated clone — the exact
check this task's brief calls out as load-bearing (the Wave-A/B `income_events.json` bug class).

**`/rewind` proof, one turn, three writes, two different fates**: within one `begin_turn`/
`end_turn` span, added a payee (`payees`, book-scoped, bridged), toggled `memory_settings`
(`settings`, namespace-scoped), AND granted a brand-new tool (`tool_grants`, checkpoint-
EXCLUDED) — all three in the SAME turn. `entry["db_tables"]` contained `payees`/`settings` but
NOT `tool_grants` (the structural no-op, live). `restore(mode="data")` reverted `payees` and
`settings` byte-exact to their pre-turn state, while `tool_grants` was left holding the turn's
OWN write (2 grants, not the pre-turn 1) — the stronger proof named in the brief: not merely
"never touched," but "untouched by the restore despite being written in the same turn."

**Bundle round-trip proof**: exported a bundle from the clone, wiped all seven bridged config
tables via `portable_bridge.wipe`, re-imported the bundle, and confirmed all seven byte-exact
(`sha_before == sha_after` on the canonical JSON of each store's post-wipe/post-reimport
content) — `payees.json`, `automations.json`, `alerts.json`, `memory.json`,
`news_sources.json`, `intel_signals.json`, `dashboard_layout.json`.

**Cleanup**: the rehearsal's `DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` (the synthetic fixture + the
resulting SQLite DB) were deleted immediately after the run —
`rm -rf .../scratchpad/config-rehearsal/{data,cache,logs}` — leaving only the (uncommitted)
rehearsal script and its JSON summary in the scratchpad. Full detail (script, raw JSON output):
`.superpowers/sdd/2026-08-08-storage-plan4d-configs/task-6-report.md`.

## Consequences

### Positive

- `NamespacedSettingsStore` is now a proven, reusable third small-table shape (alongside
  `payload_table`/`book_payload_table`) for any FUTURE single-document config namespace — the
  connector-secrets track (deferred, above) will very likely reuse it directly rather than
  invent a fourth shape.
- The tenancy table above is the one place a future reader (or the family-mode design task)
  finds the full "why does `memory` have no partition column yet" reasoning without
  re-deriving it from two different spec paragraphs that read as if they conflict.
- All seven bridged config stores were already portable pre-wave — zero backup-gap regressions
  to fix, unlike planning's `income_events.json`/investments' proactive avoidance of the same
  bug class.
- The five wipe-only stores' reasoning (instance-local trust/tuning, not "cheap to
  regenerate") is a genuinely new category this ADR names explicitly, distinct from
  `asset_brief`'s cache reasoning — a future store facing the same question has two named
  precedents to choose between instead of one.

### Negative

- `memory`/`memory_settings` carry NEITHER `book_id` NOR `user_id` today — a deliberate
  omission, but it means a future family-mode migration must retrofit a NEW column onto data
  that already exists, exactly the cost spec:445's own risk table accepts in exchange for not
  guessing `user_id`'s eventual shape now.
- The `settings` table's namespace-scoped wipe (`_wipe_settings_namespace`) is a THIRD wipe
  shape alongside `_capture_then_delete`'s whole-table truncate — one more pattern a future
  reader has to learn, though it directly mirrors `config_io.import_namespaced`'s own scoping
  rather than inventing something new.
- Five wipe-only stores with no bundle role is the largest wipe-only cohort of any wave so far
  (previously just one, `asset_brief`) — a self-hoster's exported backup is silently narrower
  than their live instance for these five, by design, but worth naming as a real, cumulative UX
  fact rather than treating each one as an isolated decision.

### Follow-ups

- **Genuinely still open, NOT decided by this wave**: the eventual `user_id` column for
  `memory`/`memory_settings` when family mode is actually designed (spec:276's own deferral,
  unchanged by this task).
- Connector secrets (D8) and the ~17 connector stores: their own ADR, when that track starts.
- `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`'s decomposition
  table: Plan 4D marked delivered (configs) by this task; D8/connectors marked deferred.

## Alternatives considered

- **Give `memory`/`memory_settings` a `book_id` "for consistency" with the wave's other four
  Tier-1 stores** — rejected: spec:276 is explicit that a memory fact is a per-user
  relationship, not a per-book ledger fact; retrofitting `book_id` onto memory now would be
  actively wrong for family mode, not merely premature, and there is no present-day multi-book
  scenario this would even serve (`DEFAULT_BOOK_ID` is the only book that exists).
- **One shared `_wipe_settings` function that truncates the whole `settings` table, registered
  under both `memory_settings.json` and `preferred_models.json`** — rejected: a second call
  would be a silent no-op (the table's already empty), which is functionally harmless today but
  misleading to a future reader and stops working correctly the moment a THIRD namespace
  (a future connector) lands in the same table — the namespace-scoped version is correct at any
  namespace count, not just two.
- **Extend `asset_brief`'s "derived cache" reasoning to the five wipe-only config stores** —
  rejected: none of the five is a derived/rebuildable cache in `asset_brief`'s sense (a tool
  grant is an owner decision, not a computed value; a statement format is LEARNED, not
  re-derivable without re-running LLM inference; `signals`/`preferred_models`/`memory_settings`
  are runtime state or install tuning, not caches of anything else). The "instance-local trust,
  must never travel" reasoning `tool_grant_store.py` already stated is the more accurate,
  narrower fit — reused rather than stretching a precedent that doesn't actually cover this case.
- **Skip the rehearsal's `/rewind` proof's "still holding the turn's own write" assertion and
  settle for "tool_grants is absent from `entry['db_tables']`"** — rejected: the weaker version
  is true but uninteresting (of course an untouched table is absent) — writing to `tool_grants`
  DURING the turn and confirming the restore does not revert it is the proof that actually rules
  out a subtle bug (e.g., an accidental `_capture_then_delete` swap that captured the WRONG
  before-image, or a dedup bug that let the exclusion get bypassed).

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 4d task 6, on branch
  `lane/storage-plan1` (worktree `storage-plan1`). The tenancy ruling (memory: no `book_id`
  today) directly applies spec:276's own pre-existing partition-asymmetry note rather than
  deciding a new question; the `tool_grants`/`signals` checkpoint exclusions were DECIDED by
  task 1 (before this task) — this ADR records and consolidates that reasoning rather than
  re-deciding it. The wipe-only-vs-bridged split for the five non-portable stores, the
  `NamespacedSettingsStore`/namespace-scoped-wipe design, and the rehearsal's specific
  predicate/proof shapes are this task's own design calls.
- **Alternatives weighed:** see above — a speculative `book_id`/`user_id` on `memory` was
  rejected against spec:276's explicit guidance; a shared whole-table settings wipe was rejected
  as correct-today-but-wrong-at-scale; extending `asset_brief`'s cache reasoning to the five
  wipe-only stores was rejected as a category mismatch; a weaker `/rewind` proof was rejected as
  insufficiently discriminating.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md:139-140,276,445`
  (the Tier-1 list, the partition-asymmetry note, the `book_id`-carrying-cost risk entry);
  `.superpowers/sdd/2026-08-08-storage-plan4d-configs/task-1-brief.md` through `task-6-brief.md`
  and their reports (schema, store rewrites, the D8/connector scope narrowing, the absorb, this
  task's own portability/reset/seed wiring and rehearsal); `backend/db/tool_grant_store.py`'s
  own "NOT portable"/checkpoint-exclusion sections (the canonical statement this ADR extends to
  `signals`/its four wipe-only siblings); `backend/db/config_io.py`'s module docstring (the
  `reverse=True` ORDER FIX mechanics); ADR `2026-08-09-logs-tier2-classification.md` (the
  Tier-vs-checkpoint/portability independent-axes framing this ADR's tenancy table extends from
  the opposite direction); ADR `2026-08-07-portable-export-bridges-db-backed-stores.md` (the
  `Bridge`/`wipe`/`wipe_all` machinery every wave including this one builds on).
- **Why this way (one line):** two questions a schema column can't answer by itself — "whose
  data is this" (tenancy) and "does an owner's undo/backup/reset reach it" (checkpoint/
  portability) — get decided per-store from their actual real-world ownership and blast radius,
  not inferred from which wave a store happened to migrate in.
