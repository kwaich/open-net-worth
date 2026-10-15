"""``is_before_tracking_start`` is a PURE date comparison.

It takes the tracking start as a DATE, not an account dict — every fold site resolves the
cutoff itself (``ledger.opening_date`` / ``cutoffs_by_account``), so a site that forgets is a
TypeError the type checker catches rather than a silent ``None`` that counts pre-start legs.
Table-driven over both ``None`` cases and the strict-``<`` boundary."""

import pytest

from backend.services import ledger

CUTOFF = "2026-05-31"


@pytest.mark.parametrize(
    "leg_date,opening_date,expected",
    [
        # strictly before the start → summarized into the opening, not counted
        ("2026-04-05", CUTOFF, True),
        ("2026-05-30", CUTOFF, True),
        # the boundary is STRICT: a leg dated exactly at the start always counts. That is what
        # lets the opening row — dated exactly at the cutoff — land in its own fold.
        (CUTOFF, CUTOFF, False),
        ("2026-06-01", CUTOFF, False),
        # no tracking start → everything counts
        ("2026-01-01", None, False),
        # no leg date → counts (a dateless row is never curtained by a cutoff)
        (None, CUTOFF, False),
        ("", CUTOFF, False),
        (None, None, False),
    ],
)
def test_predicate(leg_date, opening_date, expected):
    assert ledger.is_before_tracking_start(leg_date, opening_date) is expected
