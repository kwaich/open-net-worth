"""The Vault — shipped to users as **Documents** — one place for the files a user keeps (ADR
2026-09-26-vault-is-a-view-over-existing-stores).

The Vault owns NO store of its own. It is a read model over four existing stores, each
keeping its own lifecycle:

  kind       source                        how a record gets in
  ---------  ----------------------------  ------------------------------------------------
  file       attachments (``vault`` meta)  added in the Vault, or a chat upload kept on purpose
  artifact   artifacts (``saved`` flag)    "Save to Documents" on something Lucius drew
  statement  statements                    every imported statement, automatically
  receipt    receipts                      every transaction receipt, automatically
  asset      owned assets' ``docs``        every file attached to an asset (services/vault_assets.py)

One copy: a Vault item IS the underlying record — a statement here is the same statement
Accounts lists; nothing is duplicated. Vault metadata rides on the record's schemaless
payload, so no migration, and export/import carry it untouched.

Private (files only) keeps a file's CONTENTS away from Lucius: every AI read path asks
``is_private_attachment`` and refuses. The user can still open and download it. A receipt
promoted from a file that is now Private is the same bytes, so it is Private too.

Every item may carry a ``note`` — the user's own words about it — and ``tags``, the user's way
of grouping items across kinds instead of folders (``vault_changes.set_note``/``set_tags``),
kept on its record like its name. They are metadata, not contents: Lucius reads them, Private
or not, and search matches them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html.parser import HTMLParser

from backend.core.errors import NotFoundError, ValidationError
from backend.db.artifact_store import ArtifactStore
from backend.db.attachment_store import AttachmentStore
from backend.db.payee_store import PayeeStore
from backend.db.receipt_store import ReceiptStore
from backend.db.statement_store import StatementStore
from backend.db.transaction_receipt_store import TransactionReceiptStore
from backend.services import vault_assets

log = logging.getLogger(__name__)

KINDS = ("file", "artifact", "statement", "receipt", "asset")
MIN_YEAR, MAX_YEAR = 1900, 2100
MAX_TITLE = 160
MAX_NOTE = 1000
MAX_TAGS, MAX_TAG = 10, 32
# Written over a Private file's row ``summary``, which is derived from its contents (a CSV's
# first row, for one) and readable wherever the row is — get_uploads, run_sql_query.
PRIVATE_SUMMARY = "Private file — contents hidden"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def clean_tags(tags: object) -> list[str]:
    """Tags as the user means them: spaces tidied, a leading "#" dropped, each tag once
    whatever its case (the first spelling wins), in the order given. Too many tags, or one too
    long, is refused — never cut."""
    if tags is None:
        return []
    if not isinstance(tags, (list, tuple)):
        raise ValidationError("tags must be a list")
    out: list[str] = []
    for raw in tags:
        tag = " ".join(str(raw or "").split()).lstrip("#").strip()
        if not tag or tag.casefold() in {t.casefold() for t in out}:
            continue
        if len(tag) > MAX_TAG:
            raise ValidationError(f"A tag can be up to {MAX_TAG} characters.")
        out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValidationError(f"An item can have up to {MAX_TAGS} tags.")
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def item_id(kind: str, source_id: str) -> str:
    """The Vault-wide id: ``<kind>-<source id>`` (a source id never starts with a kind)."""
    return f"{kind}-{source_id}"


def parse_item_id(vid: str) -> tuple[str, str]:
    kind, _, source_id = (vid or "").partition("-")
    if kind not in KINDS or not source_id:
        raise ValidationError(f"not a document id: {vid!r}")
    return kind, source_id


def _year_of(date_text: str | None) -> int | None:
    try:
        return int(str(date_text)[:4]) if date_text else None
    except ValueError:
        return None


def _month_label(date_text: str) -> str:
    try:
        return f"{_MONTHS[int(date_text[5:7]) - 1]} {date_text[:4]}"
    except (ValueError, IndexError):
        return date_text


# ── read model ─────────────────────────────────────────────────────────────────


def _file_item(row: dict) -> dict | None:
    vault = row.get("vault") or {}
    if not vault.get("saved"):
        return None
    aid = row["id"]
    return {
        "id": item_id("file", aid),
        "kind": "file",
        "source_id": aid,
        "title": vault.get("title")
        or vault_assets.display_stem(row.get("filename"))
        or "Untitled file",
        "filename": row.get("filename") or "",
        "media_type": row.get("media_type"),
        "size_bytes": row.get("size_bytes"),
        "added_at": vault.get("saved_at") or row.get("created_at"),
        "year": vault.get("year"),
        "private": bool(vault.get("private")),
        "note": vault.get("note") or None,
        "tags": list(vault.get("tags") or []),
        # Where the file came from decides what removing it means (vault_changes.remove_item).
        "origin": "vault" if row.get("source_channel") == "vault" else "chat",
        "file_url": f"/api/uploads/{aid}/file",
    }


def _artifact_item(row: dict) -> dict | None:
    if not row.get("saved"):
        return None
    aid = row["id"]
    kind = row.get("artifact_type") or "html"
    return {
        "id": item_id("artifact", aid),
        "kind": "artifact",
        "source_id": aid,
        "title": row.get("title") or "Artifact",
        "filename": row.get("filename") or "",
        "media_type": "image/svg+xml" if kind == "svg" else "text/html",
        "artifact_type": kind,
        "size_bytes": row.get("size_bytes"),
        "added_at": row.get("saved_at") or row.get("created_at"),
        "year": _year_of(row.get("created_at")),
        "private": False,
        "note": row.get("note") or None,
        "tags": list(row.get("tags") or []),
        # Its conversation was cleaned up while it was saved: the Vault is its only home now.
        "detached": bool(row.get("detached_from_session")),
        "file_url": None,
    }


def _statement_item(row: dict, account_names: dict[str, str]) -> dict:
    sid = row["id"]
    meta = row.get("import_meta") or {}
    date = meta.get("statement_date") or meta.get("period_end")
    account = account_names.get(row.get("account_id") or "")
    title = f"{account} — {_month_label(date)}" if account and date else None
    return {
        "id": item_id("statement", sid),
        "kind": "statement",
        "source_id": sid,
        "title": row.get("title")
        or title
        or vault_assets.display_stem(row.get("filename"))
        or "Statement",
        "filename": row.get("filename") or "",
        "media_type": row.get("mime") or None,
        "size_bytes": row.get("size_bytes"),
        "added_at": row.get("created_at"),
        "year": _year_of(date or row.get("created_at")),
        "account_id": row.get("account_id"),
        "account_name": account,
        "statement_date": date,
        "private": False,
        "note": row.get("note") or None,
        "tags": list(row.get("tags") or []),
        "file_url": f"/api/statements/{sid}/file",
    }


def _receipt_item(
    row: dict, first_txn: dict | None, payee_names: dict[str, str], private: bool
) -> dict:
    rid = row["id"]
    what = None
    if first_txn:
        what = payee_names.get(first_txn.get("payee_id") or "") or first_txn.get("description")
    return {
        "id": item_id("receipt", rid),
        "kind": "receipt",
        "source_id": rid,
        "title": row.get("title")
        or (f"Receipt — {what}" if what else "")
        or vault_assets.display_stem(row.get("filename"))
        or "Receipt",
        "filename": row.get("filename") or "",
        "media_type": row.get("media_type"),
        "size_bytes": row.get("size_bytes"),
        "added_at": row.get("created_at"),
        "year": _year_of((first_txn or {}).get("date") or row.get("created_at")),
        "transaction_date": (first_txn or {}).get("date"),
        # The transaction it documents — the panel links straight to it (/accounts?txn=).
        "transaction_id": (first_txn or {}).get("id"),
        # The same bytes as the chat file it was promoted from — Private if that file is.
        "private": private,
        "note": row.get("note") or None,
        "tags": list(row.get("tags") or []),
        "file_url": f"/api/receipts/{rid}/file",
    }


def _live_txn(transaction_id: str | None) -> dict | None:
    from backend.services.ledger import transactions as txn_store

    txn = txn_store.find_by_id(transaction_id) if transaction_id else None
    return txn if txn and not txn.get("deleted") else None


def _receipt_items(private_ids: set[str]) -> list[dict]:
    first_link: dict[str, str] = {}
    for link in TransactionReceiptStore().all():
        first_link.setdefault(link["receipt_id"], link["transaction_id"])
    payee_names = {p["id"]: p.get("name") or "" for p in PayeeStore().all()}
    return [
        _receipt_item(
            row,
            _live_txn(first_link.get(row["id"])),
            payee_names,
            row.get("source_attachment_id") in private_ids,
        )
        for row in ReceiptStore().list_all()
    ]


def _account_names() -> dict[str, str]:
    from backend.services.accounts import all_account_rows

    return {a["id"]: a.get("name") or "" for a in all_account_rows()}


def _all_items() -> list[dict]:
    names = _account_names()
    attachments = AttachmentStore().all()
    private_ids = {r["id"] for r in attachments if (r.get("vault") or {}).get("private")}
    private_ids |= vault_assets.private_asset_doc_ids()
    items = [i for i in map(_file_item, attachments) if i]
    items += [i for i in map(_artifact_item, ArtifactStore().all()) if i]
    items += [_statement_item(r, names) for r in StatementStore().list_catalog()]
    items += _receipt_items(private_ids)
    items += vault_assets.asset_doc_items({r["id"]: r for r in attachments})
    return sorted(items, key=lambda i: i.get("added_at") or "", reverse=True)


# ── one item, by a point read of its own store (never the four-store scan) ──────


def _one_file(attachment_id: str) -> dict | None:
    row = AttachmentStore().find_meta(attachment_id)
    return _file_item(row) if row else None


def _one_artifact(artifact_id: str) -> dict | None:
    row = ArtifactStore().find_by_id(artifact_id)
    return _artifact_item(row) if row else None


def _one_statement(statement_id: str) -> dict | None:
    rows = StatementStore().list_catalog(statement_id)
    return _statement_item(rows[0], _account_names()) if rows else None


def _one_receipt(receipt_id: str) -> dict | None:
    row = ReceiptStore().find_by_id(receipt_id)
    if row is None:
        return None
    links = TransactionReceiptStore().by_receipt(receipt_id)
    txn = _live_txn(links[0]["transaction_id"]) if links else None
    payee = PayeeStore().find_by_id((txn or {}).get("payee_id") or "") if txn else None
    names = {payee["id"]: payee.get("name") or ""} if payee else {}
    private = is_private_attachment(row.get("source_attachment_id") or "")
    return _receipt_item(row, txn, names, private)


_ONE = {"file": _one_file, "artifact": _one_artifact, "statement": _one_statement}
_ONE["receipt"] = _one_receipt
_ONE["asset"] = vault_assets.one_asset_doc


def list_items(
    *,
    kind: str | None = None,
    year: int | None = None,
    tag: str | None = None,
    query: str | None = None,
) -> list[dict]:
    """Every Vault item, newest first, optionally filtered. A bad ``kind`` RAISES rather
    than silently matching nothing (an empty answer would read as "the user has no such
    files")."""
    if kind is not None and kind not in KINDS:
        raise ValidationError(f"kind must be one of {KINDS}")
    needle = (query or "").strip().lower()
    wanted = (tag or "").strip().lstrip("#").casefold()
    out = []
    for item in _all_items():
        if kind and item["kind"] != kind:
            continue
        if year and item.get("year") != year:
            continue
        if wanted and wanted not in {t.casefold() for t in item["tags"]}:
            continue
        fields = (item["title"], item.get("filename"), item.get("note"), *item["tags"])
        haystack = " ".join(f or "" for f in fields).lower()
        if needle and needle not in haystack:
            continue
        out.append(item)
    return out


def counts(items: list[dict]) -> dict[str, int]:
    tally = {"all": len(items), **{k: 0 for k in KINDS}}
    for item in items:
        tally[item["kind"]] += 1
    return tally


def get_item(vid: str) -> dict:
    kind, source_id = parse_item_id(vid)
    item = _ONE[kind](source_id)
    if item is None:
        raise NotFoundError(f"no document {vid!r}")
    return item


def private_attachment_ids() -> set[str]:
    """Every Private file at once (Documents' switch or an asset's "Lucius can read" off) —
    for a listing that would otherwise ask ``is_private_attachment`` row by row."""
    kept = {r["id"] for r in AttachmentStore().all() if (r.get("vault") or {}).get("private")}
    return kept | vault_assets.private_asset_doc_ids()


def is_private_attachment(attachment_id: str) -> bool:
    """True when the user marked this file Private — in Documents, or by switching "Lucius
    can read" off on an asset it is attached to. Every AI read path refuses it."""
    row = AttachmentStore().find_meta(attachment_id) if attachment_id else None
    if row is None:
        return False
    if (row.get("vault") or {}).get("private"):
        return True
    return vault_assets.is_private_asset_doc(attachment_id)


# ── what an AI read needs ──────────────────────────────────────────────────────

PRIVATE_REFUSAL = (
    "This file is marked Private, so its contents are off limits to you. You can say it "
    "exists, but not what is in it — the user can switch Private off in Documents if they "
    "want you to read it."
)


class _TextOnly(HTMLParser):
    """The visible text of an artifact's HTML — scripts and styles dropped."""

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping and data.strip():
            self._parts.append(" ".join(data.split()))

    def text(self) -> str:
        return "\n".join(self._parts)


def html_to_text(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def read_source(vid: str) -> dict:
    """What an AI read of one Vault item needs — ``item`` plus exactly one of:
    ``text`` (already text), ``entry`` (a parsed file entry for the configured reader, with
    its ``content_hash``), ``error``, or — for a Private file — ``refused``, with NO
    content at all. The refusal lives here, once, so no Vault read path can forget it."""
    item = get_item(vid)
    if item.get("private"):
        return {"item": item, "refused": PRIVATE_REFUSAL}
    kind, source_id = item["kind"], item["source_id"]
    if kind == "artifact":
        html = ArtifactStore().read_text(source_id)
        if html is None:
            return {"item": item, "error": "the artifact's document is missing"}
        return {"item": item, "text": html_to_text(html)}
    if kind in ("file", "asset"):
        return _file_source(item, source_id)
    if kind == "receipt":
        return _receipt_source(item, source_id)
    record = StatementStore().find_by_id(source_id) or {}
    if not record.get("upload_entry"):
        return {"item": item, "error": "no readable copy of this statement is stored"}
    return {
        "item": item,
        "entry": record["upload_entry"],
        "content_hash": record.get("file_hash"),
        "parsed_row_count": len(record.get("parsed_rows") or []),
    }


def _file_source(item: dict, attachment_id: str) -> dict:
    from backend.services.attachments import get_attachment_for_model, get_attachment_meta

    entry = get_attachment_for_model(attachment_id)
    if not entry:
        return {"item": item, "error": "the file's stored copy is missing"}
    meta = get_attachment_meta(attachment_id) or {}
    return {"item": item, "entry": entry, "content_hash": meta.get("content_hash")}


def _receipt_source(item: dict, receipt_id: str) -> dict:
    from backend.services.attachment_parse import parse_upload

    store = ReceiptStore()
    content = store.read_bytes(receipt_id)
    if not content:
        return {"item": item, "error": "the receipt's stored file is missing"}
    try:
        entry = parse_upload(item["filename"], item.get("media_type") or "", content)
    except ValueError as exc:
        return {"item": item, "error": str(exc)}
    row = store.find_by_id(receipt_id) or {}
    return {"item": item, "entry": entry, "content_hash": row.get("content_hash")}
