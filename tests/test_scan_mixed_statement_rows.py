"""backend/scripts/scan_mixed_statement_rows.py — RFC cross-channel-attachments item
31.4. Diagnostic-only: no --apply exists at all — proves correct evidence-strength
classification and that nothing is ever reclassified or deleted.
"""

from __future__ import annotations

import pytest

from backend.scripts import scan_mixed_statement_rows as script


def _create_statement(filename: str, **create_kwargs) -> dict:
    from backend.db.statement_store import StatementStore

    return StatementStore().create(
        filename,
        "application/pdf",
        f"content-for-{filename}".encode(),
        {"kind": "text"},
        **create_kwargs,
    )


class TestScan:
    def test_a_row_with_no_evidence_is_ambiguous(self, db_isolated):
        row = _create_statement("mystery.pdf")
        classified = script.scan()
        hit = next(c for c in classified if c.statement_id == row["id"])
        assert hit.classification == "ambiguous"
        assert hit.evidence == []

    def test_parsed_rows_is_positive_evidence(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("statement.pdf")
        StatementStore().set_parsed_rows(row["id"], [{"date": "2026-01-01", "amount": -10}])
        hit = next(c for c in script.scan() if c.statement_id == row["id"])
        assert hit.classification == "likely_statement"
        assert any("parsed_row_count" in e for e in hit.evidence)

    def test_account_id_is_positive_evidence(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("statement.pdf")
        StatementStore().set_parsed_rows(row["id"], [], account_id="acc_dbs")
        hit = next(c for c in script.scan() if c.statement_id == row["id"])
        assert hit.classification == "likely_statement"
        assert any("account_id" in e for e in hit.evidence)

    def test_batch_ids_is_positive_evidence(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("statement.pdf")
        StatementStore().add_batch_id(row["id"], "batch_1")
        hit = next(c for c in script.scan() if c.statement_id == row["id"])
        assert hit.classification == "likely_statement"
        assert any("batch_ids" in e for e in hit.evidence)

    def test_explicit_promotion_is_positive_evidence(self, db_isolated):
        row = _create_statement("promoted.pdf", source_attachment_id="att_abc123")
        hit = next(c for c in script.scan() if c.statement_id == row["id"])
        assert hit.classification == "likely_statement"
        assert "explicitly_promoted_from_attachment" in hit.evidence

    def test_zero_length_parsed_rows_is_NOT_evidence(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("empty_parse.pdf")
        StatementStore().set_parsed_rows(row["id"], [])
        hit = next(c for c in script.scan() if c.statement_id == row["id"])
        assert hit.classification == "ambiguous"

    def test_mixed_set_reports_correct_counts(self, db_isolated):
        from backend.db.statement_store import StatementStore

        real = _create_statement("real.pdf")
        StatementStore().set_parsed_rows(real["id"], [{"date": "2026-01-01", "amount": -1}])
        _create_statement("ambiguous1.pdf")
        _create_statement("ambiguous2.pdf")

        classified = script.scan()
        likely = [c for c in classified if c.classification == "likely_statement"]
        ambiguous = [c for c in classified if c.classification == "ambiguous"]
        assert len(likely) == 1
        assert len(ambiguous) == 2


class TestNeverMutates:
    def test_scan_never_touches_any_row(self, db_isolated):
        from backend.db.statement_store import StatementStore

        row = _create_statement("untouched.pdf")
        before = StatementStore().find_by_id(row["id"])
        script.scan()
        after = StatementStore().find_by_id(row["id"])
        assert before == after

    def test_no_apply_flag_exists_at_all(self):
        with pytest.raises(SystemExit):
            script.main(["--db-url", "sqlite:///:memory:", "--apply"])


class TestEndToEndViaMain:
    def test_dry_run_report(self, db_isolated):
        _create_statement("a.pdf")
        rc = script.main(["--db-url", db_isolated])
        assert rc == 0

    def test_json_output(self, db_isolated, capsys):
        import json

        _create_statement("ambiguous.pdf")
        rc = script.main(["--db-url", db_isolated, "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ambiguous_count"] == 1
        assert payload["likely_statement_count"] == 0


class TestLiveDataGuard:
    def test_no_flags_refuses(self):
        with pytest.raises(SystemExit):
            script.main([])
