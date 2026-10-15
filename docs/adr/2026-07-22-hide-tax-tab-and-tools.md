---
title: Hide the Tax tab and its AI tools (reversibly), not delete them
status: Superseded by docs/adr/2026-08-25-remove-the-tax-feature.md
date: 2026-07-22
deciders: Claude, acting on an explicit owner directive
---

# Hide the Tax tab and its AI tools (reversibly), not delete them

> **SUPERSEDED 2026-08-25** by `docs/adr/2026-08-25-remove-the-tax-feature.md`. The owner
> confirmed the tax feature is not being provided ("it will be built later"), so it was
> DELETED rather than left hidden: the tab, routes, services, AI tools, jurisdiction
> rulesets and `profile.tax_config` are gone, and `_CATALOG_HIDDEN` is empty again. The
> decision below is history — read it for why the hide was built the way it was, and for
> the three reachability paths the 2026-07-22b review found, which the removal's tests
> still guard.

## Context

The owner asked to hide the Tax tab and "all relevant tax features" from the UI, and to
exclude the tax AI tools from Lucius "so it doesn't say wrong" — i.e. Lucius should never
surface or reason about tax figures while the feature is hidden, since a half-hidden feature
that Lucius can still describe would contradict what the UI shows.

`docs/launch/BOARD.md` already lists `tax` under the **Deferred** tier ("may show an
honest 'coming soon'; must never lie with a broken/blank screen"), so this request tightens an
already-declared deferred status to a full hide rather than introducing a new one.

Two existing seams made this a small, reversible change rather than a deletion:
- Frontend tab visibility is controlled by static arrays (`frontend/src/tabs.ts`'s
  `PRIMARY_TABS`, `frontend/src/components/mobile/MobileTabBar.tsx`'s `SHEET_SECTIONS`) — a
  tab not listed there simply isn't linked from any nav, while its route/component/REST/service
  code stays intact.
- `backend/ai/tools/catalog.py`'s `_CATALOG_HIDDEN` frozenset already existed, empty, with a
  comment reading "kept as the seam for the next one" — built for exactly this: hiding a tool
  from Lucius's system-prompt catalog without removing it from the registry.

## Decision

Hide, don't delete, both halves:

1. **Frontend** — removed `'tax'` from `PRIMARY_TABS` (desktop sidebar) and from
   `SHEET_SECTIONS` (mobile "more" sheet). `'tax'` stays in `TABS`/`TAB_LABELS`/etc. and the
   `/tax` route/`RouterShell` render branch is untouched — a direct URL still resolves (no
   feature-flag/redirect mechanism exists for tabs, and building one was out of scope for a
   pure nav hide), but nothing in the app links to it anymore.
2. **Backend** — populated `_CATALOG_HIDDEN` with the three tax tools (`get_tax_report`,
   `get_tax_estimate`, `get_tax_loss_harvest`), which removes them from the flat system-prompt
   tool catalog. That alone was insufficient: `ai/tools/_groups.py::_candidate_schemas()` (the
   shared backing for both `find_tools(query=…)` and `find_tools(names=[…])`) read
   `TOOL_SCHEMAS` directly and was never filtered by `_CATALOG_HIDDEN`, so a keyword search
   ("tax loss harvest") or an exact-name load could still surface/load a "hidden" tool — closing
   that gap was necessary for a real production ask ("exclude ... from Lucius"), not just the
   narrower "hide from the catalog listing" the seam's docstring described. Added the same
   `_CATALOG_HIDDEN` filter to `_candidate_schemas()`.
3. **No hard dispatch-level block was added.** The model can only call a tool by name if it has
   seen that name somewhere (catalog or a `find_tools` result), and both are now filtered — so a
   hidden tool is structurally unreachable without the model hallucinating the exact string. A
   dispatch-time refusal would reintroduce a per-tool gating concept the codebase deliberately
   removed (`docs/adr/2026-07-18-remove-layered-ai-permission-system.md`: "every tool is
   callable by every caller... no tier"); adding one back for three tools, unrequested, would be
   scope creep against that stated direction. Services/REST for tax (`backend/services/tax.py`,
   `backend/services/tlh.py`, `backend/api/tax.py`) are untouched — they're pure reads/compute
   with no store writes, so leaving them live is zero additional risk.

Also fixed in the same change: `find_tools`'s own schema-description examples
(`ai/tools/_groups.py`'s `FindToolsInput`) named `get_tax_estimate`/"tax loss harvest" as sample
usage — left as-is, that example would have told the model to try calling a tool it can no
longer discover. Swapped to `get_rebalance_plan`/"rebalance plan", updating the byte-pinned
`tests/golden/tool_input_schemas.json` entry to match (a deliberate, reviewed schema change per
the golden-file convention, not a drift). Four existing tests
(`tests/test_find_tools.py`, `tests/test_discovery_gate.py`, `tests/test_agent_offer.py`) had
pinned "tax loss harvest"/"get_tax_estimate" as their example searchable/loadable tool; updated
them to the same non-hidden example, and added an explicit regression test
(`test_search_tax_tools_are_catalog_hidden`) asserting all three tax tools no longer surface via
keyword search.

## Consequences

### Positive
- Reversible in two small diffs: emptying `_CATALOG_HIDDEN` and re-adding `'tax'` to the two nav
  arrays restores the feature exactly as it was — no data model, service, or route changes.
- Lucius can no longer discover, describe, or call any tax tool through its normal means
  (system-prompt catalog or `find_tools`), so it won't narrate tax figures while the tab is
  hidden — directly addressing "so it doesn't say wrong."
- No new gating mechanism was introduced; the fix uses two seams that already existed for this
  exact purpose.

### Negative / accepted gaps
- `/tax` is still reachable by a typed/bookmarked URL — a "hide from nav" hide, not a hard
  block. Acceptable for a personal self-hosted app with a single trusted operator; revisit if a
  harder block (redirect/404) is wanted later.
- The REST endpoints (`GET /api/tax-report`, `GET /api/tax/estimate`, tax-loss-harvest) stay
  live and callable directly (e.g. via `curl` or the API docs) — intentionally left alone since
  they're read-only and nothing in the hidden frontend calls them once `Tax.tsx` is unmounted.

## Alternatives considered

- **Delete the tools/routes/components outright** (the pattern used for the Alpaca trading
  removal) — rejected: the owner said "hide," and CLAUDE.md's own Alpaca ADR frames deletion as
  appropriate for a feature that's actually being retired, not one that's temporarily hidden
  "atm." A hide needs to be trivially reversible; a deletion is not.
- **Add a hard-block at `registry.dispatch()` for hidden tool names** — rejected: unreachable
  via normal discovery already; a dispatch-level block reintroduces the per-tool ceiling concept
  the 2026-07-18 permission-system removal deliberately eliminated, for no discovery benefit.
- **Feature-flag the route to redirect/404** — rejected as unnecessary scope for this task; no
  such mechanism exists for tabs today and building one wasn't requested.

## Follow-up (2026-07-22b, independent review — the merged commit's hide was incomplete)

An independent review of the merged change (commit `4213c2f1`, PR #31) found the hide was
real but not complete, beyond the accepted gaps above:

1. **The commit shipped with a broken frontend test suite.** `MobileTabBar.tsx` dropped
   `'tax'` from `SHEET_SECTIONS`, but `MobileTabBar.test.tsx` was never updated —
   `MB2.1`'s exact-array assertion and `MB2.2`'s `getByText('Tax', …)` both failed (one an
   assertion failure, one an uncaught `getElementError`). `make check`/`npm test` was red
   on `main` for this file. Fixed: the two tests updated to the post-hide row list/click
   target, plus an explicit `MB2.2b` asserting `'Tax'` is absent from the sheet. The
   paired QA contract (`docs/qa/acceptance/mobile-shell.md`'s `MB2.1`) was also stale
   (documented "Sections (Inbox, Tax, Commitments, Intel)") and updated to match.
2. **`_CATALOG_HIDDEN` was not enforced on the agent explicit-offer path — a real gap,
   not just an ADR wording overclaim.** The original decision's point 3 claimed "a hidden
   tool is structurally unreachable without the model hallucinating the exact string,"
   but that's only true for the DEFAULT interactive offer and `find_tools`. An agent
   whose stored `allowed_tools` names a hidden tax tool directly (a human via REST, or
   Lucius itself via the live `create_agent`/`update_agent` tools, then reached in the
   SAME interactive session via an `@agent` mention per the mention-routing section) got
   the tool's full schema handed to it in `tools=` with no discovery step at all —
   reproduced directly: `_build_agent_offer({'get_tax_estimate', ...})` returned the real
   schema. Fixed at the single choke every consumer of an explicit offer reads through:
   `ai/tools/_skill_scope.py::offer_tool_names()` now subtracts `_CATALOG_HIDDEN` from
   the caller-supplied offer (filtered at READ time, not at `set_offer_scope`'s write
   time, so `snapshot_scope`/`restore_scope` still round-trip the caller's raw list
   unchanged). This closes both `assistant._build_agent_offer` (the inline schema list)
   and the discovery-gate's own raw re-read (`assistant.py`'s explicit-offer branch) in
   one place, without reintroducing a dispatch-level permission ceiling for every other
   tool — `_CATALOG_HIDDEN` remains a narrow, deliberate 3-tool exception, not a new
   tiering concept. Regression: `tests/test_agent_offer.py`'s
   `test_small_agent_offer_excludes_a_catalog_hidden_tool` +
   `test_large_agent_offer_discovery_gate_never_loads_a_catalog_hidden_tool`.
3. **Catalog-exclusion test coverage was thin.** The new `find_tools` regression test
   covered `_candidate_schemas()` (`ai/tools/_groups.py`) only — a different function
   from `build_tool_catalog()`/`_internal_names()` (`ai/tools/catalog.py`), which is what
   actually renders the flat system-prompt catalog (the fix's own step 1). The existing
   `test_hidden_shims_not_advertised` (`tests/test_tool_catalog.py`) was itself stale
   (asserted only the already-deleted `request_tools` shim, with a docstring claiming
   "none currently" hidden). Updated it to assert every name in the LIVE `_CATALOG_HIDDEN`
   set is absent from `build_tool_catalog()`'s output, so a regression in that filter is
   caught regardless of which tools are hidden at the time.
4. **Two Lucius starter-prompt chips still invited tax questions with no tool behind
   them.** `InvestmentsTab.tsx`'s per-subtab prompt registry offered "Plan a
   tax-efficient trim" (Holdings) and "Tax at my next vest" (Equity Comp) — clicking
   either sends that literal text as a chat message, and since the tax tools are now
   unreachable, Lucius would answer from general reasoning with no real numbers and no
   hedge: close to the exact "Lucius says wrong [tax] things" failure the hide exists to
   prevent, on a surface the original change never considered. Replaced with
   "Where am I overweight?" and "How many units are still unvested?" respectively.

None of this reverses the decision above — hide, not delete, is still correct — it closes
gaps in how completely that decision was executed.

## Agent provenance

- **Decided by:** Claude, on an explicit owner instruction ("let's hide 'tax' tab and all
  relevant tax feature features atm... exluding all relevant tools for tax from lucius too so it
  doesn't say wrong").
- **Alternatives weighed:** hide (chosen) vs. delete vs. hard dispatch block vs. route redirect.
- **Why this way (one line):** two purpose-built, already-reversible seams (`PRIMARY_TABS`
  exclusion, `_CATALOG_HIDDEN`) covered the ask exactly; the one real gap found along the way
  (`find_tools` bypassing `_CATALOG_HIDDEN`) was closed so the hide is actually complete, not
  just cosmetic. **Follow-up (2026-07-22b):** an independent review found the hide still had a
  broken test suite and a real reachability gap on the agent explicit-offer path; both fixed —
  see the Follow-up section above.
