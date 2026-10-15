"""backend/scripts/report_orphaned_statements.py — read-only diagnostic report classifying
``statements_meta`` rows by how likely they are to be spurious rows from the
now-fixed unconditional-StatementStore.create bug (commit 27fc9d65). No --execute exists
anywhere in this module; these tests are the part that most needs to be right, since a
human acts on this report's output.
"""

from __future__ import annotations

import json

import pytest

from backend.scripts import report_orphaned_statements as script

BEFORE_CUTOFF = "2026-08-16T10:00:00Z"  # strictly before 2026-08-16T11:11:05Z
AFTER_CUTOFF = "2026-08-16T12:00:00Z"  # strictly after


def _create_statement(filename: str, mime: str = "application/pdf", **create_kwargs) -> dict:
    from backend.db.statement_store import StatementStore

    return StatementStore().create(
        filename, mime, f"content-for-{filename}".encode(), {"kind": "text"}, **create_kwargs
    )


def _stamp_created_at(statement_id: str, created_at: str) -> None:
    from backend.db.statement_store import StatementStore

    StatementStore().update_by_id(statement_id, {"created_at": created_at})


class TestCutoff:
    def test_cutoff_constant_matches_commit_27fc9d65_in_utc(self):
        # 2026-08-16 18:11:05 +07:00 == 2026-08-16 11:11:05 UTC
        assert script.CUTOFF_UTC == "2026-08-16T11:11:05Z"


class TestConfirmedReal:
    def test_row_with_batch_ids_is_confirmed_real_even_if_pre_cutoff(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("real.pdf")
        _stamp_created_at(row["id"], BEFORE_CUTOFF)
        StatementStore().add_batch_id(row["id"], "batch_1")

        hit = next(r for r in script.scan() if r.statement_id == row["id"])
        assert hit.category == "confirmed_real"
        assert hit.batch_ids_count == 1

    def test_row_with_batch_ids_is_confirmed_real_post_cutoff_too(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("real2.pdf")
        _stamp_created_at(row["id"], AFTER_CUTOFF)
        StatementStore().add_batch_id(row["id"], "batch_2")

        hit = next(r for r in script.scan() if r.statement_id == row["id"])
        assert hit.category == "confirmed_real"


class TestHighConfidenceSpurious:
    def test_pre_cutoff_empty_batch_ids_no_source_attachment_is_high_confidence_spurious(
        self, db_isolated
    ):
        row = _create_statement("screenshot.png", mime="image/png")
        _stamp_created_at(row["id"], BEFORE_CUTOFF)

        hit = next(r for r in script.scan() if r.statement_id == row["id"])
        assert hit.category == "high_confidence_spurious"
        assert hit.before_cutoff is True
        assert hit.batch_ids_count == 0
        assert hit.has_source_attachment_id is False


class TestAmbiguous:
    def test_post_cutoff_empty_batch_ids_is_ambiguous_not_spurious(self, db_isolated):
        """A statement previewed but never committed after the fix — a normal, harmless
        in-flight state, NOT evidence of the pre-cutoff bug."""
        row = _create_statement("in_progress.pdf")
        _stamp_created_at(row["id"], AFTER_CUTOFF)

        hit = next(r for r in script.scan() if r.statement_id == row["id"])
        assert hit.category == "ambiguous_needs_review"

    def test_source_attachment_id_without_batch_ids_is_ambiguous(self, db_isolated):
        """Explicitly promoted (has source_attachment_id) but the import was never
        finished (no batch_ids) — some evidence of legitimacy, not enough for confirmed."""
        row = _create_statement("promoted.pdf", source_attachment_id="att_abc123")
        _stamp_created_at(row["id"], AFTER_CUTOFF)

        hit = next(r for r in script.scan() if r.statement_id == row["id"])
        assert hit.category == "ambiguous_needs_review"
        assert hit.has_source_attachment_id is True


class TestDedupOnlyImportStillGetsBatchIds:
    """The precise correctness case named in the brief: a statement whose commit produced
    ZERO new transactions (every row was an exact dedup hit) must still be treated as
    confirmed_real, because add_batch_id() runs unconditionally at the end of
    services/statements/decisions.py's _apply(), regardless of whether anything new was
    actually created or merged. Simulated directly here (calling the real decision_commit
    pipeline requires a full ledger fixture) by exercising add_batch_id the same way _apply
    does — the store-level contract this report's whole classification rests on."""

    def test_add_batch_id_called_with_no_new_rows_still_marks_confirmed(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("all_dedup.pdf")
        _stamp_created_at(row["id"], BEFORE_CUTOFF)
        # _apply() always creates a batch first and always calls add_batch_id at its tail,
        # with no "if created_rows or merged" guard — see decisions.py.
        StatementStore().add_batch_id(row["id"], "batch_alldedup")

        hit = next(r for r in script.scan() if r.statement_id == row["id"])
        assert hit.category == "confirmed_real"


class TestFileExtIsInformationalOnly:
    def test_pdf_and_png_both_still_classified_purely_on_evidence(self, db_isolated):
        pdf_row = _create_statement("statement.pdf", mime="application/pdf")
        png_row = _create_statement("screenshot.png", mime="image/png")
        for row in (pdf_row, png_row):
            _stamp_created_at(row["id"], BEFORE_CUTOFF)

        classified = {r.statement_id: r for r in script.scan()}
        assert classified[pdf_row["id"]].file_ext == ".pdf"
        assert classified[png_row["id"]].file_ext == ".png"
        # Both are high_confidence_spurious purely on timing/batch_ids/source_attachment_id
        # evidence — file type never overrides or filters the classification.
        assert classified[pdf_row["id"]].category == "high_confidence_spurious"
        assert classified[png_row["id"]].category == "high_confidence_spurious"


class TestNeverMutates:
    def test_scan_never_touches_any_row(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("untouched.pdf")
        before = StatementStore().find_by_id(row["id"])
        script.scan()
        after = StatementStore().find_by_id(row["id"])
        assert before == after

    def test_no_execute_or_apply_flag_exists_at_all(self):
        with pytest.raises(SystemExit):
            script.main(["--db-url", "sqlite:///:memory:", "--execute"])
        with pytest.raises(SystemExit):
            script.main(["--db-url", "sqlite:///:memory:", "--apply"])


class TestEndToEndViaMain:
    def test_report_runs(self, db_isolated):
        _create_statement("a.pdf")
        rc = script.main(["--db-url", db_isolated])
        assert rc == 0

    def test_json_output_counts(self, db_isolated, capsys):
        row = _create_statement("spurious.png", mime="image/png")
        _stamp_created_at(row["id"], BEFORE_CUTOFF)
        rc = script.main(["--db-url", db_isolated, "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total"] == 1
        assert payload["high_confidence_spurious_count"] == 1
        assert payload["confirmed_real_count"] == 0
        assert payload["ambiguous_needs_review_count"] == 0


class TestLiveDataGuard:
    def test_no_flags_refuses(self):
        with pytest.raises(SystemExit):
            script.main([])
