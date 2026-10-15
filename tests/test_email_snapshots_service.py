"""EM1 — services.email_snapshots unit tests.

Covers: stash_snapshots sanitizes + best-effort-per-row, promote_for_email parses/
no-ops, snapshot_for_transaction's 4 outcomes (email txn w/ snapshot, email txn w/o
snapshot, non-email txn, missing txn).

Snapshots are keyed by the SHORT INTERNAL id (``core.email_ids.internal_email_id`` of the
raw Message-ID) — the same id the model books as ``external_ref`` (ADR 2026-07-20, refined).
The stash still receives the RAW header on ``message_id``; the stored KEY (and the row's
``message_id`` FIELD) is the internal id, and the raw header rides along as ``raw_message_id``.

Isolated: both stores are DB-backed — db_isolated redirects them to a fresh, empty
database.
"""

from __future__ import annotations

from backend.core.email_ids import internal_email_id
from backend.services import email_snapshots as svc
from backend.services import ledger


def _isolate(tmp_path, monkeypatch):
    # services/email_snapshots.py holds its own module-level singleton store for snapshots
    # (DB-backed, plan 4e task 1). snapshot_for_transaction() reads the transaction through
    # services.ledger's OWN `transactions` singleton, which is DB-backed too (plan 3 task
    # 4); every caller of this helper also requests the `db_isolated` fixture, which
    # redirects the process-wide DB singleton before the test body runs — no per-instance
    # isolation needed for either store here.
    # from_account_id is now a real FK onto accounts.id — every test in this file that books a
    # transaction uses the same synthetic "acc_1" id, so stub it here once rather than at each
    # of the six call sites (harmless no-op for tests that never create a transaction).
    from tests.conftest import seed_stub_accounts

    seed_stub_accounts([{"from_account_id": "acc_1"}])


# ── stash_snapshots ──────────────────────────────────────────────────────────


def test_stash_snapshots_sanitizes_control_chars(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    n = svc.stash_snapshots(
        [
            {
                "message_id": "<m-1@x>",
                "from": "alerts@bank.example",
                "subject": "Statement\x00 ready",
                "date": "2026-07-01",
                "body_text": "Amount\x07: $50.00\ncharged.\tOK",
            }
        ]
    )
    assert n == 1
    # Stored under the SHORT INTERNAL id key (internal_email_id of the raw header).
    row = svc._snapshots.get(internal_email_id("<m-1@x>"))
    assert row["message_id"] == internal_email_id("<m-1@x>")
    assert row["raw_message_id"] == "<m-1@x>"  # raw header preserved for the drawer/debug
    assert "\x00" not in row["subject"]
    assert "\x07" not in row["body_text"]
    # \n and \t are explicitly kept (sanitize_untrusted's documented behavior).
    assert "\n" in row["body_text"]
    assert "\t" in row["body_text"]
    assert row["referenced"] is False


def test_stash_snapshots_skips_rows_with_no_message_id(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    n = svc.stash_snapshots(
        [{"from": "x@y.com", "subject": "no id", "date": "2026-07-01", "body_text": "hi"}]
    )
    assert n == 0
    assert svc._snapshots.all() == []


def test_stash_snapshots_hashes_an_oversized_message_id_capping_raw(
    tmp_path, monkeypatch, db_isolated
):
    """Converted from the old skip-on-oversize test: the store KEY is now a fixed 12-hex
    internal id, so an oversized RAW header is NO LONGER skipped — it hashes to 12 chars and
    is stashed, with only the display-only ``raw_message_id`` capped at 512 chars (no
    unbounded storage). A normal-sized id in the SAME batch is stashed too."""
    _isolate(tmp_path, monkeypatch)
    oversized_id = "<" + ("a" * 600) + "@x>"
    normal_id = "<normal@x>"
    n = svc.stash_snapshots(
        [
            {
                "message_id": oversized_id,
                "from": "a@x.com",
                "subject": "s1",
                "date": "2026-07-01",
                "body_text": "b1",
            },
            {
                "message_id": normal_id,
                "from": "a@x.com",
                "subject": "s2",
                "date": "2026-07-01",
                "body_text": "b2",
            },
        ]
    )
    assert n == 2  # both stash now — the oversized one is hashed, not skipped
    row = svc._snapshots.get(internal_email_id(oversized_id))
    assert row is not None
    assert len(row["message_id"]) == 12
    assert row["message_id"] == internal_email_id(oversized_id)
    # The raw header is preserved but capped at 512 chars — no unbounded storage.
    assert row["raw_message_id"] == oversized_id[:512]
    assert len(row["raw_message_id"]) == 512
    assert svc._snapshots.get(internal_email_id(normal_id)) is not None


def test_stash_snapshots_preserves_a_raw_id_at_exactly_the_cap(tmp_path, monkeypatch, db_isolated):
    """A raw header exactly at the 512-char cap is stashed and preserved verbatim (uncapped)."""
    _isolate(tmp_path, monkeypatch)
    at_cap_id = "a" * 512
    n = svc.stash_snapshots(
        [
            {
                "message_id": at_cap_id,
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    assert n == 1
    row = svc._snapshots.get(internal_email_id(at_cap_id))
    assert row is not None
    assert row["raw_message_id"] == at_cap_id  # exactly 512 — preserved, not truncated


def test_stash_snapshots_one_bad_row_does_not_sink_the_batch(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    emails = [
        {
            "message_id": "<good-1@x>",
            "from": "a@x.com",
            "subject": "s1",
            "date": "2026-07-01",
            "body_text": "b1",
        },
        None,  # malformed row — .get() on None raises AttributeError, must be swallowed
        {
            "message_id": "<good-2@x>",
            "from": "a@x.com",
            "subject": "s2",
            "date": "2026-07-01",
            "body_text": "b2",
        },
    ]
    n = svc.stash_snapshots(emails)  # type: ignore[arg-type]
    assert n == 2
    # Stored under the internal-id keys.
    assert {r["message_id"] for r in svc._snapshots.all()} == {
        internal_email_id("<good-1@x>"),
        internal_email_id("<good-2@x>"),
    }


def test_stash_snapshots_dedupes_repeat_message_id_across_calls(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    email = {
        "message_id": "<m-1@x>",
        "from": "a@x.com",
        "subject": "s1",
        "date": "2026-07-01",
        "body_text": "b1",
    }
    assert svc.stash_snapshots([email]) == 1
    assert svc.stash_snapshots([email]) == 0  # already stashed — counted as 0 new
    assert len(svc._snapshots.all()) == 1


def test_stash_snapshots_received_at_prefers_date_time_over_date(
    tmp_path, monkeypatch, db_isolated
):
    """received_at carries hour+minute (date_time) when imap_client supplied it, not just
    the date-only fallback."""
    _isolate(tmp_path, monkeypatch)
    svc.stash_snapshots(
        [
            {
                "message_id": "<m-1@x>",
                "from": "a@x.com",
                "subject": "s1",
                "date": "2026-07-01",
                "date_time": "2026-07-01T10:15:00+00:00",
                "body_text": "b1",
            }
        ]
    )
    row = svc._snapshots.get(internal_email_id("<m-1@x>"))
    assert row["received_at"] == "2026-07-01T10:15:00+00:00"


def test_stash_snapshots_received_at_falls_back_to_date_when_no_date_time(
    tmp_path, monkeypatch, db_isolated
):
    """When date_time is absent (e.g. a malformed Date header), received_at falls back to
    the plain date-only value — unchanged pre-existing behavior."""
    _isolate(tmp_path, monkeypatch)
    svc.stash_snapshots(
        [
            {
                "message_id": "<m-2@x>",
                "from": "a@x.com",
                "subject": "s2",
                "date": "2026-07-01",
                "body_text": "b2",
            }
        ]
    )
    row = svc._snapshots.get(internal_email_id("<m-2@x>"))
    assert row["received_at"] == "2026-07-01"


# ── promote / promote_for_email ─────────────────────────────────────────


def test_promote_for_email_promotes_matching_snapshot(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    svc.stash_snapshots(
        [
            {
                "message_id": "<m-1@x>",
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    # external_ref is the internal id (the model books that, not the raw header).
    svc.promote_for_email("email", internal_email_id("<m-1@x>"))
    assert svc._snapshots.get(internal_email_id("<m-1@x>"))["referenced"] is True


def test_promote_for_email_noop_on_non_email_source(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    svc.promote_for_email("recurring", "task-1:2026-07-01")  # must not raise (non-email source)
    svc.promote_for_email(None, None)
    svc.promote_for_email(None, "")


def test_promote_for_email_noop_when_no_stashed_row_matches(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    # No snapshot was ever stashed for this id — must silently no-op, never raise.
    svc.promote_for_email("email", internal_email_id("<never-stashed@x>"))


def test_promote_for_email_noop_on_malformed_email_ref(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    svc.promote_for_email("email", "")  # empty ref


# ── snapshot_for_transaction ───────────────────────────────────────────────────


def test_snapshot_for_transaction_email_txn_with_snapshot(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    svc.stash_snapshots(
        [
            {
                "message_id": "<m-1@x>",
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    row = ledger.transactions.append(
        {
            "date": "2026-07-01",
            "kind": "expense",
            "from_account_id": "acc_1",
            "from_amount": 10.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": internal_email_id("<m-1@x>")}],
        }
    )
    result = svc.snapshots_for_transaction(row["id"])
    assert result is not None and len(result) == 1
    assert result[0]["message_id"] == internal_email_id("<m-1@x>")
    assert not result[0].get("unavailable")


def test_snapshot_for_transaction_email_txn_without_snapshot(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    row = ledger.transactions.append(
        {
            "date": "2026-07-01",
            "kind": "expense",
            "from_account_id": "acc_1",
            "from_amount": 10.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": internal_email_id("<never-stashed@x>")}],
        }
    )
    # An email-booked row whose snapshot never survived is an EXPLICIT unavailable marker,
    # never a silent None (owner decision, multi-source-email drawer).
    result = svc.snapshots_for_transaction(row["id"])
    assert result == [{"message_id": internal_email_id("<never-stashed@x>"), "unavailable": True}]


def test_snapshot_for_transaction_non_email_txn(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    row = ledger.transactions.append(
        {
            "date": "2026-07-01",
            "kind": "expense",
            "from_account_id": "acc_1",
            "from_amount": 10.0,
            "from_currency": "USD",
        }
    )
    assert svc.snapshots_for_transaction(row["id"]) is None


def test_snapshot_for_transaction_missing_txn(tmp_path, monkeypatch, db_isolated):
    _isolate(tmp_path, monkeypatch)
    assert svc.snapshots_for_transaction("txn_does_not_exist") is None


# ── internal_email_id bracket-insensitivity (replaces the old canon tests) ────
#
# fetch_emails stashes the RAW bracketed RFC 5322 Message-ID header; the model books the
# short internal id under external_source="email". internal_email_id bracket-normalizes
# before hashing, so a bracketed stash and a bracketless raw both hash to the SAME id — the
# bracket-tolerance property, now folded directly into the hash.
# A representative AWS SES-style message id, used below.

_AMAZONSES_MESSAGE_ID = (
    "0100000000000000-00000000-0000-0000-0000-000000000000-000000@email.amazonses.com"
)


def test_internal_email_id_is_bracket_insensitive():
    """A bracketed header and its bracket-stripped form hash to the SAME internal id, so a
    bracketed stash and a bracketless booked ref resolve to the same snapshot."""
    assert internal_email_id(f"<{_AMAZONSES_MESSAGE_ID}>") == internal_email_id(
        _AMAZONSES_MESSAGE_ID
    )
    assert internal_email_id(f"  <{_AMAZONSES_MESSAGE_ID}>  ") == internal_email_id(
        _AMAZONSES_MESSAGE_ID
    )


def test_internal_email_id_hashes_a_bracketless_fallback_id_stably():
    """A content-fallback-style raw (no angle brackets at all) hashes to a stable 12-hex id."""
    first = internal_email_id("no-brackets-fallback-id")
    assert first == internal_email_id("no-brackets-fallback-id")
    assert len(first) == 12


def test_bracketed_stash_bracketless_ref_promotes(tmp_path, monkeypatch, db_isolated):
    """(a) Regression: fetch_emails stashes the bracketed raw id; the model
    books the short internal id. Because internal_email_id normalizes brackets, both forms
    map to the same id and the snapshot promotes."""
    _isolate(tmp_path, monkeypatch)
    svc.stash_snapshots(
        [
            {
                "message_id": f"<{_AMAZONSES_MESSAGE_ID}>",
                "from": "no-reply@amazonses.com",
                "subject": "Your receipt",
                "date": "2026-07-16",
                "body_text": "Total: $42.00",
            }
        ]
    )
    svc.promote_for_email("email", internal_email_id(_AMAZONSES_MESSAGE_ID))
    assert svc._snapshots.get(internal_email_id(f"<{_AMAZONSES_MESSAGE_ID}>"))["referenced"] is True


def test_bracketless_stash_bracketed_ref_promotes(tmp_path, monkeypatch, db_isolated):
    """(d) The reverse direction: a bracket-LESS stash (e.g. a content-fallback id, or a
    future source without brackets) still resolves to the id computed from a bracketED raw."""
    _isolate(tmp_path, monkeypatch)
    svc.stash_snapshots(
        [
            {
                "message_id": "m-plain@x",
                "from": "a@x.com",
                "subject": "s",
                "date": "2026-07-01",
                "body_text": "b",
            }
        ]
    )
    svc.promote_for_email("email", internal_email_id("<m-plain@x>"))
    assert svc._snapshots.get(internal_email_id("m-plain@x"))["referenced"] is True


def test_pre_fix_bracketed_row_found_by_tolerant_lookup(tmp_path, monkeypatch, db_isolated):
    """(c) Simulates data stashed BEFORE this scheme shipped: a row still keyed by its raw
    bracketed message_id (bypassing stash_snapshots, which now keys by the internal id, to
    reproduce the pre-fix on-disk shape directly). promote() must still find and flip it —
    the tolerant _find_snapshot recomputes internal_email_id over each stored row's raw id,
    so no data migration is needed."""
    _isolate(tmp_path, monkeypatch)
    svc._snapshots.stash(
        {
            "message_id": f"<{_AMAZONSES_MESSAGE_ID}>",  # pre-fix: keyed by the raw bracketed id
            "subject": "Your receipt",
            "from_addr": "no-reply@amazonses.com",
            "received_at": "2026-07-16",
            "body_text": "Total: $42.00",
            "referenced": False,
            "stashed_at": "2026-07-16T00:00:00+00:00",
        }
    )
    assert svc.promote(internal_email_id(_AMAZONSES_MESSAGE_ID)) is True
    # The row is promoted under whatever key it's ACTUALLY stored as (the raw bracketed one).
    assert svc._snapshots.get(f"<{_AMAZONSES_MESSAGE_ID}>")["referenced"] is True


def test_snapshot_for_transaction_finds_pre_fix_bracketed_row(tmp_path, monkeypatch, db_isolated):
    """(c)+(b), service-level: a txn booked with the internal-id external_ref must resolve to
    a pre-fix bracketed row via the tolerant lookup."""
    _isolate(tmp_path, monkeypatch)
    svc._snapshots.stash(
        {
            "message_id": f"<{_AMAZONSES_MESSAGE_ID}>",
            "subject": "Your receipt",
            "from_addr": "no-reply@amazonses.com",
            "received_at": "2026-07-16",
            "body_text": "Total: $42.00",
            "referenced": False,
            "stashed_at": "2026-07-16T00:00:00+00:00",
        }
    )
    row = ledger.transactions.append(
        {
            "date": "2026-07-16",
            "kind": "expense",
            "from_account_id": "acc_1",
            "from_amount": 42.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": internal_email_id(_AMAZONSES_MESSAGE_ID)}],
        }
    )
    result = svc.snapshots_for_transaction(row["id"])[0]
    assert result is not None
    assert result["subject"] == "Your receipt"


# ── snapshot_for_transaction self-heals a found-but-unreferenced row ──────────────


def test_snapshot_for_transaction_self_heals_an_unreferenced_row(
    tmp_path, monkeypatch, db_isolated
):
    """Addendum: a row that missed its promote pre-fix (or any other historical gap) stays
    referenced=False even though the tolerant lookup now makes the drawer work — leaving it
    prune-eligible. snapshot_for_transaction must self-heal: flip referenced=True the moment
    it successfully resolves a txn to a snapshot row."""
    _isolate(tmp_path, monkeypatch)
    svc._snapshots.stash(
        {
            "message_id": f"<{_AMAZONSES_MESSAGE_ID}>",  # pre-fix bracketed row, still False
            "subject": "Your receipt",
            "from_addr": "no-reply@amazonses.com",
            "received_at": "2026-07-16",
            "body_text": "Total: $42.00",
            "referenced": False,
            "stashed_at": "2026-07-16T00:00:00+00:00",
        }
    )
    row = ledger.transactions.append(
        {
            "date": "2026-07-16",
            "kind": "expense",
            "from_account_id": "acc_1",
            "from_amount": 42.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": internal_email_id(_AMAZONSES_MESSAGE_ID)}],
        }
    )
    assert svc._snapshots.get(f"<{_AMAZONSES_MESSAGE_ID}>")["referenced"] is False

    result = svc.snapshots_for_transaction(row["id"])[0]

    assert result is not None
    assert svc._snapshots.get(f"<{_AMAZONSES_MESSAGE_ID}>")["referenced"] is True


def test_snapshot_for_transaction_self_heal_failure_still_returns_the_row(
    tmp_path, monkeypatch, db_isolated
):
    """The self-heal promote write is best-effort — a failure there must never fail the
    READ. The snapshot is still returned even if the flag flip itself blows up."""
    _isolate(tmp_path, monkeypatch)
    svc._snapshots.stash(
        {
            "message_id": _AMAZONSES_MESSAGE_ID,
            "subject": "Your receipt",
            "from_addr": "no-reply@amazonses.com",
            "received_at": "2026-07-16",
            "body_text": "Total: $42.00",
            "referenced": False,
            "stashed_at": "2026-07-16T00:00:00+00:00",
        }
    )
    row = ledger.transactions.append(
        {
            "date": "2026-07-16",
            "kind": "expense",
            "from_account_id": "acc_1",
            "from_amount": 42.0,
            "from_currency": "USD",
            "external_refs": [{"source": "email", "ref": internal_email_id(_AMAZONSES_MESSAGE_ID)}],
        }
    )

    def _boom(message_id):
        raise RuntimeError("store is on fire")

    monkeypatch.setattr(svc._snapshots, "promote", _boom)

    result = svc.snapshots_for_transaction(row["id"])[0]
    assert result is not None
    assert result["subject"] == "Your receipt"
