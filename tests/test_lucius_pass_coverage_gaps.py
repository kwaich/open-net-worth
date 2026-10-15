"""A note applies to EVERY row matching it — not one exemplar per pattern.

Regression case: three identical ``Advice FAST Collection`` rows (0, 17, 18) in a sample
statement, each carrying the same model-extracted payee ``Flux`` and the same note, against a
standing instruction to book them as internal transfers. One model routed all three; another
read the same prompt and reported *"only the FAST collection identified as a Flux transfer"* —
singular — emitting ONE decision. The statement then imported two 340.00/120.00 rows as
ordinary expenses.

Nothing was dropped by validation (`3 proposed, 3 survived`), so no existing guard could see
it. `_coverage_gaps` catches it deterministically, off the model's own self-contradiction.
"""

from __future__ import annotations

import pytest

from backend.services.import_common import RowResult
from backend.services.statements.canonical import CanonicalRow
from backend.services.statements.lucius_pass import _coverage_gaps, _row_identity


def _fast(amount: float) -> CanonicalRow:
    return CanonicalRow(
        date="2026-07-15",
        direction="OUT",
        amount=amount,
        currency="SGD",
        description="Advice FAST Collection",
        payee="Flux",
    )


def _results(rows: list[CanonicalRow], duplicates: set[int] = frozenset()) -> list[RowResult]:
    return [
        RowResult(line=i + 1, draft={"date": r.date}, duplicate=(i in duplicates))
        for i, r in enumerate(rows)
    ]


def test_identity_ignores_amount_and_reference() -> None:
    """The three real rows differed in amount and in their printed REVDDT/IIRGPC refs —
    identity must key on the pattern, or every row is its own group and nothing ever fires."""
    a = _fast(120.00)
    b = _fast(340.00)
    b.reference = "REVDDT00000000000000000"
    assert _row_identity(a) == _row_identity(b)


def test_the_codex_repro_is_flagged() -> None:
    rows = [_fast(120.00), _fast(340.00), _fast(120.00)]
    ops = [{"row": 0, "action": "route_transfer", "account_id": "acc_flux"}]

    gaps = _coverage_gaps(rows, _results(rows), [0, 1, 2], ops)

    assert len(gaps) == 1
    assert "1, 2" in gaps[0]
    assert "row 0" in gaps[0]


def test_uniform_batches_cost_nothing() -> None:
    """The common path: every twin decided, or none of them. Must not spend an extra round."""
    rows = [_fast(120.00), _fast(340.00)]
    all_decided = [{"row": 0, "action": "skip"}, {"row": 1, "action": "skip"}]
    assert _coverage_gaps(rows, _results(rows), [0, 1], all_decided) == []
    assert _coverage_gaps(rows, _results(rows), [0, 1], []) == []


def test_unrelated_rows_are_never_grouped() -> None:
    """A decided row must not drag every OTHER row in the batch into a gap — only its twins."""
    rows = [
        _fast(120.00),
        CanonicalRow(
            date="2026-07-07",
            direction="IN",
            amount=8.5,
            description="Interest Earned",
            payee="Meridian Bank",
        ),
    ]
    gaps = _coverage_gaps(rows, _results(rows), [0, 1], [{"row": 0, "action": "route_transfer"}])
    assert gaps == []


def test_already_imported_and_skipped_rows_are_exempt() -> None:
    """Duplicates are booked ("leave them alone") and skip rows never reach the model — neither
    is an omission, so neither may trigger a re-ask."""
    rows = [_fast(120.00), _fast(340.00), _fast(55.00)]
    rows[2].skip = True
    ops = [{"row": 0, "action": "route_transfer"}]

    assert _coverage_gaps(rows, _results(rows, duplicates={1}), [0, 1, 2], ops) == []


def test_malformed_row_indexes_do_not_crash() -> None:
    """`raw_ops` is unvalidated model output at this point — a junk `row` must not take the
    pass down, it just means that decision covers nothing."""
    rows = [_fast(120.00), _fast(340.00)]
    ops = [{"row": None}, {"row": "abc"}, {"row": 0, "action": "route_transfer"}]

    gaps = _coverage_gaps(rows, _results(rows), [0, 1], ops)

    assert len(gaps) == 1 and "1" in gaps[0]


def test_a_failed_re_ask_keeps_the_round_that_worked(monkeypatch) -> None:
    """The re-ask is an enhancement. When it fails, the decisions already parsed must stand.

    Regression case: both batches parsed cleanly, the coverage re-ask hit
    `error_max_structured_output_retries`, and — because the pass now fails loud — the whole
    import died with nothing imported.
    """
    from backend.core import llm_seam
    from backend.services.statements import lucius_pass as lp

    calls = {"n": 0}

    async def _first_ok_then_boom(prompt, **kwargs):
        if kwargs.get("source") != lp.PASS_SOURCE:
            return "[]"
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                '{"analysis": "row 0 only", "summary": "",'
                ' "decisions": [{"row": 0, "action": "skip", "account_id": null,'
                ' "category_label": null, "existing_id": null, "reason": "note says so"}]}'
            )
        raise RuntimeError("Claude Code CLI reported an error: error_max_structured_output_retries")

    monkeypatch.setattr(llm_seam, "_complete", _first_ok_then_boom)

    rows = [_fast(120.00), _fast(340.00)]
    results = _results(rows)
    raw_ops, _summary, _verified, _calls, _cost = lp._run_batch(
        "notes", rows, results, {"a": {"id": "a", "name": "A", "currency": "SGD"}}, [], "a", [0, 1]
    )

    assert calls["n"] == 2, "the re-ask must have been attempted"
    assert [op.get("row") for op in raw_ops] == [0], "round 0's decision survives the failure"


def test_a_FIRST_round_failure_still_raises(monkeypatch) -> None:
    """Failing shut is unchanged when nothing was parsed — a preview that ignores the owner's
    instructions must not be offered."""
    from backend.core import llm_seam
    from backend.services.statements import lucius_pass as lp

    async def _boom(prompt, **kwargs):
        if kwargs.get("source") == lp.PASS_SOURCE:
            raise RuntimeError("provider down")
        return "[]"

    monkeypatch.setattr(llm_seam, "_complete", _boom)

    rows = [_fast(120.00)]
    with pytest.raises(RuntimeError):
        lp._run_batch(
            "notes",
            rows,
            _results(rows),
            {"a": {"id": "a", "name": "A", "currency": "SGD"}},
            [],
            "a",
            [0],
        )
