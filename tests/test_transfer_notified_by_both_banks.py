"""The emitter is the unit of identity, not the namespace — F03 must not veto on a namespace.

Two live bugs, one cause. A card bill paid from Meridian Bank to Union Bank produced two rows:

    txn_606692ee0000  11 Mar  alerts@bank-a.example   "Successful payment to another bank's card"
    txn_b211fd800000  13 Mar  alerts@bank-b.example   "Union Bank Alerts - Payment Received"

One S$180.00 movement; the sender's bank and the receiver's bank each emailed, two days apart
(GIRO settlement lag). And on 2026-08-28 a card alert and a merchant receipt for ONE S$24.40
phone charge were booked as two rows, two automation runs apart.

Both times the engine did not score the pair low — it never scored it at all. ``_classify``
returned None at the F03 veto, because both rows carry a distinct ``email`` msgid and ``email``
is an AUTHORITATIVE namespace.

F03's premise is that one namespace is one issuer's event feed, so two ids are two events. That
holds for ``ofx`` or ``simplefin`` — one connection, one id per event. It is FALSE for ``email``,
which carries every sender the book has ever received: a Message-ID is authoritative for the
EMAIL, not for the PAYMENT. So the veto's unit is the EMITTER, and it fails CLOSED — an emitter
we cannot establish reads as "may be the same one".

This supersedes the 2026-08-27 same-route transfer exemption, which was shape-scoped and reached
only transfers. Route was a proxy for "two counterparties"; the emitter is the thing itself, so
the transfer case below still passes — via the emitters, not the route.

All THREE places that enforce invariant 1 read one predicate, ``_same_source_event_veto``: the
engine (``_classify``), the merge choke (``ledger.merge_transactions`` F23(a)) and the post-LLM
re-check (``match_review._judge``). They drifted once — the engine exemption landed first and the
merge still refused the live pair with "they are two real events".
"""

from __future__ import annotations

from backend.services import matching

MERIDIAN, UNION, LIGHTHOUSE = "acc_6a8708ab0000", "acc_12d465140000", "acc_lighthouse"
ROUTES = frozenset({frozenset((MERIDIAN, UNION))})
BANK_A, BANK_B = "alerts@bank-a.example", "alerts@bank-b.example"
MERCHANT = "receipts@merchant.example"


def _ref(ref, src="email", emitter=None):
    entry = {"source": src, "ref": ref}
    if emitter:
        entry["emitter"] = emitter
    return entry


def _transfer(
    desc,
    ref,
    date="2026-03-11",
    to_account=UNION,
    amount=180.00,
    batch=None,
    src="email",
    emitter=None,
):
    return {
        "kind": "transfer",
        "date": date,
        "description": desc,
        "from_account_id": MERIDIAN,
        "from_amount": amount,
        "from_currency": "SGD",
        "to_account_id": to_account,
        "to_amount": amount,
        "to_currency": "SGD",
        "source": "ai",
        "batch_id": batch,
        "external_refs": [_ref(ref, src, emitter)],
    }


def _expense(desc, ref, date="2026-03-11", amount=20.0, emitter=None):
    return {
        "kind": "expense",
        "date": date,
        "description": desc,
        "from_account_id": MERIDIAN,
        "from_amount": amount,
        "from_currency": "SGD",
        "source": "ai",
        "external_refs": [_ref(ref, emitter=emitter)],
    }


SENT = _transfer(
    "Successful payment to another bank's card - Union Bank Rewards Card ending 4829",
    "aaaa1111cccc",
    date="2026-03-11",
    emitter=BANK_A,
)
RECEIVED = _transfer(
    "Union Bank Rewards Card Payment Received via Giro/Interbank Transfer",
    "dddd2222eeee",
    date="2026-03-13",
    emitter=BANK_B,
)


def test_both_banks_notifying_one_transfer_is_one_movement() -> None:
    cand = matching._classify(RECEIVED, SENT, None, ROUTES)

    assert cand is not None, "F03 vetoed the pair — nothing scored it and a second row was written"
    assert cand.klass is matching.MatchClass.NEAR_DUPLICATE
    assert cand.score >= matching.SCORE_STRONG
    assert "same transfer route" in "; ".join(cand.reasons)


def test_the_exemption_is_symmetric() -> None:
    """Whichever email lands first, the pair is the same movement."""
    assert matching._classify(SENT, RECEIVED, None, ROUTES) is not None


def test_a_card_alert_and_a_merchant_receipt_are_one_charge() -> None:
    """The 2026-08-28 live bug, and the one the 2026-08-27 route exemption could never reach: an
    EXPENSE, so there is no route to stand in for identity. The bank announced the charge and the
    merchant receipted it — two senders, one payment. Scores below STRONG, so it surfaces for
    review rather than auto-merging, which is the correct outcome for an ambiguous pair."""
    bank = _expense(
        "RIDGELINE MOBILE", "1111aaaa2222", date="2026-03-11", amount=24.40, emitter=BANK_A
    )
    merchant = _expense(
        "Ridgeline Mobile - 300GB plan subscription receipt",
        "3333bbbb4444",
        date="2026-03-12",
        amount=24.40,
        emitter=MERCHANT,
    )
    cand = matching._classify(merchant, bank, None, ROUTES)

    assert cand is not None, "F03 vetoed the pair — the second row was written silently"
    assert cand.klass is matching.MatchClass.NEAR_DUPLICATE
    assert matching.SCORE_FENCE <= cand.score < matching.SCORE_STRONG


def test_it_is_not_email_scoped() -> None:
    """Two banks' OFX exports of one transfer carry distinct FITIDs and hit the same hazard —
    the veto reads the emitter on any authoritative namespace, not a hardcoded ``email``."""
    a = _transfer("BILL PAY UNION 7940", "fit-meridian-1", src="ofx", emitter="ofx:meridian")
    b = _transfer(
        "PAYMENT RECEIVED", "fit-union-9", date="2026-03-13", src="ofx", emitter="ofx:union"
    )

    assert matching._classify(b, a, None, ROUTES) is not None


def test_f03_still_vetoes_two_charges_from_one_merchant() -> None:
    """Scenario 19, untouched: two receipts from ONE sender with distinct msgids are two real
    charges. This is the case F03 exists for, and the reason a blanket email exemption was
    rejected — under this book's ``auto_high`` policy a strong near-duplicate with L3 agreement
    auto-merges, so weakening F03 here would silently collapse two real charges."""
    a = _expense("Anthropic", "m1", emitter=MERCHANT)
    b = _expense("Anthropic", "m2", emitter=MERCHANT)

    assert matching._classify(b, a, None, ROUTES) is None


def test_one_bank_notifying_twice_on_one_route_is_two_movements() -> None:
    """The 2026-08-27 exemption's blind spot, and why it was retired. It exempted ANY two
    same-route two-leg transfers, so two genuine top-ups on one route days apart scored 0.95 and
    became auto-merge eligible. One emitter, two ids ⇒ two movements, route or no route."""
    a = _transfer("Wallet Top Up", "aaaa1111cccc", date="2026-03-11", emitter=BANK_A)
    b = _transfer("Wallet Top Up", "dddd2222eeee", date="2026-03-13", emitter=BANK_A)

    assert matching._classify(b, a, None, ROUTES) is None


def test_an_unresolved_emitter_fails_closed() -> None:
    """The safe direction, and the reason this loosening needs no auto-merge floor. An emitter we
    never stashed (or that was pruned) reads as "may be the same one" — we only ever loosen on a
    difference we can actually establish, matching ``is_authoritative_source``'s own default."""
    known = _transfer("Card payment", "aaaa1111cccc", date="2026-03-11", emitter=BANK_A)
    unknown = _transfer("Card payment", "dddd2222eeee", date="2026-03-13")

    assert matching._same_source_event_veto(unknown, known) is True
    assert matching._same_source_event_veto(known, unknown) is True
    assert matching._classify(unknown, known, None, ROUTES) is None


def test_f03_still_vetoes_transfers_on_different_routes() -> None:
    """Two emitters lift the veto; they do not manufacture a match. Without the route there is
    nothing standing in for the description similarity an ordinary near-duplicate demands."""
    a = _transfer("Card payment", "aaaa1111cccc", emitter=BANK_A)
    b = _transfer(
        "Card payment", "dddd2222eeee", date="2026-03-13", to_account=LIGHTHOUSE, emitter=BANK_B
    )

    assert matching._classify(b, a, None, ROUTES) is None


def test_the_same_feed_veto_still_stands() -> None:
    """A bank de-duplicates its own statement: two rows from one batch are two real movements,
    emitters or no emitters. Invariant 3 is independent of this one."""
    a = _transfer("Card payment", "aaaa1111cccc", batch="B1", emitter=BANK_A)
    b = _transfer("Card payment", "dddd2222eeee", date="2026-03-13", batch="B1", emitter=BANK_B)

    assert matching._classify(b, a, None, ROUTES) is None


def test_the_merge_choke_reads_the_same_veto() -> None:
    """Both call sites answer identically, so a pair the engine flags is a pair the merge accepts
    without ``override``. They drifted once: the engine exemption landed first and the merge still
    refused the live pair with "they are two real events"."""
    assert matching._same_source_event_veto(RECEIVED, SENT) is False
    assert matching._same_source_event_veto(
        _expense("Anthropic", "m2", emitter=MERCHANT),
        _expense("Anthropic", "m1", emitter=MERCHANT),
    )


def test_the_veto_still_reports_the_underlying_fact() -> None:
    """The emitter test is about whether the fact VETOES, not about the fact. Two distinct msgids
    under one namespace is still true of the live pair — callers that want the raw signal keep
    it, and ``_collision_sources`` names exactly where the collision is."""
    assert matching._distinct_same_source_event(RECEIVED, SENT) is True
    assert matching._collision_sources(RECEIVED, SENT) == {"email"}


def test_a_shared_ref_is_not_a_collision() -> None:
    """One ref in common is proof of identity, not of distinctness — there is no collision to
    scope an emitter test to, so the veto never engages regardless of who sent what."""
    a = _expense("Ridgeline Mobile", "shared-ref", emitter=BANK_A)
    b = _expense("Ridgeline Mobile", "shared-ref", date="2026-03-12", emitter=MERCHANT)

    assert matching._collision_sources(a, b) == set()
    assert matching._same_source_event_veto(a, b) is False


def test_every_enforcement_point_reads_the_veto_not_the_bare_fact() -> None:
    """The drift guard. Invariant 1 is enforced in three places; each one that reads
    ``_distinct_same_source_event`` directly is a place the emitter test does not reach, and the
    symptom is silent disagreement — the engine flags a pair one of the others then refuses."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    offenders = [
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if f.name != "matching.py" and "_distinct_same_source_event" in f.read_text()
    ]

    assert offenders == [], f"read _same_source_event_veto instead: {offenders}"
