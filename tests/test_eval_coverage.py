"""The coverage / reconciliation dimension of the import accuracy harness
(`backend/scripts/eval_coverage.py`) — the INVERTED view: which existing ledger entries a
statement accounts for, and which it must never claim.

Every fixture here is synthetic. The harness itself only ever runs against the owner's real
data outside the repo; nothing real belongs in a test file.
"""

from __future__ import annotations

from backend.scripts.eval_coverage import COVERAGE_DIMENSIONS, pairings, score_coverage


def _response(rows=None, uncovered=None, boundary=None, with_check=True) -> dict:
    out: dict = {"rows": rows or []}
    if with_check:
        out["ledger_check"] = {
            "status": "ok",
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "rows": [{"id": i} for i in (uncovered or [])],
            "boundary": [{"id": i} for i in (boundary or [])],
        }
    return out


def _row(index, amount, *, match=None, kind="expense", flag="near_duplicate") -> dict:
    """A statement row, optionally carrying a DIRECTION-CONSISTENT match (the ordinary case) so
    the structural crossing check stays out of tests that are about something else."""
    row: dict = {"index": index, "amount": amount, "flags": []}
    if match:
        row["flags"].append(
            {
                "type": flag,
                "existing_id": match,
                "existing": {"id": match, "kind": kind, "amount": amount},
            }
        )
    return row


# ── the dimension is additive ──────────────────────────────────────────────────────────


def test_golden_without_a_coverage_block_scores_nothing_and_flags_nothing():
    score = score_coverage({"rows": []}, _response(uncovered=["txn_a", "txn_b"]))

    assert score["declared"] is False
    assert all(score["dimension_totals"][d] == 0 for d in COVERAGE_DIMENSIONS)
    assert score["mismatches"] == []
    assert score["false_pairings"] == []
    # ...but the panel is still recorded, so an unasserted change stays inspectable.
    assert score["observed"]["uncovered_core"] == ["txn_a", "txn_b"]


# ── uncovered: entries the statement must NOT account for ──────────────────────────────


def test_expected_uncovered_scores_when_the_panel_lists_it():
    golden = {"rows": [], "coverage": {"expected_uncovered": [{"existing_id": "txn_split"}]}}
    score = score_coverage(golden, _response(uncovered=["txn_split"]))

    assert (score["dimension_correct"]["uncovered"], score["dimension_totals"]["uncovered"]) == (
        1,
        1,
    )
    assert score["mismatches"] == []


def test_expected_uncovered_fails_when_something_claimed_it():
    golden = {"rows": [], "coverage": {"expected_uncovered": [{"existing_id": "txn_split"}]}}
    score = score_coverage(golden, _response(uncovered=["txn_other"]))

    assert (score["dimension_correct"]["uncovered"], score["dimension_totals"]["uncovered"]) == (
        0,
        1,
    )
    assert score["mismatches"][0]["field"] == "uncovered"
    assert score["mismatches"][0]["existing_id"] == "txn_split"


def test_uncovered_defaults_to_either_bucket_but_an_explicit_bucket_is_enforced():
    loose = {"rows": [], "coverage": {"expected_uncovered": [{"existing_id": "txn_edge"}]}}
    assert (
        score_coverage(loose, _response(boundary=["txn_edge"]))["dimension_correct"]["uncovered"]
        == 1
    )

    pinned = {
        "rows": [],
        "coverage": {"expected_uncovered": [{"existing_id": "txn_edge", "bucket": "rows"}]},
    }
    score = score_coverage(pinned, _response(boundary=["txn_edge"]))
    assert score["dimension_correct"]["uncovered"] == 0
    assert "boundary" in score["mismatches"][0]["actual"]


def test_exhaustive_uncovered_fails_a_panel_entry_the_golden_never_named():
    golden = {
        "rows": [],
        "coverage": {
            "expected_uncovered": [{"existing_id": "txn_known"}],
            "exhaustive_uncovered": True,
        },
    }
    score = score_coverage(golden, _response(uncovered=["txn_known", "txn_surprise"]))

    assert (score["dimension_correct"]["uncovered"], score["dimension_totals"]["uncovered"]) == (
        1,
        2,
    )
    assert score["mismatches"][0]["field"] == "uncovered_unexpected"
    assert score["mismatches"][0]["existing_id"] == "txn_surprise"


def test_exhaustive_uncovered_is_silent_when_the_golden_is_not_exhaustive():
    golden = {"rows": [], "coverage": {"expected_uncovered": [{"existing_id": "txn_known"}]}}
    score = score_coverage(golden, _response(uncovered=["txn_known", "txn_surprise"]))

    assert score["dimension_totals"]["uncovered"] == 1
    assert score["mismatches"] == []


# ── covered: entries the statement DOES account for ────────────────────────────────────


def test_expected_covered_scores_when_the_entry_is_off_the_panel():
    golden = {"rows": [], "coverage": {"expected_covered": [{"existing_id": "txn_linked"}]}}
    score = score_coverage(golden, _response(uncovered=["txn_other"]))

    assert (score["dimension_correct"]["covered"], score["dimension_totals"]["covered"]) == (1, 1)


def test_expected_covered_fails_when_the_statement_stopped_accounting_for_it():
    golden = {"rows": [], "coverage": {"expected_covered": [{"existing_id": "txn_linked"}]}}
    score = score_coverage(golden, _response(uncovered=["txn_linked"]))

    assert score["dimension_correct"]["covered"] == 0
    assert score["mismatches"][0]["field"] == "covered"


def test_a_missing_ledger_check_fails_every_declared_assertion_instead_of_skipping_it():
    golden = {
        "rows": [],
        "coverage": {
            "expected_uncovered": [{"existing_id": "txn_a"}],
            "expected_covered": [{"existing_id": "txn_b"}],
        },
    }
    score = score_coverage(golden, _response(with_check=False))

    assert score["dimension_totals"] == {"uncovered": 1, "covered": 1}
    assert score["dimension_correct"] == {"uncovered": 0, "covered": 0}
    assert all("no ledger_check" in m["actual"] for m in score["mismatches"])


# ── false pairings — never averaged, always named ──────────────────────────────────────


def test_must_not_pair_catches_any_row_claiming_the_entry():
    golden = {
        "rows": [],
        "coverage": {"must_not_pair": [{"existing_id": "txn_split", "reason": "split settlement"}]},
    }
    score = score_coverage(golden, _response(rows=[_row(7, -60000, match="txn_split")]))

    assert score["dimension_totals"] == dict.fromkeys(COVERAGE_DIMENSIONS, 0)
    assert len(score["false_pairings"]) == 1
    violation = score["false_pairings"][0]
    assert (violation["row_index"], violation["declared"]) == (7, True)
    assert violation["why"] == "split settlement"


def test_must_not_pair_with_a_row_index_only_fires_on_that_row():
    golden = {"rows": [], "coverage": {"must_not_pair": [{"existing_id": "txn_x", "row_index": 3}]}}

    assert (
        score_coverage(golden, _response(rows=[_row(9, -10, match="txn_x")]))["false_pairings"]
        == []
    )
    assert (
        len(score_coverage(golden, _response(rows=[_row(3, -10, match="txn_x")]))["false_pairings"])
        == 1
    )


def test_the_incident_shape_is_caught_structurally_with_no_golden_at_all():
    """A money-IN top-up row offered as the same transaction as a money-OUT expense — the
    2026-07-28 live incident. No `coverage` block anywhere; the property carries it."""
    row = {
        "index": 18,
        "amount": 500000.0,
        "flags": [
            {
                "type": "near_duplicate",
                "existing_id": "txn_bar_tab",
                "existing": {"id": "txn_bar_tab", "kind": "expense", "amount": -500000.0},
            }
        ],
    }
    score = score_coverage({"rows": []}, _response(rows=[row]))

    assert len(score["false_pairings"]) == 1
    violation = score["false_pairings"][0]
    assert violation["declared"] is False
    assert violation["existing_id"] == "txn_bar_tab"
    assert "opposite directions" in violation["why"]


def test_a_same_direction_pairing_is_not_a_false_pairing():
    row = {
        "index": 2,
        "amount": -14.07,
        "flags": [
            {
                "type": "near_duplicate",
                "existing_id": "txn_settled",
                "existing": {"id": "txn_settled", "kind": "expense", "amount": -13.71},
            }
        ],
    }
    assert score_coverage({"rows": []}, _response(rows=[row]))["false_pairings"] == []


def test_a_transfer_counterpart_never_trips_the_structural_check():
    """`_existing_summary` reports a transfer's `from_amount` unsigned-by-construction, so its
    sign is not a direction relative to the statement's own account. Comparing against it would
    manufacture a false alarm on a legitimate link — the guard that keeps this check usable."""
    row = {
        "index": 4,
        "amount": -500000.0,
        "flags": [
            {
                "type": "transfer_pair",
                "existing_id": "txn_topup",
                "existing": {"id": "txn_topup", "kind": "transfer", "amount": 500000.0},
            }
        ],
    }
    assert score_coverage({"rows": []}, _response(rows=[row]))["false_pairings"] == []


def test_transfer_pair_formation_is_exempt_from_the_structural_check_but_not_from_a_golden():
    """Opposite legs are `transfer_pair`'s DEFINITION (`matching._classify`: "one row is a
    debit, the other a credit: the two legs of a single movement"), so the direction test would
    fire on every legitimate pair. Wrong transfer pairs are real — this eval found two on real
    data — but calling one takes domain knowledge, so `must_not_pair` owns them and the
    structural scan stays silent rather than asserting a reason that is not true."""
    row = {
        "index": 5,
        "amount": 98000.0,
        "flags": [
            {
                "type": "transfer_pair",
                "existing_id": "txn_unrelated_spend",
                "existing": {"id": "txn_unrelated_spend", "kind": "expense", "amount": -99000.0},
            }
        ],
    }
    assert score_coverage({"rows": []}, _response(rows=[row]))["false_pairings"] == []

    declared = {"rows": [], "coverage": {"must_not_pair": [{"existing_id": "txn_unrelated_spend"}]}}
    assert len(score_coverage(declared, _response(rows=[row]))["false_pairings"]) == 1


def test_a_declared_violation_is_not_double_counted_by_the_structural_scan():
    golden = {"rows": [], "coverage": {"must_not_pair": [{"existing_id": "txn_bar_tab"}]}}
    row = {
        "index": 18,
        "amount": 500000.0,
        "flags": [
            {
                "type": "near_duplicate",
                "existing_id": "txn_bar_tab",
                "existing": {"id": "txn_bar_tab", "kind": "expense", "amount": -500000.0},
            }
        ],
    }
    score = score_coverage(golden, _response(rows=[row]))

    assert len(score["false_pairings"]) == 1
    assert score["false_pairings"][0]["declared"] is True


# ── the pairing scan reads the response contract, not one matcher ──────────────────────


def test_pairings_reads_exact_duplicates_flags_and_the_retired_twin_key():
    response = {
        "rows": [
            {"index": 0, "amount": -5.0, "duplicate": True, "duplicate_of": "txn_dup"},
            _row(1, -6.0, match="txn_flag"),
        ],
        "ledger_check": {
            "status": "ok",
            "rows": [{"id": "txn_twin", "statement_twin": {"row_index": 5}}],
            "boundary": [],
        },
    }
    found = {(p["existing_id"], p["row_index"], p["source"]) for p in pairings(response)}

    assert ("txn_dup", 0, "duplicate_of") in found
    assert ("txn_flag", 1, "near_duplicate") in found
    # The key RFC 2026-07-29 §D3 deleted. It is read precisely because it should stay deleted:
    # the second matcher had already been rejected once and grew back.
    assert ("txn_twin", 5, "statement_twin (RETIRED KEY)") in found
