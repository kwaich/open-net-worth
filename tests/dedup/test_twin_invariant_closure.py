"""Invariant closure for the statement settled-drift tier — the 2026-07-29 reported case.

``ledger_diff._rank_twin_pairs`` used to be a second, independent "these might be the same
transaction" matcher sitting beside ``services/matching._classify``. It applied NONE of the
engine's invariants, and two of the gaps were load-bearing:

* **Sign** — ``amt = abs(float(entry.get("amount") or 0.0))`` and ``CanonicalRow.amount`` is a
  positive magnitude with the sign carried separately in ``direction``. An inflow and an
  outflow were therefore INDISTINGUISHABLE to the pairing loop. Reported case: a PocketPay
  Wallet statement's **+₫500,000 auto top-up** (money IN) was offered as the twin of an unrelated
  **−₫500,000 expense** (money OUT) already in the ledger. The magnitudes matched exactly, and
  an exact-magnitude pairing SKIPPED the shared-wording gate entirely, so the pairing formed on
  zero descriptive evidence.
* **Kind** — ``_collect_uncovered`` excludes only ``deleted`` and ``kind == "adjustment"``, so
  an uncovered ``kind=transfer`` ledger entry was an eligible twin for an ordinary expense
  statement row. ``matching._classify`` refuses that pairing outright (F14a).

Neither was reachable by prompt-tuning: ``lucius_pass._MATCH_FLAG_TYPES`` covers only the real
engine's flag types, so Lucius could not veto a ``statement_twin`` at all.

**These pins were ``xfail(strict=True)`` and now pass.** RFC
``.claude/rfcs/2026-07-29-one-match-engine-instructions-as-policy.md`` §D2 landed: the drift tier
is ``matching._classify_settled_drift``, reached only from inside ``_classify``'s near-duplicate
branch, so it inherits every gate above it — F20 adjustment, F05 same-feed, F03
distinct-same-source, F23 investment, **F14a kind equality**, F14c sub-unit, **same direction**
and account overlap. The harness therefore drives ``matching.match_batch(..., weak_tier=True)``,
which is the one code path that answers this question now; the assertions are unchanged in
substance. ``rm.assigned is None`` IS the acceptance statement: ``decision_commit``'s merge guard
accepts a row's own assigned id or an uncovered manual-link target and nothing else, so an
unassigned row has no engine-derived acceptance at all.

Note *which* gate closes each case — the RFC's §1b CORRECTED block is explicit that getting this
wrong leaves the incident alive. The sign cases are closed by **F14a's kind-EQUALITY leg**
(``income != expense``) plus ``_classify``'s ``same_direction`` check, NOT by any direction check
inside the drift tier. The drift tier has none, and must not be credited with one.

``test_split_settlement_*`` is the opposite kind of pin: it asserts a gap that is DELIBERATELY
PARKED (``docs/launch/BOARD.md``, "A split settlement (N statement rows ↔ 1 ledger entry) can
never match — DEFERRED 2026-07-29"). It passes today and must keep passing until someone
deliberately implements subset-sum matching — at which point it fails loudly instead of the
change landing unnoticed.

All fixtures are SYNTHETIC and reproduce only the SHAPE of the owner's book (amounts, signs,
currency, day gaps, Vietnamese narration). Vietnamese text is deliberate: it keeps the tests
honest about the separately-parked non-Latin tokenization gap (RFC R4) instead of hiding it
behind ASCII prose.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service
from backend.services import matching

_VND = {"VND": 1.0}


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch):
    """Rebind the ledger singletons to fresh per-test tmp files."""
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore

    accounts = AccountStore()
    accounts._path = tmp_path / "accounts.json"
    txns = TransactionStore()
    txns._path = tmp_path / "transactions.json"
    monkeypatch.setattr(ledger_service, "accounts", accounts)
    monkeypatch.setattr(ledger_service, "transactions", txns)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "VND")
    for acct in ("vi", "tiet_kiem", "vendor"):
        accounts.insert({"id": acct, "name": acct, "kind": "asset", "currency": "VND"})
    return accounts, txns


def _row(direction: str, amount: float, description: str, date: str = "2026-07-16") -> dict:
    """The draft a canonical statement row of this direction produces, on the statement's own
    wallet — an expense for money OUT, an income for money IN."""
    if direction == "OUT":
        return {
            "kind": "expense",
            "date": date,
            "description": description,
            "from_account_id": "vi",
            "from_amount": amount,
            "from_currency": "VND",
            "source": "csv_import",
        }
    return {
        "kind": "income",
        "date": date,
        "description": description,
        "to_account_id": "vi",
        "to_amount": amount,
        "to_currency": "VND",
        "source": "csv_import",
    }


def _entry(txns, direction: str, amount: float, note: str, date: str = "2026-07-16") -> dict:
    """A live ledger entry on the same wallet — the "uncovered" side of the old pairing."""
    return txns.append(_row(direction, amount, note, date=date) | {"source": "agent"})


def _match(rows: list[dict]) -> list[matching.RowMatch]:
    """The one code path that answers "is this ledger entry this statement row?" now."""
    return matching.match_batch(
        rows,
        context=matching.BatchContext(complete=False),
        rates=_VND,
        weak_tier=True,
    )


def _pairing(rm: matching.RowMatch):
    return (rm.band, rm.assigned.id if rm.assigned else None)


# ── 1. A reported case: a +₫500,000 top-up is not a −₫500,000 expense ──────────────


def test_inflow_row_is_never_the_twin_of_an_outflow_entry(isolate_stores):
    """A reported case. A PocketPay auto top-up of **+₫500,000** (money IN) and an unrelated
    **−₫500,000** bar tab (money OUT) already in the ledger, same wallet, same day, with
    descriptions sharing not one token. These are opposite-signed movements of money and can
    never be the same transaction.

    Closed by F14a's kind-EQUALITY leg (``income`` != ``expense``) inside ``_classify``, ahead
    of the drift tier — and by ``same_direction`` besides. The drift tier itself has no sign
    check and is never reached."""
    _, txns = isolate_stores
    _entry(txns, "OUT", 500_000.0, "Thanh toán quán bar tối thứ Bảy")

    result = _match([_row("IN", 500_000.0, "Nạp tiền tự động từ Riverside Bank")])

    assert _pairing(result[0]) == ("none", None), (
        "a money-IN statement row was paired with a money-OUT ledger expense: "
        f"{result[0].candidates}"
    )
    assert result[0].candidates == []


@pytest.mark.parametrize(
    ("amount", "row_desc", "entry_note"),
    [
        (500_000.0, "Nạp tiền tự động từ Riverside Bank", "Đặt món GrabFood trưa"),
        (500_000.0, "Nạp tiền tự động từ Riverside Bank", "Mua sắm tại AEON Mall"),
        (1_250_000.0, "Nhận tiền từ ví liên kết", "Thanh toán hoá đơn điện tháng Bảy"),
        (37_000.0, "Hoàn tiền khuyến mãi", "Cà phê sáng"),
    ],
    ids=["bar-shape", "aeon-shape", "large", "small"],
)
def test_direction_gate_holds_at_every_amount(isolate_stores, amount, row_desc, entry_note):
    """Generalises the incident: an INFLOW statement row must never be paired with an OUTFLOW
    ledger entry, at any amount. Nothing in the incident depended on ₫500,000 — only on the two
    magnitudes being equal."""
    _, txns = isolate_stores
    _entry(txns, "OUT", amount, entry_note)

    result = _match([_row("IN", amount, row_desc)])

    assert _pairing(result[0]) == ("none", None), (
        f"IN row ₫{amount:,.0f} paired with an OUT ledger entry −₫{amount:,.0f}"
    )
    assert result[0].candidates == []


def test_direction_gate_holds_in_the_mirror_case(isolate_stores):
    """The mirror of the incident, so the fix cannot be a one-sided sign check: an OUTFLOW
    statement row must never be paired with an INFLOW ledger entry (an income row)."""
    _, txns = isolate_stores
    _entry(txns, "IN", 500_000.0, "Lương tháng bảy")

    result = _match([_row("OUT", 500_000.0, "Thanh toán hoá đơn điện")])

    assert _pairing(result[0]) == ("none", None), (
        f"OUT row paired with an IN ledger entry: {result[0].candidates}"
    )
    assert result[0].candidates == []


def test_same_direction_exact_pair_still_pairs_control(isolate_stores):
    """CONTROL — proves the fixtures above are refused for their SIGN and nothing else.
    Identical setup with the ledger entry on the same side of the money as the statement row
    (both OUT) still forms the pairing it always did — here the ordinary near-duplicate tier
    (exact amount, same day, overlapping wording), well above the fence."""
    _, txns = isolate_stores
    entry = _entry(txns, "OUT", 500_000.0, "Thanh toán quán bar tối thứ Bảy")

    result = _match([_row("OUT", 500_000.0, "Thanh toán quán bar")])

    assert _pairing(result[0]) == ("strong", entry["id"])


def test_same_direction_settled_drift_still_pairs_control(isolate_stores):
    """CONTROL — the tier D2 ABSORBS, not one it drops. A booked estimate 3% under the
    statement's settled figure, same wallet, same day, sharing wording: outside every ordinary
    tolerance (the near-duplicate re-check is 1%), inside ``TWIN_SETTLED_DRIFT_TOL``. It must
    pair, and it must land in the WEAK band — never review, never strong."""
    _, txns = isolate_stores
    entry = _entry(txns, "OUT", 485_000.0, "Thanh toán quán bar tối thứ Bảy")

    result = _match([_row("OUT", 500_000.0, "Thanh toán quán bar")])

    assert _pairing(result[0]) == ("weak", entry["id"])
    assigned = result[0].assigned
    assert assigned.weak is True
    assert matching.SCORE_WEAK <= assigned.score < matching.SCORE_FENCE
    assert result[0].promoted is False


# ── 2. The kind gate — the RFC's named load-bearing defect ───────────────────────────


def _transfer_entry(txns, from_acct: str, amount: float = 500_000.0) -> dict:
    """A genuine uncovered ``kind=transfer`` ledger entry."""
    return txns.append(
        {
            "kind": "transfer",
            "date": "2026-07-16",
            "description": "Chuyển khoản nội bộ",
            "from_account_id": from_acct,
            "from_amount": amount,
            "from_currency": "VND",
            "to_account_id": "vendor",
            "to_amount": amount,
            "to_currency": "VND",
            "source": "agent",
        }
    )


@pytest.mark.parametrize("direction", ["OUT", "IN"])
def test_unrelated_transfer_is_never_proposed_for_an_ordinary_row(isolate_stores, direction):
    """The incident shape: a booked transfer between accounts the statement does NOT route to,
    coincidentally the same magnitude as an ordinary statement row. ``_rank_twin_pairs`` gated
    on currency, date and amount only — no kind, and no ACCOUNT either — so it paired them and
    put the transfer's id in the commit-time merge acceptance set.

    Every engine tier closes: no transfer pair (a two-leg transfer is not a bare single leg),
    no echo (neither leg is on the statement's wallet), no near-duplicate and therefore no
    drift tier (F14a: ``transfer`` is not ``expense``/``income``).

    Parametrised over BOTH row directions on purpose: whichever sign convention applies, one of
    the two cases stays sign-compatible, so the only things that can refuse it are the kind and
    account gates."""
    _, txns = isolate_stores
    entry = _transfer_entry(txns, "tiet_kiem")
    assert entry["kind"] == "transfer"

    result = _match([_row(direction, 500_000.0, "Thanh toán dịch vụ")])

    assert _pairing(result[0]) == ("none", None), (
        "an uncovered kind=transfer ledger entry was proposed for an ordinary "
        f"{direction} statement row: {result[0].candidates}"
    )
    assert result[0].candidates == []


@pytest.mark.parametrize("direction", ["OUT", "IN"])
def test_kind_gate_alone_refuses_a_transfer_on_the_statements_own_wallet(isolate_stores, direction):
    """The kind gate ISOLATED — the account gate cannot be what's doing the work here.

    The transfer's debit leg sits on the statement's OWN wallet, and its amount is 3% off the
    row's: too far for F06's echo (which needs the tight 1%), squarely inside
    ``TWIN_SETTLED_DRIFT_TOL``. So the ONLY tier that could still fire is the settled-drift one
    — and it cannot, because it lives inside ``_classify``'s near-duplicate branch, downstream
    of F14a. That is the entire closure argument for retiring the invariant-free matcher, and
    it is what this pin exists to hold. (When the amounts DO match tightly the engine has a
    real, correct claim — ``TRANSFER_LEG_ECHO``, pinned in
    tests/dedup/test_golden_books.py::test_pocketpay_incident_book_engine_calls_a_shared_leg_an_echo_never_a_near_dup.)
    """
    _, txns = isolate_stores
    _transfer_entry(txns, "vi", amount=485_000.0)

    result = _match([_row(direction, 500_000.0, "Chuyển khoản dịch vụ")])

    assert _pairing(result[0]) == ("none", None), (
        f"a kind=transfer entry reached the drift tier: {result[0].candidates}"
    )
    assert result[0].candidates == []


# ── 3. N:1 split settlement — a PARKED gap, pinned so it flips loudly ────────────────


def test_split_settlement_leaves_the_ledger_entry_uncovered_known_gap(isolate_stores):
    """KNOWN GAP, DELIBERATELY PARKED — do not "fix" this test.

    ``docs/launch/BOARD.md``: *"[Statement import] A split settlement (N statement rows ↔ 1
    ledger entry) can never match — DEFERRED 2026-07-29"* (owner call: *"i don't think the
    engine can match — and we shouldn't try to catch all of these all the times, we can
    improve later"*).

    Real shape from 2026-07-16: one Grab ride booked from its email receipt as a SINGLE ledger
    entry (``base_amount`` S$3.14 = ₫64,000), charged by the merchant as TWO statement rows the
    same day — S$2.94 (₫60,000) + S$0.20 (₫4,000). The parts sum to the whole EXACTLY, and
    neither part matches the whole: ₫60,000 is 6.25% off ₫64,000, outside
    ``TWIN_SETTLED_DRIFT_TOL`` (5%), and ₫4,000 is not close at all. Nothing sums sibling rows
    before comparing, so the ride is double-counted until a human notices.

    All three descriptions DO share the wording token ``grab``, both sides are money OUT, and
    everything is on the one wallet — so the amount band is the only thing refusing these
    pairings; neither the kind, direction, account nor wording gate is doing the work here.

    This asserts the CURRENT behaviour, not the desired one. It is here so that a future
    subset-sum candidate pass fails this test LOUDLY (with this docstring attached) instead of
    quietly changing what "uncovered" means — and so nobody repairs it by accident while working
    on the matching-engine unification RFC, which explicitly excludes it."""
    assert 60_000.0 + 4_000.0 == 64_000.0, (
        "the fixture must be an exact split, or it proves nothing"
    )
    _, txns = isolate_stores
    _entry(txns, "OUT", 64_000.0, "Chuyen xe Grab ve nha")

    result = _match(
        [
            _row("OUT", 60_000.0, "Thanh toan Grab"),
            _row("OUT", 4_000.0, "Phi dich vu Grab"),
        ]
    )

    assert [_pairing(rm) for rm in result] == [("none", None), ("none", None)], (
        "PARKED GAP CHANGED: a split settlement now pairs. If subset-sum matching was "
        f"implemented deliberately, update docs/launch/BOARD.md and rewrite this pin. "
        f"Got {[rm.candidates for rm in result]}"
    )
