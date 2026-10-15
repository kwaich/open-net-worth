"""The Vault read model and its writes (services/vault.py, services/vault_changes.py).

The Vault owns no store: kept files ride on attachment rows, saved artifacts on artifact rows,
and statements/receipts appear on their own. These tests pin that one-copy contract, what
removing an item means per kind, and that Private survives un-keeping (fail closed).
Synthetic data only; every store is isolated under tmp_path.
"""

from __future__ import annotations

import pytest

from backend.core.errors import NotFoundError, ValidationError
from backend.services import ledger as ledger_svc
from backend.services import vault, vault_changes


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, db_isolated):
    import backend.db.artifact_store as art_mod
    import backend.db.attachment_store as att_mod
    import backend.db.receipt_store as rcpt_mod
    from backend.services import attachments as attachments_svc

    for mod, attr, name in (
        (att_mod, "ATTACHMENTS_DIR", "attachments"),
        (rcpt_mod, "RECEIPTS_DIR", "receipts"),
        (art_mod, "ARTIFACTS_DIR", "artifacts"),
    ):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(mod, attr, path)
    monkeypatch.setattr(art_mod, "DATA_DIR", tmp_path)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


def _chat_upload(name: str = "notes.txt", body: bytes = b"policy number 123") -> str:
    from backend.services.attachment_ingress import persist_inbound_file

    return persist_inbound_file(name, "text/plain", body, source_channel="chat:web")[
        "attachment_id"
    ]


def _artifact(session_id: str | None = "sess-1") -> str:
    from backend.db.artifact_store import ArtifactStore

    html = "<html><head><style>h1{color:red}</style><script>alert(1)</script></head>"
    html += "<body><h1>Spending review</h1><p>Groceries up 12%</p></body></html>"
    return ArtifactStore().create("Spending review", "review.html", "html", html, session_id)["id"]


class TestFiles:
    def test_a_file_added_in_the_vault_is_listed_with_its_details(self):
        item = vault_changes.add_file(
            "return.txt",
            "text/plain",
            b"wages 1000",
            title="2025 return",
            year="2025",
        )
        assert item["kind"] == "file"
        assert item["id"] == f"file-{item['source_id']}"
        assert (item["title"], item["year"]) == ("2025 return", 2025)
        assert "doc_type" not in item  # a hand-set type was retired (2026-09-28)
        assert item["origin"] == "vault"
        assert item["private"] is False
        assert [i["id"] for i in vault.list_items()] == [item["id"]]

    def test_bad_details_are_refused_before_anything_is_stored(self):
        from backend.services.attachments import list_attachments

        with pytest.raises(ValidationError):
            vault_changes.add_file("a.txt", "text/plain", b"x", year="1492")
        assert list_attachments() == []

    def test_a_chat_upload_is_not_in_the_vault_until_kept(self):
        aid = _chat_upload()
        assert vault.list_items() == []
        item = vault_changes.keep_attachment(aid, title="Home policy")
        assert (item["origin"], item["title"]) == ("chat", "Home policy")
        again = vault_changes.keep_attachment(aid, year=2024)
        assert (again["title"], again["year"], again["added_at"]) == (
            "Home policy",
            2024,
            item["added_at"],
        )

    def test_keeping_never_switches_private_off(self):
        aid = _chat_upload()
        vault_changes.keep_attachment(aid, private=True)
        with pytest.raises(ValidationError, match="Only the user"):
            vault_changes.keep_attachment(aid, private=False)
        assert vault.is_private_attachment(aid) is True

    def test_the_users_edit_can_switch_private_off(self):
        item = vault_changes.add_file("a.txt", "text/plain", b"x", private=True)
        assert vault.is_private_attachment(item["source_id"]) is True
        updated = vault_changes.update_file(item["id"], {"private": False, "title": "  A  file "})
        assert (updated["private"], updated["title"]) == (False, "A file")

    def test_edits_are_refused_for_unknown_fields_other_kinds_and_unkept_files(self):
        item = vault_changes.add_file("a.txt", "text/plain", b"x")
        with pytest.raises(ValidationError):
            vault_changes.update_file(item["id"], {"filename": "b.txt"})
        with pytest.raises(ValidationError):
            vault_changes.update_file(f"artifact-{_artifact()}", {"title": "x"})
        with pytest.raises(NotFoundError):
            vault_changes.update_file(f"file-{_chat_upload()}", {"title": "x"})

    def test_removing_a_file_added_in_documents_deletes_it(self):
        from backend.services.attachments import get_attachment_meta

        item = vault_changes.add_file("a.txt", "text/plain", b"x")
        assert vault_changes.remove_item(item["id"]) == {"id": item["id"], "action": "deleted"}
        assert get_attachment_meta(item["source_id"]) is None

    def test_removing_a_kept_chat_file_leaves_it_in_the_chat_and_still_private(self):
        from backend.services.attachments import get_attachment_meta

        aid = _chat_upload()
        vault_changes.keep_attachment(aid, private=True)
        assert vault_changes.remove_item(f"file-{aid}")["action"] == "removed"
        assert get_attachment_meta(aid) is not None
        assert vault.list_items() == []
        assert vault.is_private_attachment(aid) is True


class TestArtifacts:
    def test_saving_and_unsaving_an_artifact(self):
        art = _artifact()
        assert vault.list_items() == []
        item = vault_changes.save_artifact(art)
        assert (item["kind"], item["source_id"], item["file_url"]) == ("artifact", art, None)
        assert vault_changes.save_artifact(art)["added_at"] == item["added_at"]  # idempotent
        assert vault_changes.remove_item(item["id"])["action"] == "removed"
        assert vault.list_items() == []

    def test_a_saved_artifact_outlives_its_sessions_cleanup(self):
        from backend.db.artifact_store import ArtifactStore

        kept, dropped = _artifact("automation-1"), _artifact("automation-1")
        vault_changes.save_artifact(kept)
        assert ArtifactStore().delete_for_session("automation-1") == 1
        assert ArtifactStore().find_by_id(dropped) is None
        row = ArtifactStore().find_by_id(kept)
        assert (row["session_id"], row["detached_from_session"]) == (None, "automation-1")
        assert ArtifactStore().list_for_session("automation-1") == []
        assert ArtifactStore().read_text(kept)
        assert [i["source_id"] for i in vault.list_items()] == [kept]

    def test_unsaving_an_artifact_whose_conversation_is_gone_deletes_it(self):
        """Detached + un-saved would otherwise be an orphan nothing lists and nothing frees,
        still counted against create_artifact's global cap (code review, 2026-09-26)."""
        from backend.db.artifact_store import ArtifactStore

        art = _artifact("automation-2")
        vault_changes.save_artifact(art)
        ArtifactStore().delete_for_session("automation-2")
        item = vault.get_item(f"artifact-{art}")
        assert item["detached"] is True
        assert vault_changes.remove_item(item["id"])["action"] == "deleted"
        assert ArtifactStore().find_by_id(art) is None
        assert ArtifactStore().totals() == (0, 0)

    def test_unsaving_a_live_artifact_keeps_it_in_its_chat(self):
        from backend.db.artifact_store import ArtifactStore

        art = _artifact()
        vault_changes.save_artifact(art)
        assert vault_changes.unsave_artifact(art) == {"artifact_id": art, "saved": False}
        assert ArtifactStore().find_by_id(art)["session_id"] == "sess-1"

    def test_unknown_artifact_is_a_not_found(self):
        with pytest.raises(NotFoundError):
            vault_changes.save_artifact("art_nope")


class TestRename:
    def test_a_file_is_renamed_and_an_empty_name_goes_back_to_its_file_name(self):
        item = vault_changes.add_file("scan_0042.txt", "text/plain", b"deed")
        renamed = vault_changes.rename_item(item["id"], "  House   deed ")
        assert renamed["title"] == "House deed"
        # Back to its file name — without the extension; the full name stays in `filename`.
        cleared = vault_changes.rename_item(item["id"], "")
        assert (cleared["title"], cleared["filename"]) == ("scan_0042", "scan_0042.txt")

    def test_a_name_from_the_file_name_drops_only_a_real_extension(self):
        from backend.services.vault_assets import display_stem

        assert display_stem("Statement 13.pdf") == "Statement 13"
        assert display_stem("2026-09-01_Statement 2.PDF") == "2026-09-01_Statement 2"
        assert display_stem("v1.2 notes.txt") == "v1.2 notes"
        assert display_stem("README") == "README"
        assert display_stem(".pdf") == ".pdf"
        assert display_stem("archive.backup-2026") == "archive.backup-2026"

    def test_a_saved_artifact_is_renamed_on_its_own_row(self):
        from backend.db.artifact_store import ArtifactStore

        art = _artifact()
        vault_changes.save_artifact(art)
        assert vault_changes.rename_item(f"artifact-{art}", "Q3 spending")["title"] == "Q3 spending"
        assert ArtifactStore().find_by_id(art)["title"] == "Q3 spending"
        with pytest.raises(ValidationError, match="can't be empty"):
            vault_changes.rename_item(f"artifact-{art}", "   ")
        assert ArtifactStore().find_by_id(art)["title"] == "Q3 spending"

    def test_an_artifact_not_in_documents_is_a_not_found(self):
        with pytest.raises(NotFoundError):
            vault_changes.rename_item(f"artifact-{_artifact()}", "Nope")


class TestStatementsAndReceipts:
    @pytest.fixture
    def book(self, monkeypatch):
        monkeypatch.setattr(ledger_svc, "base_currency", lambda: "SGD")
        ledger_svc.accounts.insert(
            {"id": "acc_chk", "name": "Everyday", "kind": "asset", "currency": "SGD"}
        )
        txn = ledger_svc.create_transaction(
            {
                "kind": "expense",
                "date": "2026-08-01",
                "description": "Dinner",
                "category": None,
                "from_account_id": "acc_chk",
                "from_amount": 25.0,
                "from_currency": "SGD",
            }
        )
        return {"txn_id": txn["id"]}

    @staticmethod
    def _statement_and_receipt(book) -> tuple[dict, dict]:
        from backend.db.statement_store import StatementStore
        from backend.services.receipts import apply_receipt_change

        store = StatementStore()
        stmt = store.create(
            "aug.csv", "text/csv", b"d,a\n", {"kind": "text", "text": "d,a", "summary": "aug"}
        )
        store.update_by_id(
            stmt["id"],
            {"account_id": "acc_chk", "import_meta": {"statement_date": "2026-08-31"}},
        )
        receipt = apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": _chat_upload("r.txt", b"total 25"),
                "transaction_id": book["txn_id"],
            }
        )["receipt"]
        return stmt, receipt

    def test_renamed_and_back_to_the_automatic_name(self, book):
        from backend.db.statement_store import StatementStore

        stmt, receipt = self._statement_and_receipt(book)
        for vid, auto in (
            (f"statement-{stmt['id']}", "Everyday — Aug 2026"),
            (f"receipt-{receipt['id']}", "Receipt — Dinner"),
        ):
            assert vault_changes.rename_item(vid, "  My   name ")["title"] == "My name"
            assert [i["title"] for i in vault.list_items() if i["id"] == vid] == ["My name"]
            assert vault_changes.rename_item(vid, "")["title"] == auto
        vault_changes.rename_item(f"statement-{stmt['id']}", "DBS Sep")
        # Promoted, so the list never reads the payload for it — and kept in the payload too.
        assert StatementStore().list_catalog(stmt["id"])[0]["title"] == "DBS Sep"
        assert StatementStore().find_by_id(stmt["id"])["title"] == "DBS Sep"

    def test_deleting_one_keeps_its_transactions(self, book):
        from backend.db.receipt_store import ReceiptStore
        from backend.db.statement_store import StatementStore

        stmt, receipt = self._statement_and_receipt(book)
        for vid in (f"statement-{stmt['id']}", f"receipt-{receipt['id']}"):
            assert vault_changes.remove_item(vid) == {"id": vid, "action": "deleted"}
        assert StatementStore().find_by_id(stmt["id"]) is None
        assert ReceiptStore().find_by_id(receipt["id"]) is None
        assert ledger_svc.get_transaction(book["txn_id"]) is not None
        assert vault.list_items() == []

    def test_statements_and_receipts_file_themselves(self, book):
        from backend.db.statement_store import StatementStore
        from backend.services.receipts import apply_receipt_change

        store = StatementStore()
        stmt = store.create(
            "aug.csv", "text/csv", b"d,a\n", {"kind": "text", "text": "d,a", "summary": "aug"}
        )
        store.update_by_id(
            stmt["id"],
            {"account_id": "acc_chk", "import_meta": {"statement_date": "2026-08-31"}},
        )
        receipt = apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": _chat_upload("r.txt", b"total 25"),
                "transaction_id": book["txn_id"],
            }
        )["receipt"]

        by_kind = {i["kind"]: i for i in vault.list_items()}
        assert by_kind["statement"]["title"] == "Everyday — Aug 2026"
        # A single-item read (tools, mentions) is a point read that answers the same.
        for kind in ("statement", "receipt"):
            assert vault.get_item(by_kind[kind]["id"]) == by_kind[kind]
        catalog = StatementStore().list_catalog()
        assert "payload" not in catalog[0] and "upload_entry" not in catalog[0]
        assert by_kind["statement"]["year"] == 2026
        assert by_kind["receipt"]["title"] == "Receipt — Dinner"
        assert by_kind["receipt"]["source_id"] == receipt["id"]
        assert by_kind["receipt"]["transaction_id"] == book["txn_id"]  # the panel links to it
        assert vault.counts(vault.list_items())["statement"] == 1


class TestListing:
    def test_filters_and_search(self):
        vault_changes.add_file("w2.txt", "text/plain", b"x", year=2025)
        vault_changes.add_file("deed.txt", "text/plain", b"y", year=2019)
        vault_changes.save_artifact(_artifact())
        assert {i["filename"] for i in vault.list_items(kind="file")} == {"w2.txt", "deed.txt"}
        assert [i["filename"] for i in vault.list_items(year=2019)] == ["deed.txt"]
        assert [i["kind"] for i in vault.list_items(query="SPENDING")] == ["artifact"]
        with pytest.raises(ValidationError):
            vault.list_items(kind="photo")

    def test_bad_ids(self):
        for bad in ("", "nope", "file-", "photo-att_1"):
            with pytest.raises(ValidationError):
                vault.parse_item_id(bad)
        with pytest.raises(NotFoundError):
            vault.get_item("file-att_missing")


class TestReadSource:
    def test_a_private_file_yields_no_content_at_all(self):
        item = vault_changes.add_file("a.txt", "text/plain", b"secret words", private=True)
        source = vault.read_source(item["id"])
        assert source["refused"] == vault.PRIVATE_REFUSAL
        assert not {"entry", "text", "error"} & set(source)

    def test_an_artifact_reads_as_its_visible_text(self):
        source = vault.read_source(vault_changes.save_artifact(_artifact())["id"])
        assert source["text"] == "Spending review\nGroceries up 12%"

    def test_a_file_reads_as_its_parsed_entry(self):
        item = vault_changes.add_file("a.txt", "text/plain", b"hello vault")
        source = vault.read_source(item["id"])
        assert source["entry"]["kind"] == "text"
        assert "hello vault" in source["entry"]["text"]


def test_document_mention_carries_metadata_and_how_to_read_never_contents():
    from backend.services.mentions.dispatch import resolve_mentions

    open_item = vault_changes.add_file("a.txt", "text/plain", b"body text", title="Open doc")
    private = vault_changes.add_file("b.txt", "text/plain", b"hidden text", private=True)
    resolved = resolve_mentions(
        [
            {"type": "document", "ref": open_item["id"], "label": "Open doc"},
            {"type": "document", "ref": private["id"], "label": "b.txt"},
            {"type": "document", "ref": "file-att_gone", "label": "gone"},
        ]
    )
    first, second, third = (r.get("data") for r in resolved)
    assert first["title"] == "Open doc"
    assert "read_documents" in first["contents"]
    assert second["private"] is True and "off limits" in second["contents"]
    assert third is None and resolved[2]["error"] == "not_found"
    assert "body text" not in str(resolved) and "hidden text" not in str(resolved)
