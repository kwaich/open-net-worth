"""Descriptor for the ``document`` mentionable type — one item in the user's Documents
(internally the Vault).

Rows come from the read model (``services/vault.list_items``: kept files, saved artifacts,
statements, receipts); the stable id is ``<kind>-<source id>``. "Ask Lucius about this" is
this mention: it hands Lucius the item's METADATA (the user's note included) plus how to
read it — never the contents.
A Private item says so instead, and ``read_documents`` refuses it anyway
(``services.vault.read_source``).
"""

from __future__ import annotations

from backend.core.errors import OpenNetWorthError
from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery

_KIND_LABEL = {"file": "Upload", "artifact": "Saved artifact", "statement": "Statement"}
_KIND_LABEL["receipt"] = "Receipt"
_KIND_LABEL["asset"] = "Asset file"


def _with_contents(item: dict) -> dict:
    how = (
        "Private — off limits to Lucius"
        if item.get("private")
        else f"read with read_documents(document_ids=['{item['id']}'])"
    )
    tags = ", ".join(item.get("tags") or []) or None
    return {**item, "tags": tags, "contents": how}


def _vault_rows(query: SearchQuery) -> list[dict]:
    from backend.services import vault

    return [_with_contents(i) for i in vault.list_items()]


def _vault_find(ref: str) -> dict | None:
    from backend.services import vault

    try:
        return _with_contents(vault.get_item(ref))
    except OpenNetWorthError:
        return None


def _vault_sublabel(row: dict) -> str:
    head = _KIND_LABEL.get(row.get("kind") or "", "")
    if row.get("kind") == "asset" and row.get("asset_name"):
        head = f"{head} · {row['asset_name']}"
    when = row.get("statement_date") or row.get("transaction_date") or row.get("year") or ""
    tail = " • Private" if row.get("private") else ""
    return f"{head} • {when}".strip(" •") + tail


register(
    EntityDescriptor(
        type="document",
        label="Document",
        icon="▥",
        order=36,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_vault_rows,
        find_one=_vault_find,
        id_field="id",
        label_field="title",
        search_fields=("title", "filename", "account_name", "asset_name", "note", "tags"),
        sublabel_fn=_vault_sublabel,
        fields={
            "id": FieldSpec("Vault id", show_in_popover=False),
            "title": FieldSpec("Title"),
            "kind": FieldSpec("Kind", "enum"),
            "filename": FieldSpec("File name"),
            "year": FieldSpec("Year"),
            "added_at": FieldSpec("Added", "date"),
            "account_name": FieldSpec("Account"),
            "asset_name": FieldSpec("Asset"),
            "statement_date": FieldSpec("Statement date", "date"),
            "transaction_date": FieldSpec("Transaction date", "date"),
            "private": FieldSpec("Private"),
            "note": FieldSpec("Note"),
            "tags": FieldSpec("Tags"),
            "contents": FieldSpec("Contents", show_in_popover=False),
        },
    )
)
