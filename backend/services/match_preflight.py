"""match_preflight — the PRE-write duplicate check: run the match engine over transactions the
caller has NOT written yet and report, per row, what the engine already knows about them.

WHY THIS EXISTS (owner incident, 2026-07): the same S$2,000.00 card payment was written twice
by the email agent — once as a two-leg transfer from the bank alert, once as a single-leg credit
from the Giro alert two days later. Every dedup surface we had was POST-hoc:
``match_review.review_created_rows`` runs after the rows exist (created-then-flagged, by design,
so a flagged row is never silently dropped) and ``match_review.scan_duplicates`` scans the
LEDGER by date range. Neither can answer "here are rows I am ABOUT to write; are they already
there?" — so an agent's only protection ran after it had already written, and when that hook
returned nothing the agent had no signal at all.

WHAT THIS MODULE IS NOT: a second matcher. Every verdict here comes from ``matching.match_batch``
— the SAME entry point ``add_transactions``' own duplicate pre-flight and
``match_review.review_created_rows`` call, over the SAME candidate pool
(``matching._batch_ledger_pool``, reached by NOT overriding ``candidates=``, so a fix to that
pool reaches this surface for free). Using ``match_batch`` rather than per-row ``check()`` is
deliberate and load-bearing:

  * it is what the WRITE path computes, so this tool's answer IS the write's answer — one
    dialect, no drift, and a caller can act on it without re-deriving anything;
  * it classifies the proposed rows against EACH OTHER as well as the ledger, so a list that
    duplicates itself is caught (per-row ``check()`` against a ledger pool cannot see that);
  * it resolves global 1:1 exclusivity, so two proposed rows never both claim the same existing
    transaction — the returned set is actionable as given.

(``scan_duplicates`` deliberately does NOT use ``match_batch``, for a reason that does not apply
here: its rows are unrelated LEDGER rows spread over months, and ``_classify_batch_pairs``'
shared synthetic ``batch_id`` would blanket-veto every cross-source pair via the same-feed
invariant. Rows handed to THIS module genuinely did arrive together — one statement, one agent
turn, one bulk-add — which is exactly the completeness claim that stamp encodes, and is why
``add_transactions`` stamps them the same way.)

The cost of that stamp, stated plainly: two SAME-DIRECTION identical rows in ONE call are NOT
flagged against each other (invariant 3 — "the bank already de-duplicated its own statement").
Opposite-direction legs of one movement still pair, and every ledger-side comparison is
unaffected. This is inherited on purpose rather than patched around: a check that flagged what
the write it precedes then books happily would teach the caller to ignore it. The incident this
module exists for is cross-CALL anyway — two alerts, two days apart, two agent turns.

L0 IS RUN HERE, unlike in ``match_batch``. ``match_batch`` skips the exact-external-ref floor
because for its callers L0 already ran at the create choke. For a row that has NOT been written,
L0 has not run — and its verdict is the most decisive one available: ``create_transaction``
returns the EXISTING row instead of creating, so "booking this changes nothing" is a fact worth
reporting rather than a probability.

WRITES NOTHING. Every call below is a read: ``matching.check(profile=FLOOR)`` is an indexed
external-ref lookup, ``match_batch`` reads the ledger and mutates only the caller's own in-memory
dicts (it stamps ``batch_id`` and restores it in a ``finally``), ``_load_rates`` reads the
FX cache file, ``known_transfer_routes`` reads the ledger. No store append, no review enqueue, no
decision remembered. Keeping this module physically separate from ``match_review`` — which owns
the merge primitive, the review queue and the verdict cache — is part of that guarantee: there is
no write primitive in scope to reach for by accident.

Layer: ``services → db → core``.
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.services import matching

# Deliberately equal to ``ai.tools.ledger._MAX_BULK_ADD_ROWS``: a check that precedes a write must
# accept exactly what the write accepts, or it stops being usable at the only boundary where it
# matters. ``tests/dedup/test_check_duplicates.py`` pins the two equal so they cannot drift apart.
MAX_PROPOSED_ROWS = 500

# Per-row candidate cap. The engine's ``assigned`` candidate is THE answer; the rest are F20-style
# "could also be this one" ties. Three covers a genuine tie without paying for a full transaction
# row per near-miss on every row of a 500-row batch.
DEFAULT_CANDIDATES_PER_ROW = 3
MAX_CANDIDATES_PER_ROW = 10

# One sentence per class, in the caller's tense ("you have not written this yet"). Keyed on
# ``MatchClass`` values so a new class shows up as a missing note, never as a wrong one.
_KLASS_NOTES: dict[str, str] = {
    "exact": (
        "an existing row already carries this external reference — booking it returns that row "
        "rather than creating a second one"
    ),
    "near_duplicate": "the same charge is already in the ledger",
    "transfer_pair": (
        "this is the OTHER LEG of the matched row — booking it records one movement twice; "
        "convert the existing row into a transfer instead"
    ),
    "transfer_leg_echo": (
        "this single-leg row echoes one leg of an existing two-leg transfer — that movement is "
        "already recorded"
    ),
}


def _recommended_action(band: str, klass: str | None, sibling_index: int | None) -> dict:
    """What the caller should DO with this row, in the vocabulary of the write it precedes.

    ``skip``/``create`` are two of ``add_transactions``' own three ``decisions`` actions, so a
    strong verdict feeds straight back into the write call. ``confirm`` is the third outcome and
    has no write-side equivalent on purpose: the engine's REVIEW band means "ambiguous", and the
    only correct handling of ambiguous money is a human deciding. Bands come from
    ``matching._band`` — the same SCORE_STRONG/SCORE_FENCE boundary ``list_match_reviews``'
    ``recommended_action`` reads, not a second threshold invented here.
    """
    if klass is None or band == "none":
        return {"action": "create", "note": "no duplicate evidence — safe to book"}
    if sibling_index is not None:
        detail = (
            f"the match is row {sibling_index} of THIS SAME list, not a ledger row — "
            "book one of the two, not both"
        )
    else:
        detail = _KLASS_NOTES.get(klass, f"matched an existing row as {klass}")
    if band == "strong":
        return {"action": "skip", "note": f"do not book — {detail}"}
    if band == "review":
        return {
            "action": "confirm",
            "note": f"ambiguous — {detail}. Confirm with the user before booking.",
        }
    return {"action": "create", "note": f"weak evidence only — {detail}"}


def _candidate(cand: matching.Candidate, row: dict, rates: dict | None, sibling: int | None):
    """One candidate in the engine's own vocabulary — the same fields ``find_duplicates``
    returns per pair, plus which side of the pool it came from.

    A SIBLING candidate (another not-yet-written row from this same call) carries no id and no
    ``signature``: a pair signature identifies two PERSISTED rows, and emitting one for a pair
    that does not exist would hand the caller a key that resolves to nothing.
    """
    out = {
        "klass": cand.klass.value,
        "score": cand.score,
        "become_transfer": cand.become_transfer,
        "reasons": list(cand.reasons),
        "proposed_index": sibling,
        "existing_id": None if sibling is not None else cand.id,
        "existing": None if sibling is not None else cand.txn,
        "signature": None if sibling is not None else matching.pair_signature(row, cand.txn, rates),
    }
    return out


def _floor_result(index: int, floor: matching.MatchResult, row: dict, rates) -> dict:
    """L0 hit: the create choke would return the existing row. Decisive — nothing the batch pass
    could say changes what booking this row does."""
    cand = floor.best
    return {
        "index": index,
        "band": "strong",
        "recommended_action": _recommended_action("strong", cand.klass.value, None),
        "duplicate_of_index": None,
        "candidates": [_candidate(cand, row, rates, None)],
        "candidates_showing": 1,
        "candidates_total": 1,
    }


def check_proposed_rows(
    rows: list[dict], *, max_candidates: int = DEFAULT_CANDIDATES_PER_ROW
) -> list[dict]:
    """Classify ``rows`` (two-leg ledger draft shape, NOT persisted) and return one result each.

    ``rows`` must already be in the shape ``create_transaction`` takes — the caller-vocabulary
    mapping lives at the tool head (``ai.tools._shared._txn_data_from_ai``), exactly as it does
    for ``add_transactions``, so both surfaces classify byte-identical drafts.

    Each result is ``{index, band, recommended_action, duplicate_of_index, candidates,
    candidates_showing, candidates_total}``. ``candidates`` is ranked with the engine's assigned
    (1:1-exclusive) candidate first and capped at ``max_candidates`` — ``candidates_total`` always
    reports the true pre-cap count.

    Raises ``ValidationError`` over ``MAX_PROPOSED_ROWS``.
    """
    if len(rows) > MAX_PROPOSED_ROWS:
        raise ValidationError(
            f"{len(rows)} proposed transactions exceeds the {MAX_PROPOSED_ROWS}-row check cap — "
            "split into smaller batches (20-25 rows is the guidance for the write itself)"
        )
    if not rows:
        return []
    cap = max(1, min(int(max_candidates), MAX_CANDIDATES_PER_ROW))
    rates = matching._load_rates()

    # Object identity, not id: a not-yet-written row has none, and ``_classify_batch_pairs``
    # matches siblings the same way.
    sibling_of: dict[int, int] = {id(r): i for i, r in enumerate(rows)}

    # L0 — the exact-external-ref floor. Skipped by ``match_batch`` (its callers already ran it
    # at the choke); for an unwritten row it has not run and it is the most decisive answer.
    floors = [matching.check(r, profile=matching.FLOOR) for r in rows]

    row_matches = matching.match_batch(
        rows, context=matching.BatchContext(complete=False), rates=rates
    )

    out: list[dict] = []
    for i, (row, rm) in enumerate(zip(rows, row_matches)):
        floor = floors[i]
        if floor.action == "noop" and floor.best is not None:
            out.append(_floor_result(i, floor, row, rates))
            continue
        ranked = [c for c in rm.candidates if c is rm.assigned] + [
            c for c in rm.candidates if c is not rm.assigned and c.score >= matching.SCORE_FENCE
        ]
        shown = ranked[:cap]
        sibling = rm.intra_batch.get("index") if rm.intra_batch else None
        out.append(
            {
                "index": i,
                "band": rm.band,
                "recommended_action": _recommended_action(
                    rm.band, rm.assigned.klass.value if rm.assigned else None, sibling
                ),
                "duplicate_of_index": sibling,
                "candidates": [_candidate(c, row, rates, sibling_of.get(id(c.txn))) for c in shown],
                "candidates_showing": len(shown),
                "candidates_total": len(ranked),
            }
        )
    return out
