# Statement Import v3 — the Choose and Read steps, ported verbatim

- **Date:** 2026-07-26
- **Status:** Accepted
- **Task:** #174 (follows #119 / #153, `2026-07-26-statement-import-decide-verbatim-port.md`)
- **Agent provenance:** built by an autonomous agent following the repo's `port-design-handoff`
  skill; the design reference was produced with Open Design.

## Context

`frontend/design_handoffs/statement_import_handoff/` has shipped a complete interactive prototype
since it was delivered. Two ports (#119, then #153) took its **Decide** step into production
verbatim. Both recorded the same reason for stopping there, and `PARITY_AND_GAPS.md` repeated it:

> Choose / Read step chrome … **NOT ported** — out of the handoff's own declared scope.

That reasoning was circular. The scope note lives in `index.html` — a **write-up** — which says
"Scope: the Decide step of Statement Import v3 only (Choose / Read steps and the rest of the app are
unchanged)". The **prototype** says otherwise: `prototype/import_v3/flow3.jsx` contains `DropStep`
(lines 8–87) and `ReadStep` (lines 89–104), fully designed, in the same `imp3-` language, with their
own rules in `import3.css` (`── drop ──`, `── reading ──`). They were never written up, so two ports
in a row skipped them as out of scope, and production kept running the older `imp-` Choose/Read
screens inherited from a *different* handoff
(`docs/adr/2026-06-28-accounts-tab-redesign-faithful-port.md`).

The result was one modal speaking two design languages: a pre-v3 tab strip, `<select>` and checkbox
pile for two steps, then the v3 review canvas for the third.

## Decision

Port `DropStep` and `ReadStep` verbatim — same DOM, same class names, same copy, same CSS values —
onto the existing production data layer. **No backend file changed.**

The step machine already had the right shape, so this is a render swap, not a flow change:

| prototype | production step | note |
|---|---|---|
| `DropStep`, `!picked` | `source` | the centred dropzone |
| `DropStep`, `picked` | `choose` | file row + known banner + accounts + Tell Lucius + CTA |
| `ReadStep` | `processing` | the pulsing orb and the rotating phase line |

CSS went into `statementImport.css` (already registered in `check-css-classes.mjs`), copied
rule-for-rule from `import3.css`. The now-dead pre-v3 rules were deleted from `accounts.css`
(21 of them), which is what keeps the dead-class ratchet at zero.

### Four judgement calls, and why

**1. The Choose and Read steps render NO modal footer.** The prototype puts the CTA inside the pane
(`imp3-drop-cta`), reaches "back" through the file row's own **Change**, and gives the Read step no
controls at all. Production's Back / Cancel footer buttons are therefore gone from those two steps;
the Modal's ✕ is the way out. The manual-mapping `configure` step keeps its footer — it has no
prototype counterpart at all.

**2. "✦ Detect from the statement" is an extra chip, and the CTA stays live while it shows.** The
prototype's fixture always knows its account (`scenario.detected`), so it disables the CTA whenever
the chip list is empty. Production genuinely detects the account server-side
(`decisions._suggest_account`: printed last-4s, then format-profile memory) and that is the
*recommended* default, not a missing answer. Honouring the prototype's disabled state literally would
have deleted a working capability to reproduce an artefact of a static fixture. The disabled state is
not lost — it is exactly what shows once the server answers `needs_account`, because detection has
then provably failed for this file. That is the honest reading of "no account chosen and the CTA
can't proceed".

**3. "Re-read from scratch" is one checkbox driving two flags.** The pre-v3 screen had two:
`force_reparse` (cheap, re-runs the deterministic parse) and `force_relearn` (an LLM call,
re-detects the schema). `flow3.jsx` states what its single checkbox means — *"fresh format detection
+ fresh processing"* — so both ride it. The cheap half alone is still reachable from the Decide
step's own **Re-read** link.

**4. Extra "Into accounts" chips route through the per-row seam, not the request.** This was the one
place where the design implies a capability the request shape does not carry, so the alternatives
were: fake it, drop the multi-chip UI, or invent backend behaviour. None was taken.

`POST /api/import/preview` takes one `account_id`, and it is an **anchor**: `_route_accounts` expands
it into a currency → account map (platform siblings, a remembered `account_map`, the anchor winning
its own currency), each preview row carries its own destination, each is re-pointable by hand, and
the commit books each row into its own account. Multi-account statements already work end to end. So the
first chip is the anchor, and the extra chips fill only the rows the server left with **no**
destination, matching on currency — landing as ordinary decision `edits.account_id` through the seam
the Decide step's own per-row account picker already uses. The server's routing always runs first and
always wins. Currency matching is mandatory: the commit hard-fails a cross-currency pick
(`StatementParseError`) because the native balance fold is currency-blind, so a chip matching no
currency in the statement fills nothing. A leg of an intra-statement FX pair is also never filled
— see the round-2 correction below.

Explicitly **not** built: automatic routing of two same-currency accounts from one statement. That
would be new backend behaviour, not a port.

## Consequences

- One design language across all three steps of the modal.
- `PARITY_AND_GAPS.md` and the handoff's own `index.html` scope note are corrected — the stale
  "Choose/Read are out of scope" claim is the reason this sat undone for two ports, and leaving it
  in place would have caused a third.
- The `imp3-cfg-row.top` modifier is deliberately dropped: `top` has no rule anywhere in
  `import3.css`, and keeping it would mean allowlisting a generic global token in the class linter.
- `Spark` now renders the prototype's own glyph (a filled four-point star), which also corrects the
  already-shipped Booked screen — `DoneStep` uses the same component and had been drawing a
  different, stroke-based icon.
- Copy the design has no slot for is dropped or demoted, listed item by item in
  `PARITY_AND_GAPS.md` § "Deliberate deviations" 6–10 — not silently.

## Verification

- `ImportModal.chooseRead.test.tsx` — 14 new tests pinning the design's DOM/class structure and
  copy, the chip lifecycle (detect default → anchor → extras → removal promotes), the
  `needs_account` withdrawal, the rotating phase line, and the currency-matched extra-chip routing
  asserted on the real commit payload, plus the `configure`-step regression below.
- **`code-reviewer` gate: one real bug found, reproduced, and fixed.** `accountId` has a
  SECOND writer — the manual-mapping `configure` step's account select, which predates the
  chips. Re-pointing the anchor there at an account already held as an extra chip, then going
  Back, rendered the same id twice (duplicate React keys) and made its ✕ un-removable, since
  both copies resolved to `removeAccount`'s anchor branch. Fixed at both ends: that select now
  routes through `setAnchorAccount` (which drops the id from the extras) and `acctIds` dedupes
  against the anchor. Pinned fail-first by
  `"the manual-mapping step's own account select can never duplicate a chip"`. That review also
  traced `accountId` reachability, the removed behaviour, the mechanical test renames, and the
  DOM/CSS diff against `flow3.jsx`/`import3.css`, all clean. **It reported
  `routeUnroutedDrafts`' currency guard as clean too, and that report was wrong** — see below.
  The lesson is recorded rather than quietly overwritten: a gate's "clean" is only as wide as
  the inputs it actually exercised, and this one exercised no FX-pair draft.
- **Round-2 review: a second real defect in the same function, fixed.** `routeUnroutedDrafts`
  filled `destId` on ANY destination-less draft whose currency matched a chip — including a leg
  of an intra-statement FX pair. That is wrong twice over. (1) The commit books a pair as ONE
  two-leg transfer through `decisions._transfer_draft`, which reads both accounts off
  `ctx.route` and never looks at `edits.account_id`, so the fill promised a destination the
  write ignores. (2) The serious half: the fill flips the row's FATE. `isUnrouted` goes false
  the moment `touched.account && destId` holds, so a leg that must commit as an explicit `skip`
  instead emitted `action: "create"` — a deliberately-left-out row turned into a booked one.
  Fixed by excluding pair legs (`if (d.destId || d.pair) return`), pinned fail-first by
  `"an FX-pair leg is NEVER routed by a chip — it keeps its safe skip"` (without the guard the
  test fails with `expected 'create' to be 'skip'`).
  **One scope note, stated precisely because the round-2 report went further:** that review also
  concluded the escaped `create` reaches `_transfer_draft` and raises a bare `KeyError` →
  raw 500. I could not confirm that half from a REAL server response: `_pair_fx_transfers`
  (`decisions.py:255-261`) only ever pairs rows whose currency IS in `route`, and `active_pairs`
  is derived from that same `ctx`, so `ctx.route[out_ccy]` should not miss. The fix is required
  regardless — reasons (1) and (2) each suffice on their own, and neither depends on the crash —
  so this is recorded as "unconfirmed", not "disputed", and nothing rests on it.
- **Round-3 review: the URL source fell through the same hole, for a different reason.**
  `peekSavedInstructions` runs only from `pickFile`, so a URL-loaded source stayed `pending`
  forever — neither the banner nor the `unavailable` fallback rendered, and "Re-read from
  scratch" never appeared. A hosted source stuck on a bad saved format profile was
  unresettable from the UI (the Decide step's Re-read link passes `force_reparse` only). The
  mirror image was worse: `loadUrl` never reset `fresh`, so file → check Re-read → Change →
  paste a URL left the PAID `force_relearn` flag set with no checkbox on screen to show or
  cancel it. Both entry points now share `resetPickedSource()`, and a URL source resolves to
  `unavailable` — which is the TRUTHFUL answer rather than a fallback, since the peek
  fingerprints file bytes (`onSourcePeek` takes a `File`; there is no URL probe) and a hosted
  source can never be told known or unknown. The shared reset also clears the other route's
  handle: `loadUrl` never cleared `file` and `pickFile` never cleared `url`, so switching routes
  could send both to a server that refuses exactly that (422, `_resolve_source`) — pre-existing,
  but it lives in the code this change owns. Two fail-first tests, both verified to fail without
  the fix.
- **Also from round 3 (non-blocking, fixed):** `runServerPreview`'s `setAccountId` was the THIRD
  writer of the anchor (reached from "Preview into {name}"), so round 1 had fixed the class of
  bug at only one of three sites. Both remaining writers now go through `setAnchorAccount`.
- **Also from round 2 (non-blocking, fixed):** `force_relearn` had become reachable ONLY through
  the `sourceKnown` banner, while the peek that sets `sourceKnown` fails silently — so a network
  hiccup removed the only escape from a stuck format profile. The peek now distinguishes
  `pending | known | unknown | unavailable`; on `unavailable` (a failed or unwired probe) the
  "Re-read from scratch" control renders standalone, WITHOUT the banner's "I know this source"
  claim, which would be an assertion the probe never made. A confirmed-`unknown` source still
  shows neither, correctly — nothing is remembered to forget.
- 457/457 `components/tabs/accounts` tests pass; `npm run typecheck` clean; `npm run build` exit 0;
  `npm run lint:classes` green with `STRICT_DEAD=1`.
- Visual evidence at 1440 and 390, dark and light:
  `qa/artifacts/statement-import-parity/choose-read-*.png`, captured by
  `qa/browser-use/capture_import_choose_read.mjs` against the dev-only story harness (zero network).
- Acceptance: `SI-87` … `SI-92` in `docs/qa/acceptance/statement-import.md`, all mapped into the
  `SI-HANDOFF-01` browser spec (new AC11–AC14) — `qa/browser-use/acceptance_check.py` →
  STATEMENT-IMPORT **24/24**.
- **`make qa-e2e` for AC11–AC14: ATTEMPTED LIVE, could not complete — recorded, not claimed.**
  `docs/UI_QUALITY_GATE.md` asks for the live run before merge, so it was run
  (`QA_MODEL=claude-haiku-4-5 make qa-e2e SPECS=SI-HANDOFF-01`, scoped to the one touched spec
  to bound cost). The harness itself worked end to end: the QA venv built, the operator's book
  cloned to an isolated `/tmp` dir with secrets stripped under `ONW_QA_MODE=1`, backend 8091 and
  frontend 8092 came up, and browser-use navigated to the app. Every agent STEP then failed
  identically: `400 invalid_request_error — You have reached your specified API usage limits.
  You will regain access on 2026-08-01`. The driving key is exhausted, so the run reports FAIL
  after 5 steps having verified nothing about the product. That is an environment precondition,
  not a result: it is neither evidence of a defect nor evidence of a pass, and it is written
  here as unmet rather than folded into the green list. **This gate item remains open until the
  key's limit resets (2026-08-01) or a key with headroom is supplied**; the run is a single
  command away and needs no code change. Note the runner reports an EXHAUSTED key as FAIL while
  a MISSING key is a clean SKIP (`docs/qa/browser-use.md` § Model choice) — worth reconciling,
  since only one of those two is distinguishable from a real failure.
  What does stand meanwhile, and is deterministic: the 14 `chooseRead` unit tests drive the same
  interactions the browser spec would (dropzone copy, picked-file row, chip lifecycle, URL
  source, phase line) against the REAL component, and the story-harness captures render the
  real production build.
- Backend, untouched but re-run because the DoD asks for it: import smoke `IMPORT_OK`,
  `pytest -q -m "not integration"` → **12076 passed, 13 skipped**, `lint-imports` 5 contracts kept
  0 broken, `ruff check` clean.
- Static UI gate: `lint:entity-rows` 0 findings, `lint:tokens` no FAIL-level, `lint:breakpoints` OK.
