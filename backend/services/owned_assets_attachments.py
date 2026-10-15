"""Owned-assets attachments — the photo and document ops. Dispatched ONLY by the write choke
(``services.owned_assets.apply_owned_asset_change``); each returns the stored row and the choke
projects it. The upload routes (``api/assets_api.py``) persist the file first, then call the
choke with its attachment ref.

Removing a document or replacing the photo also deletes the file it pointed at — only an
asset upload (see `_ours`), and only once no owned asset references it — best-effort, AFTER
the asset write commits: a failure is logged and leaves an orphan visible in Settings →
Uploaded files, never a broken document link. Deleting an asset keeps its files (a
checkpoint undo restores the row, not the file).
"""

from __future__ import annotations

import logging
import re

from backend.core.errors import NotFoundError, ValidationError
from backend.db.engine import locked_unit_of_work
from backend.db.owned_assets_store import OwnedAssetsStore
from backend.db.tables import owned_assets as owned_assets_table
from backend.services import attachments as attachments_service
from backend.services import owned_assets_rules as rules

_log = logging.getLogger(__name__)

_MISSING_DOC = "That document no longer exists."
_ASSET_UPLOAD_ID = re.compile(r"att_[0-9a-f]{12}")


def _referenced(store: OwnedAssetsStore, ref_id: str) -> bool:
    """Whether any owned asset still points at `ref_id` — as its photo or a document."""
    return any(
        asset.get("photo_ref") == ref_id
        or any(doc.get("ref_id") == ref_id for doc in asset.get("docs") or [])
        for asset in store.all()
    )


def _ours(ref_id) -> bool:
    """Whether an asset op may delete `ref_id`'s file: an id the attachment store mints,
    uploaded through an asset route (`assets:photo` / `assets:docs`). Never a chat upload or a
    statement an asset merely points at, and never a crafted id a restore planted (security
    review 2026-09-26, S1 — `../../*` climbed out of the attachments folder, `*` matched every
    file in it)."""
    if not isinstance(ref_id, str) or not _ASSET_UPLOAD_ID.fullmatch(ref_id):
        return False
    meta = attachments_service.get_attachment_meta(ref_id) or {}
    return str(meta.get("source_channel") or "").startswith("assets:")


def discard_unreferenced(store: OwnedAssetsStore, ref_id: str) -> None:
    """Delete `ref_id`'s attachment if it is ours and no owned asset references it —
    best-effort: a failure is logged, never raised."""
    try:
        if _ours(ref_id) and not _referenced(store, ref_id):
            attachments_service.delete_attachment(ref_id)
    except Exception:
        _log.warning("owned-asset attachment %s kept: deleting it failed", ref_id, exc_info=True)


def set_photo(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    ref = change.get("ref")
    with locked_unit_of_work(owned_assets_table) as conn:
        replaced = rules.require_asset(store, asset_id, conn=conn).get("photo_ref")
        updated = store.set_photo(asset_id, ref, conn=conn)
    if replaced and replaced != ref:
        discard_unreferenced(store, replaced)
    return updated


def add_doc(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict | None:
    if not change.get("ref_id"):
        raise ValidationError("A document reference is required.", field="ref_id")
    doc = {
        "ref_id": change["ref_id"],
        "name": str(change.get("name") or "")[: rules.MAX_DOC_NAME],
        "lucius_readable": change.get("lucius_readable", True),
    }
    with locked_unit_of_work(owned_assets_table) as conn:
        rules.require_asset(store, asset_id, conn=conn)
        return store.add_doc(asset_id, doc, conn=conn)


def update_doc(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict:
    """A typed rename is refused past the cap; an upload's file name is cut to it instead."""
    changes = dict(change.get("changes") or {})
    if "name" in changes:
        # Spaces tidied as Documents cleans a title; a blank name would leave the row nameless.
        name = " ".join(str(changes["name"] or "").split())
        if not name:
            raise ValidationError("Enter a name.", field="name")
        changes["name"] = rules.bounded_text(name, rules.MAX_DOC_NAME, "name")
    if "note" in changes:
        note = str(changes["note"] or "").strip()
        changes["note"] = rules.bounded_text(note, rules.MAX_DOC_NOTE, "doc_note") or None
    if "tags" in changes:
        from backend.services.vault import clean_tags  # Documents' own tag rules

        changes["tags"] = clean_tags(changes["tags"])
    with locked_unit_of_work(owned_assets_table) as conn:
        rules.require_asset(store, asset_id, conn=conn)
        updated = store.update_doc(asset_id, change.get("ref_id"), changes, conn=conn)
    if updated is None:
        raise NotFoundError(_MISSING_DOC)
    return updated


def remove_doc(store: OwnedAssetsStore, asset_id: str, change: dict) -> dict:
    ref_id = change.get("ref_id")
    with locked_unit_of_work(owned_assets_table) as conn:
        rules.require_asset(store, asset_id, conn=conn)
        updated = store.remove_doc(asset_id, ref_id, conn=conn)
    if updated is None:
        raise NotFoundError(_MISSING_DOC)
    discard_unreferenced(store, ref_id)
    return updated
