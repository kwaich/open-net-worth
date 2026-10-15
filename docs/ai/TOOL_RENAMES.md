# AI tool renames — the migration log

> **The aliases described below are GONE** (2026-08-25, ADR
> [`2026-08-25-tool-names-are-final`](../adr/2026-08-25-tool-names-are-final.md)). Nothing
> resolves an old tool name at runtime any more: a rename is a DATA MIGRATION, and
> `backend/scripts/migrate_tool_names.py`'s `RENAMES` is its only record. Every entry below
> is still in that table, so every rename here is still migratable — but read the prose as
> HISTORY. Where it says "declared BOTH ways (`@tool(aliases=…)` + a mirrored
> `_LEGACY_TOOL_ALIASES` entry)", only the migration entry survives.
>
> **Historical log.** Read this when auditing a name or adding a new rename; the *procedure*
> for renaming lives in [`TOOL_CONVENTIONS.md`](TOOL_CONVENTIONS.md).

**Tool renames — Tier 1 + Tier 2 (2026-07-19, harness audit finding "4 name families lie about retired concepts," `docs/launch/BOARD.md`):** applied the alias+migration pattern above to two confusable name families. **Tier 1** — `create_category_group`/`rename_category_group`/`reorder_category_groups`/`delete_category_group` → `create_envelope_group`/`rename_envelope_group`/`reorder_envelope_groups`/`delete_envelope_group`: group placement has been envelope-keyed (not category-keyed) since the envelope split, so the old names literally contradicted their own sibling `move_envelope_to_group` in the names-only catalog. **Tier 2** — the pre-envelope flat per-category spending-cap system (P2-BU1, `services/budgets.py` — REST + AI-tool reachable but never called by the frontend) renamed off "budget" vocabulary, which collided with the Planning envelope system's `assign_budget`/`apply_budget_change`/`get_budget_month`: `update_spending_budgets` → `set_spending_limits`, `set_budget_rollover` → `set_spending_limit_rollover`, `set_budget_rollover_start` → `set_spending_limit_rollover_start`. **Deliberate non-change:** `assign_budget` itself was NOT renamed — it was the one name in its own family not saying "envelope," but once the Tier 2 trio stopped using "budget," the 3-candidate collision the audit described ("set a budget for groceries") resolves without touching a CORE, heavily-referenced tool name; the marginal clarity of adding "envelope" wasn't judged worth that churn. **Out of scope:** `rollforward_import_batch`/`restore_import_batch` (a separate audit finding — two registry slots for the literally same function) is a duplicate-tool problem, not a naming problem; renaming can't fix it, it needs a decision to delete one. All 7 pairs recorded in `registry._LEGACY_TOOL_ALIASES` (not decorator `aliases=`, to stay in the migration-lockstep table) + `migrate_tool_names.RENAMES`; fixtures extended in `tests/fixtures/playbooks/` (`agents.json`'s `budget-keeper`, `skills/legacy-names-skill/`); golden schema keys renamed 1:1 with byte-identical `input_schema` values (`tests/golden/tool_input_schemas.json`).

**Tool renames — payee batch pluralization (2026-07-19, owner directive: "check all tools that we still have, and merge the ones that can be — such as `update_payees`, instead of just `update_payee`"):** distinct motivation from Tier 1/2 above — not a naming collision, but every single-payee write tool in `ai/tools/payees.py` gaining a batch shape and being renamed to its plural form to say so: `create_payee`→`create_payees`, `rename_payee`→`rename_payees`, `set_payee_default_category`→`set_payees_default_category`, `set_payee_hidden`→`set_payees_hidden`, `set_payee_autocategorize`→`set_payees_autocategorize`, `add_payee_alias`→`add_payee_aliases`, `remove_payee_alias`→`remove_payee_aliases`, `add_payee_rule`→`add_payee_rules`, `remove_payee_rule`→`remove_payee_rules`, `set_payee_rules`→`set_payees_rules`, `apply_payee_rules_to_payee`→`apply_payee_rules_to_payees`, `apply_payee_category`→`apply_payee_categories`, `delete_payee`→`delete_payees`. Each old name is declared BOTH ways (`@tool(..., aliases=("old_name",))` on the tool itself, for in-file discoverability, AND a mirrored entry in `registry._LEGACY_TOOL_ALIASES` + `migrate_tool_names.RENAMES`, since the alias-table-equality test compares against `_LEGACY_TOOL_ALIASES` specifically, not the full runtime alias map) — harmless redundancy, not a conflict (`tool()`'s registration check tolerates re-asserting an identical existing mapping). **Deliberately NOT renamed:** `get_payees` (already plural; `payee_id` widened to scalar-or-list instead), `merge_payees` (already plural — a merge always names two payees; gained a `merges` batch array), `apply_payee_rules` (a GLOBAL ledger rescan, same reasoning as `restore_checkpoint` — "apply to several ledgers" is incoherent; `apply_payee_rules_to_payees` is its already-scoped sibling). Two batch shapes, chosen per tool on whether a divergent-per-item value is a real scenario (`ai/tools/payees.py`'s module docstring): **items-or-bare** (`_items_or_bare`) for tools where each item is inherently distinct (a new payee's `name`, a rename's `new_name`, an alias/rule's own text — normalization/conflict rules mean the SAME value can't legitimately apply to two payees) — bare top-level fields wrap into a one-item batch internally, so the single-target call stays byte-identical to before; **broadcast-or-updates** (`_broadcast_or_updates`) for the "same change on N payees" case (hide these 5, set this category on these 3) — `payee_id` widens to scalar-or-list (the `_txn_schema.py` 2-way `anyOf` idiom, no `null` branch) and the tool's own field(s) broadcast to every id, with an `updates`/`removals` array escape hatch for divergent values. Every batch is non-atomic per item (mirrors `bulk_update_transactions`, not `add_transactions`' ledger-write atomicity) and reports a `results: [{index, ok|error, ...}]` array so partial failure stays legible; a single-target call gets the exact pre-batch response shape (`payee`/`merged_into`/`deleted`+`reassigned_to`/`updated`), never a `results` wrapper it didn't ask for. `apply_payee_rules_to_payees`/`apply_payee_categories` keep `confirm=true` REQUIRED regardless of batch size (never auto-approved by a long `payee_id` list) since they're bulk HISTORICAL relabels, not idle metadata — the blast-radius note already on these two tools before this change.

**Tool renames — arity-naming fixup (2026-07-19b, owner incident: "why delete_account is still singular, and account_id is even though it's a list -> it's not account_ids? ... please do it properly for all tools!"):** a re-audit of `portfolio.py`/`commitments.py`/`budgets.py` found tool NAMES the earlier batch-pluralization passes missed — each already had a batch ARRAY param (the set of entities the tool acts on), but kept a singular name, so the name lied about arity. Renamed: `resolve_instrument`→`resolve_instruments`, `update_instrument`→`update_instruments` (`portfolio.py`); `update_commitment`→`update_commitments`, `update_commitment_action`→`update_commitment_actions`, `update_commitment_installment`→`update_commitment_installments` (`commitments.py`, overriding an earlier in-file note that had argued against it — the blocking concern, a literal name pinned in `tests/test_parity_backfill_tools.py`, is a test to update, not a reason to keep the lie); `set_budget_rollover`/`set_spending_limit_rollover`→`set_spending_limit_rollovers` (`budgets.py` — the alias chain is RE-POINTED at the final name in one hop, since `set_budget_rollover` already aliased to `set_spending_limit_rollover`, and aliases never chain). Each rename declares `aliases=(...)` on the new tool AND a mirrored `registry._LEGACY_TOOL_ALIASES` + `migrate_tool_names.RENAMES` entry, per the payee lane's precedent above. **The rule applied (not just these six):** is the array the SET OF ENTITIES the tool acts on, or an ATTRIBUTE of one entity? Entity set → tool name AND param plural. Attribute of one → leave alone — confirmed correct as-is: `update_grant(tranches=[…])`, `propose_lot_dispose(lot_selection=[…])`, `set_funding_plan(funding_plan=[…])`, `update_target(linked_account_ids=[…])`, `set_commitment_actions(actions=[…])`/`set_commitment_installments(installments=[…])` (each already-plural, describing ONE commitment's own schedule/checklist). **Deliberately left singular despite an entity-set array** (a judgment call, not an oversight): `apply_position_add/update/remove` (`adds`/`updates`/`removals`) and `apply_lot_acquire/dispose/delete/dispose_undo` (`acquisitions`/`disposals`/`deletions`/`event_ids`) — each pairs with a same-named `propose_*` tool that previews exactly ONE item; renaming only the `apply_*` half would break the propose→apply name symmetry the model uses to find the tool that commits a given proposal, and the batch param name + tool description already disclose the arity honestly. **SUPERSEDED 2026-07-19d** (see "Tool renames — apply_* retirement" below): the propose→apply pair collapse retired the propose halves entirely, so there is no symmetry left to preserve, and all seven were renamed to their plural form (`add_positions`, `acquire_lots`, …). Their `tool_inventory.py` exemptions were DELETED rather than re-keyed — the rename satisfies the rule outright, so the linter now enforces those names like any other. `envelopes.py` had no defects — every tool there is inherently single-envelope (even `merge_envelopes` names exactly two, never a list).

**Tool renames — accounts/audit batch pluralization (2026-07-19, owner complaint: "why delete_account is still singular, and account id is [a list] -> it's not account_ids? what the fuck is that?"):** an earlier lane (`lane/accounts-audit-batch-tools`) had widened `update_account`/`delete_account`/`reconcile_account`/`set_transaction_cleared` (`ai/tools/accounts.py`) and `trace_transaction` (`ai/tools/audit.py`) to accept a batch by turning their SINGULAR id param into `anyOf[string, array]` WITHOUT renaming the tool — exactly the trap the "Tool error contract" `coerce_stringified_containers` note below describes: a singular name that secretly accepts a list primes the model to stringify a real list instead of passing one (`registry.coerce_stringified_containers`'s docstring quotes the live repro, `delete_account(account_id='["acc_fbae2c470000", "acc_b1af41ae0000"]')`). Fixed: `update_account`→`update_accounts`, `delete_account`→`delete_accounts`, `reconcile_account`→`reconcile_accounts`, `set_transaction_cleared`→`set_transactions_cleared`, `trace_transaction`→`trace_transactions` — both the tool name AND the batch param are now plural. **Shape, chosen per tool:** a bare list of ids (`delete_accounts`' `account_ids`, `set_transactions_cleared`'s `txn_ids`, `trace_transactions`' `transaction_ids`) is now a PLAIN ARRAY, no `anyOf` — there is nothing per-item to vary, so a scalar branch would only reopen the same ambiguity; `update_accounts` needed per-item changes (different accounts get different edits) so it mirrors `update_transactions` exactly: `updates: [{account_id, ...fields}]`, one item the trivial single-edit case; `reconcile_accounts` needed no shape change at all (it already took a batch `accounts` array) — only the tool name was singular. Every renamed tool's RESPONSE still special-cases exactly one resolved target back to its pre-rename singular shape (`{"account": {...}}`/`{"transaction": {...}}`/the bare single-trace object) — batching never changes what a single-target call sees, matching the payee-pluralization precedent below. Each old name is declared BOTH ways (`@tool(aliases=…)` + a mirrored `registry._LEGACY_TOOL_ALIASES`/`migrate_tool_names.RENAMES` entry, per the payee lane's rationale) and dispatch-time leniency still reads the legacy singular field (`account_id`/`txn_id`/`transaction_id`) when the new plural key is absent, so an old alias call keeps working byte-for-byte. `registry.coerce_stringified_containers` (the harness half — decodes a model-stringified list at dispatch regardless of tool) stays in place as the backstop for any tool, old or new.

**Tool renames — naming-finish (2026-07-19c, `backend/scripts/tool_inventory.py --lint`, four prior lanes had already taken the surface from 80 naming defects to 12; owner: "please do it properly for all tools! what the fuck is this naming convention!"):** the linter's rule is mechanical — is the array the set of ENTITIES the tool acts on, or an attribute OF one entity? Each of the remaining 12 already had an entity-set array (several DIFFERENT categories/rules/envelopes/groups, each independently resolved and reported — never one entity's own sub-parts) with an ALREADY-plural param, but kept a singular tool NAME, so 11 renamed straight: `add_category`→`add_categories`, `rename_category`→`rename_categories`, `remove_category`→`remove_categories` (`categories`/`renames`/`removals`); `set_category_color`→`set_categories_color`, `set_category_taxable`→`set_categories_taxable` (`updates`); `create_rule`→`create_rules`, `test_rule`→`test_rules` (`rules`); `dismiss_rule_suggestion`→`dismiss_rule_suggestions` (`dismissals`); `set_envelope_target`→`set_envelope_targets` (`targets`); `rename_envelope_group`→`rename_envelope_groups` (`groups`); `delete_envelope_group`→`delete_envelope_groups` (`group_ids`). **`assign_budget` was the one judgment call, and was left AS-IS** — its `assignments` array is the identical entity-set shape, but "budget" plays the same substance/domain-noun role `move_money`'s "money" already does (assigning BUDGET — a mass noun, like "assign money" — to however many envelopes, not several distinct "budgets"); `backend/scripts/tool_inventory.py`'s `UNCOUNTABLE_OBJECTS` gained `"budget"` alongside `money`/`cash`/`history`/`config`, recorded there with the full reasoning rather than a bare exemption, so a future reviewer can see WHY rather than just that. **NO ALIAS CHAINS**: three of these renames had an EXISTING alias pointing at their old name (`set_category_target`→`set_envelope_target`, `rename_category_group`→`rename_envelope_group`, `delete_category_group`→`delete_envelope_group`, all from earlier lanes) — since the rename target is now itself an alias, all three are RE-POINTED at the final canonical name in one hop (`set_category_target`→`set_envelope_targets`, etc.), mirroring the `set_budget_rollover` re-point precedent above; `test_no_alias_chains_and_no_alias_shadows_a_live_tool` catches a miss here. Each rename declares `@tool(aliases=(...))` on the tool itself AND a mirrored `registry._LEGACY_TOOL_ALIASES`/`migrate_tool_names.RENAMES` entry (49 pairs total, up from 38), per the payee lane's precedent (the alias-table-equality test compares against `_LEGACY_TOOL_ALIASES` specifically). `tests/golden/tool_input_schemas.json` had its 11 renamed keys updated in place (values byte-identical except the 3 schemas whose OWN description text named a sibling tool being renamed in the same pass — `apply_budget_change`, `rename_envelope_groups`, `set_envelope_targets` — updated to match, reviewed by hand). `.venv/bin/python3 -m backend.scripts.tool_inventory --lint` now returns zero defects.

**Tool renames — `apply_*` retirement + `create_account` (2026-07-19d naming-close).** The 2026-07-19 propose→apply pair collapse merged 14 pairs into one tool each, but left the survivor named after the SECOND HALF of a protocol that no longer exists — there is no propose step to "apply", so `apply_` described retired machinery instead of the effect, and a model looking for "sell a lot" had to know a dead two-call flow to guess the verb. **13 VESTIGIAL `apply_*` tools renamed to their real verb**, each confirmed by three markers (a `confirm_token` param, a `propose_*` entry in `_LEGACY_TOOL_ALIASES` pointing at it, no surviving propose half): `apply_lot_acquire`→`acquire_lots`, `apply_lot_dispose`→`dispose_lots`, `apply_lot_delete`→`delete_lots`, `apply_lot_dispose_undo`→`undo_lot_disposals`, `apply_position_add`→`add_positions`, `apply_position_update`→`update_positions`, `apply_position_remove`→`remove_positions`, `apply_vest`→`record_vest`, `apply_vest_undo`→`undo_vest`, `apply_record_income`→`record_income`, `apply_open_option`→`open_option`, `apply_close_option`→`close_option`, `apply_8b_import`→`import_8b`. **ARITY was decided, not inherited:** the 7 carrying an entity-set array (`adds`/`updates`/`removals`/`acquisitions`/`disposals`/`deletions`/`event_ids`) land PLURAL per rule 1 above — they had been exempted in `ATTRIBUTE_LIST_PARAMS` on the sole grounds of "propose/apply name symmetry", a rationale this rename retires, so those 7 exemptions were DELETED rather than re-keyed and the linter now enforces them; the 6 with no entity-set array stay singular, which is what the same rule requires. Each `propose_*` alias is RE-POINTED at the final name in ONE hop (never chained through `apply_*`) and each `apply_*` name is itself recorded as an alias — it is persisted data too. **10 `apply_*` tools KEPT**, and the distinction matters: choke points (`apply_budget_change`, `apply_agent_change`, `apply_skill_change`), "apply X to Y" (`apply_rules_to_history`, `apply_payee_rules`, `apply_payee_rules_to_payees`, `apply_payee_categories`), and — the ones a careless sweep would wrongly take — the LIVE nonce-confirm halves `apply_run_code`, `apply_code_skill`, `apply_external_mcp_call`: their propose halves never carried the prefix (`run_code`/`save_code_as_skill` are still live and still stage a nonce card, and external-MCP confirm is explicitly outside the permission teardown), each takes only a `nonce` with args read from disk, so `apply_` there is load-bearing semantics ("only after the user approved"), not scar tissue. Separately, `add_account`→`create_account` per rule 2. Alias bookkeeping for all 14 follows the payee-lane precedent (`@tool(aliases=…)` + mirrored `registry._LEGACY_TOOL_ALIASES` + `migrate_tool_names.RENAMES`; 89 pairs total). Golden schema keys renamed 1:1 with byte-identical values except one deliberate cascade (`add_positions`' `adds` description named `apply_lot_acquire`). ADR: `docs/adr/2026-07-19-tool-naming-rules-enforced.md`.

## 2026-08-03 — `set_run_plan` → `add_run_tasks` + `update_run_task`

Lived for about an hour, on this branch only, and was never shipped — so it is recorded
here rather than aliased. It took the WHOLE plan on every call and replaced it wholesale.
That is idempotent, but it is not how a run thinks: one task begins, one task ends. Every
status change re-sent every subject, and the chat timeline filled with identical
`set_run_plan` rows that said nothing about what had actually changed.

Replaced by the granular pair, modelled on how Claude's own scheduled tasks read
("Added task: …", "Started task", "Completed task"):

* `add_run_tasks(tasks=[{subject, description?}])` — appends, returns each task's id.
* `update_run_task(task_id, status)` — one id, one status.

A run may add more tasks part-way through without restating the ones it has finished.

## 2026-08-09 — `run_skill` → `activate_skill`

Landed the same day as the Claude-runtime-parity rewrite (`ai/skill_runtime.py`,
`docs/adr/2026-08-09-host-owns-skill-invocation-lifecycle.md`) — never shipped under the old
name, so recorded here rather than aliased, per the `set_run_plan` precedent above.

`run_skill` mis-described the effect: the tool executes nothing itself — it validates a
requested activation and returns a private marker; the host then loads the skill's rendered
instructions and widens the turn's tool offer. The verb "run" belongs to `run_script`/
`run_code`, its neighbors in `_GATE_NEVER_AUTO_EXECUTE`, which really do execute something
with a direct effect. `activate_skill` matches the vocabulary the implementation already used
throughout (`activate_skill_scope`, `ACTIVATION_MARKER`, `InvocationRequest`) and reads
unambiguously next to the skill-definition CRUD tools (`create_skill`/`update_skill`/
`delete_skill`), which "load_skill" — the other name considered — could be misread as extending
into ("load the skill's definition for editing").

`RunSkillInput` → `ActivateSkillInput` (`ai/tools/skills.py`); the golden schema key and every
test/doc reference renamed alongside it.

## 2026-08-02 — Agent tools RETIRED (no alias, no successor)

Not a rename. The Agents product was retired (ADR `2026-08-02-retire-agents.md`) and these
~20 tool names were **deleted outright**. They are recorded here because a tool name is
persisted data — it lives in skill frontmatter, backups, and in-flight transcripts — so a
reader who finds one needs to learn what happened to it rather than assume a typo.

**Deliberately NOT aliased.** Every rename above wires an alias so an old call keeps
working. There is nothing to point these at: Agents no longer exist, so a call to
`run_agent_now` cannot be honoured by anything. An alias would have to either fail with a
confusing error from a differently-named tool, or silently do something the caller did not
ask for. Failing as an unknown tool is the honest outcome — `tests/test_agents_retired.py`
asserts no registered name contains "agent".

**Authoring / lifecycle:** `create_agent`, `update_agent`, `delete_agent`,
`apply_agent_change`, `enable_agent`, `disable_agent`, `list_agents`.

**Dispatch:** `run_agent_now`, `dispatch_to_agent`.

**Findings + metrics:** `get_agent_findings`, `review_agent_finding`, `get_agent_runs`,
`get_agent_metrics`.

**Self-improvement loop:** `get_self_improvements`, `review_self_improvement`.

The replacement for all of it is an Automation running Lucius: `create_automation`,
`update_automations`, `run_automation_now`, `list_automations`. Those are not aliases of the
above — an Automation is a different object with a different shape — which is the other
reason no alias table entry would have been honest.

Rules and Scheduled Tasks were retired in the same wave (`f9b5d790`), taking
`create_rules`, `test_rules`, `apply_rules_to_history` and the
`schedule_task` / `update_scheduled_task` family with them, for the same reason and with the
same non-alias decision.

**Correction (verified against the live registry, 2026-08-23): `get_rule_suggestions` and
`dismiss_rule_suggestions` are back** — both are live, dispatchable tools today
(`backend/ai/tools/automations.py`), not deleted. The standalone rules CRUD/apply-to-history
surface stayed retired, but the "you keep re-categorizing this payee" correction-suggestion
signal (`services/corrections.py`) survived and was re-homed into the Automations tool group —
see `docs/ai/TOOL_CATALOG.md`'s "Automations" entry. Whether this counts as "never actually
deleted" or "deleted then re-added under the same name" wasn't tracked at the time; either way,
do not treat this doc's original 2026-08-02 line above as proof these two names are dead.

## 2026-08-20 — `get_signals` → `get_notifications`, `mark_signals_read` → `mark_notifications_read`

Naming-collision fix, not an arity/vocabulary rename: an audit of `SignalsStore`'s actual live
content (200/200 ring rows, full cap) found it held operational/automation activity — daily
transaction/IBKR digests, mail-scan summaries, `run_ledger` failure/recovery alerts,
`ai/_confirm_gate.py`'s unattended-write review rows, a handful of reminders — never market or
news signal data, which lives entirely in the separate, checkpoint-INCLUDED `IntelSignalsStore`.
The name also collided with that store's `@`-mention type: `intel_signal` was actually backed
by `SignalsStore`, not `IntelSignalsStore`, exactly the opposite of what its name implied.
Renamed the store to `NotificationLogStore`/table `notification_log` (revision 0044) and both
tools to match; the `@`-mention type `intel_signal` → `notification` in the same pass
(`services/mentions/descriptors/new_types.py`).

**Deliberately NOT aliased** — a departure from every rename above. This instance is
single-operator and self-hosted; there is no other caller who could hold a stale reference this
change doesn't already control, so the alias/migration-table machinery this doc otherwise
mandates is pure overhead here. See
`docs/adr/2026-08-20-no-compat-aliases-single-user-instance.md` for the standing exception this
sets for future renames in this repo.

## 2026-09-05 — `connector_sync`/`<provider>_sync_now` retired (RFC `connectors-as-data-sources`)

Not a rename — an outright retirement, same non-alias reasoning as the Automations/Rules wave
above and the same standing single-user exception the 2026-08-20 ADR sets. Every bank/broker
connector was a hidden scheduler-driven sync pipeline (a poll interval, an ingest job per
enabled provider, an adapter that fetched-deduped-AND-booked a row) with a REST/tool `sync now`
action layered on top. All of it is gone: `connector_sync` (the bulk, cross-provider action)
and the nine per-provider `<id>_sync_now` tools (`simplefin_sync_now`, `plaid_sync_now`,
`lunch_money_sync_now`, `lunch_flow_sync_now`, `ynab_sync_now`, `enable_banking_sync_now`,
`yodlee_sync_now`, `alpaca_sync_now`, `ibkr_sync_now`) no longer resolve to anything.

**No single-tool replacement for `connector_sync`** — a bulk action across "whichever
providers" made sense for a scheduler ticking unattended; a Lucius turn already knows which
provider it means, so the model now calls that provider's own fetch tool directly. Each
`<id>_sync_now` DOES have a direct sibling: `<id>_fetch_transactions` for the seven bank
aggregators, `alpaca_fetch_positions`/`ibkr_fetch_activity` for the two brokers — but a fetch
tool is a different CONTRACT, not a renamed sync: it never books anything itself (the model's
own next `add_transactions`/position-tool call does, through the ordinary choke, which is what
makes a re-fetch idempotent), it is read-shaped rather than scheduled, and it returns
draft rows the caller decides whether to book. `connector_status`/`<id>_get_status` are
UNCHANGED — status reporting was never part of what retired here.

Recorded in `backend/scripts/tool_renames.py`'s `RENAMES` (the live linter table — the bulk
name maps to advisory text naming the pattern above, each per-provider name to its direct
fetch-tool sibling) rather than aliased, per the ADR referenced above. ADR:
`docs/adr/2026-09-05-connectors-are-data-sources-lucius-books.md`.

**Tool retirement — `set_spending_limit_rollover_start` (2026-09-26, budget & category tools review):** it wrote `budget_config.rollover_start`, which is not only the legacy spending-limit carryover anchor its name described but Zero-Based's carry floor and Ready to Assign's zero line — so a "spending-limit" call could move the whole budget's start without the fresh start's same-transaction assignment clear or its Undo. Retired outright (no replacement tool; a fresh start is the Plan screen's). Recorded in `backend/scripts/tool_renames.py`'s `RENAMES`; `set_spending_limit_rollovers`' description no longer names it. ADR: `docs/adr/2026-09-26-budget-and-category-tools.md`.

**Tool renames — budget & category tools, phase 2 (2026-09-26):** `add_categories`→`create_categories` (rule 2 — a category has no parent) and `remove_categories`→`delete_categories` (`remove_` detaches from a parent; its batch field is `deletions`, after `delete_lots`); `link_category`/`unlink_category`→`link_categories`/`unlink_categories` (`category_ids` — several in one card); `move_envelope_to_group`→`move_envelopes_to_group` (`envelope_ids` — its `categories` param held envelope ids); `rename_envelope` + `set_envelope_note`→`update_envelopes` (`updates=[{envelope_id, name?, color?, note?}]`, the note through a new `envelope_note` op on the budget choke); `get_envelope_note` retired (every `list_envelopes` row carries its note). Param-only: `reorder_envelopes_in_group`'s `categories`→`envelope_ids`; `delete_envelope_groups` advertises only `group_ids`. Deliberately NOT renamed: `create_envelope_group` (its `categories` really are category keys) and `auto_assign` (it writes on approval — it was re-classified from READ_ONLY_TOOLS to `mutates=("budget",)` instead). All recorded in `RENAMES`. ADR: `docs/adr/2026-09-26-budget-and-category-tools.md`.

**Tool retirements + renames — budget & category tools, phase 3 (2026-09-26):** retired `set_spending_limits` and `set_spending_limit_rollovers`. The flat per-category limits have no screen and feed no figure the user sees, and a live chat built a whole "set your caps" answer on them. A cap belongs to the budget method: `apply_budget_verb` (50/30/20 `set_caps`/`copy_targets_to_caps`) or `set_envelope_targets` (Zero-Based). The savings-goal tools say goal, as the app does: `get_targets`→`get_goals`, `get_target_allocation`→`get_goal_allocation`, `create_target`/`update_target`/`delete_target`→`create_goal`/`update_goal`/`delete_goal` (`target_id`→`goal_id`, results carry `goal`; module `ai/tools/budgets.py`→`goals.py`). All recorded in `RENAMES`. The starters that named them (`budget-review`, `investment-checkup`) were rewritten, and an unedited installed copy is refreshed at boot (`SkillStore.refresh_superseded_starters`). ADR: `docs/adr/2026-09-26-budget-and-category-tools.md` (phase-3 amendment).

**Tool rename — `apply_budget_verb` → `apply_budget_operation` (2026-09-26, owner):** "verb" was the budget engines' internal word for a method's own action, and it surfaced verbatim as a chat step ("Apply budget verb"). The param `verb_id` is now `operation`, and `list_budget_engines` reports `engines[].operations`. Results name every envelope they touch. The step row is labelled from the operation ("Set caps (50/30/20)"), including for stored transcripts that still carry the old name. Engine internals (`Verb`, `apply_verb`, `POST /api/planning/verb`) keep the old word. Recorded in `RENAMES`.

**Tool retirements — `restore_envelope` + `list_archived_envelopes` (2026-09-26, owner: "we should stop archiving envelope, just delete"):** `delete_envelope` now HARD-DELETES an envelope — its row, budget_assignments across every month, its Zero-Based target/note and its 50/30/20 cap all go, in one transaction, atomically — instead of archiving it. With nothing left that ever becomes archived-and-restorable going forward, both tools are retired outright (no replacement tool): a delete just made is undone by the human's own Undo pill in the UI (`POST /api/planning/envelopes/undo-delete`, a server-held one-shot token — never a Lucius tool, the same shape `fresh-start/undo` already uses), and an OLDER Lucius write's recovery path is the generic `restore_checkpoint`. Every other envelope death (merge's source, a category rekey's sole-member merge, a category delete's now-empty envelope, a dropped-out Commitment/credit-card's synced envelope) hard-deletes the same way. Pre-existing archived rows are left untouched and still readable via `GET /api/planning/envelopes/archived` (REST) — nothing new can ever land there again. Both retired names recorded in `RENAMES`. ADR: `docs/adr/2026-09-26-deleting-an-envelope-deletes-it.md`.

**Tool rename — `connector_status` → `get_connector_status` (2026-10-10, owner):** it reported only the bank/broker aggregators, so Lucius could not see whether email (or Telegram, a webhook, a price source, web search, an MCP server) was connected and asked the user instead — while the onboarding skill and the system prompt already told it to check. It now reads the same `services.connectors.list_connectors()` view the Connectors screen renders: per connector `{id, name, enabled, status, error?}` (the sanitized `error` only while the connector is failing; never config). The param is `connectors` (any Connectors id; omit for all); the old `providers` is still accepted, unadvertised. Renamed to the `get_` read prefix with the contract change. Recorded in `backend/scripts/tool_renames.py`'s `RENAMES`; stored transcripts keep their step label (`ai/tools/display.py`).

**Tool rename — `get_connector_permissions` → `get_connector_tools` (2026-10-10, owner):** once a connector's on/off "capabilities" were removed (ADR `2026-10-10-connectors-live-in-a-connectors-table-and-secrets-in-connector-secrets`), the honest answer to "what can this connector do?" is its tool list. The tool already returned exactly that — every tool, grouped read-only vs write, each with its permission level — so it is named for it, beside `get_connector_status` ("is it set up and working?"). Same input and output; recorded in `backend/scripts/tool_renames.py`'s `RENAMES`, no alias.
