"""The INVERTED half of the import accuracy eval: does the statement account for the right
LEDGER entries?

``eval_import_accuracy`` scores four dimensions and every one of them is per STATEMENT ROW —
"given this row, what did the pipeline decide about it". That is the forward view. The import
preview also renders the INVERSE, ``preview_ledger_check``'s "In your ledger, not on this
statement" panel: of the owner's existing ledger entries in this period + these accounts, which
does the statement account for and which does it leave unexplained. Nothing scored that.

The cost of not scoring it, verbatim: RFC ``2026-07-29-one-match-engine-instructions-as-policy``
Phases 0-2 deleted an entire second matcher whose whole output landed on that panel, and the
harness reported byte-identical numbers before and after — 165/198 either way. The owner's most
visible real bug lived there (a money-IN top-up row offered as "the same transaction" as
unrelated money-OUT expenses, because the retired matcher compared ``abs()`` amounts under no
kind or account gate) and the four row dimensions could not see it in either direction.

WHAT THIS MODULE SCORES
------------------------
Two graded dimensions, both derived from ``response["ledger_check"]`` (``rows`` = the core
window, ``boundary`` = entries near the cut-off that likely belong to the adjacent statement):

``uncovered``  every id the golden says must STILL be listed as unexplained, plus — when the
               golden sets ``exhaustive_uncovered`` — every id the panel lists that the golden
               did not name (a statement that stops accounting for something is the same defect
               as one that never did).
``covered``    every id the golden says the statement DOES account for, which must therefore be
               absent from the panel. This is the only dimension that can catch a coverage path
               the row dimensions never touch: an entry linked by external reference rather than
               by a match flag carries no ``expected_match_existing_id`` anywhere.

Plus ONE ungraded, deliberately un-averageable signal:

``false_pairings``  a statement row and a ledger entry asserted to be "the same transaction"
               when they are not. This is a money-correctness defect — the entry silently
               disappears from the owner's "not on this statement" panel and a merge would
               rewrite a real ledger row — so it is NEVER folded into an accuracy percentage,
               where one bad pairing among two hundred rows rounds to 99.5% and reads as fine.
               It is counted, named, printed on its own line, and it fails the run.

TWO SOURCES OF FALSE PAIRINGS
------------------------------
1. **Golden-declared** (``coverage.must_not_pair``) — the author names a ledger entry that no
   row may claim (or that one specific row may not claim). Needs ground truth; scoped to what
   the author actually verified.
2. **Structural, always on, no golden required** — a pairing whose two sides run in OPPOSITE
   directions: a money-IN statement row claimed to be a money-OUT ledger expense, or the
   reverse. That is the incident class stated as a property rather than as a list of ids, so it
   covers every statement in the eval dir including ones nobody has authored a golden for.

   It is scoped by the MATCH CLASS's own contract, because "opposite directions" is only a
   defect where the class promises sameness:
     * ``near_duplicate`` — same charge seen twice. Opposite directions are always wrong
       (``matching._classify``'s F14a demands kind equality). CHECKED.
     * ``transfer_leg_echo`` — the row duplicates one leg of an existing transfer, on that
       leg's own side. CHECKED.
     * ``statement_twin`` — the retired second matcher, and where the live incident happened.
       It made no promise at all, which was the problem. CHECKED.
     * ``transfer_pair`` — NOT CHECKED. Opposite directions are its ENTIRE definition ("one
       row is a debit, the other a credit: the two legs of a single movement"), so this test
       would fire on every legitimate pair. A wrong ``transfer_pair`` is real and this eval has
       caught them, but it takes domain knowledge to call — that is what ``must_not_pair`` is
       for, and pretending otherwise would make the harness lie with a confident reason.
   Counterparts of kind ``transfer`` are skipped for a separate reason: ``_existing_summary``
   reports a transfer's ``from_amount`` unsigned-by-construction, so its sign on the wire is
   not a direction relative to the statement's own account.

The pairing scan reads the RESPONSE CONTRACT, not one matcher: today's assertions live on
``rows[].duplicate_of`` and ``rows[].flags[].existing_id``, and it also reads the retired
``ledger_check.rows[].statement_twin`` key. That key is gone (RFC §D3) and is expected to stay
gone — which is exactly why it is still read here. The ADR's own argument for deleting the
second matcher was that a previous RFC had already rejected it once and "the code grew back and
then grew teeth"; a harness that only knows about the surfaces that exist today would go blind
again the same way.

NOTE TO GOLDEN AUTHORS
-----------------------
The same warning the row dimensions carry applies with more force here, because the inverted
view is a SET and it is tempting to paste the observed set in as the expectation. Do not. An id
belongs in ``expected_uncovered`` only if you established, from the statement's own contents,
that the statement does not contain that transaction — and in ``expected_covered`` only if you
established which row (or which external reference) accounts for it. ``exhaustive_uncovered``
is a claim that you enumerated the ENTIRE window, not that you looked at the panel and agreed
with it.
"""

from __future__ import annotations

COVERAGE_DIMENSIONS = ("uncovered", "covered")

_MATCH_FLAG_TYPES = ("near_duplicate", "transfer_pair", "transfer_leg_echo")
# Kinds whose `_existing_summary` amount is genuinely SIGNED (income = +to_amount,
# expense = -from_amount). A transfer reports +from_amount regardless of which side the
# statement sits on, so it carries no comparable direction — see the module docstring.
_SIGNED_KINDS = ("income", "expense")
# Match classes that PROMISE the two sides run the same way. `transfer_pair` is deliberately
# absent: opposite legs are its definition, not a defect — module docstring, source 2.
_SAME_DIRECTION_CLASSES = ("near_duplicate", "transfer_leg_echo", "statement_twin (RETIRED KEY)")


def uncovered_buckets(response: dict) -> dict[str, str] | None:
    """``{existing_id: "rows" | "boundary"}`` for everything the ledger-check panel lists as
    unexplained, or None when the preview produced no ledger check at all (no usable window,
    no routed account, or the advisory computation failed)."""
    check = response.get("ledger_check")
    if not check:
        return None
    out: dict[str, str] = {}
    for bucket in ("rows", "boundary"):
        for entry in check.get(bucket) or []:
            if entry.get("id"):
                out[entry["id"]] = bucket
    return out


def pairings(response: dict) -> list[dict]:
    """Every "this statement row and this ledger entry are the same transaction" assertion in
    the response, whatever surface it arrives on."""
    found: list[dict] = []
    for entry in response.get("rows") or []:
        idx = entry.get("index")
        if entry.get("duplicate") and entry.get("duplicate_of"):
            found.append(
                {
                    "row_index": idx,
                    "existing_id": entry["duplicate_of"],
                    "source": "duplicate_of",
                    "row_amount": entry.get("amount"),
                    "existing": None,
                }
            )
        for flag in entry.get("flags") or []:
            if flag.get("type") in _MATCH_FLAG_TYPES and flag.get("existing_id"):
                found.append(
                    {
                        "row_index": idx,
                        "existing_id": flag["existing_id"],
                        "source": flag["type"],
                        "row_amount": entry.get("amount"),
                        "existing": flag.get("existing") or {},
                    }
                )
    for entry in (response.get("ledger_check") or {}).get("rows") or []:
        twin = entry.get("statement_twin")
        if twin and entry.get("id"):
            found.append(
                {
                    "row_index": twin.get("row_index") if isinstance(twin, dict) else None,
                    "existing_id": entry["id"],
                    # The retired second matcher (RFC 2026-07-29 §D3). Its reappearance is
                    # itself the finding, so it is named rather than folded in with the rest.
                    "source": "statement_twin (RETIRED KEY)",
                    "row_amount": None,
                    "existing": entry,
                }
            )
    return found


def _crossing(pair: dict) -> str | None:
    """The structural false-pairing test: opposite money directions. Returns the reason text,
    or None when the pairing is direction-consistent or carries no comparable direction."""
    if pair["source"] not in _SAME_DIRECTION_CLASSES:
        return None
    existing = pair.get("existing") or {}
    if existing.get("kind") not in _SIGNED_KINDS:
        return None
    row_amt, exist_amt = pair.get("row_amount"), existing.get("amount")
    if not isinstance(row_amt, (int, float)) or not isinstance(exist_amt, (int, float)):
        return None
    if not row_amt or not exist_amt or (row_amt > 0) == (exist_amt > 0):
        return None
    return (
        f"money-{'IN' if row_amt > 0 else 'OUT'} statement row paired with a money-"
        f"{'IN' if exist_amt > 0 else 'OUT'} {existing.get('kind')} ledger entry "
        f"({pair['source']}) — opposite directions can never be the same transaction"
    )


def _declared_violations(must_not_pair: list[dict], found: list[dict]) -> list[dict]:
    out: list[dict] = []
    for rule in must_not_pair:
        target = rule.get("existing_id")
        want_row = rule.get("row_index")
        for pair in found:
            if pair["existing_id"] != target:
                continue
            if want_row is not None and pair["row_index"] != want_row:
                continue
            out.append(
                {
                    "existing_id": target,
                    "row_index": pair["row_index"],
                    "source": pair["source"],
                    "declared": True,
                    "why": rule.get("reason") or "the golden forbids this pairing",
                }
            )
    return out


def _score_ids(
    declared: list[dict], buckets: dict[str, str] | None, *, want_listed: bool
) -> tuple[int, int, list[dict]]:
    """Grade one list of id assertions. ``want_listed`` True = the id must appear on the panel
    (uncovered), False = it must be absent (covered, i.e. the statement accounts for it)."""
    total = correct = 0
    mismatches: list[dict] = []
    for item in declared:
        existing_id = item.get("existing_id")
        if not existing_id:
            continue
        total += 1
        if buckets is None:
            mismatches.append(
                {
                    "field": "uncovered" if want_listed else "covered",
                    "existing_id": existing_id,
                    "expected": "listed as not-on-statement" if want_listed else "accounted for",
                    "actual": "no ledger_check in the response — nothing to score against",
                    "reason": item.get("reason"),
                }
            )
            continue
        bucket = buckets.get(existing_id)
        want_bucket = item.get("bucket") or "any"
        ok = (bucket is not None) if want_listed else (bucket is None)
        if ok and want_listed and want_bucket != "any":
            ok = bucket == want_bucket
        if ok:
            correct += 1
        else:
            mismatches.append(
                {
                    "field": "uncovered" if want_listed else "covered",
                    "existing_id": existing_id,
                    "expected": (
                        f"listed as not-on-statement ({want_bucket})"
                        if want_listed
                        else "accounted for by this statement (absent from the panel)"
                    ),
                    "actual": (
                        f"listed in {bucket!r}"
                        if bucket
                        else "absent from the panel (something claimed it)"
                    ),
                    "reason": item.get("reason"),
                }
            )
    return total, correct, mismatches


def score_coverage(golden: dict, response: dict) -> dict:
    """Grade the inverted view. A golden with no ``coverage`` block scores exactly as it did
    before this dimension existed — 0/0 on both dimensions — but STILL gets the structural
    false-pairing scan, which needs no ground truth to be right."""
    cov = golden.get("coverage") or {}
    buckets = uncovered_buckets(response)
    found = pairings(response)

    totals = dict.fromkeys(COVERAGE_DIMENSIONS, 0)
    correct = dict.fromkeys(COVERAGE_DIMENSIONS, 0)
    mismatches: list[dict] = []

    expected_uncovered = cov.get("expected_uncovered") or []
    t, c, m = _score_ids(expected_uncovered, buckets, want_listed=True)
    totals["uncovered"] += t
    correct["uncovered"] += c
    mismatches += m

    t, c, m = _score_ids(cov.get("expected_covered") or [], buckets, want_listed=False)
    totals["covered"] += t
    correct["covered"] += c
    mismatches += m

    if cov.get("exhaustive_uncovered") and buckets is not None:
        named = {i.get("existing_id") for i in expected_uncovered}
        for existing_id, bucket in sorted(buckets.items()):
            if existing_id in named:
                continue
            totals["uncovered"] += 1
            mismatches.append(
                {
                    "field": "uncovered_unexpected",
                    "existing_id": existing_id,
                    "expected": "accounted for — the golden enumerated the whole window",
                    "actual": f"listed in {bucket!r} as not-on-statement",
                    "reason": cov.get("notes"),
                }
            )

    violations = _declared_violations(cov.get("must_not_pair") or [], found)
    seen = {(v["existing_id"], v["row_index"]) for v in violations}
    for pair in found:
        why = _crossing(pair)
        if why and (pair["existing_id"], pair["row_index"]) not in seen:
            violations.append(
                {
                    "existing_id": pair["existing_id"],
                    "row_index": pair["row_index"],
                    "source": pair["source"],
                    "declared": False,
                    "why": why,
                }
            )

    return {
        "declared": bool(cov),
        "dimension_totals": totals,
        "dimension_correct": correct,
        "mismatches": mismatches,
        "false_pairings": violations,
        # What the panel actually said, kept in the scorecard JSON so a change in the inverted
        # view is inspectable even where no golden asserts anything about it yet.
        "observed": {
            "status": (response.get("ledger_check") or {}).get("status"),
            "period": (response.get("ledger_check") or {}).get("period"),
            "uncovered_core": sorted(i for i, b in (buckets or {}).items() if b == "rows"),
            "uncovered_boundary": sorted(i for i, b in (buckets or {}).items() if b == "boundary"),
            "pairings": len(found),
        },
    }


def print_coverage(cov: dict) -> None:
    """The per-statement coverage block, in the scorecard's own style. False pairings print as
    their own named lines — never as a ratio."""
    obs = cov["observed"]
    if obs["status"] is None:
        print("  ledger check: NONE (no usable window / no routed account / computation failed)")
    else:
        period = obs["period"] or {}
        print(
            f"  ledger check: {period.get('start')}..{period.get('end')} "
            f"not-on-statement={len(obs['uncovered_core'])} core "
            f"/ {len(obs['uncovered_boundary'])} boundary  pairings={obs['pairings']}"
        )
    if not cov["declared"]:
        print("    (golden asserts nothing about coverage — structural pairing scan only)")
    for mismatch in cov["mismatches"]:
        print(
            f"    {mismatch['field']}: {mismatch['existing_id']} — "
            f"expected {mismatch['expected']}, actual {mismatch['actual']}"
        )
    for violation in cov["false_pairings"]:
        origin = "golden" if violation["declared"] else "structural"
        print(
            f"    ** FALSE PAIRING [{origin}] row {violation['row_index']} <-> "
            f"{violation['existing_id']}: {violation['why']}"
        )
