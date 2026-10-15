"""End-to-end: the import PREVIEW surfaces advisory anomaly flags, but the COMMIT still
imports EVERYTHING (the flags are advisory only — never auto-drop a flagged row).

Drives the real import_csv preview/commit path (which funnels through import_common's
build_preview / build_commit), so it pins the wiring the UI + the import_transactions
tool consume."""

from __future__ import annotations

import pytest

from backend.services import import_csv as svc


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, fx_rates):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as ledger_service

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


@pytest.fixture
def checking(isolate_stores):
    accounts, _ = isolate_stores
    return accounts.create({"name": "Checking", "kind": "asset", "type": "cash", "currency": "USD"})


_MAPPING = {"date": "Date", "description": "Memo", "amount": "Amount"}


def test_preview_flags_near_duplicate_but_commit_imports_all(checking):
    from backend.services import ledger as ledger_service

    # An existing hand-entered txn (different external_source namespace than csv).
    ledger_service.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "description": "Acme Store",
            "from_account_id": checking["id"],
            "from_amount": 50.0,
            "from_currency": "USD",
            "external_refs": [{"source": "manual", "ref": "1"}],
        }
    )

    # A CSV with two new rows: one is a near-dup of the existing txn, one is fresh.
    csv = b"Date,Memo,Amount\n2026-06-02,Acme Store,-50.00\n2026-06-03,Totally New Cafe,-9.00\n"

    preview = svc.preview_import(csv, _MAPPING, account_id=checking["id"])
    # Both rows are importable (the hard dedup does NOT catch the near-dup — different id).
    assert preview.importable == 2
    assert preview.duplicates == 0
    # ...but the advisory layer flags the near-dup.
    assert preview.anomaly_summary["near_duplicate"] == 1
    assert preview.anomaly_summary["flagged_rows"] == 1
    flagged = [d for d in preview.sample if d["flags"]]
    assert len(flagged) == 1
    assert flagged[0]["flags"][0]["type"] == "near_duplicate"

    # COMMIT: the FRESH row books; the near-duplicate is REFUSED at the write choke
    # (ADR 2026-08-01). The flag was advisory at PREVIEW time — the user saw it — but a blind
    # commit-everything path is exactly where "created-then-flagged" silently double-books,
    # so the guard stops it rather than trusting a queue nobody reads.
    result = svc.commit_import(csv, _MAPPING, account_id=checking["id"])
    assert result["created"] == 1
    assert result["duplicates"] == 0  # not an L0 exact-ref hit; a SEMANTIC refusal
    # Ledger holds the original + the one genuinely new row.
    assert len(ledger_service.transactions.all()) == 2


def test_preview_clean_book_zero_flags(checking):
    csv = b"Date,Memo,Amount\n2026-07-01,Fresh Vendor,-15.00\n"
    preview = svc.preview_import(csv, _MAPPING, account_id=checking["id"])
    assert preview.anomaly_summary == {
        "near_duplicate": 0,
        "intra_batch_duplicate": 0,
        "transfer_pair": 0,
        "transfer_leg_echo": 0,
        "amount_anomaly": 0,
        "flagged_rows": 0,
    }
    assert all(d["flags"] == [] for d in preview.sample)
