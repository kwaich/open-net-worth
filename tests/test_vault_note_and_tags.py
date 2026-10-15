"""The user's own fields on any Documents item — a note and tags (services/vault_changes
set_note/set_tags, PUT /api/vault/items/{id}/note|tags).

Both are kept on the record the item lives on — like its name — so every kind carries them: a
file's ``vault`` meta, an artifact's row, a statement's promoted column, a receipt through its
choke, an asset file's doc on its asset. They are the user's words, not the file's: Lucius
reads them Private or not, and search matches them. Synthetic data only.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import registry
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


def _upload(name: str, body: bytes, channel: str) -> str:
    from backend.services.attachment_ingress import persist_inbound_file

    return persist_inbound_file(name, "text/plain", body, source_channel=channel)["attachment_id"]


@pytest.fixture
def every_kind(monkeypatch) -> dict[str, str]:
    """One item of each kind, by kind → Documents id."""
    from backend.db.artifact_store import ArtifactStore
    from backend.db.statement_store import StatementStore
    from backend.services import owned_assets
    from backend.services.receipts import apply_receipt_change

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
    stmt = StatementStore().create(
        "aug.csv", "text/csv", b"d,a\n", {"kind": "text", "text": "d,a", "summary": "aug"}
    )
    receipt = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": _upload("r.txt", b"total 25", "chat:web"),
            "transaction_id": txn["id"],
        }
    )["receipt"]
    art = ArtifactStore().create("Plan", "plan.html", "html", "<p>plan</p>", "s1")["id"]
    vault_changes.save_artifact(art)
    asset = owned_assets.apply_owned_asset_change(
        {"op": "create", "name": "Flat", "category": "property", "value": 900000}
    )
    doc_ref = _upload("valuation.txt", b"valued at 950000", "assets:docs")
    owned_assets.apply_owned_asset_change(
        {"op": "add_doc", "id": asset["id"], "ref_id": doc_ref, "name": "Valuation"}
    )
    return {
        "file": vault_changes.add_file("deed.txt", "text/plain", b"deed")["id"],
        "artifact": f"artifact-{art}",
        "statement": f"statement-{stmt['id']}",
        "receipt": f"receipt-{receipt['id']}",
        "asset": f"asset-{doc_ref}",
    }


class TestEveryKind:
    def test_a_note_is_written_listed_and_removed_on_every_kind(self, every_kind):
        for kind, vid in every_kind.items():
            assert vault.get_item(vid)["note"] is None, kind
            written = vault_changes.set_note(vid, f"  Signed copy — {kind}  \n  - in the safe  ")
            assert written["note"] == f"Signed copy — {kind}\n  - in the safe", kind
            [listed] = [i for i in vault.list_items() if i["id"] == vid]
            assert listed["note"] == written["note"], kind
            assert vault_changes.set_note(vid, "   ")["note"] is None, kind
            assert vault.get_item(vid)["note"] is None, kind

    def test_it_lives_on_the_record_itself(self, every_kind):
        from backend.db.artifact_store import ArtifactStore
        from backend.db.attachment_store import AttachmentStore
        from backend.db.owned_assets_store import OwnedAssetsStore
        from backend.db.receipt_store import ReceiptStore
        from backend.db.statement_store import StatementStore

        for vid in every_kind.values():
            vault_changes.set_note(vid, "kept here")
        source = {k: vault.parse_item_id(v)[1] for k, v in every_kind.items()}
        assert AttachmentStore().find_meta(source["file"])["vault"]["note"] == "kept here"
        assert ArtifactStore().find_by_id(source["artifact"])["note"] == "kept here"
        assert ReceiptStore().find_by_id(source["receipt"])["note"] == "kept here"
        # Promoted, so the list never reads a statement's payload for it — and kept there too.
        assert StatementStore().list_catalog(source["statement"])[0]["note"] == "kept here"
        assert StatementStore().find_by_id(source["statement"])["note"] == "kept here"
        [asset] = OwnedAssetsStore().all()
        assert asset["docs"][0]["note"] == "kept here"

    def test_a_rename_keeps_the_note(self, every_kind):
        for kind, vid in every_kind.items():
            vault_changes.set_note(vid, "kept")
            renamed = vault_changes.rename_item(vid, f"Renamed {kind}")
            assert (renamed["title"], renamed["note"]) == (f"Renamed {kind}", "kept"), kind

    def test_the_other_details_are_untouched(self, every_kind):
        for vid in every_kind.values():
            before = vault.get_item(vid)
            after = vault_changes.set_note(vid, "a note")
            assert {k: v for k, v in after.items() if k != "note"} == {
                k: v for k, v in before.items() if k != "note"
            }


class TestRules:
    def test_a_note_past_the_cap_is_refused_and_nothing_is_written(self):
        vid = vault_changes.add_file("a.txt", "text/plain", b"x")["id"]
        vault_changes.set_note(vid, "first")
        with pytest.raises(ValidationError, match="up to 1000"):
            vault_changes.set_note(vid, "x" * (vault.MAX_NOTE + 1))
        assert vault.get_item(vid)["note"] == "first"
        assert vault_changes.set_note(vid, "y" * vault.MAX_NOTE)["note"] == "y" * vault.MAX_NOTE

    def test_the_asset_page_is_held_to_the_same_cap(self):
        from backend.services import owned_assets_rules

        assert owned_assets_rules.MAX_DOC_NOTE == vault.MAX_NOTE

    def test_unknown_items_are_not_found(self):
        with pytest.raises(NotFoundError):
            vault_changes.set_note("file-att_missing", "x")
        with pytest.raises(ValidationError):
            vault_changes.set_note("nonsense", "x")

    def test_search_matches_a_note(self):
        vid = vault_changes.add_file("scan_1.txt", "text/plain", b"x")["id"]
        vault_changes.add_file("scan_2.txt", "text/plain", b"x")
        vault_changes.set_note(vid, "Original is in the SAFE deposit box")
        assert [i["id"] for i in vault.list_items(query="safe deposit")] == [vid]

    def test_a_kept_chat_file_keeps_its_note_when_taken_out_and_kept_again(self):
        aid = _upload("p.txt", b"policy", "chat:web")
        vid = vault_changes.keep_attachment(aid)["id"]
        vault_changes.set_note(vid, "renews in March")
        assert vault_changes.remove_item(vid)["action"] == "removed"
        assert vault_changes.keep_attachment(aid)["note"] == "renews in March"


async def _call(name: str, args: dict) -> dict:
    text, _ = await registry.dispatch(name, args)
    return json.loads(text)


class TestLucius:
    async def test_the_note_is_listed_bounded_and_read_whole(self):
        vid = vault_changes.add_file("a.txt", "text/plain", b"premium 1200")["id"]
        vault_changes.set_note(vid, "n" * 900)
        [listed] = (await _call("list_documents", {}))["items"]
        assert listed["note"] == "n" * 300 + "…"
        [read] = (await _call("read_documents", {"document_ids": [vid]}))["results"]
        assert read["note"] == "n" * 900 and "premium 1200" in read["text"]

    async def test_a_private_files_note_is_read_but_never_its_contents(self, monkeypatch):
        async def must_not_call(*a, **k):
            raise AssertionError("a Private file reached the image reader")

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_call)
        vid = vault_changes.add_file("will.txt", "text/plain", b"password hunter2", private=True)[
            "id"
        ]
        vault_changes.set_note(vid, "The lawyer has the original")
        [listed] = (await _call("list_documents", {}))["items"]
        assert listed["note"] == "The lawyer has the original"
        [read] = (await _call("read_documents", {"document_ids": [vid]}))["results"]
        assert (read["ok"], read["private"], read["note"]) == (
            False,
            True,
            "The lawyer has the original",
        )
        assert "hunter2" not in json.dumps(read)

    async def test_a_note_spends_its_files_share_of_the_read_budget(self):
        vid = vault_changes.add_file("long.txt", "text/plain", b"x" * 30_000)["id"]
        vault_changes.set_note(vid, "n" * 900)
        [read] = (await _call("read_documents", {"document_ids": [vid]}))["results"]
        assert len(read["note"]) + len(read["text"]) == 24_000
        assert read["next_offset"] == len(read["text"])

    def test_the_document_mention_carries_the_note(self):
        from backend.services.mentions.dispatch import resolve_mentions

        vid = vault_changes.add_file("a.txt", "text/plain", b"body text", title="Deed")["id"]
        vault_changes.set_note(vid, "Signed copy")
        [resolved] = resolve_mentions([{"type": "document", "ref": vid, "label": "Deed"}])
        assert resolved["data"]["note"] == "Signed copy"


def test_the_api_writes_and_clears_a_note():
    from backend.api.vault import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    vid = vault_changes.add_file("a.txt", "text/plain", b"x")["id"]
    put = client.put(f"/api/vault/items/{vid}/note", json={"note": "Signed copy"})
    assert put.status_code == 200 and put.json()["item"]["note"] == "Signed copy"
    assert client.get("/api/vault").json()["items"][0]["note"] == "Signed copy"
    cleared = client.put(f"/api/vault/items/{vid}/note", json={"note": None})
    assert cleared.status_code == 200 and cleared.json()["item"]["note"] is None
    too_long = client.put(f"/api/vault/items/{vid}/note", json={"note": "x" * 1001})
    assert too_long.status_code == 422
    assert client.put(f"/api/vault/items/{vid}/note", json={"size": 1}).status_code == 422
    missing = client.put("/api/vault/items/file-att_missing/note", json={"note": "x"})
    assert missing.status_code == 404


class TestTags:
    def test_tags_are_tidied_kept_once_and_capped_never_cut(self):
        tidy = vault.clean_tags(["  Apartment ", "#apartment", "Taxes   2025", ""])
        assert tidy == ["Apartment", "Taxes 2025"]
        with pytest.raises(ValidationError, match="up to 10 tags"):
            vault.clean_tags([f"t{n}" for n in range(11)])
        with pytest.raises(ValidationError, match="up to 32"):
            vault.clean_tags(["x" * 33])
        with pytest.raises(ValidationError, match="must be a list"):
            vault.clean_tags("Apartment")

    def test_every_kind_takes_tags_and_the_list_filters_by_them(self, every_kind):
        for kind, vid in every_kind.items():
            assert vault.get_item(vid)["tags"] == [], kind
            assert vault_changes.set_tags(vid, ["Apartment", f"k-{kind}"])["tags"] == [
                "Apartment",
                f"k-{kind}",
            ], kind
        assert {i["id"] for i in vault.list_items(tag="#apartment")} == set(every_kind.values())
        assert [i["id"] for i in vault.list_items(tag="K-RECEIPT")] == [every_kind["receipt"]]
        assert [i["id"] for i in vault.list_items(query="k-statement")] == [every_kind["statement"]]
        for kind, vid in every_kind.items():
            assert vault_changes.set_tags(vid, [])["tags"] == [], kind

    def test_they_live_on_the_record_itself(self, every_kind):
        from backend.db.artifact_store import ArtifactStore
        from backend.db.attachment_store import AttachmentStore
        from backend.db.owned_assets_store import OwnedAssetsStore
        from backend.db.receipt_store import ReceiptStore
        from backend.db.statement_store import StatementStore

        for vid in every_kind.values():
            vault_changes.set_tags(vid, ["Apartment"])
        source = {k: vault.parse_item_id(v)[1] for k, v in every_kind.items()}
        assert AttachmentStore().find_meta(source["file"])["vault"]["tags"] == ["Apartment"]
        assert ArtifactStore().find_by_id(source["artifact"])["tags"] == ["Apartment"]
        assert ReceiptStore().find_by_id(source["receipt"])["tags"] == ["Apartment"]
        assert StatementStore().list_catalog(source["statement"])[0]["tags"] == ["Apartment"]
        assert StatementStore().find_by_id(source["statement"])["tags"] == ["Apartment"]
        [asset] = OwnedAssetsStore().all()
        assert asset["docs"][0]["tags"] == ["Apartment"]

    def test_a_note_or_a_rename_keeps_the_tags(self, every_kind):
        for kind, vid in every_kind.items():
            vault_changes.set_tags(vid, ["Apartment"])
            vault_changes.set_note(vid, "a note")
            assert vault_changes.rename_item(vid, f"Renamed {kind}")["tags"] == ["Apartment"], kind

    async def test_lucius_sees_tags_and_lists_by_one(self):
        vid = vault_changes.add_file("a.txt", "text/plain", b"x")["id"]
        vault_changes.add_file("b.txt", "text/plain", b"y")
        vault_changes.set_tags(vid, ["Apartment", "Taxes 2025"])
        data = await _call("list_documents", {"tag": "apartment"})
        assert [(i["id"], i["tags"]) for i in data["items"]] == [(vid, ["Apartment", "Taxes 2025"])]

    def test_the_document_mention_carries_the_tags(self):
        from backend.services.mentions.dispatch import resolve_mentions

        vid = vault_changes.add_file("a.txt", "text/plain", b"x", title="Deed")["id"]
        vault_changes.set_tags(vid, ["Apartment", "Taxes 2025"])
        [resolved] = resolve_mentions([{"type": "document", "ref": vid, "label": "Deed"}])
        assert resolved["data"]["tags"] == "Apartment, Taxes 2025"


def test_the_api_sets_and_clears_tags():
    from backend.api.vault import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    vid = vault_changes.add_file("a.txt", "text/plain", b"x")["id"]
    put = client.put(f"/api/vault/items/{vid}/tags", json={"tags": ["Apartment", " #car "]})
    assert put.status_code == 200 and put.json()["item"]["tags"] == ["Apartment", "car"]
    assert client.get("/api/vault").json()["items"][0]["tags"] == ["Apartment", "car"]
    cleared = client.put(f"/api/vault/items/{vid}/tags", json={"tags": []})
    assert cleared.status_code == 200 and cleared.json()["item"]["tags"] == []
    too_many = client.put(
        f"/api/vault/items/{vid}/tags", json={"tags": [str(n) for n in range(11)]}
    )
    assert too_many.status_code == 422
    assert client.put(f"/api/vault/items/{vid}/tags", json={"tags": "car"}).status_code == 422
    missing = client.put("/api/vault/items/file-att_missing/tags", json={"tags": ["x"]})
    assert missing.status_code == 404


class TestAssetFilePrivate:
    """An asset file's Private in Documents IS its "Lucius can read" switch on the asset page,
    flipped — one flag (owner: "i can't see we can control that in the document modal")."""

    def _doc(self):
        from backend.db.owned_assets_store import OwnedAssetsStore

        [asset] = OwnedAssetsStore().all()
        return asset["docs"][0]

    def test_private_switches_lucius_can_read_off_and_back_on(self, every_kind):
        vid = every_kind["asset"]
        item = vault_changes.update_file(vid, {"private": True})
        assert item["private"] is True
        assert self._doc()["lucius_readable"] is False
        assert vault.is_private_attachment(item["source_id"])

        item = vault_changes.update_file(vid, {"private": False})
        assert item["private"] is False
        assert self._doc()["lucius_readable"] is True

    def test_it_is_the_only_setting_and_a_refusal_writes_nothing(self, every_kind):
        with pytest.raises(ValidationError):
            vault_changes.update_file(every_kind["asset"], {"private": True, "year": 2026})
        with pytest.raises(ValidationError):
            vault_changes.update_file(every_kind["asset"], {"private": "yes"})
        assert self._doc()["lucius_readable"] is True

    def test_the_api_switch_reaches_the_asset(self, every_kind):
        from backend.api.vault import router

        app = FastAPI()
        app.include_router(router)
        res = TestClient(app).patch(
            f"/api/vault/items/{every_kind['asset']}", json={"private": True}
        )
        assert res.status_code == 200, res.text
        assert res.json()["item"]["private"] is True
        assert self._doc()["lucius_readable"] is False
