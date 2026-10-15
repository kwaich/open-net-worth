"""Deterministic account detection for tabular statements (SI-25, live bug
stmt_ab4a65dc0000).

A Riverside Bank XLSX prints "Số tài khoản/ Account number:" in one preamble cell and the
number in the NEXT cell — but the tabular tiers never run the metadata LLM call, so
``meta.account_last4s`` was structurally ALWAYS empty for grids and "Detect from the
statement" could never work. The fix is LABEL-FREE by design (label vocabularies are
per-country and never finish): harvest every contiguous ASCII digit run of >= 6 digits from
the PREAMBLE rows (above the header) — contiguity alone excludes dates (01/06/2026 → 2+2+4)
and separator-grouped amounts (1,900,000 → 1+3+3) — and let the intersection with the
user's OWN accounts' last-4s be the semantic filter. Accepted consequence (documented, not
a bug): a non-account preamble number (e.g. a citizen ID) suggests an account only if the
user's own account ends in those exact 4 digits, and the blast radius is a SUGGESTION,
never a booking.

Layer 2: format-profile memory — commit stamps the chosen account onto the source's
profile (``account_id``), and a statement with NO printed match falls back to it. Printed
match first (the statement's own claim beats memory); both missing → needs_account.
"""

from __future__ import annotations

import csv as _csv
import io as _io

import pytest

from backend.core import llm_seam
from backend.db.institution_store import InstitutionStore
from backend.db.statement_format_store import StatementFormatStore
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements import profiles
from backend.services.statements.canonical import CanonicalRow, StatementMeta


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated, fx_rates):
    import backend.db.statement_store as stmt_mod

    # PayeeStore and StatementFormatStore are DB-backed now (plan 4d task 2) — db_isolated
    # redirects the process-wide singleton, so payee_registry._store and every
    # StatementFormatStore() constructed below resolve against this fixture's isolated
    # database with no per-instance/module-constant path-sync needed.
    monkeypatch.setattr(stmt_mod, "DATA_DIR", tmp_path)
    files_dir = tmp_path / "statements"
    files_dir.mkdir()
    # Fix 3 (security review round 2, consolidate-derived-data-storage): every consumer of
    # the statements blob directory now reads through
    # backend.core.paths.statements_files_dir(), a bare accessor resolved at call time —
    # there is exactly ONE binding left to patch, not one per consumer module.
    import backend.core.paths as core_paths_mod

    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", files_dir)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    # Any LLM call in these tests is a bug — every path under test is deterministic.
    monkeypatch.setattr(llm_seam, "_complete", None)
    ledger_service.accounts.insert(
        {
            "id": "vcb",
            "name": "Riverside Bank",
            "kind": "asset",
            "currency": "VND",
            "last_four": "4321",
        }
    )
    ledger_service.accounts.insert(
        {
            "id": "uob",
            "name": "Meridian Bank One",
            "kind": "asset",
            "currency": "SGD",
            "last_four": "1111",
        }
    )


# The owner's exact preamble shape (rows 0-13 of his Riverside Bank XLSX, anonymized): the
# account-number label and the number in SEPARATE cells, a citizen-ID decoy row, date
# cells, thousands-separated amount cells, header at grid index 12.
_VCB_GRID = [
    ["Riverside Bank", "", "", "", ""],
    ["SAO KÊ TÀI KHOẢN/ ACCOUNT STATEMENT", "", "", "", ""],
    ["", "", "", "", ""],
    ["Số tài khoản/ Account number:", "0000000544321", "", "", ""],
    ["CMND/CCCD/Hộ chiếu/ ID/Passport No.:", "001199012345", "", "", ""],
    ["Tên tài khoản/ Account name:", "NGUYEN VAN B", "", "", ""],
    ["Từ ngày/ From date:", "01/06/2026", "", "", ""],
    ["Đến ngày/ To date:", "30/06/2026", "", "", ""],
    ["Loại tiền/ Currency:", "VND", "", "", ""],
    ["Số dư đầu kỳ/ Opening balance:", "900,000", "", "", ""],
    ["Số dư cuối kỳ/ Closing balance:", "1,100,000", "", "", ""],
    ["Chi nhánh/ Branch:", "Riverside Example Branch", "", "", ""],
    [
        "Ngày giao dịch/ Transaction date",
        "Số tiền ghi nợ/ Debit",
        "Số tiền ghi có/ Credit",
        "Số dư/ Balance",
        "Nội dung/ Description",
    ],
    ["13/06/2026", "50,000", "", "850,000", "MBRIV.123456789.CT tu NGUYEN VAN B"],
    ["15/06/2026", "", "250,000", "1,100,000", "LUONG THANG 6 20260615"],
]

_VCB_SCHEMA = {
    "header_row": 12,
    "date_col": 0,
    "debit_col": 1,
    "credit_col": 2,
    "signed": False,
    "desc_col": 4,
    "payee_col": None,
    "id_col": None,
    "balance_col": 3,
    "status_col": None,
    "success_values": [],
    "currency": "VND",
    "date_order": "DMY",
}


def _csv_bytes(grid: list[list[str]]) -> bytes:
    out = _io.StringIO()
    writer = _csv.writer(out)
    for row in grid:
        writer.writerow(row)
    return out.getvalue().encode("utf-8")


def _meta(last4s: list[str] | None = None, extra: dict | None = None) -> StatementMeta:
    return StatementMeta(account_last4s=list(last4s or []), extra=dict(extra or {}))


def _seed_cached_record(content: bytes, meta: StatementMeta, fmt: str = "csv") -> dict:
    """A statement record as the PRE-FIX pipeline cached it: rows parsed, meta stamped
    (empty account_last4s), no re-parse needed — the owner's stmt_ab4a65dc0000 shape."""
    from backend.db.statement_store import StatementStore

    store = StatementStore()
    record = store.create("vcb-june.csv", "text/csv", content, {"kind": "import"})
    row = CanonicalRow(
        date="2026-06-13",
        direction="OUT",
        amount=50000.0,
        currency="",
        description="MBRIV.123456789.CT tu NGUYEN VAN B",
    )
    store.update_by_id(
        record["id"],
        {
            "import_rows": [row.to_dict()],
            "import_meta": meta.to_dict(),
            "format": fmt,
            "tier": "tabular-profile",
            "reconcile": None,
            "hints_attempts": 3,
        },
    )
    return store.find_by_id(record["id"])


# ── Layer 1: the label-free preamble harvest (pure function) ──────────────────────────


def test_harvest_owner_preamble_yields_account_and_citizen_runs_only():
    """A representative Riverside Bank preamble: the 13-digit account number and the 12-digit national
    ID are the ONLY
    >= 6-digit contiguous runs in the preamble — dates and amounts split on their separators,
    and data rows below the header (bank refs, yyyymmdd stamps) are never scanned."""
    from backend.services.statements.tiers import harvest_preamble_last4s

    assert harvest_preamble_last4s(_VCB_GRID, 12) == ["4321", "2345"]


def test_harvest_date_and_amount_cells_yield_nothing():
    from backend.services.statements.tiers import harvest_preamble_last4s

    grid = [
        ["Từ ngày/ From date:", "01/06/2026"],
        ["Số dư đầu kỳ/ Opening balance:", "900,000"],
        ["Date", "Amount"],
    ]
    assert harvest_preamble_last4s(grid, 2) == []


def test_harvest_masked_card_cell():
    from backend.services.statements.tiers import harvest_preamble_last4s

    grid = [
        ["Chủ thẻ/ Card holder:", "NGUYEN VAN B"],
        ["Số thẻ/ Card number:", "xxxx-xxxx-1234"],
        ["Date", "Debit", "Credit"],
    ]
    assert harvest_preamble_last4s(grid, 2) == ["1234"]


def test_harvest_never_raises_on_garbage():
    from backend.services.statements.tiers import harvest_preamble_last4s

    assert harvest_preamble_last4s([], 5) == []
    assert harvest_preamble_last4s([[None, 12345678], ["x"]], "not-an-int") == []


# ── The suggestion rule: printed-digit intersection with the user's own accounts ──────


def test_suggest_unique_intersection_wins():
    """{4321, 8765} ∩ his accounts = exactly Riverside Bank — the citizen-ID run rides along
    harmlessly because no account of his ends 8765."""
    assert svc._suggest_account(_meta(["4321", "8765"])) == "vcb"


def test_suggest_ambiguous_across_institutions_suggests_nothing():
    ledger_service.accounts.insert(
        {
            "id": "cit",
            "name": "Meridian Bank",
            "kind": "asset",
            "currency": "SGD",
            "last_four": "8765",
        }
    )
    assert svc._suggest_account(_meta(["4321", "8765"])) is None


def test_suggest_institution_sibling_wallets_still_anchor():
    """One printed number, many currency wallets (the Meridian Bank Multiplier shape, SI-21): matches
    confined to ONE shared institution are not ambiguity — anchor the first, routing covers
    the siblings (pinned end-to-end in test_multi_account_import.py)."""
    institution = InstitutionStore().resolve_or_create("Riverside Bank")
    ledger_service.accounts.insert(
        {
            "id": "vcb_usd",
            "name": "Riverside Bank (USD)",
            "kind": "asset",
            "currency": "USD",
            "institution_id": institution["id"],
            "last_four": "4321",
        }
    )
    ledger_service.accounts.update_by_id("vcb", {"institution_id": institution["id"]})
    assert svc._suggest_account(_meta(["4321"])) == "vcb"


def test_suggest_citizen_id_collision_is_accepted_behavior():
    """ACCEPTED consequence of the label-free design: intersection-with-own-accounts IS the
    filter, so a citizen-ID run whose last-4 equals a stored last_four suggests that
    account. Blast radius = a suggestion the user sees, never a booking."""
    ledger_service.accounts.update_by_id("vcb", {"last_four": "8765"})
    assert svc._suggest_account(_meta(["4321", "8765"])) == "vcb"


def test_suggest_archived_accounts_never_match():
    ledger_service.accounts.update_by_id("vcb", {"archived": True})
    assert svc._suggest_account(_meta(["4321"])) is None


# ── Layer 2: format-profile memory (the last-committed account) ───────────────────────


def test_suggest_falls_back_to_profile_memory_when_nothing_printed():
    prof = StatementFormatStore().upsert(
        "fp-test", kind="tabular", schema=_VCB_SCHEMA, source_label="RB"
    )
    StatementFormatStore().record_use(prof["id"], account_ids=["vcb"])
    meta = _meta([], {"format_profile": {"id": prof["id"]}})
    assert svc._suggest_account(meta) == "vcb"


def test_profile_memory_reads_live_row_not_cached_snapshot():
    """The cached meta's format_profile snapshot may predate the commit stamp — the
    fallback re-reads the store, so a stamp landing AFTER the record cached still counts."""
    prof = StatementFormatStore().upsert(
        "fp-test", kind="tabular", schema=_VCB_SCHEMA, source_label="RB"
    )
    meta = _meta([], {"format_profile": {"id": prof["id"]}})
    assert svc._suggest_account(meta) is None  # nothing committed yet
    StatementFormatStore().record_use(prof["id"], account_ids=["vcb"])
    assert svc._suggest_account(meta) == "vcb"


def test_profile_memory_skips_archived_or_missing_account():
    prof = StatementFormatStore().upsert(
        "fp-test", kind="tabular", schema=_VCB_SCHEMA, source_label="RB"
    )
    StatementFormatStore().record_use(prof["id"], account_ids=["vcb"])
    ledger_service.accounts.update_by_id("vcb", {"archived": True})
    meta = _meta([], {"format_profile": {"id": prof["id"]}})
    assert svc._suggest_account(meta) is None
    StatementFormatStore().record_use(prof["id"], account_ids=["gone"])
    assert svc._suggest_account(meta) is None


def test_printed_match_beats_profile_memory():
    prof = StatementFormatStore().upsert(
        "fp-test", kind="tabular", schema=_VCB_SCHEMA, source_label="RB"
    )
    StatementFormatStore().record_use(prof["id"], account_ids=["uob"])
    meta = _meta(["4321"], {"format_profile": {"id": prof["id"]}})
    assert svc._suggest_account(meta) == "vcb"


# ── The owner's cached record: self-heal at preview, zero LLM, zero re-parse ──────────


def test_cached_tabular_record_self_heals_and_detects_account():
    """His stmt_ab4a65dc0000 shape: a record cached with import_meta.account_last4s == []
    and a learned format profile. The NEXT detect-mode preview must harvest the preamble
    from the same bytes (no re-parse, no LLM), resolve Riverside Bank, and persist the healed
    meta onto the record."""
    from backend.db.statement_store import StatementStore

    content = _csv_bytes(_VCB_GRID)
    grid = [list(r) for r in _VCB_GRID]
    assert profiles.learn(grid, _VCB_SCHEMA) is not None
    record = _seed_cached_record(content, _meta([]))
    assert record["import_meta"]["account_last4s"] == []  # the pre-fix state

    res = svc.decision_preview(content, "csv", None, filename="vcb-june.csv")

    assert res["llm_calls"] == 0
    assert "4321" in res["meta"]["account_last4s"]
    assert res["resolved_account_id"] == "vcb"
    assert res["needs_account"] is False
    assert res["account_suggestion"] == "vcb"
    assert res["rows"][0]["account_id"] == "vcb"
    healed = StatementStore().find_by_id(record["id"])
    assert "4321" in healed["import_meta"]["account_last4s"]


def test_cached_record_without_profile_stays_honest():
    """No learned profile → the header position is unknown, so the heal declines (data rows
    are full of >= 6-digit runs — an unbounded scan would guess) and detect-mode asks."""
    content = _csv_bytes(_VCB_GRID)
    record = _seed_cached_record(content, _meta([]))
    res = svc.decision_preview(content, "csv", None, filename="vcb-june.csv")
    assert res["needs_account"] is True and res["resolved_account_id"] is None
    assert record["id"] == res["statement_id"]


def test_cached_record_with_no_printed_digits_uses_profile_memory():
    """A source whose preamble prints NO account digits at all: layer 1 finds nothing, the
    profile's last-committed account carries the suggestion."""
    digitless = [row[:] for row in _VCB_GRID]
    digitless[3] = ["Số tài khoản/ Account number:", "(on file)", "", "", ""]
    digitless[4] = ["CMND/CCCD/Hộ chiếu:", "(on file)", "", "", ""]
    digitless[6] = ["Từ ngày/ From date:", "", "", "", ""]
    digitless[7] = ["Đến ngày/ To date:", "", "", "", ""]
    digitless[9] = ["Số dư đầu kỳ/ Opening balance:", "", "", "", ""]
    digitless[10] = ["Số dư cuối kỳ/ Closing balance:", "", "", "", ""]
    content = _csv_bytes(digitless)
    grid = [list(r) for r in digitless]
    prof = profiles.learn(grid, _VCB_SCHEMA)
    assert prof is not None
    StatementFormatStore().record_use(prof["id"], account_ids=["vcb"])
    record = _seed_cached_record(content, _meta([], {"format_profile": {"id": prof["id"]}}))
    res = svc.decision_preview(content, "csv", None, filename="vcb-june.csv")
    assert res["resolved_account_id"] == "vcb"
    assert res["needs_account"] is False
    assert record["id"] == res["statement_id"]


# ── An explicit account choice must never be second-guessed by the suggestion banner ──


def test_explicit_account_choice_suppresses_the_suggestion_banner():
    """Owner incident: the user explicitly picked an account, but the preview's
    ``account_suggestion`` (driven by the SAME printed-digit heuristic as detect-mode) still
    pointed at a different account, and the modal's "looks like X" banner offered a one-click
    override of the choice the user just made. An explicit ``account_id`` must suppress the
    suggestion entirely, not merely lose the anchor tie-break — the banner has no business
    existing on a preview the caller didn't ask to be detected."""
    content = _csv_bytes(_VCB_GRID)
    grid = [list(r) for r in _VCB_GRID]
    assert profiles.learn(grid, _VCB_SCHEMA) is not None
    _seed_cached_record(content, _meta([]))

    res = svc.decision_preview(content, "csv", "uob", filename="vcb-june.csv")

    assert res["resolved_account_id"] == "uob"  # the explicit choice always wins the anchor
    assert res["account_suggestion"] is None  # ...and the banner never second-guesses it


# ── Commit stamps the chosen account onto the profile (the memory's write side) ───────


def test_decision_commit_stamps_profile_account_id():
    content = _csv_bytes(_VCB_GRID)
    grid = [list(r) for r in _VCB_GRID]
    prof = profiles.learn(grid, _VCB_SCHEMA)
    assert prof is not None and prof.get("account_ids") == []
    record = _seed_cached_record(content, _meta([], {"format_profile": {"id": prof["id"]}}))

    outcome = svc.decision_commit(record["id"], "vcb")

    assert outcome["created"] == 1
    assert StatementFormatStore().find_by_id(prof["id"])["account_ids"] == ["vcb"]


# ── The choose-step probe answers from the PRINTED number too (document sources) ──────
#
# Live bug 2026-09-24: a card statement that printed one extra conditional template line (an
# FX-fee notice, only there in a month with a foreign charge) hashed to a new fingerprint, so
# the probe missed the source's profile and the Choose step pre-selected nothing — although
# the card number sat on page 1. The printed number must answer on its own.

_CARD_TEXT = """\
SAMPLE BANK CREDIT CARDS
STATEMENT OF ACCOUNT
1 EXAMPLE ROAD #02-06 SINGAPORE 418941
STATEMENT DATE CREDIT LIMIT MINIMUM PAYMENT
23 Sep 2026 $52,000.00 $72.61
SAMPLE WORLD MASTERCARD CARD NO.: 5420 8911 0092 4321
PREVIOUS BALANCE 1,081.73
01 SEP BILL PAYMENT REF NO: 17882339939361197944 885.53 CR
"""


def test_harvest_text_reads_a_space_grouped_card_number():
    from backend.services.statements.tiers import harvest_text_last4s

    last4s = harvest_text_last4s(_CARD_TEXT)
    assert "4321" in last4s
    # A date or a grouped amount is never card-shaped.
    assert harvest_text_last4s("23 Sep 2026 1,081.73 01/06/2026") == []


def test_peek_detects_a_document_account_from_its_printed_number(monkeypatch):
    monkeypatch.setattr(profiles, "_template_text", lambda content, fmt: _CARD_TEXT)

    out = profiles.peek_source(b"%PDF-unknown-source", "pdf")

    assert out["known"] is False  # no profile — the fingerprint is irrelevant here
    assert out["account_ids"] == ["vcb"]


def test_peek_printed_account_leads_the_remembered_chips(monkeypatch):
    monkeypatch.setattr(profiles, "_template_text", lambda content, fmt: _CARD_TEXT)
    prof = profiles.ensure_source_profile(b"%PDF-known", "pdf")
    assert prof is not None
    profiles.remember_accounts(prof["id"], ["uob", "vcb"])

    out = profiles.peek_source(b"%PDF-known", "pdf")

    assert out["known"] is True
    assert out["account_ids"] == ["vcb", "uob"]


def test_peek_ambiguous_printed_number_keeps_memory_untouched(monkeypatch):
    monkeypatch.setattr(profiles, "_template_text", lambda content, fmt: _CARD_TEXT)
    ledger_service.accounts.update_by_id("uob", {"last_four": "7944"})  # a ref-number twin
    prof = profiles.ensure_source_profile(b"%PDF-known", "pdf")
    assert prof is not None
    profiles.remember_accounts(prof["id"], ["uob"])

    assert profiles.peek_source(b"%PDF-known", "pdf")["account_ids"] == ["uob"]
