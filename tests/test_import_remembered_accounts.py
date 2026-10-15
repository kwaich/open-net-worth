"""Remembered account chips (Choose step prefill).

A statement layout already remembers HOW to read it (the format profile) and the owner's
upload-time instructions. It now also remembers WHICH ACCOUNTS the statement routes into —
the full chip set, anchor first, not just the single anchor — so the next import of the same
layout pre-selects the same wallets instead of making the owner re-pick them every month.

Written on PREVIEW (the selection is the intent, and the case that most needs the memory is a
monthly statement whose rows all de-duplicate and never commit), read back by the choose-step
``source-peek`` probe.
"""

from __future__ import annotations

import json

import pytest

from backend.db.statement_format_store import StatementFormatStore
from backend.services.statements import profiles


@pytest.fixture()
def store(db_isolated) -> StatementFormatStore:
    """StatementFormatStore is DB-backed (PayloadTableStore, plan 4d task 2) — db_isolated
    redirects the process-wide engine singleton to a fresh, empty database for the test."""
    return StatementFormatStore()


def _new(store: StatementFormatStore, **kw) -> dict:
    return store.upsert("fp1", kind="tabular", schema={"header_row": 0}, **kw)


class TestStore:
    def test_upsert_stores_the_whole_chip_set_anchor_first(self, store):
        row = _new(store, account_ids=["acc_pocket", "acc_riv"])
        assert row["account_ids"] == ["acc_pocket", "acc_riv"]

    def test_upsert_dedupes_and_drops_blanks(self, store):
        row = _new(store, account_ids=["acc_a", "acc_a", "", "  ", "acc_b", "acc_b"])
        assert row["account_ids"] == ["acc_a", "acc_b"]

    def test_set_accounts_replaces_the_set(self, store):
        row = _new(store, account_ids=["acc_a", "acc_b"])
        store.set_accounts(row["id"], ["acc_c", "acc_a"])
        after = store.find_by_id(row["id"])
        assert after["account_ids"] == ["acc_c", "acc_a"]

    def test_set_accounts_with_an_empty_list_is_a_no_op_not_a_clear(self, store):
        """The choose step always posts what it has; an import with nothing picked must not
        wipe what a previous one taught."""
        row = _new(store, account_ids=["acc_a", "acc_b"])
        store.set_accounts(row["id"], [])
        store.set_accounts(row["id"], ["", " "])
        assert store.find_by_id(row["id"])["account_ids"] == ["acc_a", "acc_b"]

    def test_record_use_can_carry_the_set(self, store):
        row = _new(store, account_ids=["acc_a"])
        store.record_use(row["id"], account_ids=["acc_b", "acc_c"])
        after = store.find_by_id(row["id"])
        assert after["account_ids"] == ["acc_b", "acc_c"]
        assert after["uses"] == 1

    def test_accounts_for_heals_a_row_written_before_account_ids_existed(self, store):
        row = _new(store, account_ids=["acc_a"])
        legacy = dict(row)
        legacy.pop("account_ids")
        legacy["account_id"] = "acc_a"  # the pre-consolidation shape
        assert store.accounts_for(legacy) == ["acc_a"]

    def test_accounts_for_reads_the_stored_list_as_is(self, store):
        assert store.accounts_for({"account_ids": ["acc_a", "acc_b"]}) == ["acc_a", "acc_b"]


class TestPeek:
    """``peek_source`` is the choose step's zero-cost recognition probe — it must answer with
    the remembered chips so the modal can restore them."""

    def _grid_csv(self) -> bytes:
        return b"Date,Description,Amount\n2026-06-01,Grab,-119000\n"

    def test_peek_returns_the_remembered_accounts(self, store):
        grid = [["Date", "Description", "Amount"], ["2026-06-01", "Grab", "-119000"]]
        prof = profiles.learn(grid, {"header_row": 0})
        assert prof is not None
        profiles.remember_accounts(prof["id"], ["acc_pocket", "acc_riv"])

        out = profiles.peek_source(self._grid_csv(), "csv")
        assert out["known"] is True
        assert out["account_ids"] == ["acc_pocket", "acc_riv"]

    def test_peek_of_an_unknown_source_answers_with_an_empty_list(self, store):
        out = profiles.peek_source(self._grid_csv(), "csv")
        assert out["known"] is False
        assert out["account_ids"] == []

    def test_remember_accounts_never_raises_on_a_missing_profile(self, store):
        profiles.remember_accounts("fmt_doesnotexist", ["acc_a"])  # best-effort, no throw


class TestEndpoint:
    """The preview endpoint carries the chip set and stamps it onto the profile the parse
    just used, so the NEXT source-peek can hand it back."""

    def test_preview_remembers_the_posted_chip_set(self, store, monkeypatch):
        from backend.api import import_api

        prof = profiles.learn([["Date", "Description", "Amount"], ["x", "y", "1"]], {"h": 0})
        assert prof is not None
        import_api._remember_source_accounts(
            {"format_profile": {"id": prof["id"]}},
            "acc_pocket",
            json.dumps(["acc_pocket", "acc_riv"]),
        )
        assert store.find_by_id(prof["id"])["account_ids"] == ["acc_pocket", "acc_riv"]

    def test_the_anchor_leads_even_when_the_chip_list_orders_it_second(self, store):
        from backend.api import import_api

        prof = profiles.learn([["Date", "Description", "Amount"], ["x", "y", "1"]], {"h": 0})
        import_api._remember_source_accounts(
            {"format_profile": {"id": prof["id"]}},
            "acc_pocket",
            json.dumps(["acc_riv", "acc_pocket"]),
        )
        assert store.find_by_id(prof["id"])["account_ids"] == ["acc_pocket", "acc_riv"]

    @pytest.mark.parametrize("raw", ["not json", '{"a":1}', "", None, "[1, 2]"])
    def test_malformed_account_ids_are_ignored_never_a_422(self, raw):
        from backend.api import import_api

        # A prefill hint must never cost the owner a parse they already paid for.
        assert import_api._parse_account_ids(raw) in ([], ["1", "2"])

    def test_a_preview_with_no_learned_profile_stamps_nothing(self, store):
        from backend.api import import_api

        import_api._remember_source_accounts({}, "acc_pocket", json.dumps(["acc_pocket"]))
        import_api._remember_source_accounts(
            {"format_profile": None}, "acc_pocket", json.dumps(["acc_pocket"])
        )
        assert store.all_profiles() == []
