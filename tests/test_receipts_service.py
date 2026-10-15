"""services/receipts.py — the write choke for transaction receipts (cross-channel-
attachments RFC section 2.2/2.3, items 23/24). Covers the acceptance-matrix "Receipt
promotion" row: promote-from-attachment (with idempotent re-promotion), the m:n
attach-existing case, unlink (receipt survives), delete refuse/force, audit-context
capture, and "deleting the source chat attachment never deletes the receipt".
"""

from __future__ import annotations

import hashlib
import threading

import pytest

from backend.core import write_receipt as collect
from backend.core.audit_context import clear_audit_scope, set_audit_scope
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.services import ledger as svc
from backend.services.receipts import (
    apply_receipt_change,
    receipt_store,
    receipts_for_transaction,
    transactions_for_receipt,
)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as att_mod
    import backend.db.receipt_store as rcpt_mod
    from backend.services import attachments as attachments_svc

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(att_mod, "ATTACHMENTS_DIR", att_dir)
    rcpt_dir = tmp_path / "receipts"
    rcpt_dir.mkdir()
    monkeypatch.setattr(rcpt_mod, "RECEIPTS_DIR", rcpt_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-01",
            "description": "Groceries",
            "category": None,
            "from_account_id": "acc_chk",
            "from_amount": 42.0,
            "from_currency": "SGD",
        }
    )
    txn2 = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-02",
            "description": "More groceries",
            "category": None,
            "from_account_id": "acc_chk",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    return {"txn_id": txn["id"], "txn2_id": txn2["id"]}


def _minimal_scanned_pdf_bytes() -> bytes:
    """A real, minimal PDF with a blank page and no text layer — what pypdf/fitz see for an
    actually-scanned/photographed receipt. Same construction as
    tests/test_statements_parser.py::test_textless_pdf_routes_to_vision_pages."""
    import fitz

    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _upload_attachment(content: bytes = b"jpeg-bytes", filename: str = "receipt.jpg") -> str:
    """Go through the REAL ingress path (parse_upload + persist_inbound_file), not a
    hand-built entry dict — a hand-built ``{"kind": "image", "content": ...}`` entry is a
    shape parse_upload never actually produces for an image (it stores base64 ``data``, not
    raw ``content`` bytes); a fixture that skips the real parser silently masked a real 422
    on every image-receipt promotion (security review finding, HIGH — see
    test_promote_from_attachment_image_kind_has_no_content_key below)."""
    from backend.services.attachment_ingress import persist_inbound_file

    result = persist_inbound_file(filename, "image/jpeg", content, source_channel="chat:web")
    return result["attachment_id"]


# ── promote_from_attachment ──────────────────────────────────────────────────────


def test_promote_from_attachment_creates_receipt_and_links(env):
    att_id = _upload_attachment()
    result = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    assert result["receipt"]["source_attachment_id"] == att_id
    assert result["link"]["transaction_id"] == env["txn_id"]
    assert result["link"]["receipt_id"] == result["receipt"]["id"]
    assert receipt_store.read_bytes(result["receipt"]["id"]) == b"jpeg-bytes"


def test_promote_from_attachment_image_kind_has_no_content_key(env):
    """Regression (security review, HIGH): ``attachment_parse.parse_upload``'s image branch
    stores base64 ``data``, never raw ``content`` bytes — the shape every OTHER kind
    (text/structured-format) uses. Promoting an actual photographed receipt — the feature's
    primary real-world case — always raised ValidationError ("no raw bytes to promote")
    until ``_receipt_bytes_from_entry`` learned to fall back to ``data``. A prior version of
    this test file's own ``_upload_attachment`` fixture hand-built a ``content``-bearing
    entry that the real parser never produces, silently hiding this."""
    from backend.services.attachment_ingress import persist_inbound_file

    result = persist_inbound_file(
        "photo.jpg", "image/jpeg", b"raw-jpeg-bytes", source_channel="chat:web"
    )
    out = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": result["attachment_id"],
            "transaction_id": env["txn_id"],
        }
    )
    assert receipt_store.read_bytes(out["receipt"]["id"]) == b"raw-jpeg-bytes"
    assert out["receipt"]["media_type"] == "image/jpeg"


def test_promote_same_attachment_twice_is_idempotent(env):
    att_id = _upload_attachment()
    first = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    second = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    assert first["receipt"]["id"] == second["receipt"]["id"], "re-promotion must reuse the receipt"
    assert first["link"]["id"] == second["link"]["id"], "re-linking the same pair must be a no-op"
    assert len(receipts_for_transaction(env["txn_id"])) == 1


def test_promote_without_transaction_id_creates_unlinked_receipt(env):
    att_id = _upload_attachment()
    result = apply_receipt_change({"type": "promote_from_attachment", "attachment_id": att_id})
    assert result["link"] is None
    assert transactions_for_receipt(result["receipt"]["id"]) == []


def test_promote_requires_existing_transaction(env):
    att_id = _upload_attachment()
    with pytest.raises(NotFoundError):
        apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": att_id,
                "transaction_id": "txn_does_not_exist",
            }
        )


def test_promote_requires_existing_attachment(env):
    with pytest.raises(NotFoundError):
        apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": "att_does_not_exist",
                "transaction_id": env["txn_id"],
            }
        )


def test_promote_requires_attachment_id(env):
    with pytest.raises(ValidationError):
        apply_receipt_change({"type": "promote_from_attachment", "transaction_id": env["txn_id"]})


def test_promote_scanned_pdf_stores_the_real_original_bytes(env):
    """Regression (security review, MEDIUM — two rounds): a scanned, text-less PDF
    (kind="pdf_pages") is parsed into per-page rendered PNGs for Lucius to READ, but the
    original PDF bytes are ALSO kept on the entry now (attachment_parse.py fix) — so
    promotion stores the real file, not a lossy first-page substitute. An earlier version of
    this code either silently promoted just page 1 (wrong content_hash, dropped pages,
    unviewable result) or refused outright; both are gone now that the actual bytes survive
    parsing, same as every other PDF kind."""
    from backend.services.attachment_ingress import persist_inbound_file

    # A real fitz-renderable scanned PDF (no text layer) — invoked through the real parser,
    # not a hand-built entry, so this proves attachment_parse's OWN behavior, not a fixture.
    scanned_pdf_bytes = _minimal_scanned_pdf_bytes()
    result = persist_inbound_file(
        "invoice.pdf", "application/pdf", scanned_pdf_bytes, source_channel="chat:web"
    )
    assert result["kind"] == "pdf_pages"

    out = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": result["attachment_id"],
            "transaction_id": env["txn_id"],
        }
    )
    assert receipt_store.read_bytes(out["receipt"]["id"]) == scanned_pdf_bytes
    assert out["receipt"]["media_type"] == "application/pdf"
    assert out["receipt"]["content_hash"] == hashlib.sha256(scanned_pdf_bytes).hexdigest()


# ── m:n: one receipt, multiple transactions ──────────────────────────────────────


def test_attach_existing_links_receipt_to_a_second_transaction(env):
    att_id = _upload_attachment()
    first = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    receipt_id = first["receipt"]["id"]
    apply_receipt_change(
        {"type": "attach_existing", "receipt_id": receipt_id, "transaction_id": env["txn2_id"]}
    )
    assert sorted(transactions_for_receipt(receipt_id)) == sorted([env["txn_id"], env["txn2_id"]])
    assert receipts_for_transaction(env["txn_id"])[0]["receipt_id"] == receipt_id
    assert receipts_for_transaction(env["txn2_id"])[0]["receipt_id"] == receipt_id


def test_attach_existing_requires_real_receipt_and_transaction(env):
    with pytest.raises(NotFoundError):
        apply_receipt_change(
            {"type": "attach_existing", "receipt_id": "rcpt_nope", "transaction_id": env["txn_id"]}
        )


# ── unlink (receipt survives) ────────────────────────────────────────────────────


def test_unlink_removes_link_but_receipt_survives(env):
    att_id = _upload_attachment()
    promoted = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    receipt_id = promoted["receipt"]["id"]
    apply_receipt_change(
        {"type": "unlink", "transaction_id": env["txn_id"], "receipt_id": receipt_id}
    )
    assert receipts_for_transaction(env["txn_id"]) == []
    assert receipt_store.find_by_id(receipt_id) is not None, "unlink must never delete the receipt"
    assert receipt_store.read_bytes(receipt_id) == b"jpeg-bytes"


def test_unlink_unknown_pair_raises(env):
    with pytest.raises(NotFoundError):
        apply_receipt_change(
            {"type": "unlink", "transaction_id": env["txn_id"], "receipt_id": "rcpt_nope"}
        )


# ── delete (refuse while linked, force removes evidence explicitly) ─────────────


def test_delete_refuses_while_linked(env):
    att_id = _upload_attachment()
    promoted = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    receipt_id = promoted["receipt"]["id"]
    with pytest.raises(ConflictError) as excinfo:
        apply_receipt_change({"type": "delete", "receipt_id": receipt_id})
    assert env["txn_id"] in str(excinfo.value)
    assert receipt_store.find_by_id(receipt_id) is not None


def test_delete_with_force_removes_links_and_receipt(env):
    att_id = _upload_attachment()
    promoted = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    receipt_id = promoted["receipt"]["id"]
    blob = receipt_store.blob_path(receipt_id)
    assert blob.exists()
    result = apply_receipt_change({"type": "delete", "receipt_id": receipt_id, "force": True})
    assert result["unlinked_transaction_ids"] == [env["txn_id"]]
    assert receipt_store.find_by_id(receipt_id) is None
    assert not blob.exists()
    assert receipts_for_transaction(env["txn_id"]) == []


def test_delete_unlinked_receipt_needs_no_force(env):
    att_id = _upload_attachment()
    promoted = apply_receipt_change({"type": "promote_from_attachment", "attachment_id": att_id})
    receipt_id = promoted["receipt"]["id"]
    result = apply_receipt_change({"type": "delete", "receipt_id": receipt_id})
    assert result["deleted"] == receipt_id


# ── deleting the source chat attachment never deletes the receipt ───────────────


def test_deleting_source_attachment_preserves_the_receipt(env):
    from backend.services.attachments import delete_attachment

    att_id = _upload_attachment()
    promoted = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    receipt_id = promoted["receipt"]["id"]

    assert delete_attachment(att_id) is True

    assert receipt_store.find_by_id(receipt_id) is not None
    assert receipt_store.read_bytes(receipt_id) == b"jpeg-bytes"
    assert receipts_for_transaction(env["txn_id"])[0]["receipt_id"] == receipt_id


# ── audit context ─────────────────────────────────────────────────────────────


def test_attach_captures_the_active_audit_scope(env):
    att_id = _upload_attachment()
    token = set_audit_scope("user", "chat:web", session_id="sess_123", intent="save this receipt")
    try:
        result = apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": att_id,
                "transaction_id": env["txn_id"],
            }
        )
    finally:
        clear_audit_scope(token)
    audit = result["link"]["audit_context"]
    assert audit["actor"] == "user"
    assert audit["surface"] == "chat:web"
    assert audit["session_id"] == "sess_123"


def test_attach_with_no_active_scope_stores_empty_audit_context(env):
    att_id = _upload_attachment()
    result = apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    assert result["link"]["audit_context"] == {}


# ── transaction-detail read (item 26) ────────────────────────────────────────────


def test_get_transaction_includes_receipts(env):
    att_id = _upload_attachment()
    apply_receipt_change(
        {
            "type": "promote_from_attachment",
            "attachment_id": att_id,
            "transaction_id": env["txn_id"],
        }
    )
    txn = svc.get_transaction(env["txn_id"])
    assert len(txn["receipts"]) == 1
    assert txn["receipts"][0]["filename"] == "receipt.jpg"


def test_get_transaction_receipts_empty_by_default(env):
    txn = svc.get_transaction(env["txn2_id"])
    assert txn["receipts"] == []


# ── RFC acceptance matrix: "Receipt promotion" row, walked end to end ───────────


def test_acceptance_matrix_receipt_promotion_flow(env):
    """Verbatim proof of the RFC's own acceptance criterion: 'create a transaction from
    two clear receipt images, reload, retrieve both independent receipt records from
    transaction detail, delete the source chat attachments without losing the receipts,
    then attach/unlink another receipt with audit evidence intact.'

    'Reload' is exercised by re-reading through the SAME durable service functions a
    fresh request would use (get_transaction / receipts_for_transaction) rather than
    trusting an in-process variable — the DB round-trip through PayloadTableStore is the
    real proof, matching how every other Wave 5 restart/reload regression in this repo is
    written (there is no in-process cache anywhere on this read path to accidentally paper
    over a persistence bug)."""
    from backend.services.attachments import delete_attachment

    # Step 1: two clear receipt images arrive as chat attachments and are explicitly
    # promoted+linked to the SAME transaction (two independent artifacts, one purchase).
    att_a = _upload_attachment(content=b"receipt-photo-A", filename="front.jpg")
    att_b = _upload_attachment(content=b"receipt-photo-B", filename="back.jpg")
    token = set_audit_scope("user", "chat:web", session_id="sess_accept", intent="save both")
    try:
        promoted_a = apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": att_a,
                "transaction_id": env["txn_id"],
            }
        )
        promoted_b = apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": att_b,
                "transaction_id": env["txn_id"],
            }
        )
    finally:
        clear_audit_scope(token)
    receipt_a_id = promoted_a["receipt"]["id"]
    receipt_b_id = promoted_b["receipt"]["id"]
    assert receipt_a_id != receipt_b_id, "two distinct artifacts must mint two distinct receipts"

    # Step 2: "reload" — re-read via the durable service path, not a held reference.
    reloaded = svc.get_transaction(env["txn_id"])
    reloaded_ids = {r["receipt_id"] for r in reloaded["receipts"]}
    assert reloaded_ids == {receipt_a_id, receipt_b_id}, (
        "both independent receipt records must be retrievable from transaction detail"
    )

    # Step 3: delete both source chat attachments — the receipts and their bytes survive.
    assert delete_attachment(att_a) is True
    assert delete_attachment(att_b) is True
    still_there = svc.get_transaction(env["txn_id"])
    assert {r["receipt_id"] for r in still_there["receipts"]} == {receipt_a_id, receipt_b_id}
    assert receipt_store.read_bytes(receipt_a_id) == b"receipt-photo-A"
    assert receipt_store.read_bytes(receipt_b_id) == b"receipt-photo-B"

    # Step 4: attach a THIRD, already-existing receipt (simulating one that documents
    # another purchase too) to this same transaction, with audit evidence intact.
    third_txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-03",
            "description": "Shared bill",
            "category": None,
            "from_account_id": "acc_chk",
            "from_amount": 8.0,
            "from_currency": "SGD",
        }
    )
    token2 = set_audit_scope("user", "chat:web", session_id="sess_accept2", intent="link too")
    try:
        attach_result = apply_receipt_change(
            {
                "type": "attach_existing",
                "receipt_id": receipt_a_id,
                "transaction_id": third_txn["id"],
            }
        )
    finally:
        clear_audit_scope(token2)
    assert attach_result["link"]["audit_context"]["session_id"] == "sess_accept2"
    assert sorted(transactions_for_receipt(receipt_a_id)) == sorted(
        [env["txn_id"], third_txn["id"]]
    )

    # ...then unlink it from the original transaction — the link disappears, the receipt
    # (still supporting third_txn) and its audit trail on that surviving link stay intact.
    unlink_result = apply_receipt_change(
        {"type": "unlink", "transaction_id": env["txn_id"], "receipt_id": receipt_a_id}
    )
    assert unlink_result["transaction_id"] == env["txn_id"]
    final = svc.get_transaction(env["txn_id"])
    assert {r["receipt_id"] for r in final["receipts"]} == {receipt_b_id}
    assert transactions_for_receipt(receipt_a_id) == [third_txn["id"]]
    surviving_link = receipts_for_transaction(third_txn["id"])[0]
    assert surviving_link["receipt_id"] == receipt_a_id


# ── concurrency: promotion idempotency must hold under a genuine race ───────────


def test_concurrent_promotion_of_the_same_attachment_creates_only_one_receipt(env):
    """The idempotency check (`_find_receipt_for_attachment`) must run INSIDE the same
    lock/transaction as the insert it guards. Before this fix the check ran before the
    lock was acquired and was never re-verified, so two near-simultaneous promotions of
    the same attachment_id could both observe "no existing receipt" and both insert —
    two rows for one attachment, contradicting this module's own idempotency claim."""
    att_id = _upload_attachment()
    n = 6
    barrier = threading.Barrier(n)
    results: list[dict] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            result = apply_receipt_change(
                {
                    "type": "promote_from_attachment",
                    "attachment_id": att_id,
                    "transaction_id": env["txn_id"],
                }
            )
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            with results_lock:
                errors.append(exc)
            return
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent promotion raised: {errors}"
    assert len(results) == n
    receipt_ids = {r["receipt"]["id"] for r in results}
    assert len(receipt_ids) == 1, f"the race minted separate receipts: {receipt_ids}"

    matching = [row for row in receipt_store.all() if row.get("source_attachment_id") == att_id]
    assert len(matching) == 1, "exactly one receipt row must exist for this attachment"
    assert len(receipts_for_transaction(env["txn_id"])) == 1, "the link must also be deduped"


# ── concurrency: a delete must never leave a dangling link ──────────────────────


def test_delete_refuses_when_a_link_lands_in_the_race_window(env, monkeypatch):
    """Simulates a concurrent `attach_existing` landing between `_delete`'s outer
    snapshot read (before the lock) and the lock being acquired. Before this fix the
    delete proceeded against the STALE (empty) snapshot, unlinked nothing, deleted the
    receipt, and left the newly created link dangling — pointing at evidence that no
    longer exists. The fix re-fetches the link set INSIDE the lock and must now refuse."""
    import backend.services.receipts as receipts_mod

    att_id = _upload_attachment()
    promoted = apply_receipt_change({"type": "promote_from_attachment", "attachment_id": att_id})
    receipt_id = promoted["receipt"]["id"]
    # Unlinked at this point, so `_delete`'s outer `if links and not force` check will
    # pass cleanly and proceed toward the lock.

    real_by_receipt = receipts_mod.link_store.by_receipt
    call_count = {"n": 0}

    def racing_by_receipt(rid: str):
        call_count["n"] += 1
        result = real_by_receipt(rid)
        if call_count["n"] == 1:
            # The FIRST call is `_delete`'s outer, pre-lock snapshot read. Right after it
            # observes "no links", race a concurrent attach_existing into the window.
            apply_receipt_change(
                {
                    "type": "attach_existing",
                    "receipt_id": receipt_id,
                    "transaction_id": env["txn_id"],
                }
            )
        return result

    monkeypatch.setattr(receipts_mod.link_store, "by_receipt", racing_by_receipt)

    with pytest.raises(ConflictError):
        apply_receipt_change({"type": "delete", "receipt_id": receipt_id})

    assert receipt_store.find_by_id(receipt_id) is not None, "a refused delete must not delete"
    assert receipts_for_transaction(env["txn_id"])[0]["receipt_id"] == receipt_id, (
        "the link that landed mid-race must survive intact, not dangle"
    )


# ── the write-receipt chip: receipts are a deliberate, tested exclusion ─────────


def test_receipt_writes_never_populate_the_write_receipt_chip(env):
    """`core.write_receipt` (the write-then-verify CHIP) is a separate mechanism from
    `/rewind` checkpointing despite the similar name — `receipts`/`transaction_receipts`
    are checkpoint-INCLUDED (reversed 2026-08-21), but `"receipt"` is still not a
    registered `services.mentions` type, so a call to `record_write` here would just be
    silently dropped by the reader rather than rendering a chip. This choke does not call
    `record_write` for that reason; see the module docstring in `services/receipts.py`."""
    att_id = _upload_attachment()
    token = collect.open_receipt()
    try:
        promoted = apply_receipt_change(
            {
                "type": "promote_from_attachment",
                "attachment_id": att_id,
                "transaction_id": env["txn_id"],
            }
        )
        receipt_id = promoted["receipt"]["id"]
        apply_receipt_change(
            {"type": "attach_existing", "receipt_id": receipt_id, "transaction_id": env["txn2_id"]}
        )
        apply_receipt_change(
            {"type": "unlink", "transaction_id": env["txn2_id"], "receipt_id": receipt_id}
        )
        apply_receipt_change({"type": "delete", "receipt_id": receipt_id, "force": True})
        recorded = collect.collected()
    finally:
        collect.close_receipt(token)

    assert recorded == []
