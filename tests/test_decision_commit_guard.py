"""decision_commit's merge-decision security guard (FIX 2, security review 2026-07-16).

Before this fix, a merge decision's ``existing_id`` went straight to ``apply_import_merge``
with no check that the engine ever proposed it — reproduced live: rewriting an unrelated
transaction on another account (e.g. a $4,000 rent payment silently re-dated/re-amounted to a
$4 coffee). ``decision_commit`` now re-derives the SAME batch-aware engine verdict the preview
runs (``_apply_batch_match``) and refuses a merge whose ``existing_id`` isn't accepted by one
of exactly TWO server-re-derived paths:

1. the row's OWN engine-assigned candidate (``assigned_by_index``) — which since RFC 2026-07-29
   §D2 includes the settled-drift weak band, because that tier now lives inside the engine; and
2. any UNCOVERED in-window entry the preview's "Not on statement" panel showed
   (``_manual_link_ids``) — the manual-link feature (owner, 2026-07-18).

A THIRD path used to exist: the twin matcher's PRE-assignment eligible map
(``_twin_eligible_ids``). Security review 2026-07-29 (#1) deleted it — it authorized writes from
a matcher with no invariants, no statement window and no 1:1 exclusivity, and it bought nothing,
being a subset of path 2 by construction. Path 2 still accepts every merge it did; the tests
below that used to exercise it now prove exactly that.

A fabricated, covered, out-of-window, or foreign-account id still refuses. Two merges naming the
SAME id refuse, and so does a merge onto an id another row's flag already claims (#2) — one
ledger entry backs at most one statement row, at commit as well as in the engine. The merge-apply
loop also no longer lets one bad merge (e.g. a protected row) sink an otherwise-good commit —
failures land in ``merge_errors`` instead.

LLM stubbed via the seam; stores isolated (mirrors test_decision_import.py's env fixture)."""

from __future__ import annotations

import json

import pytest

from backend.core import llm_seam
from backend.core.errors import ValidationError
from backend.services import ledger as ledger_service
from backend.services.statements import decisions as svc


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, db_isolated):
    import backend.db.statement_store as stmt_mod

    # accounts/transactions/import_batches/txn_audit are DB-backed now (plan 3 task 4), and
    # payees too (plan 4d task 2) — db_isolated redirects the process-wide DB singleton, so
    # ledger_service's module-level store instances, services.audit's own separate
    # TransactionStore()/TransactionAuditStore() instances, AND payee_registry._store all
    # resolve against the same isolated database, with no per-instance path-sync needed.
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
    ledger_service.accounts.insert(
        {
            "id": "acc",
            "name": "Meridian Bank Card",
            "kind": "liability",
            "currency": "SGD",
            "last_four": "1111",
        }
    )
    ledger_service.accounts.insert(
        {"id": "other", "name": "Meridian Bank Savings", "kind": "asset", "currency": "SGD"}
    )


def _stub_text_tier(monkeypatch, rows: list[dict], meta: dict | None = None):
    async def _complete(prompt, **kwargs):
        if kwargs.get("source") == "statement_metadata":
            return json.dumps(meta or {})
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)


_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 60.36,
        "direction": "OUT",
        "payee": "Cinebar",
        "description": "MPOS*CINEBAR 60.36",
        "reference": "74720746142802720528652",
        "confidence": 0.97,
    },
]
_META = {
    "currency": "SGD",
    "sign_convention": "card",
    "issuer": "Meridian Bank",
    "opening_balance": 0.0,
    "closing_balance": 60.36,
    "account_last4s": ["1111"],
}


def _make_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_FAKE_PDF = _make_pdf(
    "Meridian Bank statement\nMPOS*CINEBAR 60.36\nRef No. : 74720746142802720528652"
)


def _preview(monkeypatch, rows=_ROWS, meta=_META, content: bytes | None = None):
    _stub_text_tier(monkeypatch, rows, meta)
    return svc.decision_preview(content or _FAKE_PDF, "pdf", "acc", filename="june.pdf")


def _live_txns() -> list[dict]:
    return [t for t in ledger_service.transactions.all() if not t.get("deleted")]


# ── (a) fabricated existing_id → ValidationError, victim byte-unchanged ─────────────


def test_fabricated_existing_id_is_rejected_victim_unchanged(monkeypatch):
    # A real, unrelated transaction on a DIFFERENT account — the engine would never propose it
    # for this row (different account, wildly different amount, different date).
    victim = ledger_service.create_transaction(
        {
            "date": "2026-01-01",
            "kind": "expense",
            "description": "Rent",
            "from_account_id": "other",
            "from_amount": 4000.0,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    before_snapshot = dict(victim)
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(ValidationError):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 0, "action": "merge", "existing_id": victim["id"]}],
        )
    after = ledger_service.transactions.find_by_id(victim["id"])
    assert after == before_snapshot  # byte-unchanged — not re-dated, not re-amounted
    # the whole commit aborted — nothing else was written either.
    assert [t for t in _live_txns() if t["id"] != victim["id"]] == []
    assert ledger_service.import_batches.all() == []


def test_fabricated_existing_id_on_a_routed_account_is_still_rejected(monkeypatch):
    # Belt-and-suspenders check (b): even a txn ON a routed account is rejected when it wasn't
    # THIS row's own engine-assigned candidate (e.g. a random other charge on the same card).
    other_charge = ledger_service.create_transaction(
        {
            "date": "2026-03-01",
            "kind": "expense",
            "description": "Groceries",
            "from_account_id": "acc",
            "from_amount": 88.20,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch)
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(ValidationError):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 0, "action": "merge", "existing_id": other_charge["id"]}],
        )
    unchanged = ledger_service.transactions.find_by_id(other_charge["id"])
    assert unchanged["from_amount"] == 88.20 and unchanged["description"] == "Groceries"


# ── (b) the engine-proposed id → merge succeeds exactly as before ───────────────────


def test_engine_proposed_id_merges_successfully(monkeypatch):
    existing = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "Cinebar night (manual)",
            "from_account_id": "acc",
            "from_amount": 60.00,  # close enough (within engine tolerance) to be proposed
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch)
    # Sanity: the engine really did propose this pairing in preview (not the assertion itself).
    flagged = [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")]
    assert flagged and flagged[0]["existing_id"] == existing["id"], flagged
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": existing["id"]}],
    )
    assert out["merged"] == 1
    assert out["merge_errors"] == []
    merged = ledger_service.transactions.find_by_id(existing["id"])
    assert merged["from_amount"] == 60.36  # statement wins the money
    assert merged["description"] == "Cinebar night (manual)"  # label preserved
    # The merge stamps the statement row's ONE deterministic ref (b7f607da8 retired the legacy
    # "ref:"-prefixed twin), so the row stays matched on a re-import.
    assert any(
        e.get("source") == "statement" and e.get("ref") for e in merged.get("external_refs") or []
    )


# ── (c) a merge onto a protected row lands in merge_errors, other rows still commit ──


def test_apply_merge_error_for_protected_row_does_not_sink_the_commit(monkeypatch):
    # The Starting-Balance opening txn (kind=adjustment) can never reach decision_commit's own
    # existing_id-matches-engine-proposal gate through a real preview — adjustment rows are
    # structurally excluded from the engine's candidate pool (F20 invariant in matching.py), so
    # the ONLY way a merge decision could ever name one is if something upstream mis-assigned
    # it. This exercises _apply()'s per-row try/except DIRECTLY — the defensive backstop for
    # "apply_import_merge fails for a reason decision_commit's own gate didn't catch" (a
    # protected row, a race, or any other exception) — proving one bad merge never sinks an
    # otherwise-good commit.
    from backend.db.statement_store import StatementStore

    acc = ledger_service.set_account_opening("acc", 500.0, "2026-01-01")
    opening = ledger_service.transactions.find_by_id(acc["opening_txn_id"])
    assert opening is not None

    record = StatementStore().create("june.pdf", "application/pdf", _FAKE_PDF, {"kind": "import"})

    ok_draft = {
        "kind": "expense",
        "date": "2026-05-21",
        "description": "MPOS*CINEBAR 60.36",
        "from_account_id": "acc",
        "from_amount": 60.36,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:1"}],
        "source": "pdf_import",
    }
    bad_draft = {
        "kind": "expense",
        "date": "2026-05-22",
        "description": "FOODIE 11.04",
        "from_account_id": "acc",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "external_refs": [{"source": "pdf", "ref": "ref:acc:2"}],
        "source": "pdf_import",
    }
    result = svc._apply(
        statement_id=record["id"],
        record=record,
        rows=[1, 2],  # _apply only reads len(rows) for total_rows
        fmt="pdf",
        resolved_default="acc",
        creates=[ok_draft],
        merges=[(1, opening["id"], bad_draft, False)],
        skipped=0,
        duplicates=0,
        transfers=0,
        account_map={"SGD": "acc"},
    )
    assert result["created"] == 1
    assert result["merged"] == 0
    assert len(result["merge_errors"]) == 1
    err = result["merge_errors"][0]
    assert err["row"] == 1 and err["existing_id"] == opening["id"]
    assert "Starting Balance" in err["error"]
    # the good row still landed despite the bad merge.
    live = _live_txns()
    assert any(t["description"] == "MPOS*CINEBAR 60.36" for t in live)
    # the protected row itself is untouched.
    still_opening = ledger_service.transactions.find_by_id(opening["id"])
    assert still_opening["to_amount"] == 500.0


# ── (d) existing tests unmodified-green — see tests/test_decision_import.py,
# tests/test_multi_account_import.py (run as part of the same gate; not duplicated here).


# ── (e) weak-band merges (RFC 2026-07-29 §D2) ────────────────────────────────────────
# The review UI's weak band now sources its Merge target from the ENGINE's own settled-drift
# tier, so it arrives at commit as an ordinary `assigned_by_index` entry — the same acceptance
# path a strong match uses, re-derived server-side, with every invariant applied.

_WEAK_META = {
    **_META,
    "closing_balance": 14.63,
    "period_start": "2026-05-01",
    "period_end": "2026-05-31",
}

# The SI-53 pinned drift shape: statement "ANTHROPIC" −14.63 vs ledger "Anthropic Claude API"
# −14.16, 1 day apart — beyond the engine's near-dup tolerance, inside the settled-drift tier's
# 5% band + shared-wording gate.
_WEAK_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 14.63,
        "direction": "OUT",
        "payee": "ANTHROPIC",
        "description": "ANTHROPIC",
        "reference": "74720746142802720528653",
        "confidence": 0.97,
    },
]


def _weak_twin_ledger_row():
    return ledger_service.create_transaction(
        {
            "date": "2026-05-20",
            "kind": "expense",
            "description": "Anthropic Claude API",
            "from_account_id": "acc",
            "from_amount": 14.16,
            "from_currency": "SGD",
            "source": "agent",
        }
    )


def test_weak_twin_merge_is_accepted_with_statement_facts(monkeypatch):
    existing = _weak_twin_ledger_row()
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    # Sanity on the fixture, not the assertion itself. CHANGED by §D2: the engine now proposes
    # this pairing itself, in the weak band, and the entry leaves the "not on statement" list —
    # where it previously sat WHILE an annotation beside it said "this is actually row 0".
    flags = [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")]
    assert [(f["band"], f["existing_id"]) for f in flags] == [("weak", existing["id"])]
    assert res["ledger_check"]["rows"] == []
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": existing["id"]}],
    )
    assert out["merged"] == 1 and out["merge_errors"] == []
    merged = ledger_service.transactions.find_by_id(existing["id"])
    assert merged["from_amount"] == 14.63  # facts="statement": settled amount wins
    assert merged["date"] == "2026-05-21"  # facts="statement": date re-stamped
    assert merged["description"] == "Anthropic Claude API"  # labels never change
    assert merged["cleared"] is True
    assert any(e.get("source") == "statement" for e in merged.get("external_refs") or [])
    # ONE record — the merge never also created the statement row as its own transaction.
    assert [t["id"] for t in _live_txns()] == [existing["id"]]


def test_manual_link_overrides_the_engine_twin_suggestion(monkeypatch):
    # The user's assertion beats the engine's guess: row 0's twin is the Anthropic entry, but
    # the user may deliberately link it to a DIFFERENT uncovered in-window entry instead (owner
    # 2026-07-18 manual link). Both are real, in-window, in-account uncovered entries — the
    # target is the user's call, not the twin matcher's. (Pre-2026-07-18 this refused; the
    # manual-link path intentionally widens acceptance to the whole uncovered set.)
    _weak_twin_ledger_row()
    bystander = ledger_service.create_transaction(
        {
            "date": "2026-05-15",
            "kind": "expense",
            "description": "Rent May",
            "from_account_id": "acc",
            "from_amount": 200.0,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    assert bystander["id"] in {r["id"] for r in res["ledger_check"]["rows"]}  # it's uncovered
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": bystander["id"]}],
    )
    assert out["merged"] == 1 and out["merge_errors"] == []
    merged = ledger_service.transactions.find_by_id(bystander["id"])
    assert merged["from_amount"] == 14.63  # the user adopted the statement's settled figure
    assert merged["description"] == "Rent May"  # labels never change


# ── (f) match picker: the engine's alternatives are all mergeable, ineligible ids never ─
# The review UI's match PICKER lets the user swap a weak-band pair row's ledger-side candidate
# to one of the flag's `alternatives` — the engine's own near-tie set (same class, ≤0.02 score
# delta, unclaimed by any other row), which now rides the wire with each option's summary.
# Every such option is an UNCOVERED entry by construction (the winner is the only one the flag
# covers), so the manual-link path accepts each one WITHOUT the acceptance set being widened to
# the engine's candidate pool. That is the R5 property, obtained honestly.


def test_picker_merge_into_a_non_assigned_alternative_is_accepted(monkeypatch):
    """The picker scenario: TWO uncovered Anthropic entries both reach the ANTHROPIC statement
    row through the settled-drift tier; the assignment picks the amount-closer one and offers the
    other as an `alternative`, the user swaps to it — the merge commits with facts="statement"
    onto the picked entry."""
    assigned = ledger_service.create_transaction(
        {
            "date": "2026-05-20",
            "kind": "expense",
            "description": "Anthropic Claude API",
            "from_account_id": "acc",
            "from_amount": 14.20,
            "from_currency": "SGD",
            "source": "agent",
        }
    )
    alt = ledger_service.create_transaction(
        {
            "date": "2026-05-20",
            "kind": "expense",
            "description": "Anthropic subscription",
            "from_account_id": "acc",
            "from_amount": 14.00,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    # Sanity on the fixture, not the assertion itself: the weak tier assigns the amount-closer
    # entry and offers the other as an alternative, carrying its OWN summary so the picker can
    # render an option the client could not otherwise look up (RFC §D3 amendment (a)).
    flag = [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")][0]
    assert (flag["band"], flag["existing_id"]) == ("weak", assigned["id"])
    assert [(a["id"], a["existing"]["note"]) for a in flag["alternatives"]] == [
        (alt["id"], "Anthropic subscription")
    ]
    for a in flag["alternatives"]:
        assert {"original_amount", "original_currency"} <= set(a["existing"])
    # The winner is covered; the alternative stays in the uncovered set, which is exactly why
    # the manual-link path accepts it without the acceptance set being widened.
    assert [r["id"] for r in res["ledger_check"]["rows"]] == [alt["id"]]
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": alt["id"]}],
    )
    assert out["merged"] == 1 and out["merge_errors"] == []
    merged = ledger_service.transactions.find_by_id(alt["id"])
    assert merged["from_amount"] == 14.63  # facts="statement": settled amount wins
    assert merged["date"] == "2026-05-21"  # facts="statement": date re-stamped
    assert merged["description"] == "Anthropic subscription"  # labels never change
    assert merged["cleared"] is True
    assert any(e.get("source") == "statement" for e in merged.get("external_refs") or [])
    # ONE record for the purchase — no create; the non-picked twin is byte-untouched.
    untouched = ledger_service.transactions.find_by_id(assigned["id"])
    assert untouched["from_amount"] == 14.20 and untouched["date"] == "2026-05-20"
    assert {t["id"] for t in _live_txns()} == {assigned["id"], alt["id"]}


def test_engine_ineligible_uncovered_entries_are_manually_linkable(monkeypatch):
    """The manual-link widening (owner 2026-07-18): three live in-window entries that each fail
    exactly one AUTO gate — zero shared wording on drift, outside the ±1-day window, amount past
    the 5% band — are ones the engine will not propose, but ARE in the uncovered set, so the user
    may hand-link any of them. This is the inverse of the pre-feature 'no-widening' contract: the
    auto gates bound what the engine SUGGESTS, not what the user may ASSERT. The uncovered-set
    membership + the account floor are what keep it safe."""
    wordless = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "Utilities autopay",
            "from_account_id": "acc",
            "from_amount": 14.00,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    far_day = ledger_service.create_transaction(
        {
            "date": "2026-05-08",
            "kind": "expense",
            "description": "Anthropic early charge",
            "from_account_id": "acc",
            "from_amount": 14.40,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    wide = ledger_service.create_transaction(
        {
            "date": "2026-05-21",
            "kind": "expense",
            "description": "Anthropic annual",
            "from_account_id": "acc",
            "from_amount": 13.63,
            "from_currency": "SGD",
            "source": "ai",
        }
    )
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    lc = res["ledger_check"]
    # All three are UNCOVERED (in the panel's 'not on statement' set) → all three are valid
    # manual-link targets, even though NONE is twin-eligible: the uncovered-set membership IS
    # the acceptance criterion for a manual link.
    assert {r["id"] for r in lc["rows"]} == {wordless["id"], far_day["id"], wide["id"]}
    # None of the three passes the AUTO gates — the row carries no match flag at all.
    assert not [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")]
    monkeypatch.setattr(llm_seam, "_complete", None)
    # Commit-link the date-gap case (far_day — the live ExampleSub shape: same charge, >1 day
    # apart, which the ±1-day auto gate can never reach). One commit consumes the row.
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": far_day["id"]}],
    )
    assert out["merged"] == 1 and out["merge_errors"] == []
    assert ledger_service.transactions.find_by_id(far_day["id"])["from_amount"] == 14.63


def test_fabricated_id_is_refused_even_when_the_row_has_a_weak_match(monkeypatch):
    """A nonexistent id never merges — the acceptance set is a closed list of real ids (this
    row's own assignment, plus the uncovered entries), not a bypass of the engine gate."""
    existing = _weak_twin_ledger_row()
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    flags = [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")]
    assert [(f["band"], f["existing_id"]) for f in flags] == [("weak", existing["id"])]
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(ValidationError):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 0, "action": "merge", "existing_id": "txn_fabricated"}],
        )
    assert ledger_service.transactions.find_by_id(existing["id"])["from_amount"] == 14.16


# ── (e) manual link — a USER-ASSERTED merge onto an uncovered entry the engine's gates were
# too tight to propose (owner 2026-07-18: "let the user decide to merge these two themselves").
# Accepted iff the target is a real, in-window, in-account, UNCOVERED transaction; a fabricated
# / covered / out-of-window / foreign-account id is still refused, and the account floor holds.


def _unrelated_uncovered_entry(date="2026-05-15", amount=25.90, account="acc"):
    """A real ledger entry in the statement's window+account that the engine and the twin
    matcher BOTH reject for the statement row (6 days away, unrelated amount + wording) — but
    which the 'not on statement' panel legitimately lists as uncovered."""
    return ledger_service.create_transaction(
        {
            "date": date,
            "kind": "expense",
            "description": "ExampleSub subscription renewal",
            "from_account_id": account,
            "from_amount": amount,
            "from_currency": "SGD",
            "source": "agent",
        }
    )


def test_manual_link_onto_an_uncovered_entry_is_accepted(monkeypatch):
    entry = _unrelated_uncovered_entry()
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    # Sanity: the engine proposed nothing at all for this row (6-day gap, no shared wording,
    # unrelated amount) — so only the manual-link path can accept it.
    assert not [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")]
    # ...but it IS in the uncovered 'not on statement' set the user picks from.
    uncovered = {r["id"] for r in res["ledger_check"]["rows"]}
    assert entry["id"] in uncovered
    monkeypatch.setattr(llm_seam, "_complete", None)
    out = svc.decision_commit(
        res["statement_id"],
        "acc",
        decisions=[{"row": 0, "action": "merge", "existing_id": entry["id"]}],
    )
    assert out["merged"] == 1 and out["merge_errors"] == []
    merged = ledger_service.transactions.find_by_id(entry["id"])
    assert merged["from_amount"] == 14.63  # facts="statement": the user adopts the settled figure
    assert merged["date"] == "2026-05-21"
    assert merged["description"] == "ExampleSub subscription renewal"  # labels never change
    assert merged["cleared"] is True
    # ONE record — the manual link merged, never also created the statement row.
    assert [t["id"] for t in _live_txns()] == [entry["id"]]


def test_manual_link_to_an_out_of_window_entry_is_refused(monkeypatch):
    # A real entry on the RIGHT account but OUTSIDE the statement period → not in the uncovered
    # set → refused (the window bound is what keeps 'any uncovered entry' honest).
    stale = _unrelated_uncovered_entry(date="2026-01-01")
    before = dict(stale)
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    assert stale["id"] not in {r["id"] for r in res["ledger_check"]["rows"]}
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(ValidationError):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 0, "action": "merge", "existing_id": stale["id"]}],
        )
    assert ledger_service.transactions.find_by_id(stale["id"]) == before


def test_manual_link_to_a_foreign_account_entry_is_refused(monkeypatch):
    # An in-window entry on an account this statement does NOT route to → excluded from the
    # uncovered set (account-filtered) AND tripped by the account floor → refused.
    foreign = _unrelated_uncovered_entry(account="other")
    before = dict(foreign)
    res = _preview(monkeypatch, rows=_WEAK_ROWS, meta=_WEAK_META)
    assert foreign["id"] not in {r["id"] for r in res["ledger_check"]["rows"]}
    monkeypatch.setattr(llm_seam, "_complete", None)
    with pytest.raises(ValidationError):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 0, "action": "merge", "existing_id": foreign["id"]}],
        )
    assert ledger_service.transactions.find_by_id(foreign["id"]) == before


# ── (g) 1:1 at the commit boundary (security review 2026-07-29 #2) ───────────────────
# `apply_import_merge(facts="statement")` RE-DATES and RE-AMOUNTS its target. Two merges naming
# one id therefore rewrite a real transaction twice, and corrupt rollback with it: the per-merge
# `before` snapshots replay in order, so the second one restores post-first-merge state and
# reports success. The engine already enforces 1:1 in its assignment; the commit boundary must
# too, because `decisions` are caller-supplied — including by the model-reachable
# `import_transactions(commit=true)` tool.

_TWO_ROWS = [
    {
        "date": "2026-05-21",
        "amount": 14.63,
        "direction": "OUT",
        "payee": "ANTHROPIC",
        "description": "ANTHROPIC",
        "reference": "74720746142802720528653",
        "confidence": 0.97,
    },
    {
        "date": "2026-05-22",
        "amount": 31.40,
        "direction": "OUT",
        "payee": "COLD STORAGE",
        "description": "COLD STORAGE JURONG",
        "reference": "74720746142802720528654",
        "confidence": 0.97,
    },
]
# _WEAK_META's closing_balance (14.63) was tuned for the single-row weak-twin fixture — reused
# as-is here it makes the statement's own reconcile fail ("off"), which withholds the
# ledger_check panel these tests read (ADR
# 2026-09-09-an-incomplete-parse-withholds-the-ledger-diff). Both rows' own closing balance
# keeps the parse a clean "ok", so the gate stays open and the assertions test what they say
# they test.
_TWO_ROWS_META = {**_WEAK_META, "closing_balance": 46.03}


def test_two_rows_may_not_merge_onto_the_same_existing_id(monkeypatch):
    """Both rows name the one uncovered entry through the manual-link path — each is
    individually legal, together they are not."""
    entry = _unrelated_uncovered_entry()
    before = dict(entry)
    res = _preview(monkeypatch, rows=_TWO_ROWS, meta=_TWO_ROWS_META)
    assert entry["id"] in {r["id"] for r in res["ledger_check"]["rows"]}
    monkeypatch.setattr(llm_seam, "_complete", None)

    with pytest.raises(ValidationError, match="already the target of another merge"):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[
                {"row": 0, "action": "merge", "existing_id": entry["id"]},
                {"row": 1, "action": "merge", "existing_id": entry["id"]},
            ],
            default_action="skip",
        )
    # The whole commit aborted before any write — the victim is byte-unchanged.
    assert ledger_service.transactions.find_by_id(entry["id"]) == before
    assert ledger_service.import_batches.all() == []


def test_a_merge_onto_another_rows_matched_entry_is_refused(monkeypatch):
    """Row 1 tries to take the entry row 0's own engine flag already claims. The uncovered set
    cannot let it through — a flagged entry is COVERED, so it is not in that set — but the guard
    says so explicitly rather than relying on that coincidence, and names the holder."""
    claimed = _weak_twin_ledger_row()
    before = dict(claimed)
    res = _preview(monkeypatch, rows=_TWO_ROWS, meta=_TWO_ROWS_META)
    flags = [f for f in (res["rows"][0].get("flags") or []) if f.get("existing_id")]
    assert [(f["band"], f["existing_id"]) for f in flags] == [("weak", claimed["id"])]
    monkeypatch.setattr(llm_seam, "_complete", None)

    with pytest.raises(ValidationError, match="already matched to row 0"):
        svc.decision_commit(
            res["statement_id"],
            "acc",
            decisions=[{"row": 1, "action": "merge", "existing_id": claimed["id"]}],
            default_action="skip",
        )
    assert ledger_service.transactions.find_by_id(claimed["id"]) == before
