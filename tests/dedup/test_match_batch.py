"""Unit tests for the batch entry point (services/matching.py: BatchContext/RowMatch/
match_batch — RFC batch-aware-dedup).

Pins the BUS/MRT incident (2026-07-16): three identical-fare rides on one statement vs three
correctly alert-booked ledger entries dated a day later each. The pair engine alone can only
ever review-cap this (F21 widened, tests/dedup/test_matching_engine.py); batch completeness is
what lifts an assigned, mutually-exclusive pair to strong (books-close promotion) — and the
dangerous look-alike (two real rides, one booked alert) must stay review, never promote.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching

BUS_MRT_LEDGER_NOTE = "BUS/MRT — Public transport – Beacon Bank Visa Infinite ···4471"


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch, db_isolated):
    """AccountStore/TransactionStore are DB-backed (plan 3 task 4) — db_isolated
    redirects the process-wide DB engine, no per-instance path wiring needed.

    from/to_account_id are now real foreign keys into accounts; every already-booked
    ledger entry here is seeded via ``_book`` -> ``txns.append(...)`` onto the
    synthetic "card" id. txns.append is wrapped to auto-seed a stub account for any
    id not already present (tests.conftest.seed_stub_accounts) BEFORE each write.
    The batch ``rows`` passed to match_batch are never persisted (in-memory only),
    so they need no seeding."""
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
    return f"row-{_ID[0]}"


def expense(
    acct, amt, date, desc, source="import", ccy="SGD", original_amount=None, original_currency=None
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
        "original_amount": original_amount,
        "original_currency": original_currency,
        "external_refs": [{"source": None, "ref": None}],
        "source": source,
        "id": _next_id(),
    }


def _book(txns, acct, amt, date, desc, source, original_amount=None, original_currency=None):
    """Insert an already-booked ledger entry (a persisted twin of a statement row)."""
    return txns.append(
        expense(
            acct,
            amt,
            date,
            desc,
            source=source,
            original_amount=original_amount,
            original_currency=original_currency,
        )
    )


# ── The BUS/MRT trio: 1:1 pairing + books-close promotion ──────────────────────────


def test_bus_mrt_trio_pairs_1to1_and_promotes_to_strong(isolate_stores):
    _, txns = isolate_stores
    # Three alert-booked ledger entries, each dated exactly ONE day after its ride, sources
    # mixed "ai"/"agent" (never "import" — an ingestion channel other than this statement).
    for d, source in (
        ("2026-06-02", "ai"),
        ("2026-06-06", "agent"),
        ("2026-06-10", "ai"),
    ):
        _book(txns, "card", 1.59, d, BUS_MRT_LEDGER_NOTE, source)

    # Three statement rows, well separated so no pair is ambiguous with another pair. Wording
    # shares nothing with the ledger's verbose note (Jaccard ~0.22 — well below DESC_SIMILARITY)
    # so the ONLY pair-level evidence is the widened F21 wordless tier (review-capped at 0.75);
    # promotion is what the batch itself proves.
    rows = [
        expense("card", 1.59, "2026-06-01", "BUS/MRT 900000011 SINGAPORE"),
        expense("card", 1.59, "2026-06-05", "BUS/MRT 900000012 SINGAPORE"),
        expense("card", 1.59, "2026-06-09", "BUS/MRT 900000013 SINGAPORE"),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)

    assert [rm.band for rm in result] == ["strong", "strong", "strong"]
    assert all(rm.promoted for rm in result)
    # Each row paired with its OWN nearest (+1 day) ledger entry — never a cross-pair.
    assert [rm.assigned.txn["date"] for rm in result] == ["2026-06-02", "2026-06-06", "2026-06-10"]
    assert {rm.assigned.id for rm in result} == {t["id"] for t in txns.all()}  # all 3 distinct
    for rm in result:
        assert rm.assigned.klass == matching.MatchClass.NEAR_DUPLICATE
        assert rm.assigned.score < matching.SCORE_STRONG  # the RAW score stays review-capped
        assert "unique pairing in a complete statement window (books-close)" in rm.assigned.reasons


# ── The dangerous counterexample: two real rides, one booked alert ─────────────────


def test_adjacent_rides_counterexample_stays_review_no_promotion(isolate_stores):
    _, txns = isolate_stores
    # Only ONE alert was booked (06-06) even though the statement shows two same-fare rides —
    # the classic "which one does it explain?" ambiguity the books-close soundness argument
    # exists to reject.
    booked = _book(txns, "card", 1.59, "2026-06-06", BUS_MRT_LEDGER_NOTE, "ai")

    rows = [
        expense("card", 1.59, "2026-06-05", "BUS/MRT 900000011 SINGAPORE"),
        expense("card", 1.59, "2026-06-06", "BUS/MRT 900000012 SINGAPORE"),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)

    winner, loser = result[1], result[0]
    assert winner.assigned is not None and winner.assigned.id == booked["id"]
    assert winner.band == "review" and winner.promoted is False
    assert loser.assigned is None and loser.band == "none"


# ── complete=False never promotes ───────────────────────────────────────────────────


def test_incomplete_context_never_promotes(isolate_stores):
    _, txns = isolate_stores
    _book(txns, "card", 1.59, "2026-06-02", BUS_MRT_LEDGER_NOTE, "ai")
    rows = [expense("card", 1.59, "2026-06-01", "BUS/MRT 900000011 SINGAPORE")]

    # A pasted/dictated list claims nothing about completeness (RFC default).
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))
    assert result[0].band == "review"
    assert result[0].promoted is False

    # Even a well-formed window+accounts claim without complete=True must not promote.
    ctx = matching.BatchContext(
        complete=False, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result2 = matching.match_batch(rows, context=ctx)
    assert result2[0].band == "review"
    assert result2[0].promoted is False


# ── same source on both sides never promotes (owner decision #3) ───────────────────


def test_same_source_never_promotes(isolate_stores):
    _, txns = isolate_stores
    # The ledger twin was ALSO written by "import" (e.g. a re-run of the same ingestion path) —
    # a same-source self-pair is excluded even though it would otherwise uniquely qualify.
    _book(txns, "card", 1.59, "2026-06-02", BUS_MRT_LEDGER_NOTE, "import")
    rows = [expense("card", 1.59, "2026-06-01", "BUS/MRT 900000011 SINGAPORE", source="import")]

    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)
    assert result[0].band == "review"
    assert result[0].promoted is False


# ── determinism ──────────────────────────────────────────────────────────────────────


def test_match_batch_is_deterministic(isolate_stores):
    _, txns = isolate_stores
    for d, source in (("2026-06-02", "ai"), ("2026-06-06", "agent"), ("2026-06-10", "ai")):
        _book(txns, "card", 1.59, d, BUS_MRT_LEDGER_NOTE, source)
    rows = [
        expense("card", 1.59, "2026-06-01", "BUS/MRT 900000011 SINGAPORE"),
        expense("card", 1.59, "2026-06-05", "BUS/MRT 900000012 SINGAPORE"),
        expense("card", 1.59, "2026-06-09", "BUS/MRT 900000013 SINGAPORE"),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )

    def _fingerprint(result):
        return [
            (rm.index, rm.band, rm.promoted, rm.assigned.id if rm.assigned else None)
            for rm in result
        ]

    first = _fingerprint(matching.match_batch(rows, context=ctx))
    second = _fingerprint(matching.match_batch(rows, context=ctx))
    assert first == second


# ── reassignment parity (ADR #30 logic, now inside the engine) ─────────────────────


def test_losing_claim_reassigns_to_next_free_candidate(isolate_stores):
    _, txns = isolate_stores
    # Both statement rows initially tie toward the SAME nearest ledger entry (1-day gap either
    # way); the loser must fall back to its own next-ranked FREE candidate rather than end up
    # unmatched — mirrors tests/test_multi_account_import.py::
    # test_losing_exclusive_claim_reassigns_to_next_free_twin, ported into the engine itself.
    e1 = _book(txns, "card", 1.59, "2026-06-11", "BUS/MRT", "ai")
    e2 = _book(txns, "card", 1.59, "2026-06-13", "BUS/MRT", "ai")
    rows = [
        expense("card", 1.59, "2026-06-10", "BUS/MRT 900000011 SINGAPORE"),
        expense("card", 1.59, "2026-06-12", "BUS/MRT 900000012 SINGAPORE"),
    ]
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert result[0].assigned is not None and result[0].assigned.id == e1["id"]
    assert result[1].assigned is not None and result[1].assigned.id == e2["id"]
    assert result[0].assigned.id != result[1].assigned.id  # one existing txn never covers two


# ── shared-reference frequency guard: per-token counting across the batch ───────────


def test_distinct_per_pair_references_all_lift_strong_in_one_batch(isolate_stores):
    """FIX 2 (2026-07-17 generality audit) precision check at the batch layer: three pairs,
    each sharing its OWN distinct 9-digit reference (one row per side — 2 distinct rows per
    token in the whole comparison scope). The merchant-constant guard counts rows PER TOKEN,
    so every pair still lifts to strong — only a token printed on >2 rows (a hotline, an
    outlet code) is suppressed, never "references in general"."""
    _, txns = isolate_stores
    ledger = [
        _book(txns, "card", 45.00, "2026-06-05", "TRANSIT 900000011 TAP", "ai"),
        _book(txns, "card", 32.00, "2026-06-12", "TRANSIT 900000012 TAP", "ai"),
        _book(txns, "card", 28.00, "2026-06-19", "TRANSIT 900000013 TAP", "ai"),
    ]
    rows = [
        expense("card", 45.00, "2026-06-10", "BUS/MRT 900000011 SINGAPORE"),
        expense("card", 32.00, "2026-06-16", "BUS/MRT 900000012 SINGAPORE"),
        expense("card", 28.00, "2026-06-23", "BUS/MRT 900000013 SINGAPORE"),
    ]
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert [rm.band for rm in result] == ["strong", "strong", "strong"]
    assert [rm.assigned.id for rm in result] == [t["id"] for t in ledger]
    for rm, ref in zip(result, ("900000011", "900000012", "900000013")):
        assert rm.assigned.reasons == [f"shared reference {ref}"]


# ── engine backstop (FIX 5, unbounded batch cost) ───────────────────────────────────


def test_match_batch_over_cap_raises(isolate_stores):
    # An unbounded caller (bug, or a hostile payload) must fail loudly and immediately rather
    # than silently burn CPU for minutes on an O(rows × pool) classification pass. The guard is
    # the FIRST statement in match_batch, so this 2001-row construction never actually runs the
    # O(n²) pass — cheap even at the real cap.
    rows = [
        expense("card", 1.00, "2026-06-01", f"Row {i}") for i in range(matching.MAX_BATCH_ROWS + 1)
    ]
    with pytest.raises(ValueError):
        matching.match_batch(rows, context=matching.BatchContext(complete=False))


def test_match_batch_at_cap_does_not_raise(isolate_stores, monkeypatch):
    # Exactly at the cap must still work — the guard is "over", not "at or over". Lower the cap
    # via monkeypatch so this boundary check stays cheap (an O(n²) pass at the real 2000-row
    # cap would be a slow test for no extra coverage).
    monkeypatch.setattr(matching, "MAX_BATCH_ROWS", 5)
    rows = [expense("card", 1.00, "2026-06-01", f"Row {i}") for i in range(5)]
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))
    assert len(result) == 5


# ── ranking fidelity: capped ties resolve by amount closeness, not list order ──────
#
# Owner repro 2026-07-16 (two screenshots, verified against his data): the engine CROSS-LINKED
# two same-day pairs. Statement rows −14.59 (orig 10.95 USD) and −14.62 (orig 10.97 USD) were
# assigned to 'Anthropic, PBC — 10.97' (−14.62, orig 10.97) and 'Anthropic — USD 10.95' (−14.59,
# orig 10.95) respectively — SWAPPED. Both statement rows amount-match BOTH ledger twins under
# the same-day 1% tolerance (F19b/F21), and the review cap (min(score, SCORE_STRONG - 0.05))
# flattens the TRUE pair (exact F22 fingerprint) and the FALSE pair (0.18%-off impostor) to the
# identical 0.75 — pre-fix, the global claim assignment's tie-break fell through to raw
# ledger-pool list order instead of the evidence.


def test_original_amount_fingerprint_pair_is_not_cross_linked(isolate_stores):
    _, txns = isolate_stores
    # Ledger insertion order deliberately puts the row-1 IMPOSTOR (PBC/10.97) first — this is
    # what made list order pick the wrong partner pre-fix (stable-sort ties keep pool order).
    ledger_pbc = _book(
        txns,
        "card",
        14.62,
        "2026-06-07",
        "Anthropic, PBC — 10.97",
        "ai",
        original_amount=10.97,
        original_currency="USD",
    )
    ledger_usd = _book(
        txns,
        "card",
        14.59,
        "2026-06-07",
        "Anthropic — USD 10.95",
        "ai",
        original_amount=10.95,
        original_currency="USD",
    )
    rows = [
        expense(
            "card", 14.59, "2026-06-07", "ANTHROPIC", original_amount=10.95, original_currency="USD"
        ),
        expense(
            "card", 14.62, "2026-06-07", "ANTHROPIC", original_amount=10.97, original_currency="USD"
        ),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)

    assert result[0].assigned is not None and result[0].assigned.id == ledger_usd["id"]
    assert result[0].assigned.txn["description"] == "Anthropic — USD 10.95"
    assert result[0].assigned.txn["from_amount"] == 14.59
    assert result[1].assigned is not None and result[1].assigned.id == ledger_pbc["id"]
    assert result[1].assigned.txn["description"] == "Anthropic, PBC — 10.97"
    assert result[1].assigned.txn["from_amount"] == 14.62


def test_settled_amount_closeness_alone_resolves_the_same_swap_shape(isolate_stores):
    # Same cross-link shape, but with NO original_amount anywhere — the F22 fingerprint never
    # enters the picture, so fidelity must fall back to settled-amount closeness alone and still
    # resolve the pairing correctly (not just "when an F22 fingerprint happens to be present").
    _, txns = isolate_stores
    ledger_pbc = _book(txns, "card", 14.62, "2026-06-07", "Anthropic, PBC — 10.97", "ai")
    ledger_usd = _book(txns, "card", 14.59, "2026-06-07", "Anthropic — USD 10.95", "ai")
    rows = [
        expense("card", 14.59, "2026-06-07", "ANTHROPIC"),
        expense("card", 14.62, "2026-06-07", "ANTHROPIC"),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)

    assert result[0].assigned is not None and result[0].assigned.id == ledger_usd["id"]
    assert result[1].assigned is not None and result[1].assigned.id == ledger_pbc["id"]


def test_contested_cluster_assigns_order_preserving_not_greedy_crossed(isolate_stores):
    """The crossed-partner shape of the twin-matcher owner repro (2026-07-16, statement
    14.66/14.71 vs agent-booked 14.18/14.11), through match_batch's own claim assignment:
    two wordless same-day review-band rows vs two booked entries, every cross-pairing inside
    the same-day 1% band, all four claims flattened to the identical capped score at gap 0 —
    only the fidelity tail differs. Greedy took the single closest claim anywhere
    (100.00↔99.60, diff .40) and forced the crossed leftover (100.30↔99.40, .90); the
    contested-cluster refinement minimizes Σ fidelity² over the tied cluster, which is the
    order-preserving assignment (100.00↔99.40 / 100.30↔99.60 — bigger row, bigger entry)."""
    _, txns = isolate_stores
    ledger_hi = _book(txns, "card", 99.60, "2026-06-07", "Anthropic API subscription", "ai")
    ledger_lo = _book(
        txns, "card", 99.40, "2026-06-07", "Anthropic, PBC - Auto-recharge credits", "agent"
    )
    rows = [
        expense("card", 100.00, "2026-06-07", "MERCHANT POS SG"),
        expense("card", 100.30, "2026-06-07", "MERCHANT POS SG"),
    ]
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assert result[0].assigned is not None and result[0].assigned.id == ledger_lo["id"]
    assert result[1].assigned is not None and result[1].assigned.id == ledger_hi["id"]
    assert [rm.band for rm in result] == ["review", "review"]
    # Determinism through the refinement: a second pass yields the identical assignment.
    rerun = matching.match_batch(rows, context=matching.BatchContext(complete=False))
    assert [rm.assigned.id for rm in rerun] == [ledger_lo["id"], ledger_hi["id"]]


def test_same_figure_adjacent_day_fares_pair_across_instead_of_one_stealing_the_other(
    isolate_stores,
):
    """Owner repro (2026-10-06): two identical S$3.72 BUS/MRT fares on consecutive days vs two
    alert-booked entries dated a day AFTER each ride. The 09-10 statement row has two tied
    candidates (the same-day entry and the +1 entry); the 09-09 row has exactly one (the entry
    a day later — the next is 2 days out, past the wordless cap). Greedy gave the same-day
    entry to the 09-10 row first, stranding the 09-09 row as a brand-new import and leaving
    the 09-11 entry "Ledger only". Both rows are same-figure (fidelity 0.0) claims, so the
    cardinality-first cluster refinement must reach them too: two pairs, not one."""
    _, txns = isolate_stores
    first = _book(txns, "card", 3.72, "2026-06-10", BUS_MRT_LEDGER_NOTE, "automation")
    second = _book(txns, "card", 3.72, "2026-06-11", BUS_MRT_LEDGER_NOTE, "automation")
    rows = [
        expense("card", 3.72, "2026-06-09", "BUS/MRT 900000101 SINGAPORE SG"),
        expense("card", 3.72, "2026-06-10", "BUS/MRT 900000102 SINGAPORE SG"),
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)

    assert result[0].assigned is not None and result[0].assigned.id == first["id"]
    assert result[1].assigned is not None and result[1].assigned.id == second["id"]
    # Row order must not matter: the same two rows reversed pair the same way.
    reversed_result = matching.match_batch(rows[::-1], context=ctx)
    assert reversed_result[0].assigned.id == second["id"]
    assert reversed_result[1].assigned.id == first["id"]


def test_exact_fingerprint_pair_is_never_reshuffled_by_the_cluster_optimizer(isolate_stores):
    """Strong/exact evidence is never re-shuffled by the squared-sum arithmetic: row A holds
    an EXACT F22 fingerprint on entry X (fid 0.0), row B's only claim is a drift claim on the
    same X. Maximizing pairs over the whole cluster would prefer tearing the fingerprint
    (A↔Y drift + B↔X drift = two pairs); instead exact-evidence claims peel greedily BEFORE
    the refinement ever sees the drift remainder — A keeps its fingerprint partner and B
    stays unmatched rather than stealing it."""
    _, txns = isolate_stores
    fingerprint_x = _book(
        txns,
        "card",
        14.62,
        "2026-06-07",
        "Anthropic charge card",
        "ai",
        original_amount=10.97,
        original_currency="USD",
    )
    drift_y = _book(txns, "card", 14.40, "2026-06-07", "Grab booked ride", "agent")
    row_a = expense(
        "card", 14.50, "2026-06-07", "ANTHROPIC", original_amount=10.97, original_currency="USD"
    )
    row_b = expense("card", 14.68, "2026-06-07", "MERCHANT POS SG")  # only X is within 1%
    result = matching.match_batch([row_a, row_b], context=matching.BatchContext(complete=False))

    assert result[0].assigned is not None and result[0].assigned.id == fingerprint_x["id"]
    assert result[0].assigned.fidelity == 0.0
    assert result[1].assigned is None and result[1].band == "none"
    assert drift_y["id"] not in {c.id for c in result[1].candidates}


def test_over_cap_cluster_falls_back_to_greedy_1to1(isolate_stores):
    """A contested component wider than the 8-per-side cap keeps the existing greedy
    behavior: 9 same-day wordless rows × 9 booked entries, every cross-pairing eligible in
    the drift tier (one K9,9 component). No exception, and 1:1 exclusivity holds — nine
    distinct assignments, deterministically."""
    _, txns = isolate_stores
    for i in range(9):
        _book(txns, "card", 99.50 + 0.01 * i, "2026-06-07", f"Ledger {i}", "ai")
    rows = [expense("card", 100.00 + 0.01 * i, "2026-06-07", f"POS {i}") for i in range(9)]
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assigned = [rm.assigned for rm in result]
    assert all(a is not None for a in assigned)
    assert len({a.id for a in assigned}) == 9
    rerun = matching.match_batch(rows, context=matching.BatchContext(complete=False))
    assert [rm.assigned.id for rm in rerun] == [a.id for a in assigned]


def test_over_cap_same_figure_rows_hold_1to1(isolate_stores):
    """Nine rows and nine entries all printing the SAME figure: every claim is exact-tier
    (fidelity 0.0), so the whole 9×9 block peels through the greedy exact path — no cluster
    is ever built, no exception, and 1:1 exclusivity holds."""
    _, txns = isolate_stores
    for i in range(9):
        _book(txns, "card", 1.59, "2026-06-07", f"Ledger {i}", "ai")
    rows = [expense("card", 1.59, "2026-06-07", f"POS {i}") for i in range(9)]
    result = matching.match_batch(rows, context=matching.BatchContext(complete=False))

    assigned = [rm.assigned for rm in result]
    assert all(a is not None for a in assigned)
    assert len({a.id for a in assigned}) == 9


def test_fidelity_never_overrides_score(isolate_stores):
    # A higher-RAW-band candidate (wording-similar, uncapped ~1.0) must still win over an
    # exact-fidelity (F22-fingerprinted, 0.0) but review-CAPPED (0.75) one — fidelity is a
    # tie-breaker strictly AFTER score, never a substitute for it.
    _, txns = isolate_stores
    wording_similar = _book(txns, "card", 50.01, "2026-06-07", "GRAB SG", "ai")
    exact_fingerprint_capped = _book(
        txns,
        "card",
        50.03,
        "2026-06-07",
        "STORE XYZ 998877",
        "ai",
        original_amount=37.50,
        original_currency="USD",
    )
    rows = [
        expense(
            "card",
            50.00,
            "2026-06-07",
            "GRAB SG 491",
            original_amount=37.50,
            original_currency="USD",
        )
    ]
    ctx = matching.BatchContext(
        complete=True, window=("2026-06-01", "2026-06-30"), account_ids=frozenset({"card"})
    )
    result = matching.match_batch(rows, context=ctx)

    assert result[0].candidates[0].id == wording_similar["id"]
    assert result[0].candidates[0].score > matching.SCORE_STRONG
    assert result[0].candidates[1].id == exact_fingerprint_capped["id"]
    assert result[0].candidates[1].score < matching.SCORE_STRONG
    assert result[0].candidates[1].fidelity == 0.0  # perfect F22 fidelity — still loses on score
    assert result[0].candidates[0].fidelity > result[0].candidates[1].fidelity
    assert result[0].assigned is not None and result[0].assigned.id == wording_similar["id"]
