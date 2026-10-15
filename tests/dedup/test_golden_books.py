"""Golden books — end-to-end synthetic scenarios through ``matching.match_batch``, each named
for the REAL-WORLD book shape it represents (2026-07-17 generality audit — owner: "are we sure
these will be efficient in real life?").

Every book asserts the FULL verdict set — bands, assigned partners, promoted flags, scores and
reasons — so any future tolerance tweak that shifts a foreign book's outcome fails loudly with a
NAMED book instead of a bare number drifting somewhere in a unit test.

Two books used to run against ``ledger_diff``'s separate twin matcher; RFC 2026-07-29 §D2 deleted
it and they now drive the same ``match_batch`` as everything else, with ``weak_tier=True`` — the
one keyword ``statements/decisions._apply_batch_match`` passes.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, db_isolated):
    """AccountStore/TransactionStore are DB-backed (plan 3 task 4) — db_isolated
    redirects the process-wide DB engine, no per-instance path wiring needed.

    from/to_account_id are now real foreign keys into accounts, but every golden
    book below books straight onto synthetic account ids (e.g. "harbor_savings",
    "sakura", "pocketpay") that were never real JsonStore-era account rows. txns.append
    is wrapped to auto-seed a stub account for any id not already present
    (tests.conftest.seed_stub_accounts) BEFORE each write — deriving the id set
    directly from what's actually being inserted, rather than hand-enumerating it
    per test (this file books onto ~10 distinct synthetic ids across 9 tests).
    Batch rows passed to ``matching.match_batch`` itself are never persisted (the
    engine only reads them in-memory + reads the ledger pool), so they need no
    seeding."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from tests.conftest import seed_stub_accounts

    accounts = AccountStore()
    txns = TransactionStore()

    real_append = txns.append

    def _append_seeding_accounts(data, *args, **kwargs):
        seed_stub_accounts([data])
        return real_append(data, *args, **kwargs)

    monkeypatch.setattr(txns, "append", _append_seeding_accounts)
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    return accounts, txns


_ID = [0]


def _next_id():
    _ID[0] += 1
    return f"gb-row-{_ID[0]}"


def expense(acct, amt, date, desc, source="import", ccy="SGD", category=None):
    return {
        "kind": "expense",
        "date": date,
        "description": desc,
        "category": category,
        "from_account_id": acct,
        "from_amount": amt,
        "from_currency": ccy,
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": source,
        "id": _next_id(),
    }


def income(acct, amt, date, desc, source="import", ccy="SGD", category=None):
    return {
        "kind": "income",
        "date": date,
        "description": desc,
        "category": category,
        "to_account_id": acct,
        "to_amount": amt,
        "to_currency": ccy,
        "from_account_id": None,
        "from_amount": None,
        "from_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": source,
        "id": _next_id(),
    }


def _verdicts(result):
    """The full verdict set for one batch: (band, assigned txn id, promoted) per row."""
    return [(rm.band, rm.assigned.id if rm.assigned else None, rm.promoted) for rm in result]


def _full(result):
    """EVERY field ``match_batch`` puts on a ``RowMatch`` — the Phase 0 characterization
    projection (RFC 2026-07-29, "Phase 0 — characterization pins, zero behavior change").

    ``_verdicts`` above pins three fields; Phase 2 changed the wire shape (``band`` gained
    ``"weak"``, ``alternatives`` now rides out to the client as the match picker's option list —
    RFC §D2/§D3), so the pins below capture the rest as well: klass, score, reasons,
    become_transfer, echo_side, alternatives, intra_batch, and the raw candidate count. Any diff
    outside the weak band shows up here as a failure with a NAMED book."""
    return [
        {
            "index": rm.index,
            "band": rm.band,
            "promoted": rm.promoted,
            "assigned_id": rm.assigned.id if rm.assigned else None,
            "assigned_klass": rm.assigned.klass.value if rm.assigned else None,
            "assigned_score": rm.assigned.score if rm.assigned else None,
            "assigned_reasons": list(rm.assigned.reasons) if rm.assigned else [],
            "assigned_become_transfer": rm.assigned.become_transfer if rm.assigned else False,
            "assigned_echo_side": rm.assigned.echo_side if rm.assigned else None,
            "alternatives": list(rm.alternatives),
            "intra_batch": rm.intra_batch,
            "candidate_count": len(rm.candidates),
        }
        for rm in result
    ]


def _pin(index, band, assigned_id, klass, score, reasons, **over):
    """One expected ``_full`` row, with the all-default fields spelled once."""
    return {
        "index": index,
        "band": band,
        "promoted": False,
        "assigned_id": assigned_id,
        "assigned_klass": klass,
        "assigned_score": score,
        "assigned_reasons": reasons,
        "assigned_become_transfer": False,
        "assigned_echo_side": None,
        "alternatives": [],
        "intra_batch": None,
        "candidate_count": 1,
        **over,
    }


# ── us_ach_book — a Friday→Monday internal transfer, mis-booked as expense+income ──


def test_us_ach_book(isolate_stores):
    """FIX 1 (formation gap 2 → 3): a genuine US ACH transfer leaves checking on Friday and
    lands in savings on Monday — 3 calendar days. The credit leg was connector-synced and
    mis-booked as a bare income; the checking statement brings the debit leg. The pair must
    FORM (it died at the old 2-day cap) — review-capped (unconfirmed route), never silently
    auto-linked, never promoted."""
    _, txns = isolate_stores
    booked_credit = txns.append(
        income(
            "harbor_savings",
            2000.0,
            "2026-06-08",
            "ACH CREDIT RECEIVED",
            source="simplefin",
            ccy="USD",
        )
    )
    rows = [expense("anchor_chk", 2000.0, "2026-06-05", "ACH TRANSFER TO SAVINGS", ccy="USD")]

    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert _verdicts(result) == [("review", booked_credit["id"], False)]
    rm = result[0]
    assert rm.assigned.klass == matching.MatchClass.TRANSFER_PAIR
    assert rm.assigned.become_transfer is True
    assert rm.assigned.score < matching.SCORE_STRONG  # unknown route → capped, human confirms
    assert _full(result) == [
        _pin(
            0,
            "review",
            booked_credit["id"],
            "transfer_pair",
            0.75,
            [
                "opposite-direction legs across two accounts, same base amount & near date "
                "(unconfirmed route — review)"
            ],
            assigned_become_transfer=True,
        )
    ]


# ── merchant_hotline_book — the merchant prints its phone number in EVERY descriptor ──


def test_merchant_hotline_book(isolate_stores):
    """FIX 2 (shared-reference frequency guard): five same-merchant rows all print
    "CALL 68881234" — the merchant's hotline, an 8-digit run that passes every per-pair
    reference screen (not date-like, not the amount). It appears on 5 distinct rows of the
    comparison scope, so it is a merchant CONSTANT, not a per-event reference: NO pair may
    lift to strong on it. The genuine pairs still land in the review band through their other
    tiers (wording similarity, capped past a 2-day gap)."""
    _, txns = isolate_stores
    l1 = txns.append(
        expense("card", 45.00, "2026-06-05", "PIZZAHOUSE DELIVERY CALL 68881234", source="ai")
    )
    l2 = txns.append(
        expense("card", 32.00, "2026-06-12", "PIZZAHOUSE DELIVERY CALL 68881234", source="ai")
    )
    rows = [
        expense("card", 45.20, "2026-06-10", "PIZZAHOUSE CALL 68881234"),
        expense("card", 32.10, "2026-06-16", "PIZZAHOUSE CALL 68881234"),
        expense("card", 28.40, "2026-06-20", "PIZZAHOUSE CALL 68881234"),
    ]

    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    # No strong lift anywhere; the two real twins pair review-band; the third row (no ledger
    # counterpart in any amount band) stays unmatched.
    assert _verdicts(result) == [
        ("review", l1["id"], False),
        ("review", l2["id"], False),
        ("none", None, False),
    ]
    for rm in result:
        for cand in rm.candidates:
            assert not any("shared reference" in r for r in cand.reasons)
    _near_dup = ["same amount, similar description, near date, different source"]
    assert _full(result) == [
        _pin(0, "review", l1["id"], "near_duplicate", 0.7478, _near_dup),
        _pin(1, "review", l2["id"], "near_duplicate", 0.75, _near_dup),
        _pin(2, "none", None, None, None, [], candidate_count=0),
    ]


# ── jpy_zero_decimal_book — recurring ¥1,000 same-day charges (zero-decimal currency) ──


def test_jpy_zero_decimal_book(isolate_stores):
    """A zero-decimal-currency book full of IDENTICAL figures: two ¥1,000 konbini charges the
    same day, both already alert-booked with wordless notes. Every cross-pairing is an exact
    same-day amount hit, so the engine must hold the line: wordless pairs stay REVIEW,
    books-close promotion is REFUSED (the pairing is not unique — each row has another exact
    in-window candidate and each target is contested), 1:1 exclusivity holds, and nothing is
    auto-linkable (no strong band)."""
    _, txns = isolate_stores
    l1 = txns.append(expense("sakura", 1000.0, "2026-06-07", "Konbini run", source="ai", ccy="JPY"))
    l2 = txns.append(expense("sakura", 1000.0, "2026-06-07", "Konbini run", source="ai", ccy="JPY"))
    rows = [
        expense("sakura", 1000.0, "2026-06-07", "LAWSON SHIBUYA JP", ccy="JPY"),
        expense("sakura", 1000.0, "2026-06-07", "LAWSON SHIBUYA JP", ccy="JPY"),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"sakura"})
    )

    result = matching.match_batch(rows, context=ctx)

    assert _verdicts(result) == [
        ("review", l1["id"], False),
        ("review", l2["id"], False),
    ]
    for rm in result:
        assert rm.assigned.score < matching.SCORE_STRONG  # promotion refused → never strong
    _wordless = ["same account, same amount, same day — wording differs (review)"]
    assert _full(result) == [
        _pin(0, "review", l1["id"], "near_duplicate", 0.75, _wordless, candidate_count=2),
        _pin(1, "review", l2["id"], "near_duplicate", 0.75, _wordless, candidate_count=2),
    ]


# ── non_latin_book — Vietnamese/Chinese descriptors: exact tiers work, drift is a CHOICE ──


def test_non_latin_book_engine_exact_tier_still_forms(isolate_stores):
    """Non-Latin-script descriptors through the ENGINE: a Chinese convenience-store charge,
    already booked with different Chinese wording, still pairs on the exact-amount same-day
    wordless tier (review-capped — wording contributes nothing, and that's fine)."""
    _, txns = isolate_stores
    booked = txns.append(
        expense("tw_card", 250.0, "2026-06-10", "全家便利商店 忠孝店", source="ai", ccy="TWD")
    )
    rows = [expense("tw_card", 250.0, "2026-06-10", "全家便利商店台北", ccy="TWD")]

    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert _verdicts(result) == [("review", booked["id"], False)]
    assert result[0].assigned.klass == matching.MatchClass.NEAR_DUPLICATE
    assert _full(result) == [
        _pin(
            0,
            "review",
            booked["id"],
            "near_duplicate",
            0.75,
            ["same account, same amount, same day — wording differs (review)"],
        )
    ]


def test_non_latin_book_drift_tier_now_fires_on_fragment_tokens(isolate_stores):
    """CHANGED BY RFC 2026-07-29 §D2 — and the direction of the change is the point.

    The retired ``ledger_diff`` twin matcher tokenized wording as ``[a-z]{3,}``, which shreds
    Vietnamese diacritics into nothing, so its settled-drift tier NEVER fired for a non-Latin
    book. That was a documented, deliberate gap (this test used to pin it as such).

    Absorbing the tier into ``_classify`` retires that tokenizer in favour of the engine's own
    ``desc_tokens``, which splits on ``[^a-z0-9]+`` and therefore FRAGMENTS the same text into
    1–2 character junk rather than dropping it: "Bún chả Hà Nội" → ``{b, ch, h, i, n}``. The
    drift tier's shared-token requirement is satisfied by those fragments, so the second pairing
    below now forms — on grammar debris, not on a merchant name.

    That is **RFC R4, explicitly OUT OF SCOPE and explicitly named**: "the engine's own
    fragmentation remains and could in principle manufacture false Jaccard overlap between two
    Vietnamese descriptions… The fix (NFKD + ``Mn``-strip) changes scoring for EVERY surface and
    every golden book. It needs its own change, its own ADR and a golden-book re-pin. Do not fold
    it into this RFC."

    So this pin is now the R4 tripwire: it holds the current behaviour, weak band and junk tokens
    and all, so the normalization change lands as a visible diff HERE with this docstring
    attached — not silently. The mitigation meanwhile is the band itself: a fragment-token pairing
    is WEAK, so it can never promote, never auto-merge and never reach ``check()``'s review
    verdict. It is an offer the owner sees and accepts, not a decision made for them."""
    _, txns = isolate_stores
    e_exact = txns.append(
        expense("vn_wallet", 280000.0, "2026-06-18", "Phở bò tái", source="ai", ccy="VND")
    )
    e_drift = txns.append(
        expense("vn_wallet", 279000.0, "2026-06-18", "Bún chả Hà Nội", source="ai", ccy="VND")
    )
    rows = [
        expense("vn_wallet", 280000.0, "2026-06-18", "PHỞ BÒ TÁI QUẬN 2", ccy="VND"),
        expense("vn_wallet", 285000.0, "2026-06-18", "BÚN CHẢ 26 LÊ VĂN HƯU", ccy="VND"),
    ]

    result = matching.match_batch(
        rows, context=matching.BatchContext(complete=False), weak_tier=True
    )

    assert _full(result) == [
        # Exact amount + fragment overlap → the ordinary near-duplicate tier, unchanged.
        _pin(
            0,
            "strong",
            e_exact["id"],
            "near_duplicate",
            1.0,
            ["same amount, similar description, near date, different source"],
            candidate_count=2,
        ),
        # 2.1% apart → the settled-drift tier, banded WEAK. Was: no twin at all.
        _pin(
            1,
            "weak",
            e_drift["id"],
            "near_duplicate",
            0.44,
            ["same account, settled 2.1% off, same day — wording overlaps (b, ch, h, n)"],
            candidate_count=2,
        ),
    ]


# ── salary_refund_book — same money in and out the same week, NOT a transfer ──


def test_salary_refund_book(isolate_stores):
    """Same amount in (salary, categorized income) and out (a refund-shaped payment) within
    one week across two accounts — the classic false transfer-pair look-alike. The pair may
    ASK (review) but must NEVER reach strong or auto-link: the categorized-leg cap (owner
    decision #2, 2026-07-16) holds even on a confirmed route, and books-close promotion over
    a complete both-accounts window must not launder the cap away."""
    _, txns = isolate_stores
    # A confirmed meridian↔cc route, so the ONLY thing keeping this pair in review is the category.
    txns.append(
        {
            "kind": "transfer",
            "date": "2026-01-05",
            "description": "seed route",
            "from_account_id": "meridian",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "to_account_id": "cc",
            "to_amount": 100.0,
            "to_currency": "SGD",
        }
    )
    salary = txns.append(
        income("meridian", 2500.0, "2026-06-01", "Salary June", source="ai", category="cat_salary")
    )
    rows = [expense("cc", 2500.0, "2026-06-03", "DEPOSIT REFUND PAID OUT")]
    ctx = matching.BatchContext(
        complete=True,
        window=("2026-06-01", "2026-06-30"),
        account_ids=frozenset({"cc", "meridian"}),
    )

    result = matching.match_batch(rows, context=ctx)

    assert _verdicts(result) == [("review", salary["id"], False)]
    rm = result[0]
    assert rm.assigned.klass == matching.MatchClass.TRANSFER_PAIR
    assert rm.assigned.score < matching.SCORE_STRONG
    assert any("categorized leg" in r for r in rm.assigned.reasons)
    assert _full(result) == [
        _pin(
            0,
            "review",
            salary["id"],
            "transfer_pair",
            0.75,
            [
                "opposite-direction legs across two accounts, same base amount & near date "
                "(categorized leg — review)"
            ],
            assigned_become_transfer=True,
        )
    ]


# ── estimate_drift_book — N agent-estimated entries vs N settled statement rows ──


def test_estimate_drift_book(isolate_stores):
    """The classic agent-estimate-vs-settled cluster, now through the ENGINE (RFC §D2): three
    same-day statement rows (settled, ~3.6–3.8% above the booking-time estimates — an FX fee) vs
    three agent-booked ledger entries. Every cross-pairing reaches the settled-drift tier (within
    ``TWIN_SETTLED_DRIFT_TOL``, one shared wording token, same day), and every one of them scores
    IDENTICALLY once clamped into the weak band — so the contested-cluster refinement is what
    decides, and it must assign ORDER-PRESERVING partners (min Σ fidelity², bigger estimate ↔
    bigger settled figure), never greedy's crossed leftovers.

    This is the book that proves absorbing the tier lost nothing: the retired ``_rank_twin_pairs``
    reached the same three pairings through its own fidelity ranking and its own copy of
    ``_global_claim_assignment``. Now there is one of each. ``alternatives`` is empty on every row
    because each near-tie target was claimed by another row — exactly the 1:1 property the twin
    matcher was hand-rolling."""
    _, txns = isolate_stores
    e_1411 = txns.append(
        expense("meridian", 14.11, "2026-06-15", "Anthropic estimate A", source="agent")
    )
    e_1418 = txns.append(
        expense("meridian", 14.18, "2026-06-15", "Anthropic estimate B", source="agent")
    )
    e_1425 = txns.append(
        expense("meridian", 14.25, "2026-06-15", "Anthropic estimate C", source="agent")
    )
    rows = [
        expense("meridian", amt, "2026-06-15", "ANTHROPIC SAN FRANCISCO")
        for amt in (14.66, 14.71, 14.80)
    ]

    result = matching.match_batch(
        rows, context=matching.BatchContext(complete=False), weak_tier=True
    )

    # Order-preserving 1:1 — smallest estimate ↔ smallest settled figure, and so on up.
    assert _full(result) == [
        _pin(
            0,
            "weak",
            e_1411["id"],
            "near_duplicate",
            0.44,
            ["same account, settled 3.8% off, same day — wording overlaps (anthropic)"],
            candidate_count=3,
        ),
        _pin(
            1,
            "weak",
            e_1418["id"],
            "near_duplicate",
            0.44,
            ["same account, settled 3.6% off, same day — wording overlaps (anthropic)"],
            candidate_count=3,
        ),
        _pin(
            2,
            "weak",
            e_1425["id"],
            "near_duplicate",
            0.44,
            ["same account, settled 3.7% off, same day — wording overlaps (anthropic)"],
            candidate_count=3,
        ),
    ]
    # Weak never promotes and never auto-merges: both gates test the fence/strong thresholds,
    # and the band sits below them by construction.
    assert all(rm.assigned.score < matching.SCORE_FENCE for rm in result)
    assert all(rm.assigned.weak for rm in result)
    # With the tier OFF (every other caller) the whole cluster is invisible, as it always was.
    off = matching.match_batch(rows, context=matching.BatchContext(complete=False))
    assert _verdicts(off) == [("none", None, False)] * 3


# ── pocketpay_incident_book — the divergence the RFC exists to close ──────────────────────


def _pocketpay_row():
    return expense("pocketpay", 500000.0, "2026-06-18", "NAP TIEN VAO VI POCKETPAY", ccy="VND")


def _pocketpay_transfer(txns, from_acct):
    return txns.append(
        {
            "kind": "transfer",
            "date": "2026-06-18",
            "description": "Transfer to HO KINH DOANH THAI HA",
            "from_account_id": from_acct,
            "from_amount": 500000.0,
            "from_currency": "VND",
            "to_account_id": "vendor",
            "to_amount": 500000.0,
            "to_currency": "VND",
            "source": "ai",
            "id": _next_id(),
        }
    )


def test_pocketpay_incident_book_engine_calls_a_shared_leg_an_echo_never_a_near_dup(isolate_stores):
    """A reported case (2026-07-28), through the ENGINE — half one.

    When the booked transfer's DEBIT leg genuinely sits on the statement's own account, the
    engine has a real, correct claim to make: F06 TRANSFER_LEG_ECHO ("this debit entry matches
    one leg of an existing transfer"), review-capped and side-qualified. What it never says is
    NEAR_DUPLICATE — F14a (matching.py, "NEAR_DUPLICATE is only for expense/income") refuses
    that outright. The retired twin matcher made exactly the near-duplicate claim instead, with
    no kind gate at all; the kind gate is now the only thing standing between the settled-drift
    tier and that pairing (pinned: tests/dedup/test_twin_invariant_closure.py::
    test_kind_gate_alone_refuses_a_transfer_on_the_statements_own_wallet).
    """
    _, txns = isolate_stores
    transfer = _pocketpay_transfer(txns, "pocketpay")
    rows = [_pocketpay_row()]

    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert _full(result) == [
        _pin(
            0,
            "review",
            transfer["id"],
            "transfer_leg_echo",
            0.75,
            ["This debit entry matches one leg of an existing transfer."],
            assigned_echo_side="debit",
        )
    ]


def test_pocketpay_incident_book_engine_refuses_an_unrelated_transfer_entirely(isolate_stores):
    """A reported case (2026-07-28), through the ENGINE — half two, the load-bearing one.

    Move the booked transfer's legs off the statement's account and every engine tier closes:
    no transfer pair (the transfer is not a bare single leg), no echo (neither leg matches),
    no near-duplicate (F14a: kind ``transfer`` is not ``expense``/``income``). ZERO candidates.

    The retired twin matcher, on the same two records, paired them and put the transfer's id in
    ``twin_eligible`` — the commit-time merge acceptance set (RFC §1b/§1c). ``_rank_twin_pairs``
    gated on currency, date and amount only: no kind, no account, no invariant. That was the
    whole argument for one engine; this stays as the fail-loud contrast.
    """
    _, txns = isolate_stores
    _pocketpay_transfer(txns, "riverside")
    rows = [_pocketpay_row()]

    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert _full(result) == [_pin(0, "none", None, None, None, [], candidate_count=0)]
