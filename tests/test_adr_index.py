"""`docs/adr/README.md` indexes every ADR — the ratchet, not the prose.

The index had drifted ~170 entries behind by 2026-08-27, back to July, because nothing ever
checked it. Agents and people both reach for that table to find a prior decision, and a
decision missing from it is functionally a decision nobody made.

This guards COVERAGE only. The Title column is hand-written prose carrying real substance
(several of the rows are a paragraph long), so it is never regenerated — `--write` seeds a
missing row from the ADR's own H1 and leaves enriching it to a human.
"""

from __future__ import annotations

from backend.scripts.adr_index import adr_files, indexed_slugs, missing


def test_every_adr_has_a_row_in_the_index():
    gaps = sorted(p.name for p in missing())
    assert not gaps, (
        f"{len(gaps)} ADR(s) are not in docs/adr/README.md: {gaps[:10]}"
        f"{' …' if len(gaps) > 10 else ''}. Run: python -m backend.scripts.adr_index --write"
    )


def test_the_index_names_no_adr_that_does_not_exist():
    """The other direction — a row pointing at a deleted or renamed file is a dead link."""
    real = {p.stem for p in adr_files()}
    dangling = sorted(indexed_slugs() - real)
    assert not dangling, f"docs/adr/README.md links ADRs that do not exist: {dangling}"


def test_there_are_adrs_to_check():
    """Stops the two guards above from passing vacuously if the glob ever breaks."""
    assert len(adr_files()) > 100
