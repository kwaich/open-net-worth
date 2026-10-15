---
title: Yodlee hidden from every discovery/listing surface — enterprise-only pricing, code kept intact
status: Accepted
date: 2026-08-14
deciders: repo owner (product decision) + implementing agent (Claude)
---

# Yodlee hidden from every discovery/listing surface — code kept intact

## Context

Yodlee (Envestnet) is one of five account-aggregation providers wired into
`services/aggregation/providers.py`, alongside Plaid, Enable Banking, Alpaca, and IBKR. Unlike
those four, Yodlee has no public self-serve pricing tier: linking it requires an enterprise sales
contract, and real reported minimums are $1,000–2,000+/month. This app's actual users are
individual self-hosters running their own instance — nobody in that position can realistically
turn Yodlee on. Presenting it in the UI and to the AI assistant as an option on equal footing with
Plaid/Enable Banking implies an accessibility it does not have.

This is explicitly a **hide, not a removal**. It is NOT the same move as the same-day-precedented
SimpleFIN/GoCardless/Pluggy deletion (`2026-08-13-connector-tool-splitting-and-discovery-gating.md`
§ context) or the Slack/Discord/WhatsApp removal
(`2026-08-13-channels-scope-down-slack-discord-whatsapp-removed.md`): those had no working
integration worth keeping, or a maintenance/security cost the owner decided wasn't worth carrying.
Yodlee's integration works fine — the constraint is purely commercial, and Yodlee's own pricing
could change. Every file (`backend/api/yodlee_api.py`, `backend/db/yodlee_config_store.py`,
`backend/services/aggregation/yodlee.py`, `frontend/.../YodleeSection.tsx`, their tests) stays
exactly as it was, so re-enabling it later is a small, localized diff, not a rebuild.

## Precedent search (before building anything new)

Two existing "disabled but present" idioms were found and evaluated:

1. **`ai/tools/catalog.py`'s `_CATALOG_HIDDEN`** — a flat, hand-maintained set of tool NAMES
   (currently the three Tax-tab tools, Task 87), unconditionally excluded from the flat catalog
   and (per the RFC's discovery-gating ADR) unioned into `_groups.py`'s candidate set too. Close,
   but it models "a deferred FEATURE AREA with no connector concept at all" — it carries no
   relationship to `services/connectors.py`'s enabled/configured state, and (crucially) nothing
   reads it at DISPATCH time, only at discovery time. Yodlee already has a `connector="yodlee"`
   tag on its two promoted tools; duplicating its name into a second, unrelated hidden-name list
   would be two independent mechanisms answering the same question for the same two tools, and
   would say nothing about the bulk `connector_status`/`connector_sync` leak (see Decision §3).
2. **`ai/tools/_connector_gate.py`'s `hidden_connector_tools()`** — the live mechanism for "a
   connector-tagged tool whose connector isn't (yet) ENABLED." This is the right shape (keyed off
   the same `connector=` tag Yodlee's tools already carry) but the wrong PREDICATE: it hides a
   tool only while its connector is unconfigured, and — per
   `2026-08-13-connector-tool-splitting-and-discovery-gating.md`'s own "Alternatives considered"
   — deliberately, explicitly does NOT block dispatch, because for an ordinary not-yet-configured
   connector the underlying handler already self-reports `status: "disabled"`, so a hard block
   would be "a second, redundant mechanism doing the same job worse."

Neither idiom fit Yodlee exactly, so this change extends #2 minimally rather than reusing #1 or
inventing something unrelated to either.

## Decision

**1. `services/aggregation/providers.py`** — `Provider` gains a `disabled: bool = False` field.
Yodlee's row sets `disabled=True` (with an inline ADR reference) but is otherwise byte-identical
— row, adapter, config store untouched. `PROVIDER_KEYS` (previously "every registered key") is
redefined as "every registered key EXCEPT a disabled one." Every consumer already keys off
`PROVIDER_KEYS`, not `PROVIDERS` directly, for the resolvable/offered set (`resolve()`,
`_provider_docs()`, the bulk tools' `Literal` schema enum) — so this ONE field change removes
Yodlee from the bulk `connector_status`/`connector_sync` tools' schema, description text, and
`resolve()`'s valid-key set, while the `PROVIDERS` dict itself (which the promoted
`get_yodlee_status`/`sync_yodlee_now` tool registrations index directly by key, at import time)
is untouched — so those two `@tool(...)` registrations keep importing.

**2. `ai/tools/_connector_gate.py`** gains `DISABLED_CONNECTORS: frozenset[str] = frozenset({"yodlee"})`.
`hidden_connector_tools()` now computes `enabled_connector_ids() - DISABLED_CONNECTORS` before the
membership test, so Yodlee's tools are hidden from the catalog / `find_tools` UNCONDITIONALLY —
even if the store's `enabled` flag is somehow `True` (an instance that had it configured before
this change, or an env-seeded credential). This is a real scenario the ordinary "not yet enabled"
gate does not need to handle, because an ordinary connector is never simultaneously "enabled" and
"withdrawn."

**3. `ai/tools/registry.py`'s `dispatch()`** gains one new short-circuit, immediately after the
unknown-tool check and before schema validation: a call whose tool carries a `connector` in
`DISABLED_CONNECTORS` is refused outright — `{"error": "the 'yodlee' connector is disabled..."}`
— before its handler ever runs. This is the one deliberate departure from the settled
"discovery-only, never blocks dispatch" architecture the prior ADR established and this change
does not relitigate for any other connector. It is necessary here specifically because "let the
handler self-report its own disabled status" — correct for an unconfigured Plaid/IBKR/etc., which
has no real data to leak — is NOT equivalent for Yodlee: an instance where it genuinely IS
configured and enabled would have its promoted tools succeed and return real account data,
exactly the "still an option" outcome this whole change exists to prevent. `entry.connector is
None` for every untagged/bulk tool, so `None in DISABLED_CONNECTORS` is always `False` and this
never touches anything else.

**4. `ai/tools/connectors.py`** — `_provider_docs()` skips a `disabled` provider explicitly (belt
and suspenders now that `PROVIDER_KEYS` already excludes it); `connector_status`/`connector_sync`'s
hardcoded "prefer the single-provider tool" description lists drop `get_yodlee_status`/
`sync_yodlee_now`; `set_connector_enabled`'s hardcoded "fixed connector ids" list drops `'yodlee'`
(this tool has no `connector=` tag, so it is always offered — leaving Yodlee in that string would
have been a direct catalog leak). The two promoted tool registrations themselves are untouched.

**5. Frontend `catalog.ts`** — Yodlee's `CatalogEntry` is removed from `CATALOG` (the Browse &
add source), with a new bullet in the "Deliberately ABSENT" doc block explaining why (the existing
convention for every other deliberate absence in that list). The connector's own config section
(`YodleeSection.tsx`), its REST endpoints, and `services/connectors.py`'s synthesis of the Yodlee
`ConnectorView` are all untouched — see "Already-configured instances" below for why that is the
right split, not an oversight. Its logo asset (`connector-logos/yodlee.svg`) and `LOGO_FILES`
entry are removed too, since `logos.test.ts` enforces "every logo maps to a real catalog entry" —
an already-configured Yodlee row now falls back to its tinted category glyph, exactly like
`email`/`webhook` already do (neither of those has a logo either).

**6. `ai/tools/connectors.py`'s `set_connector_enabled` handler (2026-08-14, follow-up fix —
closes a gap an independent security review found in Decision §3 as originally shipped).**
Decision §3's dispatch-level refusal covers `get_yodlee_status`/`sync_yodlee_now` because both
carry `connector="yodlee"` — but `set_connector_enabled` deliberately carries no `connector=` tag
(Decision §4: its id space is open, not just the aggregation providers), so `registry.dispatch`'s
`DISABLED_CONNECTORS` check never sees a call to it. Decision §4's fix at the time was cosmetic
only — dropping `'yodlee'` from the tool's free-text "fixed connector ids" description — while the
actual `connector_id: str` field carried (and still carries) no enum/`Literal` constraint, and
`services/connectors.py::set_connector_enabled` handled Yodlee exactly like every other provider:
flip `YodleeConfigStore.enabled`, call `_reschedule_yodlee()`. On an instance with pre-existing
valid Yodlee credentials, Lucius calling `set_connector_enabled(connector_id="yodlee",
enabled=true)` — one ordinary approve-tier confirm — would flip that flag and resume the
scheduler's `ingest:yodlee` poll job, which calls `run_yodlee_sync()` **directly**, never through
`registry.dispatch()`. From that point every sync ran unattended, forever, surviving restarts,
with zero involvement of the Decision §3 refusal — the direct Yodlee tools being refused became
irrelevant once the scheduler was doing the pulling on its own.

The fix adds one check inside the `set_connector_enabled` **tool handler**: `enabled=True` for a
`DISABLED_CONNECTORS` member is refused with the same error style as Decision §3, checked BEFORE
either `connectors_service.set_connector_enabled`/`set_capability_enabled` call — so the store
write and the reschedule call never happen, not merely get overshadowed by an error message after
the fact. `enabled=False` is never refused (disabling can't resume anything). Deliberately placed
in the TOOL HANDLER rather than in `services/connectors.py::set_connector_enabled` itself, because
that service function is ALSO `PATCH /api/connectors/{id}`'s (`api/connectors_api.py`)
implementation — the REST route a human operator uses from Settings/Connectors. Pushing the check
into the shared service would have blocked the legitimate human path described in "Already-
configured instances" below along with the AI one; the check belongs at the layer that is unique
to the AI-tool call, which is the tool handler, not the service it delegates to. See
`tests/test_connector_tool_discovery_gating.py`'s `set_connector_enabled`-prefixed tests (the AI
path, refused, checked pre-write) and `tests/test_connectors_api.py::
test_patch_yodlee_enable_still_works_via_rest_despite_ai_tool_refusal` (the REST path, unaffected).

## Already-configured instances: kept visible, not offered fresh

Mirrors the Webhook precedent in `2026-08-13-channels-scope-down-slack-discord-whatsapp-removed.md`
exactly: "removed ONLY from the Browse & add Directory catalog... not advertised as a new thing to
add... a user who wants it can still reach it because the card is always rendered."

Concretely, this "just works" from the existing architecture rather than needing a new branch:
`frontend/.../useConnectors.ts`'s `entries` (the main Connectors-tab list) is derived purely from
`/api/connectors` via `adapter.ts::isInstalled` (`status !== 'not_configured'`), entirely
independent of `catalog.ts`. `services/connectors.py::list_connectors()` was NOT changed —
Yodlee's `ConnectorView` is still synthesized unconditionally, same as Plaid/Alpaca/IBKR. So:

- **Never configured** (`status: 'not_configured'`, true for essentially every self-hosted
  install): invisible in the main list (pre-existing behavior for any unconfigured connector,
  unrelated to this change) AND now invisible in Browse & add too. Fully hidden.
- **Already configured** (a pre-existing setup, or an operator with an enterprise contract):
  still shows on the main Connectors list, its detail page, its config form (`YodleeSection.tsx`)
  — all untouched — and can still be enabled/disabled/synced BY A HUMAN from the UI and via
  `/api/yodlee/*`/`PATCH /api/connectors/yodlee` directly, unaffected by anything in this ADR. It
  is invisible ONLY in Browse & add (nothing routes a fresh user to it), and every AI-reachable
  DISPATCH path is refused, per Decision §2–3 AND §6 (the `set_connector_enabled` follow-up fix —
  Decision §3's dispatch-level refusal alone did NOT cover it, see §6 for why), uniformly, whether
  configured or not — no call to a Yodlee tool ever executes, no matter how it was reached. What
  is NOT uniform: whether the tool's SCHEMA gets OFFERED to the model in the first place. A skill
  or agent config naming the tool explicitly can still surface it as an option (see "Follow-ups"
  below) — the refusal still fires the moment it's actually called, but the "hidden from every
  discovery surface" framing above is about the ordinary catalog/`find_tools` path, not an
  absolute. The AI-facing withdrawal is a policy choice about what Lucius may offer; it is
  independent of, and deliberately does not touch, the human/REST path described in the sentence
  above.

## Consequences

### Positive
- Zero risk to the working integration: every protected file imports cleanly and its own test
  suite (`tests/test_yodlee.py`, `ConnectorConfig.test.tsx`) is unmodified and green.
- Re-enabling is two one-line flips (`Provider.disabled = False`,
  `DISABLED_CONNECTORS = frozenset()`) plus restoring the `CATALOG` entry and the logo asset —
  bounded, reviewable, no rebuild.
- The bulk `connector_status`/`connector_sync` tools' own schema now refuses `"yodlee"` the exact
  same way they already refuse a purely bogus provider key (`UnknownProvider`, naming the actual
  valid providers) — no special-cased error path, no new failure mode to learn.
- The bulk bank-search UI (`InstitutionPickerModal` / `search_institutions`) already never
  referenced Yodlee (only Plaid/Enable Banking are wired into
  `services/account_linking.py`'s region table) — confirmed by reading the file, not assumed, so
  no separate fix was needed there.

### Negative
- **One documented, deliberate exception to a settled architectural rule.** The prior connector-
  gating ADR explicitly rejected execution-blocking as redundant with self-reporting, for every
  connector it covered. This change carves out ONE narrow exception (Decision §3) rather than
  reopening that rule generally — a future reader diffing `registry.dispatch()` should read this
  ADR, not assume the exception generalizes.
- **Two independent "provider validity" surfaces now exist for the same fact**
  (`Provider.disabled` gates the bulk path via `PROVIDER_KEYS`; `DISABLED_CONNECTORS` gates the
  promoted-tool path via the connector tag) rather than one. Not unified into a single source
  because they answer genuinely different questions at genuinely different layers (a
  `services.aggregation` domain fact vs. an `ai.tools` dispatch policy) and unifying them would
  require either `services/aggregation` importing from `ai/tools` (backwards through the
  dependency rule) or `ai/tools` deriving its policy from a services-layer dataclass field it
  does not otherwise touch. Both frozensets/fields currently name the same one provider; a second
  disabled provider would need both touched, which is an acceptable, explicit cost for two lines.
- **An independent security review found a real gap in the FIRST version of this change**: Decision
  §3's dispatch-level refusal only covers a tool carrying `connector="yodlee"`, and
  `set_connector_enabled` deliberately carries no `connector=` tag, so it was untouched by that
  refusal — Decision §4's original fix for it was cosmetic (a description string), not a behavioral
  one. Closed by Decision §6 (same day). Left here as the visible scar: the "uniformly... every
  path is hidden/refused" claim in "Already-configured instances" below was FALSE for this one path
  between this ADR's initial acceptance and the §6 follow-up — a caution for any future reader
  extending `DISABLED_CONNECTORS` to a new connector to check every tool touching that connector's
  id, not just the ones carrying its `connector=` tag.

### Follow-ups (deliberately out of scope here)
- `services/connectors.py`'s `_NATIVE_TOOLS["yodlee"]` (the connector's own detail-page tool
  bundle) still lists `connector_status`/`connector_sync` as "Yodlee's tools," matching every
  other aggregation provider's existing (pre-existing, unrelated) informational-only display —
  left as is; it is reachable only from Yodlee's own detail page, itself reachable only if
  already configured.
- No attempt was made to strip Yodlee mentions from `docs/DATA_MODEL.md` or any other prose doc
  beyond this ADR and the code comments touched above — those describe the data model, which is
  unchanged.
- **"Hidden from every AI-facing discovery surface" is not literally true.** A follow-up code
  review found two additive tool-offer paths that never consult `hidden_connector_tools()`/
  `DISABLED_CONNECTORS` at all: `services/skills.py::invocation_tool_controls()`/`validate()`
  validates a skill's `allowed-tools` frontmatter against the RAW registry (`tool_names()`), so a
  skill can still list `get_yodlee_status`/`sync_yodlee_now` and validate fine; and
  `ai/assistant.py::_build_agent_offer()`/`_apply_inline_skill_tool_state()` pull schemas straight
  from `TOOL_SCHEMAS` by name for an activated skill or an explicit `allowed_tools` config, with
  no `hidden_connector_tools()` intersection (only the narrower `_CATALOG_HIDDEN` subtraction
  applies, and only to the agent-offer case). A skill or agent config that names either tool would
  therefore genuinely have it OFFERED to the model as a schema for that turn — a real
  "encountered as an option" event — even though `registry.dispatch()` still correctly REFUSES
  the actual call every time (verified: this is not a data leak or an execution path, just an
  incomplete discovery-hiding claim). Not introduced by this change — the identical gap already
  existed for the pre-existing `_CATALOG_HIDDEN` Tax-tools precedent, which `DISABLED_CONNECTORS`
  inherits rather than introduces. Left as a follow-up rather than blocking this change (smallest-
  correct-change principle: fixing it means threading `hidden_connector_tools()` through two more
  call sites that were never audited for this before), but recorded here explicitly so this ADR
  doesn't overclaim completeness.

## Alternatives considered

- **Delete Yodlee outright**, matching the SimpleFIN/GoCardless/Pluggy and Slack/Discord/WhatsApp
  precedents. Rejected — explicit owner instruction: the integration works, the constraint is
  commercial and could change, so the code stays for a cheap future re-enable.
- **Remove the `Provider(...)` entry from `PROVIDERS` outright**, per the task's own suggested
  "simplest correct fix" if nothing else applied. Investigated and rejected: `ai/tools/
  connectors.py`'s `get_yodlee_status`/`sync_yodlee_now` registrations index `PROVIDERS['yodlee']`
  directly, at MODULE IMPORT TIME, to build their tool descriptions/`mutates=`. Deleting the dict
  entry raises `KeyError` at import and takes the whole backend down — concretely verified before
  choosing the `disabled` field instead.
- **Set Yodlee's tools to `block` in the new `tool_permissions`/`connector_tool_permissions`
  store** (the 2026-08-13 permission-ladder unification) instead of building a dispatch check.
  Investigated and rejected: `services/tool_permissions.py::is_enforced()` states, explicitly and
  by design, that a stored level is enforced for `mcp:<server_id>` ONLY —
  `mcp/client_manager` is its one caller in the whole backend. A native connector's level (Yodlee
  included) is "a recorded preference, not a stop" (the module's own docstring). Setting it would
  change what the UI displays and nothing about what actually dispatches — cosmetic, not a fix,
  and would misrepresent the guarantee CLAUDE.md's security section already documents about that
  store.
- **Use `_CATALOG_HIDDEN`** (the Tax-tools precedent) instead of extending the connector gate. See
  "Precedent search" above — evaluated and rejected in favor of the connector-tag-scoped
  extension, which is precise to the two tools that actually carry `connector="yodlee"` and
  correctly overrides an "enabled" store state, neither of which `_CATALOG_HIDDEN` models.
  Duplicating the same two names into a second, semantically-unrelated set was judged more
  confusing than the (documented) small new frozenset.
- **Hide Yodlee from `services/connectors.py::list_connectors()` when unconfigured**, so `/api/
  connectors` itself stops returning it. Rejected: every other aggregation provider is already
  "always present, even before credentials," and the frontend's own `isInstalled` filter already
  keeps an unconfigured connector off the main list — singling Yodlee out for a DIFFERENT
  synthesis rule than its four siblings would be inconsistent for no behavioral gain, since the
  Directory (the surface that actually needed to change) reads `catalog.ts`, not this list.

## Agent provenance

- **Decided by:** repo owner (the product decision to hide rather than delete, and the pricing
  rationale); implementing agent (Claude) made the file-level mechanism calls documented above.
- **Driving artifact:** owner instruction, 2026-08-14, explicitly modeled on the Webhook treatment
  in `2026-08-13-channels-scope-down-slack-discord-whatsapp-removed.md`.
- **Precedent read before building:** `ai/tools/catalog.py::_CATALOG_HIDDEN`; `ai/tools/
  _connector_gate.py::hidden_connector_tools()`; `2026-08-13-connector-tool-splitting-and-
  discovery-gating.md`'s "Alternatives considered" (the prior, explicit rejection of execution-
  blocking, which this change carves one narrow exception into); `services/tool_permissions.py`'s
  `is_enforced()` (confirmed the permission-ladder store would not have actually enforced
  anything for a native connector before ruling it out).
- **Why this way (one line):** extend the existing connector-tag discovery gate with a small,
  explicit "withdrawn by policy" set that ALSO hard-refuses at dispatch — the one property no
  existing mechanism had, and the one property this specific case (a real, potentially-configured
  integration that must never again answer with real data through the AI) actually needs.

### Decision §6 addendum (2026-08-14, same day — follow-up fix)

- **Decided by:** an independent security review found the gap (see the "Negative" consequence
  above); implementing agent (Claude) added the `set_connector_enabled` handler check, its
  regression tests, and this ADR correction. No new product decision was made — this closes the
  ADR's own stated intent, it does not change it.
- **Driving artifact:** the security review's finding, describing the concrete exploit sequence
  (`set_connector_enabled(connector_id="yodlee", enabled=true)` on a pre-configured instance →
  `YodleeConfigStore.enabled` flips → `_reschedule_yodlee()` resumes the scheduler's unattended
  poll job, entirely outside `registry.dispatch()`).
- **Why this way (one line):** the check lands in the TOOL HANDLER (`ai/tools/connectors.py`), not
  in the shared `services/connectors.py::set_connector_enabled` service function, because that
  service function is also `PATCH /api/connectors/{id}`'s REST implementation — the check needed
  to sit at the one layer unique to the AI-tool call, so the human/REST re-enable path this ADR's
  own "Already-configured instances" section promises to keep working stays genuinely unaffected.
