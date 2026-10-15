"""Files attached to owned assets, as Documents items (kind ``asset``).

An asset's attachments are ordinary uploads the asset points at — ``docs[] {ref_id, name,
added_on, lucius_readable}`` on the owned-asset row (ADR 2026-09-06-assets-tab-faithful-port).
Documents lists them the way it lists statements and receipts: automatically, managed where
they live (the asset's page). The asset's "Lucius can read" switch is their Private: off, and
every model read refuses the file — ``is_private_asset_doc``, asked by
``vault.is_private_attachment``, the one guard every model-facing read goes through.
"""

from __future__ import annotations

from collections.abc import Iterator

from backend.db.attachment_store import AttachmentStore
from backend.db.owned_assets_store import OwnedAssetsStore

ASSET_CHANNEL_PREFIX = "assets:"  # an asset upload's source_channel (owned_assets_attachments)


def display_stem(filename: str | None) -> str:
    """A file name without its extension — how an item is called when nothing better names it
    (owner: "does the name need extension?"). Details still show the full file name, and a
    download keeps it."""
    name = (filename or "").strip()
    stem, dot, ext = name.rpartition(".")
    return stem if dot and stem.strip() and 1 <= len(ext) <= 5 and ext.isalnum() else name


def _docs() -> Iterator[tuple[dict, dict]]:
    for asset in OwnedAssetsStore().all():
        for doc in asset.get("docs") or []:
            if doc.get("ref_id"):
                yield asset, doc


def _year(date_text: str | None) -> int | None:
    try:
        return int(str(date_text)[:4]) if date_text else None
    except ValueError:
        return None


def _item(asset: dict, doc: dict, row: dict) -> dict:
    aid = doc["ref_id"]
    return {
        "id": f"asset-{aid}",
        "kind": "asset",
        "source_id": aid,
        "title": doc.get("name") or display_stem(row.get("filename")) or "Asset file",
        "filename": row.get("filename") or "",
        "media_type": row.get("media_type"),
        "size_bytes": row.get("size_bytes"),
        "added_at": row.get("created_at"),
        "year": _year(doc.get("added_on") or row.get("created_at")),
        "private": not doc.get("lucius_readable", True),
        "note": doc.get("note") or None,
        "tags": list(doc.get("tags") or []),
        "asset_id": asset.get("id"),
        "asset_name": asset.get("name") or "",
        "file_url": f"/api/uploads/{aid}/file",
    }


def asset_doc_items(attachments_by_id: dict[str, dict]) -> list[dict]:
    """One item per attached file. A file two assets share is listed once (the first); a
    file whose upload is gone is not listed — the asset page hides it too."""
    seen: set[str] = set()
    out = []
    for asset, doc in _docs():
        row = attachments_by_id.get(doc["ref_id"])
        if row is None or doc["ref_id"] in seen:
            continue
        seen.add(doc["ref_id"])
        out.append(_item(asset, doc, row))
    return out


def one_asset_doc(attachment_id: str) -> dict | None:
    row = AttachmentStore().find_meta(attachment_id) if attachment_id else None
    if row is None:
        return None
    for asset, doc in _docs():
        if doc["ref_id"] == attachment_id:
            return _item(asset, doc, row)
    return None


def is_asset_upload(row: dict | None) -> bool:
    return str((row or {}).get("source_channel") or "").startswith(ASSET_CHANNEL_PREFIX)


def private_asset_doc_ids() -> set[str]:
    """Every file an asset holds with "Lucius can read" switched off — for a whole listing."""
    return {doc["ref_id"] for _asset, doc in _docs() if not doc.get("lucius_readable", True)}


def is_private_asset_doc(attachment_id: str) -> bool:
    """True when an asset holds this file with "Lucius can read" switched off."""
    return any(
        doc["ref_id"] == attachment_id and not doc.get("lucius_readable", True)
        for _asset, doc in _docs()
    )
