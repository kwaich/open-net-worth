"""Multi-account statement routing (consolidated multi-currency statements).

One statement (Meridian Bank Multiplier, Vantage Brokerage, Compass…) prints per-currency sections against one account
number; the book models per-currency wallet accounts sharing an `institution`. These pin the
HARNESS guarantees — all deterministic code, independent of what the LLM stamps:

- each row routes to the institution sibling holding its OWN currency (chosen wins its currency)
- an unroutable currency is OFFERED but never default-booked (and a mismatched explicit pick
  RAISES — the native balance fold is currency-blind, so cross-currency booking = corruption)
- an internal FX conversion (same long reference on an OUT row and an IN row in different
  currencies) books as ONE two-leg transfer, never a fake expense + fake income
- the committed routing is remembered (statement record `account_map`) and pre-routes the next
  import of the same source; the record files under EVERY wallet that received rows
- dedup stays account-scoped per ROUTED row
"""

from __future__ import annotations

import json

import pytest

# Collection-time on purpose: backend.ai.memory builds asyncio.Lock() at import (py3.9).
from backend.core import llm_seam
from backend.db.institution_store import InstitutionStore
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc
from backend.services.statements.tiers import StatementParseError


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated, fx_rates):
    """Accounts/transactions/import_batches/statements/txn_audit are DB-backed now
    (plan 3 task 4), and payees too (plan 4d task 2) — db_isolated redirects them (and every
    other unpinned store) to a fresh, empty database; no more per-instance `_path`/
    module-JSON-constant isolation for those six."""
    import backend.db.statement_store as stmt_mod

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
    institution = InstitutionStore().resolve_or_create("Meridian Bank Multiplier")
    assert institution is not None
    ledger_service.accounts.insert(
        {
            "id": "sgd",
            "name": "Meridian Bank Multiplier (SGD)",
            "kind": "asset",
            "currency": "SGD",
            "institution_id": institution["id"],
            "last_four": "3333",
        }
    )
    ledger_service.accounts.insert(
        {
            "id": "eur",
            "name": "Meridian Bank Multiplier (EUR)",
            "kind": "asset",
            "currency": "EUR",
            "institution_id": institution["id"],
            "last_four": "3333",
        }
    )


def _stub_text_tier(monkeypatch, rows: list[dict], meta: dict | None = None):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta or {})
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_ROWS = [
    {
        "date": "2026-06-08",
        "amount": 1200.00,
        "direction": "OUT",
        "currency": "SGD",
        "payee": "IRAS",
        "description": "GIRO Payments IRAS ITX",
        "confidence": 0.97,
    },
    {
        "date": "2026-06-04",
        "amount": 18114.35,
        "direction": "IN",
        "currency": "EUR",
        "payee": None,
        "description": "Advice Inward Telegraphic Transfer GB518273QKW7X2LM",
        "confidence": 0.96,
    },
    # internal FX conversion — same FT reference on both sections
    {
        "date": "2026-06-15",
        "amount": 1180.00,
        "direction": "OUT",
        "currency": "EUR",
        "payee": None,
        "description": "Advice Funds Transfer FT260615QX881402 SGD@1.421563",
        "confidence": 0.95,
    },
    {
        "date": "2026-06-15",
        "amount": 815.00,
        "direction": "IN",
        "currency": "SGD",
        "payee": None,
        "description": "Advice Funds Transfer FT260615QX881402",
        "confidence": 0.95,
    },
]

_PDF = _make_pdf(
    "Meridian Bank Multiplier consolidated\n"
    "CURRENCY: SINGAPORE DOLLAR\nGIRO IRAS ITX 1,200.00\nFT260615QX881402 815.00\n"
    "CURRENCY: EUROPEAN UNION EURO\nGB518273QKW7X2LM 18,114.35\n"
    "FT260615QX881402 1,180.00"
)


def _preview(monkeypatch, rows=_ROWS, content: bytes = _PDF, account="sgd"):
    _stub_text_tier(monkeypatch, rows, {"currency": "SGD", "issuer": "Meridian Bank"})
    return svc.decision_preview(content, "pdf", account, filename="multiplier-june.pdf")


def test_rows_route_by_currency_to_institution_siblings(monkeypatch):
    res = _preview(monkeypatch)
    by_idx = {r["index"]: r for r in res["rows"]}
    assert by_idx[0]["account_id"] == "sgd"  # SGD row → chosen
    assert by_idx[1]["account_id"] == "eur"  # EUR row → platform sibling
    assert by_idx[1]["currency"] == "EUR"  # drafted in the ROW's currency, never the chosen's
    assert res["multi"] is True
    assert res["routing"] == {
        "SGD": {"account_id": "sgd", "account_name": "Meridian Bank Multiplier (SGD)"},
        "EUR": {"account_id": "eur", "account_name": "Meridian Bank Multiplier (EUR)"},
    }
    # FX pair detected: EUR OUT leg is the transfer, SGD IN leg folds into it
    assert by_idx[2]["pair"] == {"role": "out", "partner_index": 3}
    assert by_idx[3]["pair"] == {"role": "in", "partner_index": 2}
    assert any(f["type"] == "fx_transfer_pair" for f in by_idx[2]["flags"])
    assert any(f["type"] == "fx_transfer_leg" for f in by_idx[3]["flags"])


def test_commit_books_into_routed_accounts_and_pairs_one_transfer(monkeypatch):
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)  # commit must never re-parse
    out = svc.decision_commit(res["statement_id"], "sgd")
    assert out["created"] == 3  # SGD expense + EUR income + ONE transfer
    assert out["transfers"] == 1
    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    by_kind = {}
    for t in txns:
        by_kind.setdefault(t["kind"], []).append(t)
    assert by_kind["expense"][0]["from_account_id"] == "sgd"
    assert by_kind["expense"][0]["from_currency"] == "SGD"
    inc = by_kind["income"][0]
    assert (inc["to_account_id"], inc["to_amount"], inc["to_currency"]) == ("eur", 18114.35, "EUR")
    tr = by_kind["transfer"][0]
    assert (tr["from_account_id"], tr["from_amount"], tr["from_currency"]) == ("eur", 1180.0, "EUR")
    assert (tr["to_account_id"], tr["to_amount"], tr["to_currency"]) == ("sgd", 815.00, "SGD")
    # the record files under BOTH wallets + remembers the routing
    import backend.db.statement_store as stmt_mod

    record = stmt_mod.StatementStore().find_by_id(res["statement_id"])
    assert record["account_ids"] == ["eur", "sgd"]
    assert record["account_map"] == {"SGD": "sgd", "EUR": "eur"}


def test_routing_half_of_an_fx_pair_elsewhere_is_refused(monkeypatch):
    """The OUT leg of a detected FX pair already books as a transfer between the routed
    wallets; a decision naming another destination must not be silently dropped."""
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(StatementParseError, match="FX transfer pair"):
        svc.decision_commit(
            res["statement_id"],
            "sgd",
            decisions=[{"row": 2, "action": "create", "edits": {"to_account_id": "eur"}}],
        )
    assert not [t for t in ledger_service.transactions.all() if not t.get("deleted")]


def test_explicit_in_leg_create_breaks_the_pair(monkeypatch):
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"], "sgd", decisions=[{"row": 3, "action": "create"}]
    )
    assert out["transfers"] == 0 and out["created"] == 4  # both legs booked as singles
    kinds = sorted(t["kind"] for t in ledger_service.transactions.all() if not t.get("deleted"))
    assert kinds == ["expense", "expense", "income", "income"]


def test_explicit_non_transfer_matches_on_both_fx_legs_break_the_pair(monkeypatch):
    """A near-duplicate expense/income pair is not a two-leg transfer. When review sends
    explicit decisions for both legs, commit must process both instead of absorbing the IN
    decision merely because the statement parser also detected an FX pair."""
    out_existing = ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Advice Funds Transfer FT260615QX881402 SGD@1.421563",
            "from_account_id": "eur",
            "from_amount": 1180.0,
            "from_currency": "EUR",
            "source": "manual",
        }
    )
    in_existing = ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "income",
            "description": "Advice Funds Transfer FT260615QX881402",
            "to_account_id": "sgd",
            "to_amount": 815.00,
            "to_currency": "SGD",
            "source": "manual",
        }
    )
    res = _preview(monkeypatch)
    by_idx = {row["index"]: row for row in res["rows"]}
    proposals = {
        idx: next(
            flag
            for flag in by_idx[idx]["flags"]
            if flag.get("type") == "near_duplicate" and flag.get("existing_id")
        )
        for idx in (2, 3)
    }
    assert proposals[2]["existing_id"] == out_existing["id"]
    assert proposals[3]["existing_id"] == in_existing["id"]

    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "sgd",
        decisions=[
            {"row": 2, "action": "merge", "existing_id": out_existing["id"]},
            {"row": 3, "action": "merge", "existing_id": in_existing["id"]},
        ],
    )
    assert out["transfers"] == 0
    assert out["merged"] == 2


def test_unrouted_currency_never_default_books(monkeypatch):
    ledger_service.accounts.update_by_id("eur", {"archived": True})
    res = _preview(monkeypatch)
    by_idx = {r["index"]: r for r in res["rows"]}
    assert by_idx[1]["account_id"] is None
    assert any(f["type"] == "no_account_for_currency" for f in by_idx[1]["flags"])
    assert res["routing"]["EUR"] is None
    # no pair either — pairing requires both sides routed
    assert by_idx[2]["pair"] is None
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "sgd")
    assert out["created"] == 2  # only the SGD rows; EUR rows skipped, not mis-booked
    txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]
    assert all((t.get("from_currency") or t.get("to_currency")) == "SGD" for t in txns)


def test_explicit_cross_currency_pick_raises(monkeypatch):
    ledger_service.accounts.update_by_id("eur", {"archived": True})
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(Exception) as exc:
        svc.decision_commit(
            res["statement_id"],
            "sgd",
            decisions=[{"row": 1, "action": "create", "edits": {"account_id": "sgd"}}],
        )
    assert "corrupt" in str(exc.value)


def test_routing_memory_pre_routes_next_import_without_institution(monkeypatch):
    # Institution links removed — inference finds nothing; only memory can route.
    ledger_service.accounts.update_by_id("sgd", {"institution_id": None})
    ledger_service.accounts.update_by_id("eur", {"institution_id": None})
    first = _preview(monkeypatch)
    assert {r["index"]: r for r in first["rows"]}[1]["account_id"] is None
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(
        first["statement_id"],
        "sgd",
        decisions=[{"row": 1, "action": "create", "edits": {"account_id": "eur"}}],
    )
    # Next month's statement (different bytes, same source account): EUR pre-routes from memory.
    second_pdf = _make_pdf("Meridian Bank Multiplier July\nEUR section 6.70\nSGD section 1.00")
    second_rows = [
        {
            "date": "2026-07-02",
            "amount": 6.70,
            "direction": "OUT",
            "currency": "EUR",
            "description": "TT charges",
            "confidence": 0.95,
        },
    ]
    res2 = _preview(monkeypatch, rows=second_rows, content=second_pdf)
    assert res2["rows"][0]["account_id"] == "eur"


def test_dedup_scoped_to_routed_account(monkeypatch):
    # The EUR twin lives in the EUR wallet — a preview CHOSEN on the SGD account must still
    # flag it, because the row routes to (and dedups against) the EUR wallet.
    ledger_service.create_transaction(
        {
            "date": "2026-06-04",
            "kind": "income",
            "description": "Inward TT (hand-logged)",
            "to_account_id": "eur",
            "to_amount": 18114.35,
            "to_currency": "EUR",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch)
    eur_row = {r["index"]: r for r in res["rows"]}[1]
    assert any(
        f["type"] in ("near_duplicate", "transfer_pair") and f.get("existing_id")
        for f in eur_row["flags"]
    ), eur_row["flags"]


def test_statement_lists_under_every_routed_wallet(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.import_api import router

    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "sgd")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for acct in ("sgd", "eur"):
        listing = client.get(f"/api/accounts/{acct}/statements")
        assert listing.status_code == 200
        ids = [s["id"] for s in listing.json()["statements"]]
        assert res["statement_id"] in ids, f"missing under {acct}"


def test_preview_without_account_detects_anchor_from_printed_last4(monkeypatch):
    """The choose-account step is OPTIONAL: no account passed → the statement's own printed
    account number (meta.account_last4s) anchors routing exactly like an explicit choice."""
    _stub_text_tier(
        monkeypatch,
        _ROWS,
        {"currency": "SGD", "issuer": "Meridian Bank", "account_last4s": ["3333"]},
    )
    res = svc.decision_preview(_PDF, "pdf", None, filename="multiplier-june.pdf")
    assert res["needs_account"] is False
    assert res["resolved_account_id"] in ("sgd", "eur")  # both wallets print 3104
    by_idx = {r["index"]: r for r in res["rows"]}
    assert by_idx[0]["account_id"] == "sgd" and by_idx[1]["account_id"] == "eur"
    assert res["routing"]["EUR"]["account_id"] == "eur"


def test_preview_without_account_and_no_match_asks_for_one(monkeypatch):
    """Detection failed → needs_account, nothing routed, NO scary per-row currency flags —
    and the parse is cached, so supplying an account re-previews with zero LLM calls."""
    _stub_text_tier(monkeypatch, _ROWS, {"currency": "SGD", "issuer": "Meridian Bank"})  # no last4s
    res = svc.decision_preview(_PDF, "pdf", None, filename="multiplier-june.pdf")
    assert res["needs_account"] is True and res["resolved_account_id"] is None
    assert res["routing"] == {} and res["multi"] is False
    assert all(r["account_id"] is None for r in res["rows"])
    assert not any(f["type"] == "no_account_for_currency" for r in res["rows"] for f in r["flags"])
    monkeypatch.setattr(llm_seam, "_complete", None)  # cached — the retry must be free
    res2 = svc.decision_preview(_PDF, "pdf", "sgd", filename="multiplier-june.pdf")
    assert res2["llm_calls"] == 0 and res2["needs_account"] is False
    assert res2["routing"]["EUR"]["account_id"] == "eur"


def test_fx_pair_already_booked_as_transfer_defaults_skip(monkeypatch):
    """Reported bug: the €1,300→S$815.00 conversion was ALREADY in the ledger (booked by the
    email agent), but the pair's anomaly flag matched the SIBLING DRAFT (existing_id null) —
    unactionable in review, counted "to import", and a default commit would have booked the
    transfer AGAIN. The pair must check the ledger for the transfer it would book."""
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "transfer",
            "description": "Meridian Bank Internal Transfer — Multiplier Account",
            "from_account_id": "eur",
            "from_amount": 1180.0,
            "from_currency": "EUR",
            "to_account_id": "sgd",
            "to_amount": 815.00,
            "to_currency": "SGD",
            "external_refs": [{"source": "email", "ref": "agent-booked-1"}],
            "source": "agent",
        }
    )
    res = _preview(monkeypatch)
    by_idx = {r["index"]: r for r in res["rows"]}
    out_row, in_row = by_idx[2], by_idx[3]
    # both legs carry an ACTIONABLE already-booked flag pointing at the real ledger transfer
    for leg in (out_row, in_row):
        echo = [
            f for f in leg["flags"] if f["type"] == "transfer_leg_echo" and f.get("existing_id")
        ]
        assert echo, leg["flags"]
        assert echo[0]["existing"]["kind"] == "transfer"
        # SI-50: transfer kind branch — source display-projects "agent" → "agent" (unchanged;
        # already in the display vocab), account_id picks the FROM leg (mirrors amount/currency).
        assert echo[0]["existing"]["source"] == "agent"
        assert echo[0]["existing"]["account_id"] == "eur"
    # the draft-vs-draft transfer_pair noise (existing_id null) is gone from paired rows
    assert not any(
        f["type"] == "transfer_pair" and not f.get("existing_id")
        for leg in (out_row, in_row)
        for f in leg["flags"]
    )
    # default commit NEVER re-books the transfer
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "sgd")
    assert out["transfers"] == 0
    booked = [
        t
        for t in ledger_service.transactions.all()
        if not t.get("deleted") and t.get("kind") == "transfer"
    ]
    assert len(booked) == 1  # still just the agent's original
    # explicit user override still books a second one
    res2 = svc.decision_preview(_PDF, "pdf", "sgd", filename="multiplier-june.pdf")
    out2 = svc.decision_commit(
        res2["statement_id"], "sgd", decisions=[{"row": 2, "action": "create"}]
    )
    assert out2["transfers"] == 1


def test_filing_a_fully_deduped_statement_still_links_all_wallets(monkeypatch):
    """ "Nothing to import" must not be a dead end: an all-skip commit FILES the statement —
    linked to every routed wallet (both histories), routing remembered, filing recorded so the
    next upload of the same file shows "already imported". Zero transactions created."""
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(res["statement_id"], "sgd", default_action="skip")
    assert out["created"] == 0 and out["merged"] == 0 and out["transfers"] == 0
    assert len([t for t in ledger_service.transactions.all() if not t.get("deleted")]) == 0
    import backend.db.statement_store as stmt_mod

    record = stmt_mod.StatementStore().find_by_id(res["statement_id"])
    assert record["account_id"] == "sgd"
    assert record["account_ids"] == ["eur", "sgd"]  # filed under BOTH wallets
    assert record["account_map"] == {"SGD": "sgd", "EUR": "eur"}  # routing remembered
    # Nothing was written, so no import batch is minted (owner, 2026-10-06); the filing is
    # remembered as filed_at, which the re-upload check reads in a batch id's place.
    assert not record.get("batch_ids") and record["filed_at"], "filing recorded"
    # the next preview of the same file knows it was handled
    res2 = svc.decision_preview(_PDF, "pdf", "sgd", filename="multiplier-june.pdf")
    assert res2["already_imported"] is not None


def test_one_existing_txn_never_covers_two_statement_rows(monkeypatch):
    """Reported bug: two 7-Eleven rows (Jun 5 + Jun 12, both S$5.40) near-dup-matched the SAME
    booked Jun-12 transaction — both defaulted to Skip, silently losing the Jun-5 purchase.
    Matches are exclusive: the better claim keeps the flag, the other row books normally."""
    ledger_service.create_transaction(
        {
            "date": "2026-06-12",
            "kind": "expense",
            "description": "7-ELEVEN - 138 ROBINSO",
            "from_account_id": "sgd",
            "from_amount": 5.40,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    rows = [
        {
            "date": "2026-06-05",
            "amount": 5.40,
            "direction": "OUT",
            "currency": "SGD",
            "description": "7-ELEVEN - 138 ROBINSO Singapore SG",
            "confidence": 0.95,
        },
        {
            "date": "2026-06-12",
            "amount": 5.40,
            "direction": "OUT",
            "currency": "SGD",
            "description": "7-ELEVEN - 138 ROBINSO Singapore SG",
            "confidence": 0.95,
        },
    ]
    pdf = _make_pdf("Meridian Bank\n7-ELEVEN 5.40 x2 rows\n5.40\n5.40")
    res = _preview(monkeypatch, rows=rows, content=pdf)
    by_idx = {r["index"]: r for r in res["rows"]}
    jun5 = [f for f in by_idx[0]["flags"] if f.get("existing_id")]
    jun12 = [f for f in by_idx[1]["flags"] if f.get("existing_id")]
    assert jun12, "the true duplicate (same day) keeps its match"
    assert not jun5, "the losing claim is stripped — the Jun-5 row books normally"
    monkeypatch.setattr(llm_seam, "_complete", None)
    # the review UI default-skips MATCHED rows (explicit decisions) — the Jun-5 row, now
    # unmatched, books; before the exclusivity fix it was ALSO skip-defaulted and lost.
    out = svc.decision_commit(res["statement_id"], "sgd", decisions=[{"row": 1, "action": "skip"}])
    assert out["created"] == 1 and out["skipped"] == 1


def test_losing_exclusive_claim_reassigns_to_next_free_twin(monkeypatch):
    """Reported bug (BUS/MRT, 2026-07-16): three same-fare rides in one statement all ranked the
    SAME booked ride alert as their best match (identical amount + wording; nearest-date ties
    broke toward one ledger entry), so the losers were stripped to plain creates while their
    true twins sat unclaimed — the review showed unflagged table rows AND "in your ledger but
    not on this statement" for the same purchases. A losing claim must fall back to its
    next-ranked FREE candidate, pairing N rides 1:1 with N booked alerts."""
    for d in ("2026-06-11", "2026-06-13"):
        ledger_service.create_transaction(
            {
                "date": d,
                "kind": "expense",
                "description": "BUS/MRT",
                "from_account_id": "sgd",
                "from_amount": 1.59,
                "from_currency": "SGD",
                "source": "ai",
            }
        )
    rows = [
        {
            "date": "2026-06-10",
            "amount": 1.59,
            "direction": "OUT",
            "currency": "SGD",
            "description": "BUS/MRT 900000011 SINGAPORE",
            "confidence": 0.95,
        },
        {
            "date": "2026-06-12",
            "amount": 1.59,
            "direction": "OUT",
            "currency": "SGD",
            "description": "BUS/MRT 900000012 SINGAPORE",
            "confidence": 0.95,
        },
    ]
    pdf = _make_pdf("Meridian Bank\nBUS/MRT 1.59\nBUS/MRT 1.59")
    res = _preview(monkeypatch, rows=rows, content=pdf)
    by_idx = {r["index"]: r for r in res["rows"]}
    f0 = [f for f in by_idx[0]["flags"] if f.get("existing_id")]
    f1 = [f for f in by_idx[1]["flags"] if f.get("existing_id")]
    assert f0 and f1, "BOTH rides keep a match — the loser reassigns to the free twin"
    assert f0[0]["existing_id"] != f1[0]["existing_id"], "one existing txn never covers two rows"
    # nearest-date pairing holds: 06-10 ↔ the 06-11 booking, 06-12 ↔ the 06-13 booking
    assert f0[0]["existing"]["date"] == "2026-06-11"
    assert f1[0]["existing"]["date"] == "2026-06-13"
    # nothing engine-internal leaks to the payload
    assert all("_alts" not in f for r in res["rows"] for f in r["flags"])


def test_ledger_diff_surfaces_uncovered_rows(monkeypatch):
    """The OTHER direction of reconciliation (owner ask): ledger rows in the statement's
    window that the statement does NOT contain — a slipped duplicate (agent-booked estimate
    vs settled amount, outside near-dup tolerance) must surface; batch-created and matched
    rows must not."""
    from backend.services.statements.ledger_diff import uncovered_ledger_rows

    # an agent-booked estimate the statement settles at a different amount (2.6% apart —
    # past the 1% near-dup tolerance, the live Grab case)
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Grab - Ride - Vietnam",
            "from_account_id": "sgd",
            "from_amount": 13.71,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    rows = [
        {
            "date": "2026-06-15",
            "amount": 14.07,
            "direction": "OUT",
            "currency": "SGD",
            "description": "Grab* A-9FF3C23 HA NOI VN",
            "confidence": 0.97,
        },
        {
            "date": "2026-06-20",
            "amount": 8.00,
            "direction": "OUT",
            "currency": "SGD",
            "description": "KOPITIAM 8.00",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank June\nGrab 14.07\nKOPITIAM 8.00")
    _stub_text_tier(
        monkeypatch,
        rows,
        {
            "currency": "SGD",
            "issuer": "Meridian Bank",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
    )
    res = svc.decision_preview(pdf, "pdf", "sgd", filename="meridian-june.pdf")
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "sgd")

    out = uncovered_ledger_rows(res["statement_id"])
    assert out["status"] == "ok"
    notes = [r["note"] for r in out["rows"]]
    assert notes == ["Grab - Ride - Vietnam"]  # the slipped duplicate — and ONLY it


def test_preview_carries_the_ledger_check(monkeypatch):
    """The pre-commit assurance (owner: "I shouldn't have to double-check my ledger one by
    one"): the PREVIEW payload itself carries the inverse diff — core rows need eyes,
    cut-off-adjacent rows are classified boundary, covered/matched rows are absent."""
    # a slipped duplicate mid-period + a legit boundary row on the period's first day
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Grab - Ride - Vietnam",
            "from_account_id": "sgd",
            "from_amount": 13.71,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "description": "Boundary coffee",
            "from_account_id": "sgd",
            "from_amount": 4.00,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    rows = [
        {
            "date": "2026-06-15",
            "amount": 14.07,
            "direction": "OUT",
            "currency": "SGD",
            "description": "Grab* A-9FF3C23 HA NOI VN",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank June v2\nGrab 14.07 only row")
    _stub_text_tier(
        monkeypatch,
        rows,
        {
            "currency": "SGD",
            "issuer": "Meridian Bank",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
    )
    res = svc.decision_preview(pdf, "pdf", "sgd", filename="meridian-june-v2.pdf")
    lc = res["ledger_check"]
    assert lc is not None and lc["status"] == "ok"
    # CHANGED by RFC 2026-07-29 §D2/§D3 — and this IS the feature. The Grab entry is 2.6% off
    # the statement's settled figure with "grab" shared: the settled-drift tier now pairs them
    # inside the engine, so the row carries a weak-band match card and the entry is COVERED.
    # It used to sit in this list AND carry a `statement_twin` annotation saying "this is
    # actually row 0" — the panel contradicting itself, which is the reconciliation lie the RFC
    # exists to kill. The boundary row is untouched: a genuinely absent entry stays absent.
    assert [r["note"] for r in lc["rows"]] == []
    assert [r["note"] for r in lc["boundary"]] == ["Boundary coffee"]
    weak = [f for f in res["rows"][0]["flags"] if f.get("existing_id")]
    assert [(f["band"], f["existing"]["note"]) for f in weak] == [("weak", "Grab - Ride - Vietnam")]


def test_posted_date_only_pairing_stays_uncovered(monkeypatch):
    """RFC R7, end to end: the retired twin annotator measured a row's day gap as
    ``min(gap_to_date, gap_to_posted_date)``, so a ledger entry 2 days off the row's OWN date but
    1 day off its POSTED date was annotated as that row's double. ``matching`` reads ``date``
    only, and the settled-drift tier keeps that (a statement-row concept does not belong in the
    engine's transaction dicts — see
    tests/dedup/test_settled_drift_tier.py::test_posted_date_allowance_is_deliberately_not_carried_over).

    So this entry is now honestly UNCOVERED: it appears in "In your ledger, not on this
    statement" with no annotation claiming otherwise. That is a real capability loss and a real
    honesty gain — the panel no longer both lists an entry and tells you it is on the statement.
    A genuinely absent row is indistinguishable from it, which is the cost."""
    ledger_service.create_transaction(
        {
            "date": "2026-06-07",
            "kind": "expense",
            "description": "KestrelEats — Food delivery",
            "from_account_id": "sgd",
            "from_amount": 21.80,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Totally unrelated thing",
            "from_account_id": "sgd",
            "from_amount": 99.99,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    rows = [
        {
            "date": "2026-06-05",
            "posted_date": "2026-06-06",
            "amount": 21.80,
            "direction": "OUT",
            "currency": "SGD",
            "description": "fp*Kestrel Eats Singapore SG",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank twins\nfp*Kestrel Eats 21.80")
    _stub_text_tier(
        monkeypatch,
        rows,
        {
            "currency": "SGD",
            "issuer": "Meridian Bank",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
    )
    res = svc.decision_preview(pdf, "pdf", "sgd", filename="meridian-twins.pdf")
    notes = {r["note"] for r in res["ledger_check"]["rows"]}
    assert notes == {"KestrelEats — Food delivery", "Totally unrelated thing"}
    assert not any(f.get("existing_id") for f in res["rows"][0]["flags"])
    # No key on the wire claims otherwise — `statement_twin` is gone, not merely empty.
    assert all("statement_twin" not in r for r in res["ledger_check"]["rows"])
    assert "twin_eligible" not in res["ledger_check"]


def test_two_misses_and_a_hit_after_the_unification(monkeypatch):
    """The two shapes side by side, so the RFC's trade is visible in ONE payload.

    * BUS/MRT — exact amount, but the row's own printed date is 2 days off the booking. Past
      ``WORDLESS_MAX_GAP_DAYS`` and past the drift tier's identical cap, so it stays unflagged
      and its ledger entry stays honestly uncovered. The retired annotator reached it through
      ``posted_date`` (RFC R7); nothing does now.
    * Grab — 2.6% settled drift, same day, "grab" shared. Was outside every near-dup tolerance,
      so it too stayed unflagged with a contradictory ``statement_twin`` annotation beside it.
      NOW the settled-drift tier claims it in the weak band, and its entry leaves the uncovered
      list. This is the case the tier exists for.
    """
    twin_txn = ledger_service.create_transaction(
        {
            "date": "2026-06-06",
            "kind": "expense",
            "description": "BUS/MRT — Public transport – Meridian Bank Visa Infinite ···6644",
            "from_account_id": "sgd",
            "from_amount": 1.59,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Grab - Ride - Vietnam",
            "from_account_id": "sgd",
            "from_amount": 13.71,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    rows = [
        {
            "date": "2026-06-04",
            "posted_date": "2026-06-06",
            "amount": 1.59,
            "direction": "OUT",
            "currency": "SGD",
            "description": "BUS/MRT 900000014 SINGAPORE",
            "confidence": 0.95,
        },
        # settled 2.6% off the agent's estimate — twin tier only, must NOT flag the row
        {
            "date": "2026-06-15",
            "amount": 14.07,
            "direction": "OUT",
            "currency": "SGD",
            "description": "Grab* A-9FF3C23 HA NOI VN",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank June\nBUS/MRT 1.59\nGrab 14.07")
    _stub_text_tier(
        monkeypatch,
        rows,
        {
            "currency": "SGD",
            "issuer": "Meridian Bank",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
    )
    res = svc.decision_preview(pdf, "pdf", "sgd", filename="meridian-june-2.pdf")
    # Row 0 (BUS/MRT): still unmatched — 2 days out, and nothing reads posted_date any more.
    assert not any(f.get("existing_id") for f in res["rows"][0]["flags"])
    # Row 1 (Grab): the settled-drift tier claims it, in the weak band.
    grab_flags = [f for f in res["rows"][1]["flags"] if f.get("existing_id")]
    assert len(grab_flags) == 1
    assert grab_flags[0]["band"] == "weak"
    assert grab_flags[0]["existing"]["note"] == "Grab - Ride - Vietnam"
    assert "settled 2.6% off" in grab_flags[0]["reason"]
    # ...so only the BUS/MRT entry is still uncovered, and the panel no longer contradicts the
    # table above it.
    assert [r["id"] for r in res["ledger_check"]["rows"]] == [twin_txn["id"]]


def test_totals_decompose_exactly_and_tie_when_clean(monkeypatch):
    """The money-level tie-out (owner: "how about the total amount from the ledger and the
    statement?"): ledger activity = (statement − unbooked) + uncovered + boundary + drift,
    EXACT — and tie=True only when nothing is unbooked/uncovered and drift ≈ 0."""
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Slipped dup",
            "from_account_id": "sgd",
            "from_amount": 13.71,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    rows = [
        {
            "date": "2026-06-15",
            "amount": 14.07,
            "direction": "OUT",
            "currency": "SGD",
            "description": "Grab* settled",
            "confidence": 0.97,
        },
        {
            "date": "2026-06-20",
            "amount": 8.00,
            "direction": "OUT",
            "currency": "SGD",
            "description": "KOPITIAM 8.00",
            "confidence": 0.97,
        },
    ]
    pdf = _make_pdf("Meridian Bank totals\nGrab 14.07\nKOPITIAM 8.00")
    _stub_text_tier(
        monkeypatch,
        rows,
        {
            "currency": "SGD",
            "issuer": "Meridian Bank",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
    )
    res = svc.decision_preview(pdf, "pdf", "sgd", filename="totals.pdf")
    t = res["ledger_check"]["totals"][0]
    # identity holds exactly pre-commit
    lhs = t["ledger_net"]
    rhs = (
        (t["statement_net"] - t["statement_unbooked_net"])
        + t["uncovered_net"]
        + t["boundary_net"]
        + t["drift"]
    )
    assert abs(lhs - rhs) < 0.005
    assert t["tie"] is False  # unbooked rows + the slipped dup exist

    # commit everything, remove the dup → clean tie
    monkeypatch.setattr(llm_seam, "_complete", None)
    svc.decision_commit(res["statement_id"], "sgd")
    dup = next(
        x for x in ledger_service.transactions.all() if x.get("description") == "Slipped dup"
    )
    ledger_service.transactions.update_by_id(dup["id"], {"deleted": True})
    from backend.services.statements.ledger_diff import uncovered_ledger_rows

    out = uncovered_ledger_rows(res["statement_id"])
    t2 = out["totals"][0]
    assert t2["tie"] is True
    assert abs(t2["ledger_net"] - t2["statement_net"]) < 0.005  # the totals literally match


def test_original_fingerprint_backs_the_row_at_the_right_layer(monkeypatch):
    """Owner: "how can we know they are duplicated? 13.71 vs 14.07?" — when BOTH sides recorded
    the pre-conversion foreign charge, the MATCH ENGINE's F22 pairs them exactly (the statement
    row flags "already have", the ledger row is backed, the panel lists nothing), 7.6% of settled
    drift notwithstanding.

    There is no second layer any more: the twin annotator that used to mirror this fingerprint
    for anything the engine missed is deleted, and F22 was always the engine's own gate — the
    mirror only ever existed because a second matcher could not see it."""
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "expense",
            "description": "Grab booked from receipt",
            "from_account_id": "sgd",
            "from_amount": 13.00,
            "from_currency": "SGD",
            "original_amount": 165000.0,
            "original_currency": "VND",
            "source": "agent",
        }
    )
    rows = [
        {
            "date": "2026-06-15",
            "amount": 14.07,
            "direction": "OUT",
            "currency": "SGD",
            "description": "Grab* A-9FF3C23 HA NOI VN",
            "confidence": 0.97,
            "original_amount": 165000.0,
            "original_currency": "VND",
        },
    ]
    pdf = _make_pdf("Meridian Bank f22\nGrab 14.07 VND165000")
    _stub_text_tier(
        monkeypatch,
        rows,
        {
            "currency": "SGD",
            "issuer": "Meridian Bank",
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
        },
    )
    res = svc.decision_preview(pdf, "pdf", "sgd", filename="f22.pdf")
    stmt_row = res["rows"][0]
    matched = [f for f in stmt_row["flags"] if f.get("existing_id")]
    assert matched, stmt_row["flags"]
    # F22 is a REVIEW-band verdict, not the weak tier: an exact original-amount fingerprint is
    # real evidence, and the drift tier's clamp must not be mistaken for it.
    assert matched[0]["band"] == "review"
    # Both sides' pre-conversion figures ride the wire so the pair card can show ₫165,000 next
    # to ₫165,000 rather than S$13.00 next to S$14.07 (RFC §D3 amendment (a)).
    assert matched[0]["existing"]["original_amount"] == 165000.0
    assert matched[0]["existing"]["original_currency"] == "VND"
    assert res["ledger_check"]["rows"] == []  # backed → nothing for the panel to flag
