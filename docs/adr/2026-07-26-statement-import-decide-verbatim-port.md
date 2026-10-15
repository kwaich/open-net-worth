# Statement Import — the Decide step is a VERBATIM port of the handoff, not a re-derivation

- **Date:** 2026-07-26
- **Status:** Accepted
- **Supersedes (in substance):** `docs/adr/2026-07-23-faithfully-port-statement-import-decide.md` — that ADR
  declared the intent; this one records what it actually took to get there, and why the first two
  attempts did not.
- **Agent provenance:** task #153, after PR #35 ("Improve the statement import Decide-step flow")
  and task #119 ("Port Statement Import handoff to production") both merged without the design
  landing. Owner instruction: *"Do not repeat PR #35's or task #119's outcome. A prototype bundle,
  documentation, or additive behavior changes are not completion."*

## Context

`frontend/design_handoffs/statement_import_handoff/` ships a complete, working interactive
prototype of the Statement Import **Decide** step plus a prose handoff (`index.html`) that
enumerates every change and the alignment rules that must survive future edits. The repo's
`port-design-handoff` skill states the one rule plainly:

> **The prototype IS the spec.** Port its JSX → app React/TS and its `.css` **verbatim** — same
> DOM, same class names, same structure — so the result is pixel-identical. Re-deriving from prose
> on top of generic primitives guarantees drift and is the failure mode.

Two prior attempts landed on `main` without the design landing with them:

- **PR #35** added real behaviour (the three-state decision, the off-by surfaces) but did not
  implement the visual design at all.
- **Task #119** went further: it introduced ~93 `imp3-*` class names and genuinely ported the row
  anatomy (`imp3-arow*`, the always-on `imp3-arow-match` ledger line, `imp3-dchip`, `imp3-qcard`,
  `imp3-sec`, `imp3-foot`, `imp3-done`). But the rest was **re-derived under invented names**, which
  is precisely the failure mode the skill names. A mechanical diff of the prototype's stylesheet
  against production found the gap:

| Prototype (spec) | What #119 shipped |
|---|---|
| `imp3-verdict` / `imp3-vline` / `imp3-vic` / `imp3-voff` — one recessed panel, each line a round status badge + bold headline + dim breakdown | absent; production kept its own `imp-verdict2` / `imp-verdict-line` / `imp-math` stack |
| `imp3-sec-h` + `imp3-sec-badge` + `imp3-sec-t` + `imp3-sec-sub` | `imp3-sec-head` invented; no count badge, so the per-band badge tones never rendered, and the section never named the band it was showing |
| `imp3-lpick*` — one fixed-position, flip-up, searchable picker | absent; two different pickers (`imp-twinpick`, `imp-linkpick`) with different chrome and wording — the exact inconsistent-CTA problem the handoff's change list calls out |
| `imp3-qacts` / `imp3-choice-k` / `imp3-choice-l` / `imp3-queue-h`/`-t`/`-n`/`-kbd` / `imp3-qn` | invented `imp3-choices` / `imp3-choice-label` / `imp3-qnum`; no numeric key badges, no "Needs you · x of N decided · press 1 2 3" header |
| `imp3-cmp-col` / `-h` / `-name` / `-mid` / `-drift` / `-lk` / `-src` / `imp3-pend` | invented `imp3-cmp-card` / `-label` / `-sub`; no dashed mid rail, no Δ drift pill |
| `imp3-after-l` / `-rec` / `-none` | invented `-head` / `-records` / `-note`; records rendered as bordered cards, not the design's inline rows |
| `imp3-lo-ic`/`-t`, `imp3-lc-hint`/`-acts`/`-btn`, `imp3-lunote*`, `imp3-lustrip`, `imp3-routed`, `imp3-teach*`, `imp3-commit`, `imp3-done-review-btn`, `imp3-done-taught`, `imp3-newwrap`, `imp3-arow-eq`, `imp3-facts-ok`, `imp3-facts-cost` | all absent |
| (no such thing) | `imp3-actrow-label` — an invented "Resulting decision" label the handoff does not have |
| flat rows separated by a 1px line | matched rows wrapped in per-band tinted **cards** — the "three-peer comparison card" the handoff explicitly rejects |

#119 also rewrote `PARITY_AND_GAPS.md` to assert full compliance, and its own
`ImportModal.handoffParity.test.tsx` pinned the re-derivation (e.g. asserting `imp3-cmp-card`,
`Link & update` as a queue-choice label, a collapsible "Needs you — decide with the full context"
section that the prototype does not have). Green tests therefore proved *self-consistency*, not
conformance — the same trap the schema-golden-file ADR records: **a green assertion proves no
CHANGE, never CORRECTNESS.**

## Decision

1. **The prototype's class vocabulary is the contract.** `import3.css` is ported into
   `frontend/src/components/tabs/accounts/statementImport.css` with the prototype's own selectors,
   order and values. Deliberately **not** copied: the standalone-shell scaffolding, the prototype's
   own dialog chrome (the canonical production `Modal` hosts the flow — focus trap, Escape,
   backdrop, trigger restoration), the Choose/Read step chrome (out of the handoff's own declared
   scope), the `imp3-drawer-*` transaction stand-in (the real `TxnDetail` is used), and the `:root`
   token block (every token the prototype uses already exists in
   `index.tokens.generated.css`, in **both** themes — which is why light theme needed no extra work).

2. **There is ONE definition site per class.** The re-derived `imp3-*` rules were deleted from
   `accounts.css`, along with 51 rules that the port made genuinely dead (`imp-fchip*`,
   `imp-twinpick*`, `imp-linkpick*`, `imp-lucius-strip*`, `imp-metafact*`, `imp-routebar*`,
   `imp-done*`, `imp-verdict2`, …). Two `imp-*` classes survive as **behavioural/QA hooks with no
   competing visuals** (`imp-pair-row` for the flash target, `imp-pair-side` for hard wrap
   containment); the rejected per-band card chrome on `imp-pair-row` is gone.

3. **Production data behaviour is untouched.** No backend file changed. Every write still goes
   through the same choke points, the commit payload shape is unchanged, and the port is a
   presentation-layer rewrite over the existing state model. Where the design has no equivalent for
   a real production fact, the fact is rendered **in the design's own idioms** rather than in a
   second visual language: the Lucius provenance strip reuses the "recessed panel under the row"
   idiom, the tie-out waterfall keeps its own `imp-math*` vocabulary folded behind a quiet toggle
   inside the verdict panel, and the extra honest facts (rows read / already imported / skipped /
   instructions) ride the facts line dimmed, after the design's own.

4. **Two deliberate deviations from the prototype, both recorded:**
   - **The picker does not close when you scroll its own list.** The prototype's blanket
     capture-phase `scroll` listener closes the popover on any scroll, which makes a long list
     unusable — you cannot reach the entry you opened it for. Scrolls outside the popover still
     close it (it is `position: fixed`, so it would otherwise strand away from its trigger).
   - **A weak-band twin option stays LIVE.** The unified `LinkPicker` replaced both prior pickers;
     naively applying the manual-link picker's "consumed ⇒ disabled" rule to twins would have
     silently killed the steal/swap resolution. Twins surface `taken` as a hint and stay pickable;
     only a genuinely consumed manual-link candidate is disabled. This regression was introduced
     during the port and caught by `ImportModal.matchPicker.test.tsx` — the reason that suite
     exists.

5. **The Lucius dock steps behind a workspace modal.** The dock is deliberately the top tier
   (`--z-toast + 10`) so it can float over page content (ADR 0028). But this modal fills the
   viewport and puts its primary action at the bottom-right — measured at 1024px, the expanded dock
   covered x 440–1000 / y 683–878 and the **Confirm & book** button sat inside that box, i.e. the
   CTA was unreachable. While a `modal-overlay--workspace` is open the dock drops to
   `--z-overlay - 1`. It is not interactive then anyway (the modal traps focus), so nothing is lost.
   Note the tier is `--z-overlay`, **not** `--z-modal`: the workspace overlay sits on the overlay
   tier on purpose, so a regular dialog opened from inside it still stacks over it.

6. **Responsive is production-only, and additive.** The handoff's own "known gaps" say it is
   desktop-first with no narrow-width pass. Below the 960px composition the fixed 48/190/212/140
   rails stop fitting, so the row reflows **without hiding a fact**: the amount joins the payee on
   the first line (the two facts you scan together) and the decision + category rails drop to a
   second line at full width; at ≤640px the compare columns stack and the mid rail becomes a
   horizontal divider so the Δ badge still reads as "between" the two sides.

## Consequences

- The live Accounts → Import Decide step now matches the handoff in hierarchy, layout, spacing,
  row structure, decision controls, status styling, alignment, typography and copy — including the
  copy changes the handoff itself asked for ("Import anyway" → "Import"; the verdict chip using the
  *exact* wording of the override control underneath it, so the two can never disagree).
- ~197 `ImportModal*` assertions were updated from the re-derived vocabulary to the handoff's. Two
  contract changes are worth calling out because they change how the tests reach the UI:
  - **A row's override controls live in its disclosure.** The handoff's rule is "tapping a row
    should only ever be for *overriding* something, never for *discovering* something", so the
    decision radiogroup and the pickers are behind the row. Tests now open the row (`openRow`)
    before interacting. Under the previous build these controls were in the DOM but hidden by CSS —
    invisible to a user, yet reachable by jsdom, which is why nothing caught it.
  - **The band verdict and the engine's reason stay visible on the CLOSED row**, in the ledger
    line's quiet supporting-text slot, so "what did this match and is that match right" is
    answered without opening anything — the handoff's one rule.
- Six new acceptance IDs (`SI-81`–`SI-86`) pin the surfaces #119 omitted: the verdict panel shape,
  the section badge/tone/title, the queue header + key badges, the single picker vocabulary, the
  teach affordance, and the footer commit CTA. All 18 SI handoff IDs map to `SI-HANDOFF-01`.
- The dead-class ratchet in `frontend/scripts/check-css-classes.mjs` is what forced the cleanup in
  (2) to be complete rather than approximate: it fails when a class is defined and unused, so the
  49 superseded rules could not be left behind.

## Alternatives rejected

- **Keep the production three-column card and treat it as equivalent.** This is what
  `PARITY_AND_GAPS.md` argued before task #124; the owner rejected it explicitly, and the handoff
  rejects it on design grounds ("two identically bold, identically right-aligned numbers stacked on
  top of each other reads as a bug, not a comparison").
- **Port the prototype's own dialog/nav/drawer chrome too.** The skill's "consume frozen shared
  services" rule and ADR 0028 both point the other way: the real `Modal`, `RouterShell` and
  `TxnDetail` host this flow.
- **Rename only the classes.** The gap was structural (missing components, wrong element order,
  card-vs-flat rows), not cosmetic; renaming would have produced the same picture under nicer names.
