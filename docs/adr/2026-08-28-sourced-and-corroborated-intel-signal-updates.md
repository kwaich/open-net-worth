# Intel Signal updates carry their sources, and a reversal needs corroboration

_Status: Accepted · 2026-08-28_
_Related: `2026-07-18-per-subject-analysis-coalescing`, INT19 (decision-relevance gate,
`docs/INTEL_AND_NEWS.md`)_

## Context

A live-data audit (owner, 2026-08-25 through 2026-08-28) found the Analyst pipeline pushing a
genuinely false claim to Telegram as fact. The Kestrel Dash (KSTL) Intel signal recorded, on
2026-08-21, "Morningstar confirms formal €13B takeover agreement — Prosus irrevocable
undertaking to sell its remaining stake" (materiality `new`, pushed). Four days later, the same
signal's timeline recorded "Uber's takeover bid for Kestrel Dash was rejected" — pushed to
Telegram at 14:30:43 as a fresh, confirmed fact, with:

- no `sources` field on the timeline entry (a sweep of every asset/topic signal updated in the
  prior 24h found 24 of 25 had zero recorded sources on their latest update — this is not an
  isolated KSTL incident),
- no acknowledgment it reversed the Aug-21 confirmed state,
- no corresponding evidence in the `evidence` store for this ticker pair after 2026-08-07.

Root cause: `analyst._update_signal` has never recorded which evidence backed a timeline entry —
only `{date, title, summary}`. Nothing checks whether a new entry that reverses direction from
the signal's own prior note is actually better-sourced than the note it's overwriting. The
`_push_decision` gate (materiality → decision-relevant → staleness, INT14a/INT19) has no
grounding check at all: an ungrounded reversal clears every existing gate.

An independent design review (`fable` model, 2026-08-28) of a fuller "Claims Ledger" proposal —
a new versioned/sourced table sitting between evidence and every consumer — found the
consolidation core sound (one synthesis call instead of two; grounding; cross-subject fan-out)
but its supersede/corroboration centerpiece underspecified: "independent sources" was never
defined (and the review's own reading of finding 7 — the same insider-sale story arriving under
three near-identical source labels — shows a naive count is trivially gamed by exactly the
duplication this project already found); contradiction detection was mislabeled deterministic
when it is the same semantic problem as duplicate detection; a claim fanned to a linked subject
was never re-checked against THAT subject's own active claims; a flagged claim had no owner and
could age past the freshness gate before ever confirming.

## Decision

Ship the narrow, well-specified slice first, on the existing `intel_signals` row — no new table.

1. **Every timeline entry `_update_signal` inserts carries `sources`** (`{url, title, domain}`,
   from the same evidence set the synthesis call read, each field routed through
   `sanitize_untrusted_text` like every other untrusted string in this file) and a stable `id`
   (uuid4 hex), so a later call has something concrete to reference. Capped at 3 sources per
   entry (not 5), and the stored URL is capped at 300 chars — separate from, and much tighter
   than, the 1200-char display-link heuristic `_source_links` uses.
2. **The synthesis call is shown its own subject's recent prior notes** (id + date + summary,
   not just the single collapsed `prior_summary` string it saw before, and now with an explicit
   `[UNCONFIRMED — do not treat as established fact]` / `[SUPERSEDED]` marker on any entry that
   isn't a plain confirmed note — see Correction below) and returns two new, defensively-typed
   fields alongside `materiality`/`decision_relevant`: `duplicate_of_id` (this restates an
   existing note — semantic judgment, the model's, not a token-overlap check: two paraphrases of
   one fact can share almost no words) and `contradicts_id` (this reverses a specific prior
   note). Both empty means a genuinely new, non-conflicting development. The model is also asked
   for `source_url` (the primary URL backing `why`) — **informational only** (see Correction).
3. **A duplicate never gets its own row** — the referenced note's `as_of` refreshes, nothing
   else happens, no push. (Correction below tightens WHEN a duplicate is honored at all.)
4. **A contradiction is held `flagged`, not written as confirmed, until corroborated**: below
   the corroboration bar (Correction below defines it), the entry is visible in-app with a
   `flagged` status and a `contradicts` pointer, but excluded from the external push gate.
5. **A new `_push_decision` gate: grounded.** An entry with no real backing source, or a
   `flagged` (uncorroborated contradiction) status, is suppressed under its own log reason —
   in-app timeline still updates so nothing is hidden, only the external interrupt is withheld,
   same shape as the existing materiality/decision-relevant gates. (Correction below changes
   what "no real backing source" means.)
6. **Freshness for a claim that started `flagged` is measured from the moment it turns
   confirmed, not from original enqueue** — otherwise a real reversal that takes a day to
   corroborate ages out of the 6h staleness window before it can ever legitimately push.
   (Correction below changes what "the moment it turns confirmed" is anchored to.)

Deferred, not abandoned: the full versioned Claims Ledger (a dedicated table, cross-subject
fan-out merging one LLM read across an asset and its linked topic, Asset Brief consolidation
onto the same ledger, a background precompute sweep) stays the documented target architecture.
It is not the first thing built on top of an corroboration mechanism that had not yet been
pinned down — the independence definition and the flagged-claim lifecycle above are exactly the
parts the fable review found missing, and they needed a real definition before anything is built
on top of them, table or no table.

## Correction (2026-08-28, same-day security review)

A security review did not just READ the Decision above — it RAN the actual functions against
crafted inputs and found the first implementation didn't deliver the promise it describes. Six
findings, all fixed in the same change (this ADR is edited in place rather than superseded,
since it had not shipped/merged yet):

- **The grounded gate (point 5) checked a field the model can't legitimately ground.** The
  evidence block shown to the model (`analyst_prompt.render_evidence_block`) never includes a
  URL — only title/source/date/snippet — yet the original `_push_decision` gated on the model's
  own `source_url` string. The model could only return `""` (suppressing every push forever), an
  invented/hallucinated URL (accepted), or something copied out of attacker-controlled snippet
  text. **Fix: the gate now reads `len(entry["sources"]) > 0` — the real, SERVER-BUILT list from
  point 1, built from the actual evidence, never the model.** `source_url` is downgraded to
  informational-only, and even then cross-checked against the real evidence
  (`_verified_source_url`) before being surfaced anywhere — an unverifiable string is discarded,
  never trusted at face value.
- **Corroboration counted ambient news volume, not support for the specific claim** (point 4).
  `len(dedupe_stories(evidence))` had two independent gaps: no check the sources were even about
  the claim (two unrelated headlines in the same batch counted as 2 independent sources for ANY
  reversal), and `dedupe_stories` only collapses SAME-PUBLISHER near-duplicates, so one hostile
  feed posting two differently-worded items about one fabricated story trivially cleared the
  bar. The shipped regression test for this path actively PINNED the bug (asserted `== 2` for
  the exact same-story-under-near-identical-labels case this ADR's own Context section says must
  collapse to ONE). **Fix: corroboration now filters evidence to items that actually overlap the
  claim text (`_claim_relevant_evidence`, reusing `content_tokens` — the SAME primitive
  `is_same_story`/`cluster_by_content_overlap` are built on, not a third heuristic) and then
  counts DISTINCT REGISTRABLE DOMAINS among those (`core.urls.registrable_domain`), not raw
  `dedupe_stories` cluster count.** A code-review pass on this fix's own first draft found a
  second-order bug: `_read_and_collapse_evidence`'s `collapse_to_distinct_stories` (which builds
  the evidence the PROMPT sees) is publisher-agnostic and runs at the same overlap threshold, so
  two genuinely different real outlets covering the same event routinely collapse to ONE
  representative BEFORE corroboration counting ever saw them — making the ≥2-domain bar
  unreachable in exactly the well-corroborated case it exists to recognize. `_process_row` now
  threads a SEPARATE, less-collapsed evidence read (`raw_evidence`) to corroboration counting,
  distinct from the collapsed set used for the prompt/display.
- **The staleness anchor (point 6) was `now()` at write time**, stamped in the same call that
  computed it — `_hours_since(confirmed_at)` was always ≈0 regardless of how old the actual
  evidence was, so an 8-day-old backlog row confirmed and pushed as if it just happened. **Fix:
  `confirmed_at` anchors on the freshest SUPPORTING evidence item's own (clamp-protected)
  timestamp** (`_freshest_supporting_timestamp`, reusing `analyst_prompt.clamped_recency_hours`
  — the same clamp `collapse_to_distinct_stories` already uses, not a second recency
  definition), never the wall-clock write time.
- **A flagged/superseded entry rendered identically to a confirmed one** in the next synthesis
  call's prior-notes block, so an uncorroborated or already-invalidated claim could launder
  itself as settled fact one call later — despite nothing in this slice ever re-evaluating a
  flagged entry on a later pass. **Fix: `prior_notes_block` now renders an explicit
  `[UNCONFIRMED — do not treat as established fact]` / `[SUPERSEDED]` marker.** And to be
  honest about what actually exists: **corroboration in this slice is SAME-SYNTHESIS-CALL ONLY.**
  A `contradicts_id` reversal is corroborated (or not) using the evidence available in the ONE
  call that raised it; there is no later-pass mechanism that re-evaluates a `flagged` entry once
  more corroborating evidence eventually arrives. Building that is a second, materially different
  temporal system, and specifying it under this fix round would risk repeating the exact
  underspecification problem this review round exists to close — it stays out of scope, tracked
  with the rest of the deferred Claims Ledger work, not silently implied to already exist.
- **`duplicate_of_id` was a silent, permanent, unverified deletion of a real development** — the
  id most exposed to a hostile headline (quoted in the prompt one fence-width from
  attacker-controlled snippet text), honored with ZERO relevance check. A fresh, decision-relevant
  `materiality="new"` analysis was fully discarded — no timeline row, and `evidence_watermark`
  still advanced past that evidence so it could never be reconsidered — if the model set
  `duplicate_of_id` to ANY real prior entry id, however unrelated. **Fix, four parts:** (1)
  refused outright when `materiality == "new"` (self-contradictory — a genuinely new development
  cannot also restate one); (2) requires real content-overlap between `why` and the referenced
  entry's own `summary` (`_texts_overlap`, same primitive as the corroboration fix) before being
  honored; (3) a legitimately-suppressed duplicate logs the referenced id AND the discarded `why`
  (bounded), not just the subject, for an audit trail; (4) `evidence_watermark` does NOT advance
  on a suppressed-duplicate turn, so evidence a wrongly-suppressed call discarded stays eligible
  to be read again next tick instead of being permanently lost.
- **A hardcoded structural fallback (a crash-trigger row whose signal was deleted between
  enqueue and drain) always reported `source_url: ""`**, which the grounded gate then reported as
  a generic `"ungrounded"` — indistinguishable in logs/metrics from a genuine grounding failure
  on an entry that was actually analyzed. **Fix: its own dedicated `entry_meta["signal_missing"]`
  → `"signal_missing"` suppress reason**, checked before the grounded gate.

## Consequences

- Fixes the concrete, evidenced failure (an ungrounded reversal reaching Telegram as fact)
  without a schema migration, a new store, or new scheduler jobs.
- Corroboration is now claim-relevance-filtered THEN counted by distinct registrable domain
  (`_claim_relevant_evidence` + `core.urls.registrable_domain`), reusing `content_tokens` — the
  same content-overlap primitive already used for same-story clustering elsewhere in this file —
  rather than `core.news.dedupe_stories`'s same-publisher-only collapse, which had no relevance
  check and was proven (by the security review actually running it) to accept unrelated
  headlines and under-collapse a single hostile domain's differently-worded posts. Counted
  against a LESS-collapsed evidence read than the prompt uses, so genuine multi-outlet
  corroboration isn't merged away before it can be counted.
- A `flagged` entry with no confirming source ever arriving IN THE SAME SYNTHESIS CALL is a
  real, accepted outcome: it stays visible in-app forever as unconfirmed (now explicitly marked
  `[UNCONFIRMED]` in any later prompt that references it), never silently disappears and never
  reaches Telegram. No auto-expiry in this slice — an unconfirmed claim isn't wrong, it's
  unresolved. There is also no LATER-PASS promotion in this slice — see Correction above; that
  is a documented, deliberate scope boundary, not an oversight.
- The grounded gate is now genuinely server-authoritative: a model cannot force a push by
  inventing a `source_url`, and cannot force a suppression by leaving it blank — grounding is
  decided entirely by what evidence the server actually attached to the entry.
- Two-facet consolidation (Asset Brief reading the same synthesis output as the Signal),
  cross-subject fan-out, and the background precompute sweep are NOT part of this change — the
  Asset Brief keeps its own separate `_synthesize_asset` call for now. Tracked as explicit
  follow-on work in the Claims Ledger design doc, not silently dropped.

## Correction (2026-08-29, follow-on hardening — timeline storage + the fan-out ADR's own review)

Two more fixes landed against the mechanics this ADR owns, one from a security review of the
follow-on `cross-subject-fan-out-and-asset-brief-consolidation` ADR (which reuses this file's
`_texts_overlap`/`content_tokens` primitives) and one a direct owner report:

- **`_texts_overlap` (this ADR's own content-overlap primitive, built for the F2/F5 corroboration
  and duplicate-relevance fixes above) is now ALSO reused by that ADR's `_find_linked_pairs`
  fix** to gate cross-subject pairing on genuine evidence relevance, not just a watchlist link —
  see that ADR's own Correction for the full writeup. No change to the primitive itself.
- **`_TIMELINE_CAP` — the hard 20-entry storage truncation `_update_signal` applied on every
  write (see the INT15 comment this file's own module docstring references) — was REMOVED
  entirely (owner-reported, 2026-08-29).** This was a real, evidenced data-loss bug, independent
  of the sourced/corroborated fix above: a real KSTL signal's stored timeline held only its most
  recent 20 entries (spanning ~26 days), silently evicting a genuine months-old pushed alert (a
  hallucinated "Uber terminates Taiwan deal" claim) with no way to ever produce a record of it
  again — directly contradicting this app's "never silently degrade/discard data" invariant.
  Storage is now UNBOUNDED, forever; only READS of it stay bounded (`get_asset_context`'s
  existing `timeline_limit`/showing/total; `GET /api/intel/signals`' list feed now bounds each
  embedded timeline the same way, with a new `GET /api/intel/signals/{id}` for the full,
  unbounded fetch — see `backend/services/intel_signals.py`). This ADR's own `prior_notes_block`
  window (`_PRIOR_NOTES_CONTEXT_CAP`, a cost/relevance bound on what the MODEL sees per
  synthesis call) is UNCHANGED and unrelated — a retention policy and a per-call context budget
  are two different questions, and this fix touches only the former.
