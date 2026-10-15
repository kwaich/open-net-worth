# One permission store for every tool

- **Date:** 2026-08-14
- **Status:** Accepted
- **Supersedes in part:** `2026-08-13-what-a-model-may-see-of-a-connector` (the
  single-caller / `is_enforced` half), `2026-08-13-connector-tool-permissions-storage`
  (the table name and the level vocabulary)
- **Agent provenance:** implemented by a `senior-engineer` lane on
  `feat/enforce-native-permissions`, under the owner's direct rulings recorded inline below.

## Context

The stated task was narrow: make the connector permission ladder REAL for native
connectors. `services/tool_permissions.get_tool_permission` had exactly one production
caller — `mcp/client_manager` — so a level set on Plaid, Alpaca, IBKR, email or a
web-search provider was stored, displayed, and consulted by nothing. A test
(`test_get_tool_permission_has_exactly_one_production_caller`) pinned that gap, and
`is_enforced()` existed purely so the UI could admit it.

Investigating the dispatch path turned up a bigger problem than a missing caller. Native
tools were **not** ungated — they were gated by a **second, older, independent store**:

| | `tool_grants` | `connector_tool_permissions` |
|---|---|---|
| shape | binary — granted or not | three levels |
| key | tool NAME | (connector, tool) |
| surface | Settings › Permissions + the confirm card's "Always allow" | the Connectors tab's ladder |
| enforced for | every native tool, at `registry.dispatch` | external MCP only |

Two stores, both answering *may this tool run*, able to disagree, with no way for the user
to tell which had won. A user could set `sync_plaid_now` to Blocked in Connectors and grant
it in Settings, and the grant would win silently.

The owner's ruling, on being shown this: the ladder should be **another surface of the same
permission system, not a second system**. The real defect is the duplication, not the gap.

## Decision

**One store. `connector_tool_permissions` is it, renamed `tool_permissions`.
`ToolGrantStore` is absorbed into it and deleted. One check at dispatch.**

### Why that table survived and the other did not

Not seniority — expressiveness. The grant store could not represent either of the two
things the merged table must:

1. **Three states.** `blocked` has no encoding in a binary grant. It is also the tier with
   no prior existence for native tools at all, so the merge *adds* a capability rather than
   trading one away: `delete_transactions_where` can now be refused outright.
2. **A composite key.** Two external MCP servers may each advertise a tool called `search`;
   a tool-name-only key cannot tell them apart. The same key is what stops a native
   decision leaking sideways — `ai/tools/connectors.py` records the incident that split the
   per-provider tools apart in the first place, where one shared `connector_sync` name meant
   "always allow Plaid syncs" would silently have allowed IBKR too. `plaid:sync_plaid_now`
   and `ibkr:sync_ibkr_now` are two rows that cannot be one decision.

`connector_id` therefore becomes the tool's **owner**: its `@tool(connector=...)` id,
`mcp:<server_id>`, or the reserved bucket `core` for the ~160 unscoped tools. Once unscoped
tools live there, the `connector_` prefix is false, hence the rename (owner's ruling).

### The levels say what they mean

`always_allow` | `needs_approval` | `blocked` — matching the UI labels exactly (owner's
ruling). Bare `allow` was ambiguous about "permitted once" vs "permitted always", which is
precisely the distinction a permission store exists to record.

### How the ladder composes with the confirm gate — the question this ADR exists to answer

The brief warned against double-gating: several native tools already went through the
confirm gate and `ToolGrantStore`, so stacking a ladder on top would mean two prompts for
one call. The merge dissolves the question rather than answering it, because the two
mechanisms turn out to be **one mechanism with two vocabularies**:

| confirm-gate concept | its spelling in the merged store |
|---|---|
| `is_gated_tool(t)` is False (a read) | the *default* for an undecided `core` tool: `always_allow` |
| `is_gated_tool(t)` is True (a write), no grant | the *default* for an undecided `core` tool: `needs_approval` |
| a stored `scope: "always"` grant | a stored `always_allow` row |
| — (inexpressible) | a stored `blocked` row |

So `_confirm_gate.should_gate`'s two opening questions (*is this tool gated at all* and
*has the owner granted it*) collapse into one: **is the effective level
`needs_approval`?** The gate's remaining narrowings — the Automation approval mode, the
surface's ability to render a card, a bound `tool_use_id` — are untouched, and they now
apply to exactly one level.

There is therefore **one card, never two**. The card is the existing generic permission
card (`type: "tool_permission"`, keyed by `grant_tool`), and its "Always allow" button
writes `always_allow` into this same store — which means the standing decision a user makes
on a card is the one the Connectors ladder and Settings › Permissions both display. Before
the merge that button wrote a row neither of those surfaces could see.

**One dispatch site.** `registry.dispatch` resolves the level once per call via
`_confirm_gate.effective_level`, refuses `blocked` before anything else, and hands the
resolved level down to `should_gate_dispatch` so the permission table is read once, not
twice. Measured cost of that resolution: **~71 µs** for a tool with a stored decision or an
undecided read (one primary-key lookup), **~135 µs** for an undecided tool that would ask
(a second lookup for the yolo row, deliberately skipped when the answer could not change).
The store it replaces did a **full table scan** (`ToolGrantStore.effective` called
`self.all()`) on every gated call, so writes get faster; undecided reads pay one indexed
lookup they previously did not, which buys them the ability to be blocked at all. `blocked` is checked *first*, which covers redemption as well as a fresh call: a
tool the owner blocks while its confirm card is on screen is refused rather than fired,
mirroring the external-MCP apply path, which already re-read its level before firing stored
args.

### Three resolution rules that were genuinely contested

**1. An undecided `core` tool keeps the approval model's default, not the ladder's.**
The ladder's rule is "`needs_approval` uniformly, read or write, no tiering by tool type" —
correct for a connector, where a freshly enabled provider's tools should ask before their
first run. Applying it to unscoped tools would card `get_accounts`, `get_net_worth`, and
every other read in the app. The brief's own constraint decides it: *untagged tools keep
behaving exactly as today*. So the default is per-owner — the ladder's for a connector-owned
tool, `is_gated_tool`'s for a `core` one — while the STORED vocabulary is uniform. A user
can still set any core tool to any of the three levels; only the meaning of *no row* differs.

This is not tiering by tool type inside a connector, which remains banned. It is the
boundary between a domain that had a permission model already and one that is getting its
first.

**2. YOLO does not override `blocked`.** The blanket "stop asking me" row is now
`(core, "*") = always_allow`. It is consulted ONLY for a tool that is undecided *and* would
otherwise ask — so a stored decision always wins, and an ordinary read never pays for the
lookup. Yolo means *stop asking me*; it has never meant *ignore what I refused*. (It could
not previously conflict with a block, because native tools had no block.)

**3. `always_allow` fires unattended, but an Automation's own authority still comes
first.** The standing rule (`CLAUDE.md`, Security-critical areas) is unchanged and was not
reopened: an `always_allow` tool fires on a headless/Automation/skill surface with no human
present. The converse — should an undecided-or-`needs_approval` tool REFUSE unattended
rather than fall through? — was considered and rejected. The confirm gate deliberately
fails open on a surface that cannot render a card, and making `needs_approval` fail closed
would break every shipped Automation that touches a connector tool, to enforce a preference
the owner already has a control for.

There is exactly **one** place a stored `always_allow` does not immediately fire, and it is
older than this merge: inside an Automation run, that Automation's own verdict
(`manual`/`auto`/`skip` plus its allowed list) is consulted BEFORE the global table, because
**an Automation must never inherit a permission granted in an unrelated chat**. The other
half of that same rule already existed — `grant_automation_tool` writes an "Always allow"
tapped inside a run onto the Automation, not globally. A first draft of this change
inverted the order and let the global level answer first — in **both** of the two gates
that exist (`_confirm_gate.should_gate` for the dispatch-level card, and
`registry.should_preview_write`, the 13 propose/apply-merge tools' separate, pre-existing
preview path). The first inversion was caught by
`test_a_run_can_never_write_the_policy_that_governs_runs`; the second had no test at all,
which is itself the finding — a second gate is a second place to get precedence wrong.
Both are now pinned, by `test_a_manual_automation_still_asks_about_a_globally_allowed_tool`
and `test_a_self_staging_tool_obeys_the_level_and_the_automation_in_that_order`. The
direction of the exception is narrower, never broader: a `manual` Automation asks about
something the owner globally allowed, and no mode, allowed-list entry or verdict can clear
a `blocked` (dispatch refuses it before any of that is consulted).

One related widening was needed to keep that coherent: the Automation's "approval tier" is
now *would this call ask, absent the Automation's own opinion* — the tool's shape **or** its
level. Without the `or`, a connector-tagged status READ (asking by level, not a write) would
have paused an `auto` run that the same connector's SYNC tool sails straight through.

## Consequences

**A behaviour change worth naming: connector-owned READ tools now ask.** `get_plaid_status`,
`search_via_tavily` and their siblings default to `needs_approval` where nothing gated them
before. That is the ladder's stated rule applied honestly, and it is what "a newly enabled
connector's tools are not callable until someone says otherwise" means. One tap of "Always
allow" — or one visit to the connector's page — makes it permanent, and unlike before, that
decision is visible on both surfaces.

**Migration (revision 0040) invents no consent.** A stored `scope: "always"` grant becomes
an `always_allow` row under the tool's owner. Everything else is left alone: `once` was
never persisted, the retired `session` tier already failed closed, and an ABSENT grant is an
absence, not a decision — absence already reads as `needs_approval`. This is the same
discipline revision 0039's review enforced on `_LEVEL_FROM_MCP`, where a legacy MCP
`"allow"` was refused because it had never meant auto-fire. The same revision rewrites every
existing row's level onto the new names; without that, a `block` set through the shipped
0039 UI would stop matching and silently degrade to the default — a permission quietly
discarded, the one outcome a permission migration must never produce.

**`is_enforced()` is deleted**, with its `enforced` REST field, `ConnectorView.
permissions_enforced`, the frontend prop, and the UI's "not enforced" notice. The answer is
now always yes, and a flag that is always true is a flag that will eventually be wrong.

**No compatibility layer survives anywhere** (owner's explicit ruling, mid-change: *"no
backward-compatibility layers, build the cleanest solution … delete rather than deprecate …
if a migration step exists only to preserve something the owner would happily re-set in one
click, drop the step"*, qualified by *"and then migrate data if needed"* — a one-time
forward-only data move is not compatibility). Applied literally:

* `ToolGrantStore`, `services/tool_grants.py`, `api/grants.py` — deleted outright, no
  aliases, no dual-read, no deprecation window.
* The old level strings are not accepted anywhere. `set_tool_permission("…", "…", "allow")`
  raises `ValidationError`. Revision 0040 rewrites every stored row so nothing has to read
  both vocabularies — the one place the old words appear is that transform.
* **Revision 0016's `tool_grants.json` absorption is REMOVED.** It could only ever matter to
  an instance upgrading from files, and it was the sole reason a `tool_grants` handle had to
  stay alive in `db/tables.py` — where it made `metadata` disagree with the live schema at
  head. That divergence was not cosmetic: it broke `alembic check`, and because
  `_clear_tier2_tables` runs all its DELETEs in one transaction, a single
  `DELETE FROM tool_grants` aborted the whole pass, silently disabling cross-test isolation
  while the suite still looked green. Removing the step removed the object, and with it the
  entire class of bug. Rows already absorbed into the `tool_grants` TABLE on a real instance
  are unaffected — 0040 still folds those forward, which is the migration that matters.
* `core.paths.TOOL_GRANTS_JSON` and its `SECRET_FILES`-adjacent memberships in
  `core/sensitive.py` and `ai/checkpoint/_policy.py` are deleted too — file-era protections
  for a file nothing reads. The property they asserted (revoke → restore → the permission is
  back) did not go away with them; it moved onto the TABLE, pinned by
  `tests/test_sensitive_paths.py::test_permission_table_is_non_restorable`.

The rule that did NOT bend to any of this: **absence is never backfilled into
`always_allow`.** That is a safety property about the permissive direction, not a
compatibility concern, so the simplification explicitly left it alone. Losing a permission
costs one toggle; inventing one cannot be walked back.

**The single-caller test is reworked, not deleted.** It pinned a gap so the gap could not
silently change; `test_every_dispatch_site_consults_the_permission_store` pins the closure —
this app has exactly two dispatch paths and both read the store. A third reader appearing is
not automatically wrong, but it is a new place a permission is interpreted, and it should be
reviewed rather than merged unnoticed.

**Classification is unchanged.** Checkpoint-EXCLUDED, NON_RESTORABLE, non-portable,
wipe-only on `/api/reset` — both predecessors classified identically, which is one more sign
they were always one store wearing two names.

**Not reversible in data.** `downgrade()` restores the table and its old level names but
does not split the absorbed grants back out: telling a migrated grant apart from a level the
user set afterwards would be a guess, and guessing wrong writes a permission nobody granted.

**One thing deliberately did NOT collapse into this store: `HARDLINE_UNATTENDED_TOOLS`**
(`delete_transactions_where`, `apply_payee_rules`). It answers a different question — not
*may this tool run* but *may THIS CALLER SHAPE run it*, where the caller is something like
`ai/capture.py`'s iOS tap-to-log that is not a turn and has no surface at all. An
`always_allow` therefore does not clear it, which is a real remaining inconsistency with
"`always_allow` means allow everywhere". It is left standing rather than swept because
removing an unattended floor is a security-relevant loosening that deserves its own decision
and its own review, not a side effect of a store merge.

Its live enforcement today is narrower than the name suggests, and this is unchanged by this
PR either way: `automation_gate_verdict`'s `auto`-mode branch still calls `is_hardline_blocked`
(`ai/tools/_confirm_gate.py`), so the set is enforced for a scheduled Automation run. The
handler-level check this note used to claim, `enforce_unattended_write`, has been a
hard-coded no-op (`return None` unconditionally) since 2026-08-10 — predating this PR — so it
is not a live guarantee for a non-Automation unattended caller (e.g. the iOS capture path)
today. Flagged here rather than silently restated, since a future reader of this ADR
shouldn't take it as evidence the capture path is covered.

## Alternatives considered

**Keep `ToolGrantStore` as the store and make the ladder a richer API over it.** Rejected
as structurally impossible without rebuilding the surviving table inside it: a binary store
cannot express `blocked`, and a tool-name-only key cannot express two MCP servers' identical
tool names or keep a per-provider decision from leaking to its siblings.

**Leave both stores and define a precedence order between them.** Rejected by the owner,
and correctly: a documented precedence between two records is still two records, and the
user still cannot see which one is winning from either screen.

**Add the ladder check on top of the existing confirm gate for connector-tagged tools
only.** This was the original brief, and it is what produces the double-gate the brief warned
about — a user sets `always_allow` and still gets a card because the tool also `mutates`,
making the level they set mean nothing.
