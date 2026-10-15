"""Unit tests for the deterministic Match & Dedup engine (services/matching.py, L0–L2).

Each test pins a row of the RFC Scenario Matrix that the deterministic layers decide on their
own (no LLM). The dangerous must-NOT-merge cases (invariants) are exercised here because they
are the 100% hard gate — the LLM is never the only thing standing between a real charge and a
silent merge.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from tests.conftest import seed_stub_accounts

    accounts = AccountStore()
    txns = TransactionStore()
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    # Most tests here call matching.check() directly on dicts (pure, no persistence).
    # A handful (test_l0_exact_external_ref_noop, test_l0_absorbed_id_noop,
    # test_l1_narrows_from_ledger) append a real row via txns.append(...), always
    # against account id "a1" — from_account_id is now a real FK, so seed it.
    seed_stub_accounts([{"from_account_id": "a1"}])
    return accounts, txns


# Lightweight row builders (no persistence — _classify is pure over dicts). Each row gets a
# UNIQUE id distinct from its external_ref (a fresh draft never shares an id with a candidate).
_ID = [0]


def _next_id():
    _ID[0] += 1
    return f"row-{_ID[0]}"


def _ext_fields(ext):
    """Split a legacy combined ``source:ref`` string into the structured pair the store now keys
    on. Bare (no colon) → source-less, unless it's clearly an email message-id (has ``@``)."""
    if ext is None:
        return {"external_refs": [{"source": None, "ref": None}]}
    if ":" in ext:
        src, ref = ext.split(":", 1)
        return {"external_refs": [{"source": src, "ref": ref}]}
    return {"external_refs": [{"source": ("email" if "@" in ext else None), "ref": ext}]}


def expense(
    acct,
    amt,
    date="2026-03-01",
    desc="Coffee",
    ext=None,
    ccy="SGD",
    batch=None,
    source="csv_import",
):
    return {
        "kind": "expense",
        "date": date,
        "description": desc,
        "from_account_id": acct,
        "from_amount": amt,
        "from_currency": ccy,
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        **_ext_fields(ext),
        "batch_id": batch,
        "source": source,
        "id": _next_id(),
    }


def income(
    acct,
    amt,
    date="2026-03-01",
    desc="Top-up",
    ext=None,
    ccy="SGD",
    batch=None,
    source="csv_import",
):
    return {
        "kind": "income",
        "date": date,
        "description": desc,
        "to_account_id": acct,
        "to_amount": amt,
        "to_currency": ccy,
        "from_account_id": None,
        "from_amount": None,
        "from_currency": None,
        **_ext_fields(ext),
        "batch_id": batch,
        "source": source,
        "id": _next_id(),
    }


# ── L0 exact (scenarios 22/23) ─────────────────────────────────────────────────────


def test_l0_exact_external_ref_noop(isolate_stores):
    _, txns = isolate_stores
    existing = txns.append(expense("a1", 10.0, ext="csv:abc"))
    res = matching.check(expense("a1", 10.0, ext="csv:abc"), profile=matching.FULL)
    assert res.action == "noop"
    assert res.canonical_id == existing["id"]


def test_l0_absorbed_id_noop(isolate_stores):
    _, txns = isolate_stores
    existing = txns.append(
        {
            **expense("a1", 10.0, ext="email:m1"),
            "external_refs": [{"source": "csv", "ref": "later"}],
        }
    )
    res = matching.check(expense("a1", 10.0, ext="csv:later"), profile=matching.FULL)
    assert res.action == "noop" and res.canonical_id == existing["id"]


# ── near-duplicate cross-source (scenarios 1, 9, 11, 14) ───────────────────────────


def test_near_duplicate_cross_source_merges(isolate_stores):
    cand = expense("a1", 25.50, date="2026-03-01", desc="GRAB SG", ext="email:r1", source="email")
    incoming = expense("a1", 25.50, date="2026-03-02", desc="GRAB* SG 491", ext="csv:s1")
    res = matching.check(incoming, candidates=[cand], profile=matching.FULL)
    assert res.action == "review"
    assert res.best.klass == matching.MatchClass.NEAR_DUPLICATE
    assert res.canonical_id == cand["id"]


def test_original_foreign_amount_fingerprint_f22(isolate_stores):
    # A foreign-currency spender's twin captures: the statement row settled S$11.04 carrying
    # the exact printed charge (original ₫220,000) vs a hand-logged VND-denominated leg on
    # the SAME card. FX conversion can't tie them (no VND rate here at all) — the exact
    # original-amount fingerprint can, and needs no rate.
    logged = {
        "kind": "expense",
        "date": "2026-05-22",
        "description": "Cinema night",
        "from_account_id": "card",
        "from_amount": 220000.0,
        "from_currency": "VND",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": "ai",
        "id": _next_id(),
    }
    draft = {
        "kind": "expense",
        "date": "2026-05-22",
        "description": "MPOS*CINEBAR Q HOAN KIEM",
        "from_account_id": "card",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "original_amount": 220000.0,
        "original_currency": "VND",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": "pdf", "ref": "ref:card:747"}],
        "source": "pdf_import",
        "id": _next_id(),
    }
    rates = {"SGD": 1.0}  # NO VND rate — F15 would refuse without the fingerprint
    c = matching._classify(draft, logged, rates)
    assert c is not None and c.klass == matching.MatchClass.NEAR_DUPLICATE
    assert c.score < matching.SCORE_STRONG  # review-capped, never auto-merge grade
    assert "original foreign amount" in c.reasons[0]


def test_original_fingerprint_requires_exact_match_and_an_original(isolate_stores):
    logged = {
        "kind": "expense",
        "date": "2026-05-22",
        "description": "Cinema night",
        "from_account_id": "card",
        "from_amount": 221500.0,
        "from_currency": "VND",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": "ai",
        "id": _next_id(),
    }
    draft = {
        "kind": "expense",
        "date": "2026-05-22",
        "description": "MPOS*CINEBAR",
        "from_account_id": "card",
        "from_amount": 11.04,
        "from_currency": "SGD",
        "original_amount": 220000.0,
        "original_currency": "VND",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": "pdf", "ref": "a"}],
        "source": "pdf_import",
        "id": _next_id(),
    }
    assert matching._classify(draft, logged, {"SGD": 1.0}) is None  # 0.7% off — not exact
    # Two bare cross-currency legs WITHOUT any original figure stay F15-refused (no rate).
    bare = dict(draft)
    bare.pop("original_amount"), bare.pop("original_currency")
    bare["id"] = _next_id()
    assert matching._classify(bare, logged, {"SGD": 1.0}) is None


def test_check_ranks_the_truer_amount_evidence_first_after_a_capped_tie(isolate_stores):
    # RFC batch-aware-dedup fix (owner repro 2026-07-16), ported to the single-row check() path:
    # two same-day candidates both amount-match at the review cap (0.75) — one via an EXACT F22
    # original-amount fingerprint, one via a merely-close (0.18% off) settled amount. Pre-fix,
    # check()'s own candidate sort (`scored.sort(key=lambda c: c.score, reverse=True)`) broke
    # the tie on POOL ORDER, not evidence — this pins the fix on check() itself, a separate sort
    # call site from match_batch's own per-row/global-assignment sorts.
    true_twin = {
        "kind": "expense",
        "date": "2026-06-07",
        "description": "Anthropic — USD 10.95",
        "from_account_id": "card",
        "from_amount": 14.59,
        "from_currency": "SGD",
        "original_amount": 10.95,
        "original_currency": "USD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": "ai",
        "id": _next_id(),
    }
    impostor = {
        "kind": "expense",
        "date": "2026-06-07",
        "description": "Anthropic, PBC — 10.97",
        "from_account_id": "card",
        "from_amount": 14.62,
        "from_currency": "SGD",
        "original_amount": 10.97,
        "original_currency": "USD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": None, "ref": None}],
        "source": "ai",
        "id": _next_id(),
    }
    incoming = {
        "kind": "expense",
        "date": "2026-06-07",
        "description": "ANTHROPIC",
        "from_account_id": "card",
        "from_amount": 14.59,
        "from_currency": "SGD",
        "original_amount": 10.95,
        "original_currency": "USD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": "pdf", "ref": "s1"}],
        "source": "pdf_import",
        "id": _next_id(),
    }
    # Pool order deliberately puts the IMPOSTOR first.
    res = matching.check(incoming, candidates=[impostor, true_twin], profile=matching.SCAN)
    assert res.action == "review"
    assert res.best is not None and res.best.txn["id"] == true_twin["id"]
    assert res.candidates[0].txn["id"] == true_twin["id"]
    assert res.candidates[1].txn["id"] == impostor["id"]


def test_adjustment_rows_never_classify_f20(isolate_stores):
    # LIVE BUG: an account's Starting-Balance opening (kind=adjustment, €4.72) FX-matched a
    # S$6.80 coffee and classified as its TRANSFER PAIR ("already have 'Starting Balance'").
    # An adjustment is a balance correction — never a transfer leg, never a duplicate.
    coffee = expense("card", 6.80, date="2026-06-08", desc="NET*FL BEVERAGES", ext="pdf:x")
    opening = {
        "kind": "adjustment",
        "date": "2026-06-05",
        "description": "Starting Balance",
        "to_account_id": "vantage-eur",
        "to_amount": 4.72,
        "to_currency": "EUR",
        "from_account_id": None,
        "from_amount": None,
        "from_currency": None,
        "external_refs": [{"source": "opening", "ref": "vantage-eur"}],
        "source": "opening",
        "id": _next_id(),
    }
    rates = {"SGD": 1.0, "EUR": 1.44}  # €4.72 ≈ S$6.80 — the live FX coincidence
    assert matching._classify(coffee, opening, rates) is None
    res = matching.check(coffee, candidates=[opening], profile=matching.SCAN, rates=rates)
    assert res.action == "create"


def test_investment_cash_leg_never_transfer_pairs_f23(isolate_stores):
    # Reported bug: a QQQ BUY cash leg (kind=investment, -$650.00 from the Vantage Brokerage USD wallet)
    # FX-coincided with a Meridian Bank statement deposit of +S$940.00 (the day's USD/SGD rate) and
    # classified as its TRANSFER PAIR ("matches an existing transfer" on an equity trade).
    # An investment leg is a POSITION trade's money movement — never a transfer leg.
    deposit = income(
        "meridian-sgd",
        940.00,
        date="2026-06-15",
        desc="Advice Funds Transfer FT26",
        ext="pdf:ref:meridian:ft26",
    )
    qqq_buy = {
        "kind": "investment",
        "date": "2026-06-15",
        "description": "QQQ BUY 2 @ 718.93",
        "from_account_id": "vantage-usd",
        "from_amount": 1437.86,
        "from_currency": "USD",
        "to_account_id": None,
        "to_amount": None,
        "to_currency": None,
        "external_refs": [{"source": "vantage", "ref": "qqq-buy-1"}],
        "source": "ai",
        "id": _next_id(),
    }
    rates = {"SGD": 1.0, "USD": 1.3329}  # $650.00 ≈ S$940.00 — the reported FX coincidence
    assert matching._classify(deposit, qqq_buy, rates) is None
    res = matching.check(deposit, candidates=[qqq_buy], profile=matching.SCAN, rates=rates)
    assert res.action == "create"


def test_wording_drift_same_day_same_amount_flags_review_f21(isolate_stores):
    # LIVE BUG: hand-logged "MPOS*Cinebar" vs the statement's "MPOS*CINEBAR Q HOAN KIEM" —
    # same card, same S$11.04, same day — failed the token-similarity gate (2/5 < 0.6) and
    # was NEVER flagged. Same account + same amount + SAME DAY is evidence on its own.
    logged = expense("card", 11.04, date="2026-05-22", desc="MPOS*Cinebar", source="ai")
    draft = expense(
        "card",
        11.04,
        date="2026-05-22",
        desc="MPOS*CINEBAR Q HOAN KIEM",
        ext="pdf:ref:card:747",
        source="pdf_import",
    )
    c = matching._classify(draft, logged, None)
    assert c is not None and c.klass == matching.MatchClass.NEAR_DUPLICATE
    assert c.score < matching.SCORE_STRONG  # review-only, never auto-merge grade
    res = matching.check(draft, candidates=[logged], profile=matching.SCAN)
    assert res.action == "review"


def test_wording_drift_requires_narrow_gap_and_same_account(isolate_stores):
    # Guardrails on the widened F21 (WORDLESS_MAX_GAP_DAYS = 1): a TWO-day same-amount charge
    # (recurring bus fare) and a same-day charge on a DIFFERENT account both stay unflagged
    # when the wording shares nothing.
    logged = expense("card", 1.59, date="2026-06-09", desc="BUS/MRT ride", source="ai")
    two_days = expense("card", 1.59, date="2026-06-11", desc="TRANSIT 900000012", ext="pdf:a")
    assert matching._classify(two_days, logged, None) is None
    other_acct = expense("card2", 1.59, date="2026-06-09", desc="TRANSIT 900000012", ext="pdf:b")
    assert matching._classify(other_acct, logged, None) is None


# ── F21 widened: ≤1-day gap wordless pairs (RFC batch-aware-dedup) ─────────────────


def test_f21_widened_one_day_gap_exact_amount_flags_review(isolate_stores):
    # RFC batch-aware-dedup — the BUS/MRT incident (2026-07-16): an alert-booked ledger entry
    # is dated the day AFTER the ride (the confirmation lands next-day). An exact amount match
    # compensates for the day skew; wording still shares nothing. Still review-capped at the
    # pair level — batch completeness (match_batch's books-close promotion) is what can lift it.
    logged = expense("card", 1.59, date="2026-06-09", desc="BUS/MRT ride", source="ai")
    next_day = expense("card", 1.59, date="2026-06-10", desc="TRANSIT 900000012", ext="pdf:a")
    c = matching._classify(next_day, logged, None)
    assert c is not None and c.klass == matching.MatchClass.NEAR_DUPLICATE
    assert c.score < matching.SCORE_STRONG  # review-only, never auto-merge grade
    assert "1-day gap" in c.reasons[0]
    res = matching.check(next_day, candidates=[logged], profile=matching.SCAN)
    assert res.action == "review"


def test_f21_widened_one_day_gap_requires_exact_tolerance(isolate_stores):
    # A 1-day gap needs the TIGHT EXACT_AMOUNT_REL_TOL (0.1%) as compensating evidence — a 0.9%
    # drift (within the same-day 1% band, but not 0.1%) must NOT match at a 1-day gap.
    logged = expense("card", 100.00, date="2026-06-09", desc="BUS/MRT ride", source="ai")
    next_day = expense("card", 100.90, date="2026-06-10", desc="TRANSIT 900000012", ext="pdf:a")
    assert matching._classify(next_day, logged, None) is None


# ── Shared-reference strong tier (RFC batch-aware-dedup) ───────────────────────────


def test_shared_reference_token_lifts_score_to_strong(isolate_stores):
    # A long identifier token (a bank trace/reference number) printed in BOTH raw descriptions
    # is near-conclusive evidence of the same event — even with a date gap that would otherwise
    # keep the score below STRONG (a 5-day gap zeroes the date component of _score).
    logged = expense(
        "card", 45.00, date="2026-06-05", desc="Payment ref FT260605AB912345", source="ai"
    )
    draft = expense(
        "card",
        45.00,
        date="2026-06-10",
        desc="POS FT260605AB912345 SETTLEMENT",
        ext="pdf:ref:1",
    )
    c = matching._classify(draft, logged, None)
    assert c is not None and c.klass == matching.MatchClass.NEAR_DUPLICATE
    assert c.score >= matching.SCORE_STRONG
    assert c.reasons == ["shared reference FT260605AB912345"]


def test_nine_digit_shared_reference_still_lifts_to_strong(isolate_stores):
    # FIX 1 (b): raising REF_TOKEN_MIN_DIGITS to 8 must not disturb a genuine long numeric
    # statement reference (9 digits, well clear of the new floor).
    logged = expense("card", 45.00, date="2026-06-05", desc="Payment ref 900000014", source="ai")
    draft = expense(
        "card", 45.00, date="2026-06-10", desc="POS 900000014 SETTLEMENT", ext="pdf:ref:9dig"
    )
    c = matching._classify(draft, logged, None)
    assert c is not None and c.klass == matching.MatchClass.NEAR_DUPLICATE
    assert c.score >= matching.SCORE_STRONG
    assert c.reasons == ["shared reference 900000014"]


def test_shared_six_digit_value_date_does_not_force_strong(isolate_stores):
    # FIX 1 (a) — CRITICAL, reviewer repro 2026-07-16: two UNRELATED transactions (different
    # merchants) that happen to share a 6-digit DDMMYY value-date token in their reference text,
    # same amount, same account, same day. Before the fix REF_TOKEN_MIN_DIGITS=6 + the date-like
    # exclusion (8-digit-only) let "160726" slip through as a "shared reference" and force the
    # pair to SCORE_STRONG — the UI would then default them to merge. It may still land in the
    # review band via the wordless same-day/exact-amount tier (that's fine — a human still has
    # to confirm); it must never be "strong" and must never cite "shared reference".
    logged = expense("card", 4.50, date="2026-07-16", desc="Grab Coffee BATCH 160726", source="ai")
    draft = expense(
        "card", 4.50, date="2026-07-16", desc="Toast Box VALDT 160726", ext="pdf:ref:valdt"
    )
    c = matching._classify(draft, logged, None)
    # Still a candidate via the wordless same-day/exact-amount tier (wording shares nothing,
    # but the amount+day compensate) — capped review, never strong, never "shared reference".
    assert c is not None
    assert c.score < matching.SCORE_STRONG
    assert not any("shared reference" in r for r in c.reasons)
    res = matching.check(draft, candidates=[logged], profile=matching.SCAN)
    assert res.action == "review"
    assert res.best.score < matching.SCORE_STRONG
    assert not any("shared reference" in r for r in res.reasons)


def test_six_and_seven_digit_pure_digit_runs_no_longer_qualify_as_reference_tokens():
    # FIX 1 (c): a direct check on the token extractor — 6- and 7-digit pure-digit runs must
    # never qualify as a reference token (only 8+ does now); an 8-digit non-date-like run still
    # does, so the exclusion is specifically about the lowered floor, not tokens in general.
    empty_amount_digits: frozenset[str] = frozenset()
    assert matching._reference_tokens("REF 123456 END", empty_amount_digits) == set()
    assert matching._reference_tokens("REF 1234567 END", empty_amount_digits) == set()
    assert matching._reference_tokens("REF 34551234 END", empty_amount_digits) == {"34551234"}


def test_shared_reference_token_excludes_card_last4_length(isolate_stores):
    # A 4-digit run (card-last-4 length) is below BOTH length thresholds — it may still
    # contribute to the (unchanged) wordless F21 tier, but never the shared-reference tier.
    logged = expense("card", 45.00, date="2026-06-05", desc="POS 1234 SETTLEMENT", source="ai")
    draft = expense("card", 45.00, date="2026-06-05", desc="POS 1234 PURCHASE", ext="pdf:ref:2")
    c = matching._classify(draft, logged, None)
    assert c is not None
    assert c.score < matching.SCORE_STRONG
    assert not any("shared reference" in r for r in c.reasons)


def test_shared_reference_token_excludes_date_like_run(isolate_stores):
    # An 8-digit run that parses as a plausible calendar date (YYYYMMDD) is excluded — a
    # transaction's own date printed into a reference field isn't a shared-event fingerprint.
    logged = expense("card", 20.00, date="2026-06-05", desc="REF 20260605", source="ai")
    draft = expense("card", 20.00, date="2026-06-05", desc="TXN 20260605 CONFIRM", ext="pdf:a")
    c = matching._classify(draft, logged, None)
    assert c is not None
    assert c.score < matching.SCORE_STRONG
    assert not any("shared reference" in r for r in c.reasons)


def test_shared_reference_token_excludes_amount_digit_string(isolate_stores):
    # A digit run that is just the amount restated (cents form) is excluded — it's not an
    # independent reference, it's the charge amount printed twice.
    logged = expense("card", 1500.00, date="2026-06-05", desc="AMT 150000 REF", source="ai")
    draft = expense("card", 1500.00, date="2026-06-05", desc="PAID 150000 THANKS", ext="pdf:a")
    c = matching._classify(draft, logged, None)
    assert c is not None
    assert c.score < matching.SCORE_STRONG
    assert not any("shared reference" in r for r in c.reasons)


def test_merchant_constant_reference_token_never_lifts_strong(isolate_stores):
    # FIX 2 (2026-07-17 generality audit, frequency guard): a merchant printing its HOTLINE in
    # every descriptor ("CALL 68881234" — 8 digits, not date-like, not the amount) must not
    # strong-lift two DIFFERENT purchases as a "shared reference". The token appears in >2
    # distinct rows of the comparison scope (incoming + candidate pool), so it's a merchant
    # constant; the pair falls to its other tiers (here: wording-similar, capped past a 2-day
    # gap — review, never strong).
    true_twin = expense(
        "card", 45.00, date="2026-06-05", desc="PIZZAHOUSE DELIVERY CALL 68881234", source="ai"
    )
    other_purchases = [
        expense(
            "card", 88.00, date="2026-06-07", desc="PIZZAHOUSE PARTY SET CALL 68881234", source="ai"
        ),
        expense(
            "card", 12.50, date="2026-06-09", desc="PIZZAHOUSE SIDES CALL 68881234", source="ai"
        ),
    ]
    draft = expense("card", 45.20, date="2026-06-10", desc="PIZZAHOUSE CALL 68881234", ext="pdf:h1")
    res = matching.check(draft, candidates=[true_twin, *other_purchases], profile=matching.SCAN)
    assert res.action == "review"
    assert res.best.score < matching.SCORE_STRONG
    for cand in res.candidates:
        assert not any("shared reference" in r for r in cand.reasons)


def test_genuine_once_per_side_reference_still_lifts_with_pool_context(isolate_stores):
    # FIX 2 guard precision: a genuine trace number printed on exactly ONE row per side (2
    # distinct rows in scope) still lifts to strong even when the pool contains OTHER rows —
    # the guard counts rows PER TOKEN, it is not "any busy pool disables the tier".
    logged = expense(
        "card", 45.00, date="2026-06-05", desc="Payment ref FT260605AB912345", source="ai"
    )
    unrelated = [
        expense("card", 45.10, date="2026-06-06", desc="Coffee corner", source="ai"),
        expense("card", 45.30, date="2026-06-07", desc="Bookstore", source="ai"),
    ]
    draft = expense(
        "card", 45.00, date="2026-06-10", desc="POS FT260605AB912345 SETTLEMENT", ext="pdf:g1"
    )
    res = matching.check(draft, candidates=[logged, *unrelated], profile=matching.SCAN)
    assert res.action == "review"
    assert res.best.id == logged["id"]
    assert res.best.score >= matching.SCORE_STRONG
    assert res.best.reasons == ["shared reference FT260605AB912345"]


def test_pdf_import_pairs_with_bank_feed_by_raw_note(isolate_stores):
    # The reason the PDF importer keeps the RAW descriptor in `note` (not a cleaned payee):
    # the near-duplicate gate compares note text, so a bank feed's raw line and the PDF's raw
    # line for the same charge must stay pairable. A cleaned "Cinebar" note would fail this.
    bank = expense(
        "a1",
        11.04,
        date="2026-05-21",
        desc="MPOS*CINEBAR Q HOAN KIEM",
        ext="simplefin:acc:tx1",
        source="simplefin",
    )
    pdf_row = expense(
        "a1",
        11.04,
        date="2026-05-22",
        desc="MPOS*CINEBAR Q HOAN KIEM",
        ext="pdf:ref:a1:747207",
        source="pdf_import",
    )
    res = matching.check(pdf_row, candidates=[bank], profile=matching.FULL)
    assert res.action == "review"  # flagged as a cross-source near-duplicate, not double-booked
    assert res.best.klass == matching.MatchClass.NEAR_DUPLICATE
    assert res.canonical_id == bank["id"]


def test_fx_amount_drift_within_band(isolate_stores):
    # Genuinely cross-currency: email receipt in USD, statement line settled in SGD — the base
    # amounts (via FX rate) differ ~3%, inside the 5% FX band. (F07: foreign only when ccy differ)
    cand = expense(
        "a1", 100.0, date="2026-03-01", desc="Anthropic", ext="email:r1", ccy="USD", source="email"
    )
    incoming = expense("a1", 138.0, date="2026-03-02", desc="ANTHROPIC", ext="csv:s1", ccy="SGD")
    # Provide a real USD rate so F15 doesn't block: USD 100 → SGD ~134.5, settled at 138 (~2.6%).
    rates = {"SGD": 1.0, "USD": 1.345}
    res = matching.check(incoming, candidates=[cand], profile=matching.FULL, rates=rates)
    assert res.action == "review" and res.best.klass == matching.MatchClass.NEAR_DUPLICATE


# ── transfer pair (scenarios 5, 7, 8) ──────────────────────────────────────────────


def test_transfer_pair_debit_credit(isolate_stores):
    debit = expense("meridian", 50.0, date="2026-03-03", desc="To QuickPay", ext="csv:meridian")
    credit = income("quickpay", 50.0, date="2026-03-01", desc="Top-up", ext="csv:quickpay")
    res = matching.check(debit, candidates=[credit], profile=matching.FULL)
    assert res.action == "review"
    assert res.best.klass == matching.MatchClass.TRANSFER_PAIR
    assert res.become_transfer is True


# ── INVARIANTS — must NOT merge (the hard gate) ────────────────────────────────────


def test_invariant_same_feed_keeps_both(isolate_stores):
    # Scenario 18/20 — two identical coffees in ONE import batch are two real charges.
    a = expense("a1", 4.50, desc="Coffee", ext="csv:1", batch="B1")
    b = expense("a1", 4.50, desc="Coffee", ext="csv:2", batch="B1")
    res = matching.check(b, candidates=[a], profile=matching.FULL)
    assert res.action == "create"


def test_invariant_distinct_same_source_event_keeps_all(isolate_stores):
    # Scenario 19 — multiple Anthropic charges, separate emails (distinct msgids) → keep all.
    a = income("a1", 20.0, desc="Anthropic", ext="email:m1", source="email")
    b = income("a1", 20.0, desc="Anthropic", ext="email:m2", source="email")
    res = matching.check(b, candidates=[a], profile=matching.FULL)
    assert res.action == "create"


def test_invariant_distinct_ofx_fitids_keep_both(isolate_stores):
    # Review HIGH-2 — OFX FITIDs are NATIVE per-event ids: two distinct ones on the same account
    # are two real charges and must never be near-dup merged (invariant 1 protects them).
    a = expense("a1", 30.0, desc="Anthropic", ext="ofx:acct:fit1", source="csv_import")
    b = expense("a1", 30.0, desc="Anthropic", ext="ofx:acct:fit2", source="csv_import")
    res = matching.check(b, candidates=[a], profile=matching.FULL)
    assert res.action == "create"


def test_invariant_manual_vs_manual_not_flagged(isolate_stores):
    a = expense("a1", 9.0, desc="Lunch", ext=None, source="manual")
    b = expense("a1", 9.0, desc="Lunch", ext=None, source="manual")
    res = matching.check(b, candidates=[a], profile=matching.FULL)
    assert res.action == "create"


def test_two_chat_logs_same_merchant_same_day_not_flagged(isolate_stores):
    # The KestrelEats case: morning + afternoon $5 KestrelEats both logged via chat (source="ai",
    # no import batch) are TWO real orders → never flagged as duplicates of each other.
    morning = expense("a1", 5.0, date="2026-03-01", desc="KestrelEats", source="ai")
    afternoon = expense("a1", 5.0, date="2026-03-01", desc="KESTRELEATS", source="ai")
    res = matching.check(afternoon, candidates=[morning], profile=matching.FULL)
    assert res.action == "create"


def test_two_logs_mixed_chat_channels_not_flagged(isolate_stores):
    # Same case but logged via two DIFFERENT chat channels (web AI + Telegram) — still both
    # deliberate, still two real charges.
    a = expense("a1", 5.0, date="2026-03-01", desc="KestrelEats", source="ai")
    b = expense("a1", 5.0, date="2026-03-01", desc="KestrelEats", source="telegram")
    res = matching.check(b, candidates=[a], profile=matching.FULL)
    assert res.action == "create"


def test_deliberate_log_vs_import_still_surfaces(isolate_stores):
    # A deliberate chat log vs the later STATEMENT line of the same charge IS a genuine
    # cross-source duplicate → still surfaced (the deliberate veto only suppresses
    # deliberate-vs-deliberate, never deliberate-vs-import).
    chat = expense("a1", 5.0, date="2026-03-01", desc="KestrelEats", source="ai")
    statement = expense("a1", 5.0, date="2026-03-02", desc="KESTRELEATS", ext="csv:s1", batch="B1")
    res = matching.check(statement, candidates=[chat], profile=matching.FULL)
    assert res.action == "review" and res.best.klass == matching.MatchClass.NEAR_DUPLICATE


def test_recurring_subscription_kept_both(isolate_stores):
    # Scenario 12 — Netflix Oct vs Nov: 30+ days apart → outside the window → no match.
    a = expense("a1", 19.99, date="2026-10-05", desc="Netflix", ext="csv:oct")
    b = expense("a1", 19.99, date="2026-11-05", desc="Netflix", ext="csv:nov")
    res = matching.check(b, candidates=[a], profile=matching.FULL)
    assert res.action == "create"


# ── pair signature ─────────────────────────────────────────────────────────────────


def test_pair_signature_order_independent(isolate_stores):
    a = expense("meridian", 50.0, desc="Transfer")
    b = income("quickpay", 50.0, desc="Top-up")
    assert matching.pair_signature(a, b) == matching.pair_signature(b, a)


def test_floor_profile_only_l0(isolate_stores):
    # FLOOR never runs L2 — a fuzzy near-dup is NOT surfaced at the choke (O(1) only).
    cand = expense("a1", 25.50, desc="GRAB", ext="email:r1")
    incoming = expense("a1", 25.50, desc="GRAB SG", ext="csv:s1")
    res = matching.check(incoming, candidates=[cand], profile=matching.FLOOR)
    assert res.action == "create"


# ── L1 narrowing via the real ledger read ──────────────────────────────────────────


def test_l1_narrows_from_ledger(isolate_stores):
    _, txns = isolate_stores
    txns.append(
        expense("a1", 12.0, date="2026-03-01", desc="Spotify", ext="email:r1", source="email")
    )
    incoming = expense("a1", 12.0, date="2026-03-02", desc="SPOTIFY", ext="csv:s1")
    res = matching.check(incoming, profile=matching.FULL)  # no explicit candidates → ledger read
    assert res.action == "review" and res.best.klass == matching.MatchClass.NEAR_DUPLICATE
