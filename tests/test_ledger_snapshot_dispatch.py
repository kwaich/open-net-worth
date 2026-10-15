"""services.ledger's snapshot choke dispatch (RFC connectors-as-data-sources §C, last
paragraph): ``known_external_refs``, and ``_promote_source_snapshots``/``_stamp_ref_emitters``
routing a non-email pair to ``connector_snapshots`` instead of ``email_snapshots``.
"""

from __future__ import annotations

import pytest

from backend.services import connector_snapshots, email_snapshots, ledger


@pytest.fixture(autouse=True)
def _usd_at_par_with_sgd(fx_rates):
    """Rows are USD against the SGD stub account; these tests exercise snapshot dispatch, not
    conversion, so the pair is pinned at par rather than left without a rate."""
    fx_rates(extra={"USD": 1.0})


def _seed_accounts():
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "acc_1"}])


def _expense(refs: list[dict], **over) -> dict:
    row = {
        "date": "2026-09-01",
        "kind": "expense",
        "description": "Coffee",
        "from_account_id": "acc_1",
        "from_amount": 4.5,
        "from_currency": "USD",
        "external_refs": refs,
    }
    row.update(over)
    return row


# ── known_external_refs ───────────────────────────────────────────────────────


def test_known_external_refs_maps_pair_to_live_txn_id(db_isolated):
    _seed_accounts()
    stored = ledger.create_transaction(_expense([{"source": "plaid", "ref": "t1"}]))
    assert ledger.known_external_refs("plaid") == {("plaid", "t1"): stored["id"]}


def test_known_external_refs_is_scoped_to_the_given_source(db_isolated):
    _seed_accounts()
    ledger.create_transaction(_expense([{"source": "plaid", "ref": "t1"}]))
    ledger.create_transaction(
        _expense([{"source": "simplefin", "ref": "t2"}], from_amount=99.0, description="Rent")
    )
    assert list(ledger.known_external_refs("plaid").keys()) == [("plaid", "t1")]


def test_known_external_refs_excludes_a_user_deleted_row(db_isolated):
    _seed_accounts()
    stored = ledger.create_transaction(_expense([{"source": "plaid", "ref": "t1"}]))
    ledger.transactions.update_by_id(stored["id"], {"deleted": True})
    assert ledger.known_external_refs("plaid") == {}


def test_known_external_refs_includes_every_pair_a_row_carries(db_isolated):
    """A row with several pairs for the same source (e.g. refs absorbed by a merge) maps
    EACH pair to the one canonical row id — ext_ref_pairs is the shared definition."""
    _seed_accounts()
    stored = ledger.create_transaction(
        _expense([{"source": "plaid", "ref": "a"}, {"source": "plaid", "ref": "b"}])
    )
    assert ledger.known_external_refs("plaid") == {
        ("plaid", "a"): stored["id"],
        ("plaid", "b"): stored["id"],
    }


# ── promote dispatch ──────────────────────────────────────────────────────────


def test_create_promotes_a_connector_pair_via_connector_snapshots(db_isolated, monkeypatch):
    _seed_accounts()
    calls = []
    monkeypatch.setattr(connector_snapshots, "promote", lambda s, r: calls.append((s, r)))
    ledger.create_transaction(_expense([{"source": "plaid", "ref": "t1"}]))
    assert calls == [("plaid", "t1")]


def test_create_promotes_an_email_pair_via_email_snapshots_unchanged(db_isolated, monkeypatch):
    _seed_accounts()
    calls = []
    monkeypatch.setattr(email_snapshots, "promote_for_email", lambda s, r: calls.append((s, r)))
    ledger.create_transaction(_expense([{"source": "email", "ref": "m1"}]))
    assert calls == [("email", "m1")]


def test_update_promotes_only_the_newly_appended_connector_pair(db_isolated, monkeypatch):
    _seed_accounts()
    stored = ledger.create_transaction(_expense([{"source": "plaid", "ref": "a"}]))
    calls = []
    monkeypatch.setattr(connector_snapshots, "promote", lambda s, r: calls.append((s, r)))

    ledger.update_transaction(
        stored["id"],
        {"external_refs": [{"source": "plaid", "ref": "a"}, {"source": "plaid", "ref": "b"}]},
    )

    assert calls == [("plaid", "b")]


# ── emitter dispatch ──────────────────────────────────────────────────────────


def test_emitter_stamped_from_a_connector_snapshot_on_create(db_isolated):
    _seed_accounts()
    connector_snapshots.stash_rows(
        "plaid",
        [
            {
                "ref": "t1",
                "provider_account_id": "acc_abc",
                "raw": {},
                "normalized": {
                    "date": "2026-09-01",
                    "amount": -4.5,
                    "currency": "USD",
                    "description": "Coffee",
                    "account": "acc_abc",
                },
            }
        ],
    )

    stored = ledger.create_transaction(_expense([{"source": "plaid", "ref": "t1"}]))

    assert stored["external_refs"][0]["emitter"] == "acc_abc"


def test_emitter_unresolved_for_a_never_stashed_connector_ref(db_isolated):
    _seed_accounts()
    stored = ledger.create_transaction(_expense([{"source": "plaid", "ref": "never-stashed"}]))
    assert "emitter" not in stored["external_refs"][0]
