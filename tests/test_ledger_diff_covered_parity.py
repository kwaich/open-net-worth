"""One covered set, both sides of the commit (RFC 2026-07-29, Phase 4 / divergence #5).

"What does this statement account for?" is asked twice: by ``preview_ledger_check`` before the
commit (from the flags ``decision_preview`` just built with ``_apply_batch_match``) and by
``uncovered_ledger_rows`` after it. The post-commit half used to answer with its OWN per-row
``import_anomalies.detect_anomalies`` scan — no weak settled-drift band, no owner match policy,
no batch-wide 1:1 assignment — so the owner saw one "In your ledger, not on this statement" list
in the preview and a DIFFERENT one when they re-opened the same statement.

The fixture is the ride-hailing settlement-drift shape the weak band exists for: an agent
booked the estimate (−S$15.00), the statement settles it at −S$15.39 (2.6% apart, past the
1% near-dup tolerance).
The preview pairs them in the weak band and drops the entry from the panel; before this change
the post-commit panel put it straight back.

Both panels are compared on an UNCHANGED ledger (every row skipped at commit), because that is
the only way the question is literally the same on both sides — a commit that books rows changes
the answer legitimately.
"""

from __future__ import annotations

import json

import pytest

# Collection-time on purpose: backend.ai.memory builds asyncio.Lock() at import (py3.9).
from backend.core import llm_seam
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements.ledger_diff import uncovered_ledger_rows


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    ledger_service.accounts.insert(
        {"id": "sgd", "name": "Meridian Bank Card (SGD)", "kind": "asset", "currency": "SGD"}
    )


def _stub_text_tier(monkeypatch, rows: list[dict], meta: dict) -> None:
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta)
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_META = {
    "currency": "SGD",
    "issuer": "Meridian Bank",
    "period_start": "2026-06-01",
    "period_end": "2026-06-30",
}

_ROWS = [
    {
        "date": "2026-06-15",
        "amount": 15.39,
        "direction": "OUT",
        "currency": "SGD",
        "description": "Grab* A-1QZ7K42",
        "confidence": 0.97,
    },
    {
        "date": "2026-06-20",
        "amount": 8.00,
        "direction": "OUT",
        "currency": "SGD",
        "description": "KOPITIAM 8.00",
        "confidence": 0.97,
    },
]


def _book(date: str, description: str, amount: float, source: str = "agent") -> str:
    return ledger_service.create_transaction(
        {
            "date": date,
            "kind": "expense",
            "description": description,
            "from_account_id": "sgd",
            "from_amount": amount,
            "from_currency": "SGD",
            "source": source,
        }
    )["id"]


def _preview(monkeypatch, filename: str = "meridian-june.pdf") -> dict:
    _stub_text_tier(monkeypatch, _ROWS, _META)
    return svc.decision_preview(
        _make_pdf("Meridian Bank June\nGrab 15.39\nKOPITIAM 8.00"), "pdf", "sgd", filename=filename
    )


def _ids(panel: dict) -> set:
    return {r["id"] for r in (panel.get("rows") or []) + (panel.get("boundary") or [])}


def test_post_commit_panel_agrees_with_the_preview_on_a_weak_band_entry(monkeypatch):
    """THE Phase 4 regression. A settled-drift pair the preview covers in the weak band must
    still be covered when the same statement is re-opened after commit — and the entry that is
    genuinely absent from the statement must still be listed on both sides."""
    drift_id = _book("2026-06-15", "Grab - Ride estimate", 15.00)
    missing_id = _book("2026-06-10", "Cash lunch (never on a statement)", 6.50, source="manual")

    res = _preview(monkeypatch)
    before = res["ledger_check"]
    assert before["status"] == "ok"
    # The preview's own claim: the drift entry is accounted for (weak band), the cash lunch isn't.
    assert _ids(before) == {missing_id}
    weak = [f for f in res["rows"][0]["flags"] if f.get("existing_id")]
    assert [(f["band"], f["existing_id"]) for f in weak] == [("weak", drift_id)]

    # Commit changes NOTHING in the ledger (every row skipped), so the question the post-commit
    # panel answers is literally the one the preview just answered.
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "sgd", default_action="skip")
    assert (out["created"], out["merged"]) == (0, 0)

    after = uncovered_ledger_rows(res["statement_id"])
    assert after["status"] == "ok"
    assert _ids(after) == _ids(before)
    assert drift_id not in _ids(after), (
        "the settled-drift entry the preview paired in the weak band came back as uncovered — "
        "the post-commit panel is deriving its covered set with a second engine"
    )
    # The money tie-out is derived from the same offers, so it agrees too: a weak-matched row
    # counts as booked on both sides, never as unbooked here and booked there.
    assert after["totals"] == before["totals"]


def test_uncommitted_boundary_and_covered_split_survives_the_shared_derivation(monkeypatch):
    """The panel still splits core vs boundary and still hides what the import itself booked —
    the parity above must not have been bought by covering (or dropping) everything."""
    edge_id = _book("2026-06-01", "Boundary coffee", 4.00, source="ai")
    mid_id = _book("2026-06-12", "Mid-period taxi", 21.40, source="ai")

    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "sgd", default_action="create")

    after = uncovered_ledger_rows(res["statement_id"])
    assert [r["id"] for r in after["rows"]] == [mid_id]
    assert [r["id"] for r in after["boundary"]] == [edge_id]
    # Everything this commit created is covered by its batch link — never listed against itself.
    created = {t["id"] for t in ledger_service.transactions.all() if t.get("batch_id")}
    assert created and not (created & _ids(after))


def test_a_stale_match_policy_withholds_the_panel_instead_of_widening_it(monkeypatch):
    """``decision_commit`` refuses a stale ``match_policy_hash`` because falling back to
    ``MatchPolicy()`` is the LOOSER direction. The panel reads the same stamp, so it must refuse
    too: a looser policy covers MORE entries, and this panel under-reporting "not on this
    statement" is exactly the reconciliation lie the RFC exists to kill."""
    from backend.db.statement_store import StatementStore

    _book("2026-06-12", "Mid-period taxi", 21.40, source="ai")
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "sgd", default_action="skip")
    assert uncovered_ledger_rows(res["statement_id"])["status"] == "ok"

    StatementStore().update_by_id(res["statement_id"], {"match_policy_hash": "not-the-hash"})
    withheld = uncovered_ledger_rows(res["statement_id"])
    assert withheld == {"status": "stale_policy", "rows": []}
