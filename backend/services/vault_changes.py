"""Vault writes — the only paths that put a record into, or take it out of, the Vault.

Every write lands on the underlying record (one copy — see ``services/vault.py``): a file's
``vault`` metadata on its attachment row, an artifact's ``saved`` flag and title on its
artifact row, a statement's or receipt's name on its own row, an asset file's name on its
asset — and a note and tags the same way (``set_note``, ``set_tags``). Deleting a statement, a receipt or an asset file goes through the same path its own
screen uses (the statement store's delete, the receipts choke, the owned-assets choke), so
it means here exactly what it means there. The user's action only: no tool calls these.

Removing a FILE depends on where it came from: a file added in the Vault is deleted (the
Vault is its only home); a chat upload the user kept is only taken out of the Vault, so the
conversation that holds it can still read it. Its Private flag survives that removal —
un-keeping a file never silently makes its contents readable again (fail closed).
"""

from __future__ import annotations

from backend.core.errors import NotFoundError, ValidationError
from backend.db.artifact_store import ArtifactStore
from backend.db.attachment_store import AttachmentStore
from backend.services import vault
from backend.services.vault import item_id, parse_item_id

VAULT_CHANNEL = "vault"
_EDITABLE = ("title", "year", "private", "note", "tags")


def _clean_title(title: object) -> str | None:
    text = " ".join(str(title or "").split())
    return text[: vault.MAX_TITLE] or None


def _clean_note(note: object) -> str | None:
    """Line breaks kept (a note can be a short list); a typed note past the cap is refused,
    never cut."""
    text = "\n".join(line.rstrip() for line in str(note or "").splitlines()).strip()
    if len(text) > vault.MAX_NOTE:
        raise ValidationError(f"A note can be up to {vault.MAX_NOTE} characters.")
    return text or None


def _clean_year(year: object) -> int | None:
    if year in (None, ""):
        return None
    try:
        value = int(str(year))
    except ValueError:
        raise ValidationError("year must be a whole number, e.g. 2025") from None
    if not vault.MIN_YEAR <= value <= vault.MAX_YEAR:
        raise ValidationError(f"year must be between {vault.MIN_YEAR} and {vault.MAX_YEAR}")
    return value


def _merge_meta(current: dict, changes: dict) -> dict:
    meta = dict(current)
    if "title" in changes:
        meta["title"] = _clean_title(changes["title"])
    if "year" in changes:
        meta["year"] = _clean_year(changes["year"])
    if "note" in changes:
        meta["note"] = _clean_note(changes["note"])
    if "tags" in changes:
        meta["tags"] = vault.clean_tags(changes["tags"])
    if "private" in changes:
        if not isinstance(changes["private"], bool):
            raise ValidationError("private must be true or false")
        meta["private"] = changes["private"]
    return meta


def _require_attachment(attachment_id: str) -> dict:
    row = AttachmentStore().find_meta(attachment_id) if attachment_id else None
    if row is None:
        raise NotFoundError(f"no stored upload {attachment_id!r}")
    return row


def _summary_change(attachment_id: str, was_private: bool, now_private: bool) -> dict:
    """The row's ``summary`` is derived from the contents, so Private hides it; switching
    Private off restores it from the parsed payload (never readable by SQL)."""
    if now_private:
        return {"summary": vault.PRIVATE_SUMMARY}
    if was_private:
        parsed = AttachmentStore().read_payload(attachment_id) or {}
        return {"summary": parsed.get("summary") or ""}
    return {}


def _keep(attachment_id: str, changes: dict) -> dict:
    row = _require_attachment(attachment_id)
    current = row.get("vault") or {}
    meta = _merge_meta({"private": False, **current}, changes)
    meta["saved"] = True
    meta["saved_at"] = current.get("saved_at") if current.get("saved") else vault.now_iso()
    was_private, now_private = bool(current.get("private")), bool(meta.get("private"))
    row_changes = {"vault": meta, **_summary_change(attachment_id, was_private, now_private)}
    AttachmentStore().update_by_id(attachment_id, row_changes)
    return vault.get_item(item_id("file", attachment_id))


def add_file(filename: str, mime: str, content: bytes, **meta: object) -> dict:
    """Store a new file straight into the Vault. Raises ``ValueError`` (unsupported or
    unreadable file) exactly like the chat upload path, which it shares."""
    from backend.services.attachment_ingress import persist_inbound_file
    from backend.services.attachments import delete_attachment

    changes = {k: v for k, v in meta.items() if k in _EDITABLE and v is not None}
    _merge_meta({}, changes)  # validate BEFORE anything is written
    result = persist_inbound_file(filename, mime, content, source_channel=VAULT_CHANNEL)
    try:
        return _keep(result["attachment_id"], changes)
    except Exception:
        # Never leave the file behind half-kept — e.g. stored but not yet marked Private.
        delete_attachment(result["attachment_id"])
        raise


def keep_attachment(attachment_id: str, **meta: object) -> dict:
    """Keep a file the user already sent in chat. Idempotent: keeping it again only
    applies the given details. Never switches Private OFF — Lucius calls this, and only
    the user's own edit (``update_file``) may make a Private file readable again."""
    changes = {k: v for k, v in meta.items() if k in _EDITABLE and v is not None}
    if changes.get("private") is False and vault.is_private_attachment(attachment_id):
        raise ValidationError("Only the user can switch Private off, in Documents.")
    return _keep(attachment_id, changes)


def _set_asset_file_private(vid: str, changes: dict) -> dict:
    """An asset file's Private is its "Lucius can read" switch on the asset page, flipped —
    one flag, written where the asset page reads it (owner: "i can't see we can control that
    in the document modal")."""
    if set(changes) - {"private"}:
        raise ValidationError("an asset file's Private is its only setting here")
    item = vault.get_item(vid)  # 404 before any write
    if "private" not in changes:
        return item
    if not isinstance(changes["private"], bool):
        raise ValidationError("private must be true or false")
    from backend.services import owned_assets

    owned_assets.apply_owned_asset_change(
        {
            "op": "update_doc",
            "id": item["asset_id"],
            "ref_id": item["source_id"],
            "changes": {"lucius_readable": not changes["private"]},
        }
    )
    return vault.get_item(vid)


def update_file(vid: str, changes: dict) -> dict:
    kind, attachment_id = parse_item_id(vid)
    if kind == "asset":
        return _set_asset_file_private(vid, changes)
    if kind != "file":
        raise ValidationError("only your own files have editable details")
    unknown = sorted(set(changes) - set(_EDITABLE))
    if unknown:
        raise ValidationError(f"cannot change {unknown} — editable: {list(_EDITABLE)}")
    current = _require_attachment(attachment_id).get("vault") or {}
    if not current.get("saved"):
        raise NotFoundError(f"no document {vid!r}")
    return _keep(attachment_id, changes)


def rename_item(vid: str, title: object) -> dict:
    """Rename an item on the record it lives on, so the new name shows wherever that record
    does (an asset file's name is the asset page's too). An empty name puts a file back to
    its file name, and a statement or receipt back to its automatic one (account and month,
    or its transaction's payee); an artifact or an asset file needs one."""
    item = vault.get_item(vid)  # 404 before any write
    kind, source_id = item["kind"], item["source_id"]
    if kind == "file":
        return update_file(vid, {"title": title})
    name = _clean_title(title)
    if kind == "statement":
        from backend.db.statement_store import StatementStore

        StatementStore().update_by_id(source_id, {"title": name})
        return vault.get_item(vid)
    if kind == "receipt":
        from backend.services.receipts import apply_receipt_change

        apply_receipt_change({"type": "rename", "receipt_id": source_id, "title": name})
        return vault.get_item(vid)
    if not name:
        raise ValidationError("the name can't be empty")
    if kind == "artifact":
        ArtifactStore().update_by_id(source_id, {"title": name})
    else:
        from backend.services import owned_assets

        owned_assets.apply_owned_asset_change(
            {
                "op": "update_doc",
                "id": item["asset_id"],
                "ref_id": item["source_id"],
                "changes": {"name": name},
            }
        )
    return vault.get_item(vid)


def _write_on_record(item: dict, key: str, value: object) -> dict:
    """Writes one of the user's own fields (``note``, ``tags``) on the record the item lives
    on, like its name (``rename_item``) — so export/import carry it: a file's ``vault`` meta,
    an artifact's or a statement's row, a receipt through the receipts choke, an asset file
    through the owned-assets choke."""
    kind, source_id = item["kind"], item["source_id"]
    if kind == "file":
        return update_file(item["id"], {key: value})
    if kind == "statement":
        from backend.db.statement_store import StatementStore

        StatementStore().update_by_id(source_id, {key: value or None})
    elif kind == "receipt":
        from backend.services.receipts import apply_receipt_change

        apply_receipt_change({"type": key, "receipt_id": source_id, key: value})
    elif kind == "artifact":
        ArtifactStore().update_by_id(source_id, {key: value})
    else:
        from backend.services import owned_assets

        owned_assets.apply_owned_asset_change(
            {
                "op": "update_doc",
                "id": item["asset_id"],
                "ref_id": source_id,
                "changes": {key: value},
            }
        )
    return vault.get_item(item["id"])


def set_note(vid: str, note: object) -> dict:
    """The user's note on an item. Empty removes it. It is metadata: Lucius reads it with the
    item's other details, Private or not (the panel says so)."""
    item = vault.get_item(vid)  # 404 before any write
    return _write_on_record(item, "note", _clean_note(note))


def set_tags(vid: str, tags: object) -> dict:
    """The user's tags on an item — how they group items across kinds (owner: "maybe tags?"
    rather than folders). An empty list removes them all."""
    item = vault.get_item(vid)  # 404 before any write
    return _write_on_record(item, "tags", vault.clean_tags(tags))


def save_artifact(artifact_id: str) -> dict:
    store = ArtifactStore()
    row = store.find_by_id(artifact_id) if artifact_id else None
    if row is None:
        raise NotFoundError(f"artifact not found: {artifact_id}")
    if not row.get("saved"):
        store.update_by_id(artifact_id, {"saved": True, "saved_at": vault.now_iso()})
    return vault.get_item(item_id("artifact", artifact_id))


def unsave_artifact(artifact_id: str) -> dict:
    """Take an artifact out of the Vault. One whose conversation was already cleaned up
    (detached — see ``ArtifactStore.delete_for_session``) has no other home, so it is
    DELETED rather than left behind where nothing lists it and nothing ever frees it."""
    store = ArtifactStore()
    row = store.find_by_id(artifact_id) if artifact_id else None
    if row is None:
        raise NotFoundError(f"artifact not found: {artifact_id}")
    if row.get("detached_from_session") and not row.get("session_id"):
        store.delete_artifact(artifact_id)
        return {"artifact_id": artifact_id, "saved": False, "deleted": True}
    if row.get("saved"):
        store.update_by_id(artifact_id, {"saved": False, "saved_at": None})
    return {"artifact_id": artifact_id, "saved": False}


def delete_file(vid: str) -> dict:
    """Delete one of the user's own files for good — its record and bytes — wherever it came
    from. A kept chat upload is deleted from its conversation too (the chat can no longer open
    or read it), exactly as Settings → Uploaded files does. A receipt or statement made from
    it earlier is its own record and stays. The user's action only: no tool calls this."""
    from backend.services.attachments import delete_attachment

    kind, source_id = parse_item_id(vid)
    if kind != "file":
        raise ValidationError("only your own files can be deleted here")
    item = vault.get_item(vid)  # 404 before any write
    delete_attachment(source_id)
    return {"id": item["id"], "action": "deleted"}


def _delete_filed(item: dict) -> None:
    """Delete a statement, receipt or asset file — listed here on their own, so taking one
    out of Documents IS deleting it. A statement's transactions stay in the ledger (roll the
    import back to undo them); a receipt is taken off its transaction, which stays; an asset
    file is taken off its asset and, once nothing else holds it, its upload is deleted."""
    kind, source_id = item["kind"], item["source_id"]
    if kind == "statement":
        from backend.db.statement_store import StatementStore

        if not StatementStore().delete_statement(source_id):
            raise NotFoundError(f"no document {item['id']!r}")
    elif kind == "receipt":
        from backend.services.receipts import apply_receipt_change

        apply_receipt_change({"type": "delete", "receipt_id": source_id, "force": True})
    else:
        from backend.services import owned_assets

        owned_assets.apply_owned_asset_change(
            {"op": "remove_doc", "id": item["asset_id"], "ref_id": source_id}
        )


def remove_item(vid: str) -> dict:
    """Take one item out of the Vault. Returns ``{"id", "action"}`` where ``action`` is
    ``"deleted"`` (a file whose only home was the Vault; a statement, receipt or asset file —
    see ``_delete_filed``) or ``"removed"`` (the record stays where it came from)."""
    from backend.services.attachments import delete_attachment

    kind, source_id = parse_item_id(vid)
    item = vault.get_item(vid)  # 404 before any write
    if kind == "artifact":
        deleted = unsave_artifact(source_id).get("deleted")
        return {"id": item["id"], "action": "deleted" if deleted else "removed"}
    if kind != "file":
        _delete_filed(item)
        return {"id": item["id"], "action": "deleted"}
    row = _require_attachment(source_id)
    if row.get("source_channel") == VAULT_CHANNEL:
        delete_attachment(source_id)
        return {"id": item["id"], "action": "deleted"}
    meta = dict(row.get("vault") or {})
    meta.update(saved=False, saved_at=None)
    AttachmentStore().update_by_id(source_id, {"vault": meta})
    return {"id": item["id"], "action": "removed"}


def unfile_item(vid: str) -> dict:
    """Take an item out of Documents WITHOUT deleting anything — the assistant's "unsave".

    Only two kinds can simply be un-filed: an artifact (the report stays in its conversation)
    and a file the user sent in a chat (it stays in that chat). Everything else — a statement,
    a receipt, an asset's file, a file added straight to Documents, an artifact whose
    conversation is gone — has Documents as its only home, so taking it out IS deleting it
    (``remove_item``). That is the user's action alone: refused here with a reason."""
    kind, source_id = parse_item_id(vid)
    item = vault.get_item(vid)  # 404 before any write
    only_here = "removing it from Documents would delete it — only the user can, in Documents"
    if kind == "artifact":
        row = ArtifactStore().find_by_id(source_id) or {}
        if row.get("detached_from_session") and not row.get("session_id"):
            raise ValidationError(f"{item['title']!r}: its conversation is gone, so {only_here}")
        unsave_artifact(source_id)
        return {"id": item["id"], "action": "removed"}
    if kind != "file":
        raise ValidationError(f"{item['title']!r} is a {kind}: {only_here}")
    row = _require_attachment(source_id)
    if row.get("source_channel") == VAULT_CHANNEL:
        raise ValidationError(f"{item['title']!r} was added to Documents directly: {only_here}")
    meta = dict(row.get("vault") or {})
    meta.update(saved=False, saved_at=None)
    AttachmentStore().update_by_id(source_id, {"vault": meta})
    return {"id": item["id"], "action": "removed"}
