"""Files attached to owned assets, listed in Documents (services/vault_assets.py).

They appear on their own, like statements and receipts, and are managed on the asset. The
asset's "Lucius can read" switch is their Private: off, and every model read refuses them.
Synthetic data only.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import registry
from backend.core.errors import ValidationError
from backend.services import owned_assets as assets_svc
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


def _asset_with_doc(body: bytes = b"valued at 950000", name: str = "Valuation report", **doc):
    from backend.services.attachment_ingress import persist_inbound_file

    asset = assets_svc.apply_owned_asset_change(
        {"op": "create", "name": "Flat", "category": "property", "value": 900000}
    )
    ref = persist_inbound_file("valuation.txt", "text/plain", body, source_channel="assets:docs")[
        "attachment_id"
    ]
    assets_svc.apply_owned_asset_change(
        {"op": "add_doc", "id": asset["id"], "ref_id": ref, "name": name, **doc}
    )
    return asset, ref


async def _call(name: str, args: dict) -> dict:
    text, _ = await registry.dispatch(name, args)
    return json.loads(text)


def test_an_asset_file_is_listed_by_its_asset():
    asset, ref = _asset_with_doc()
    [item] = vault.list_items(kind="asset")
    assert (item["id"], item["title"], item["asset_name"]) == (
        f"asset-{ref}",
        "Valuation report",
        "Flat",
    )
    assert (item["asset_id"], item["private"]) == (asset["id"], False)
    assert vault.get_item(item["id"]) == item
    assert vault.counts(vault.list_items())["asset"] == 1


async def test_lucius_can_read_off_makes_it_private_on_every_read_path(monkeypatch):
    async def must_not_call(*a, **k):
        raise AssertionError("a file Lucius may not read reached the image reader")

    monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_call)
    asset, ref = _asset_with_doc(b"account 99-1234 hunter2", lucius_readable=False)
    item = vault.get_item(f"asset-{ref}")
    assert item["private"] is True
    docs = await _call("read_documents", {"document_ids": [item["id"]]})
    atts = await _call("read_attachments", {"attachment_ids": [ref]})
    assert docs["results"][0]["private"] is True and atts["results"][0]["private"] is True
    assert "hunter2" not in json.dumps([docs, atts])
    assets_svc.apply_owned_asset_change(
        {"op": "update_doc", "id": asset["id"], "ref_id": ref, "changes": {"lucius_readable": True}}
    )
    again = await _call("read_documents", {"document_ids": [item["id"]]})
    assert again["results"][0]["ok"] is True and "hunter2" in again["results"][0]["text"]


async def test_only_the_user_deletes_an_asset_file_and_it_leaves_the_asset():
    from backend.db.owned_assets_store import OwnedAssetsStore
    from backend.services.attachments import delete_all_attachments, get_attachment_meta

    asset, ref = _asset_with_doc()
    refused = await _call("delete_upload", {"ref_id": ref})
    assert refused["ok"] is False and "asset" in refused["error"]
    delete_all_attachments()
    assert get_attachment_meta(ref) is not None
    with pytest.raises(ValidationError):
        vault_changes.delete_file(f"asset-{ref}")  # the own-files path
    assert vault_changes.remove_item(f"asset-{ref}")["action"] == "deleted"
    assert OwnedAssetsStore().find_by_id(asset["id"])["docs"] == []
    assert get_attachment_meta(ref) is None  # nothing else held it
    assert vault.list_items(kind="asset") == []


def test_renaming_an_asset_file_renames_it_on_the_asset():
    from backend.db.owned_assets_store import OwnedAssetsStore

    asset, ref = _asset_with_doc()
    assert vault_changes.rename_item(f"asset-{ref}", "2026 valuation")["title"] == "2026 valuation"
    [doc] = OwnedAssetsStore().find_by_id(asset["id"])["docs"]
    assert doc["name"] == "2026 valuation"  # the asset page shows the same name
    with pytest.raises(ValidationError, match="can't be empty"):
        vault_changes.rename_item(f"asset-{ref}", "")


def test_a_file_whose_upload_is_gone_is_not_listed():
    from backend.db.attachment_store import AttachmentStore

    _asset, ref = _asset_with_doc()
    AttachmentStore().delete_attachment(ref)
    assert vault.list_items(kind="asset") == []


def test_an_asset_pdf_previews_page_by_page():
    import pymupdf

    from backend.services import document_preview
    from backend.services.attachment_ingress import persist_inbound_file

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Synthetic valuation")
    pdf = doc.tobytes()
    doc.close()
    asset = assets_svc.apply_owned_asset_change(
        {"op": "create", "name": "Flat", "category": "property", "value": 900000}
    )
    ref = persist_inbound_file("val.pdf", "application/pdf", pdf, source_channel="assets:docs")[
        "attachment_id"
    ]
    assets_svc.apply_owned_asset_change(
        {"op": "add_doc", "id": asset["id"], "ref_id": ref, "name": "Valuation"}
    )
    png, count = document_preview.page_image(f"asset-{ref}", 1)
    assert png.startswith(b"\x89PNG") and count == 1
