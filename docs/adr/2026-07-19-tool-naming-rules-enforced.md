# Tool naming rules are ENFORCED, and `apply_` is retired

- **Date:** 2026-07-19
- **Status:** Accepted
- **Supersedes (in part):** the "deliberately left singular ... propose/apply name symmetry"
  judgment recorded in CLAUDE.md's 2026-07-19b arity-naming-fixup note.
- **Related:** `2026-07-19-derive-tool-schemas-from-models`,
  `2026-07-19-ledger-confirm-gate-one-tool-stored-args`,
  `2026-07-18-remove-layered-ai-permission-system`

## Context

Five separate lanes had driven the AI tool surface from 80 naming defects to 0. Every one
of them was a manual sweep, and `backend/scripts/tool_inventory.py --lint` — the script
that FOUND those defects — printed its findings and then **exited 0**. Nothing stopped the
81st. A tool name is persisted data (agent `allowed_tools`, skill frontmatter, backups,
in-flight transcripts), so a defect that ships is expensive to fix later: it needs an
alias, a migration-table entry, and a hand-reviewed golden update. That asymmetry — cheap
at review time, expensive afterwards — is the definition of something that belongs in a
blocking gate rather than in a document asking future code to behave.

Three further defect classes were still open, and they share a root cause: **the names and
the descriptions were not treated as part of the contract the way the schemas were.**

1. **`apply_*` was vestigial.** The 2026-07-19 propose→apply pair collapse merged 14 pairs
   into one tool each, but the survivor kept the name of the SECOND HALF of a protocol that
   no longer exists. There is no propose step left to "apply", so `apply_` named retired
   machinery instead of the effect. A model looking for "sell a lot" had to know a dead
   two-call flow to guess the verb.
2. **~17 descriptions steered the model to dead alias-only names** ("then call
   `add_transaction`"). Dispatch resolves those aliases, so nothing broke loudly — the
   drift was invisible, and would become a hallucination the harness itself taught the
   moment an alias was retired (aliases are explicitly "a bridge, not a permanent name").
3. **Dual-shape schemas advertised a batch array AND a root-level copy of its own item
   fields.** Two spellings of one call is a fork carrying no information: the model must
   choose, the choice means nothing, and a half-mix (a bare `payee_id` beside an `updates`
   array) type-checks while meaning something nobody designed.

## Decision

### 1. The linter is a blocking gate

`tool_inventory --lint` now exits non-zero on any defect and runs in `make lint`.

**Placement — `lint`, not `check`:** it is a static contract check over the registry, like
import-linter. It needs no server, no fixtures, and no test data. `check` runs `lint`
first, so one placement covers both gates; putting it only in `check` would leave the fast,
most-frequently-run gate blind to the exact defect class this repo has re-introduced five
times.

It enforces four rules, each with an explicit **reviewed exemption table** rather than a
heuristic — a heuristic cannot separate these cases, a person can, once:

- **Entity-set arity** (pre-existing): is the array the SET OF ENTITIES the tool acts on, or
  an ATTRIBUTE of one entity? Entity set → tool AND param plural. Exemptions:
  `ATTRIBUTE_LIST_PARAMS`, `UNCOUNTABLE_OBJECTS`.
- **No dual-shape duplication** (new): a batch array must not be advertised alongside a
  root-level copy of its item fields. Exemptions: `BROADCAST_VALUE_TOOLS`.
- **No dead alias in a description** (new): tool AND param descriptions are swept.
- **`create_` vs `add_`** — written into CLAUDE.md but **deliberately NOT machine-enforced
  yet**; see Consequences.

### 2. `apply_*` retired where it was vestigial — 13 tools

Classified by three markers (a `confirm_token` param, a `propose_*` alias pointing at it,
no surviving propose half), not by prefix:

`acquire_lots` · `dispose_lots` · `delete_lots` · `undo_lot_disposals` · `add_positions` ·
`update_positions` · `remove_positions` · `record_vest` · `undo_vest` · `record_income` ·
`open_option` · `close_option` · `import_8b`

**10 `apply_*` tools were KEPT**, and the distinction is the substance of the decision:
choke points (`apply_budget_change`, `apply_agent_change`, `apply_skill_change`); "apply X
to Y" (`apply_rules_to_history`, `apply_payee_rules`, …); and — the three a prefix-driven
sweep would have taken wrongly — the LIVE nonce-confirm halves `apply_run_code`,
`apply_code_skill`, `apply_external_mcp_call`. Their propose halves never carried the
prefix (`run_code` / `save_code_as_skill` are still live and still stage a nonce card, and
external-MCP confirm is explicitly outside the permission teardown), and each takes only a
`nonce` whose args are re-read from disk. There, `apply_` is load-bearing semantics — "only
call this after the user approved" — not scar tissue.

**Arity was decided here, not inherited.** The 7 renamed tools carrying an entity-set array
land PLURAL. They had been exempted in `ATTRIBUTE_LIST_PARAMS` on the sole stated grounds of
"propose/apply name symmetry" — a rationale this rename retires. Those 7 exemptions were
**deleted rather than re-keyed**: the rename satisfies the arity rule outright, so the
linter now enforces those names like any other. *An exemption that outlives the reason it
was granted for is how a lint table rots into a rubber stamp.* The 6 tools with no
entity-set array stay singular, which is what the same rule requires of them.

### 3. `add_account` → `create_account`

**`create_` mints a STANDALONE entity; `add_` inserts into a NAMED PARENT.** An account has
no parent, so `add_account` asserted a containment relationship the data model does not
have. The service layer already had this right (`services/categories.py::create_category`),
and the collision is real: `services/envelopes.py::add_category(envelope_id, category_id)`
is a *correct* add — linking an existing category into a parent envelope.

### 4. Dual-shape schemas advertise the array only — 35 tools

`schema_from_model.schema_of_batch_only(Model, array=…)` un-advertises the duplicated bare
fields. `update_transactions` is the shape matched: advertise the array, keep accepting the
bare form in the handler, leave that leniency **undocumented**.

**Un-advertising is not removing.** `registry.dispatch` validates the EFFECTIVE type only
and never enforces `additionalProperties`, so an undeclared key still reaches the handler
untouched — every stored bare caller keeps working byte-for-byte. The bare fields also stay
declared on the **model**, deliberately: the model records what the handler ACCEPTS, the
schema records what the model is TOLD to send. Deleting the fields would destroy the answer
to the first question in order to change the second.

## Alternatives rejected

- **Rename the 7 entity-set tools to singular forms** (`add_position`, `acquire_lot`), as
  the first-pass map proposed. This would re-commit the exact defect the arity lane exists
  to fix, in the same commit that makes the linter blocking.
- **Re-key the 7 symmetry exemptions onto the new names.** Cheaper, and wrong: it would
  preserve an exemption whose justification no longer exists, and the rename had already
  made the names compliant.
- **Delete the bare fields from the Pydantic models** instead of filtering the schema. This
  would have made the schema honest by making the model lie, and it is the model that
  documents what a stored caller may still send.
- **Machine-enforce `create_`/`add_` now.** `add_categories` violates it (a category has had
  no parent since the envelope⊥category split) and `add_transactions` / `add_news_sources`
  are borderline. Enforcing it would fail the build on tools outside this lane's scope,
  forcing either an out-of-scope rename or a rubber-stamp exemption. The rule is written
  down; the violators are on the board; the check is the follow-up.

## Consequences

- `make lint` (and therefore `make check`) fails on any naming defect. Verified by
  introducing one, observing a non-zero exit naming the tool, and reverting.
- 89 alias pairs in `registry._LEGACY_TOOL_ALIASES` / `migrate_tool_names.RENAMES` (was 75).
  Every `propose_*` alias is re-pointed at the final name in ONE hop — never chained through
  the retired `apply_*` name — and each `apply_*` name is itself recorded, since it is
  persisted data too.
- The golden schema file changed in 39 places. Reviewed by asserting the delta was **only**
  removed root properties, adjusted `required`, and description text — no structural change
  to any retained property. That verification is the review moment the file's
  never-auto-regenerate policy exists to force.
- `tests/test_domain_introspect.py` learned that a batch tool advertises its fields inside
  `<array>.items`. Reading only the root would have reported `assign_budget` as not offering
  `amount`/`envelope_id`/`month` — false, and pinning it would have recorded a lie to keep a
  test green. Three tools' required-side divergences joined the existing pinned registers,
  matching the `budget.assign` precedent exactly; one stale pin (`position.add`'s `quantity`)
  was RETIRED rather than carried.
- Registry count unchanged at 256 — every rename is a rename, not an addition.

## Agent provenance

Built by a lane agent under a charter whose binding rules were *evidence not assertion*,
*enforce don't document*, and *a finding is not progress — an out-of-scope defect goes to
the board, not into this diff*.

Two independent maps were supplied and **both were verified rather than trusted, and both
were wrong**. The dual-shape population was mapped as 22 tools (13 safe to change); the live
registry has 45, of which 35 are safe — the map's detector had missed most of them. Its
"9 broadcast tools to leave alone" was 10 (`remove_watchlist_items`). The dead-alias
description sweep was mapped at 17; sweeping PARAM descriptions as well as tool descriptions
found an 18th (`list_categories`). Acting on either map as given would have left the defect
half-fixed while reporting it closed.

Three things were nearly gotten wrong and were caught by insisting on evidence:

- BSD `sed` silently ignores `\b`, so the first rename attempt was a **no-op that printed
  success**. Caught by testing the substitution on a fixture before running it. Perl was used
  instead.
- The first "prove the linter fails" attempt returned exit 1 from an **unrelated exception**
  (the alias-shadow guard), which would have been reported as proof the gate worked. Caught
  by reading stderr instead of trusting the exit code.
- A `\n\n` in a replacement string was interpreted literally and broke a Python file. The
  linter then reported **zero defects** — because the module no longer imported. A green
  result from a broken build is the most dangerous shape of evidence there is; caught only
  because zero was a suspicious answer and stderr was checked.

Out-of-scope defects found and routed to `docs/launch/BOARD.md` rather than fixed here:
`add_categories` violating the create/add rule; 7 stale keys in `ATTRIBUTE_LIST_PARAMS`
pointing at tools that no longer exist; and a pre-existing test-order pollution in which
4 test files pass in isolation and fail in the full suite (proven pre-existing on a clean
tree at baseline).
