"""Account NAME → id resolution at the ledger write choke (the twin of
services.categories.resolve_category_ref).

Motivated by a forensic pass over 289 real chat sessions: account-reference resolution was
~20% of every tool error in the corpus — the single largest recurring failure class. The
model would name the account the user had just said out loud ("Meridian Bank Savings") and be refused,
because only an opaque `acc_<hex>` id was accepted. Categories had solved this long ago;
accounts hadn't, and the asymmetry WAS the bug.

Pinned here:
  1. id fast path (an id resolves to itself, no name scan)
  2. exact name, and case-insensitivity
  3. AMBIGUITY IS REFUSED, with the candidates named — never a silent pick. This is the
     load-bearing one: a wrong account is a wrong balance.
  4. archived accounts resolve, but never shadow a live account of the same name
  5. an unresolvable name errors with the CLOSEST real names (recovery_hint), not a bare 404
  6. the wiring: create_transaction, bulk_create_transactions, bulk_update move_to,
     the structured-import path, and the AI tool layer that used to reject a name
     BEFORE the choke ever saw it
  7. the two-leg SHAPE errors name the exact field to add (a separate failure class from
     resolution — see _missing_amount_message)

Offline + isolated: every store the paths touch is monkeypatched into tmp_path.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from backend.core.errors import ValidationError
from backend.db.institution_store import InstitutionStore
from backend.services import accounts as accounts_service
from backend.services import ledger as svc

TODAY = date.today().isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    # every AccountStore()/TransactionStore() instance (fresh or shared) to a fresh,
    # empty database, so no per-instance/module path patch is needed for them anymore.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    return tmp_path


def _mk(name, *, currency="SGD", archived=False, institution="", type_="cash"):
    inst = InstitutionStore().resolve_or_create(institution)
    return svc.accounts.create(
        {
            "name": name,
            "kind": "asset",
            "type": type_,
            "institution_id": inst["id"] if inst else None,
            "currency": currency,
            "initial_balance": 0,
            "archived": archived,
        }
    )


# ── The resolver itself ───────────────────────────────────────────────────────


def test_id_fast_path_resolves_to_itself(env):
    acc = _mk("Meridian Bank Savings")
    assert accounts_service.resolve_account_ref(acc["id"]) == acc["id"]


def test_exact_name_resolves_to_id(env):
    acc = _mk("Meridian Bank Savings")
    assert accounts_service.resolve_account_ref("Meridian Bank Savings") == acc["id"]


@pytest.mark.parametrize(
    "ref", ["meridian bank savings", "MERIDIAN BANK SAVINGS", "  Meridian Bank Savings  "]
)
def test_name_match_is_case_insensitive_and_trimmed(env, ref):
    acc = _mk("Meridian Bank Savings")
    assert accounts_service.resolve_account_ref(ref) == acc["id"]


def test_blank_and_unknown_refs_return_none(env):
    _mk("Meridian Bank Savings")
    assert accounts_service.resolve_account_ref(None) is None
    assert accounts_service.resolve_account_ref("   ") is None
    assert accounts_service.resolve_account_ref("Nonexistent Bank") is None


def test_partial_name_never_silently_resolves(env):
    """Fuzzy is for the ERROR message, never for resolution — 'Meridian' must not pick either."""
    _mk("Meridian Bank Savings")
    _mk("Meridian Bank Multiplier")
    assert accounts_service.resolve_account_ref("Meridian") is None


# ── Ambiguity: the load-bearing refusal ───────────────────────────────────────


def test_ambiguous_name_raises_and_names_every_candidate(env):
    """Two live accounts sharing a name across institutions must NEVER silently pick."""
    a = _mk("Trading", currency="USD", institution="Vantage Brokerage")
    b = _mk("Trading", currency="SGD", institution="Tiger")
    with pytest.raises(ValidationError) as exc:
        accounts_service.resolve_account_ref("Trading")
    msg = str(exc.value)
    assert "ambiguous" in msg.lower()
    # Both ids offered, so the caller can re-send an unambiguous ref without another lookup.
    assert a["id"] in msg and b["id"] in msg
    # And the fields that actually tell them apart.
    assert "USD" in msg and "SGD" in msg
    assert "Vantage Brokerage" in msg and "Tiger" in msg


def test_ambiguity_survives_case_folding(env):
    _mk("Trading", currency="USD")
    _mk("TRADING", currency="SGD")
    with pytest.raises(ValidationError):
        accounts_service.resolve_account_ref("trading")


def test_distinct_currency_wallets_are_not_ambiguous(env):
    """The custodian model names wallets distinctly — these must each resolve cleanly."""
    usd = _mk("Vantage Brokerage USD", currency="USD", institution="Vantage Brokerage")
    sgd = _mk("Vantage Brokerage SGD", currency="SGD", institution="Vantage Brokerage")
    assert accounts_service.resolve_account_ref("Vantage Brokerage USD") == usd["id"]
    assert accounts_service.resolve_account_ref("vantage brokerage sgd") == sgd["id"]


# ── Archived behaviour ────────────────────────────────────────────────────────


def test_archived_account_resolves_by_name(env):
    """An archived account has always been reachable BY ID at the choke (a closed account
    still owns its history); refusing it by name would be an arbitrary asymmetry."""
    acc = _mk("Old Meridian Bank", archived=True)
    assert accounts_service.resolve_account_ref("Old Meridian Bank") == acc["id"]


def test_live_account_wins_over_archived_namesake(env):
    """Archiving is an explicit 'out of use' signal, so this is a reading, not a coin flip."""
    old = _mk("Meridian Bank Savings", archived=True)
    live = _mk("Meridian Bank Savings", archived=False)
    assert accounts_service.resolve_account_ref("Meridian Bank Savings") == live["id"]
    assert accounts_service.resolve_account_ref(old["id"]) == old["id"]  # id still exact


def test_two_archived_namesakes_with_no_live_claimant_are_ambiguous(env):
    _mk("Closed Card", currency="USD", archived=True)
    _mk("Closed Card", currency="SGD", archived=True)
    with pytest.raises(ValidationError) as exc:
        accounts_service.resolve_account_ref("Closed Card")
    assert "archived" in str(exc.value)


def test_two_live_namesakes_are_ambiguous_even_with_an_archived_third(env):
    _mk("Trading", currency="USD")
    _mk("Trading", currency="EUR")
    _mk("Trading", currency="SGD", archived=True)
    with pytest.raises(ValidationError) as exc:
        accounts_service.resolve_account_ref("Trading")
    # Only the two LIVE candidates are offered — the archived one is not a real option here.
    assert str(exc.value).count("id=acc_") == 2


# ── Wiring: the ledger write choke ────────────────────────────────────────────


def test_create_transaction_accepts_account_names_on_both_legs(env):
    src = _mk("Meridian Bank Savings")
    dst = _mk("Lighthouse Bank 360")
    txn = svc.create_transaction(
        {
            "kind": "transfer",
            "date": TODAY,
            "description": "move savings",
            "from_account_id": "Meridian Bank Savings",
            "from_amount": 500,
            "from_currency": "SGD",
            "to_account_id": "lighthouse bank 360",
            "to_amount": 500,
            "to_currency": "SGD",
        }
    )
    # Stored as IDS — a name must never land in the ledger.
    assert txn["from_account_id"] == src["id"]
    assert txn["to_account_id"] == dst["id"]


def test_create_transaction_refuses_an_ambiguous_leg_name(env):
    _mk("Trading", currency="USD")
    _mk("Trading", currency="SGD")
    _mk("Meridian Bank Savings")
    with pytest.raises(ValidationError) as exc:
        svc.create_transaction(
            {
                "kind": "expense",
                "date": TODAY,
                "description": "fee",
                "from_account_id": "Trading",
                "from_amount": 10,
                "from_currency": "SGD",
            }
        )
    assert "ambiguous" in str(exc.value).lower()


def test_name_and_id_dedupe_to_one_row_via_external_ref(env):
    """Resolution runs BEFORE matching.check, whose L0 key includes the account id — a name
    resolved after it would hash to a different row and slip the idempotency guard."""
    acc = _mk("Meridian Bank Savings")
    payload = {
        "kind": "expense",
        "date": TODAY,
        "description": "coffee",
        "from_amount": 5,
        "from_currency": "SGD",
        "external_refs": [{"source": "email", "ref": "abc123"}],
    }
    first = svc.create_transaction({**payload, "from_account_id": acc["id"]})
    second = svc.create_transaction({**payload, "from_account_id": "Meridian Bank Savings"})
    assert first["id"] == second["id"]
    assert len([t for t in svc.transactions.all() if not t.get("deleted")]) == 1


def test_bulk_create_accepts_names(env):
    a = _mk("Meridian Bank Savings")
    b = _mk("Lighthouse Bank 360")
    created, _ = svc.bulk_create_transactions(
        [
            {
                "kind": "expense",
                "date": TODAY,
                "description": "lunch",
                "from_account_id": "Meridian Bank Savings",
                "from_amount": 12,
                "from_currency": "SGD",
            },
            {
                "kind": "income",
                "date": TODAY,
                "description": "refund",
                "to_account_id": "lighthouse bank 360",
                "to_amount": 30,
                "to_currency": "SGD",
            },
        ]
    )
    assert created[0]["from_account_id"] == a["id"]
    assert created[1]["to_account_id"] == b["id"]


def test_bulk_create_refuses_an_ambiguous_name(env):
    _mk("Trading", currency="USD")
    _mk("Trading", currency="SGD")
    with pytest.raises(ValidationError):
        svc.bulk_create_transactions(
            [
                {
                    "kind": "expense",
                    "date": TODAY,
                    "description": "fee",
                    "from_account_id": "Trading",
                    "from_amount": 10,
                    "from_currency": "SGD",
                }
            ]
        )


def test_update_transaction_accepts_a_name(env):
    src = _mk("Meridian Bank Savings")
    dst = _mk("Lighthouse Bank 360")
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": TODAY,
            "description": "lunch",
            "from_account_id": src["id"],
            "from_amount": 12,
            "from_currency": "SGD",
        }
    )
    updated = svc.update_transaction(txn["id"], {"from_account_id": "Lighthouse Bank 360"})
    assert updated["from_account_id"] == dst["id"]


def test_unresolvable_name_error_lists_the_closest_candidates(env):
    """Regression pin: the recovery hint must name real accounts, and because the choke now
    resolves NAMES, the suggestion is directly re-sendable — no second lookup call.

    Uses a transfer because that is the kind whose legs the choke existence-checks (see
    test_unknown_single_leg_is_not_refused_at_the_choke for the non-transfer gap)."""
    _mk("Meridian Bank Savings")
    _mk("Lighthouse Bank 360")
    with pytest.raises(ValidationError) as exc:
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": TODAY,
                "description": "move",
                "from_account_id": "Meridian Bank Savngs",  # typo
                "from_amount": 12,
                "from_currency": "SGD",
                "to_account_id": "Lighthouse Bank 360",
                "to_amount": 12,
                "to_currency": "SGD",
            }
        )
    msg = str(exc.value)
    assert "Meridian Bank Savings" in msg
    assert "list_accounts" in msg


def test_unknown_single_leg_now_raises_the_same_typed_error_a_transfer_does(env):
    """FIXED (plan 3 task 5, INCLUSION B — T4 concern 1, controller-ruled improvement).
    Supersedes the old characterization pin of the same name's GAP (kept in git history, not
    reproduced here) — this is a deliberate behavior CHANGE, not test drift: `services.ledger.
    _validate_legs`'s non-transfer tail now existence-checks each leg it sees, the same way
    the transfer branch already checked both of ITS legs.

    Pre-fix history (why this gap existed and how it was found): before plan 3 task 4, an
    unresolvable single-leg account silently succeeded (`transactions.json` had no schema).
    Task 4 cut `TransactionStore` over to a table where `from_account_id`/`to_account_id` are
    REAL FOREIGN KEYs (plan 3 task 2) — so the exact same unresolvable ref started failing
    CLOSED, but as a raw, uncaught `sqlalchemy.exc.IntegrityError` (no code anywhere catches
    it; `add_transaction`'s REST route only catches `OpenNetWorthError`/
    `DuplicateTransactionError`) — an unstructured 500 for a REST caller that reaches this gap
    without going through the AI-tool layer's `_validate_account_id` first. Task 4's own
    binding forbade touching choke validation logic, so it was pinned as characterization and
    routed to this task as a concern instead of silently "fixed" mid-migration.

    Now: reusing `_find_account`/`_unknown_account_message` verbatim (the transfer branch's
    own mechanism) means a single-leg miss raises the SAME clean, typed `ValidationError` a
    transfer's does — never reaching the database, so no `IntegrityError` is possible here."""
    _mk("Meridian Bank Savings")
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "kind": "expense",
                "date": TODAY,
                "description": "lunch",
                "from_account_id": "Meridian Bank Savngs",  # typo — no such account
                "from_amount": 12,
                "from_currency": "SGD",
            }
        )
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "kind": "income",
                "date": TODAY,
                "description": "refund",
                "to_account_id": "Meridian Bank Savngs",  # the to_account_id leg, same typo
                "to_amount": 12,
                "to_currency": "SGD",
            }
        )
    # The tool layer, which is what a model actually calls, ALSO refuses it — cleanly, same
    # as before this fix (unchanged; a real AI-tool call never reached the DB-error path).
    from backend.ai.tools._shared import _validate_account_id

    with pytest.raises(ValueError):
        _validate_account_id("Meridian Bank Savngs")


def test_rest_post_transactions_refuses_an_unknown_single_leg_account_with_a_4xx(env):
    """API-level companion to the service-level test above — mirrors EXACTLY how the
    transfer branch's own unknown-account refusal is proven at the REST layer
    (`test_rest_post_transactions_accepts_names_and_refuses_ambiguity`'s TestClient pattern):
    POST directly, assert the clean 4xx + message, never a raw 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.ledger import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    _mk("Meridian Bank Savings")
    bad = client.post(
        "/api/transactions",
        json={
            "kind": "expense",
            "date": TODAY,
            "description": "lunch",
            "from_account_id": "no-such-account",
            "from_amount": 12,
            "from_currency": "SGD",
        },
    )
    assert bad.status_code == 422, bad.text
    assert "does not exist" in bad.json()["detail"].lower()


def test_unknown_investment_leg_raises_the_same_typed_error(env):
    """FIX ROUND 1 (plan 3 task 5 review, FIX 3): `_validate_legs`'s ``investment`` branch
    gets the SAME existence check the transfer branch and the non-transfer tail (Inclusion B)
    both already have — the sibling gap Inclusion B's own task report flagged but left
    untouched (out of that task's named scope). Covers BOTH of investment's shapes: the buy
    leg (`from_account_id`, cash out) and the sell leg (`to_account_id`, cash in) — exactly
    one is ever present, never both, so both are exercised here rather than assuming symmetry."""
    _mk("Brokerage")
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "kind": "investment",
                "date": TODAY,
                "description": "buy VOO",
                "from_account_id": "no-such-brokerage",
                "from_amount": 500,
                "from_currency": "SGD",
                "ticker": "VOO",
            }
        )
    with pytest.raises(ValidationError, match="does not exist"):
        svc.create_transaction(
            {
                "kind": "investment",
                "date": TODAY,
                "description": "sell VOO",
                "to_account_id": "no-such-brokerage",
                "to_amount": 500,
                "to_currency": "SGD",
                "ticker": "VOO",
            }
        )


def test_rest_post_transactions_refuses_an_unknown_investment_leg_with_a_4xx(env):
    """API-level companion to the service-level test above — same TestClient pattern as
    `test_rest_post_transactions_refuses_an_unknown_single_leg_account_with_a_4xx`."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.ledger import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    _mk("Brokerage")
    bad = client.post(
        "/api/transactions",
        json={
            "kind": "investment",
            "date": TODAY,
            "description": "buy VOO",
            "from_account_id": "no-such-brokerage",
            "from_amount": 500,
            "from_currency": "SGD",
            "ticker": "VOO",
        },
    )
    assert bad.status_code == 422, bad.text
    assert "does not exist" in bad.json()["detail"].lower()


# ── Wiring: bulk move_to ──────────────────────────────────────────────────────


def test_bulk_update_move_to_accepts_a_name(env):
    src = _mk("Meridian Bank Savings")
    dst = _mk("Lighthouse Bank 360")
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": TODAY,
            "description": "lunch",
            "from_account_id": src["id"],
            "from_amount": 12,
            "from_currency": "SGD",
        }
    )
    svc.bulk_update_transactions([txn["id"]], {"to_account_id": "Lighthouse Bank 360"})
    assert svc.transactions.find_by_id(txn["id"])["from_account_id"] == dst["id"]


# ── Wiring: the structured-import path ────────────────────────────────────────


def test_import_resolve_account_accepts_id_and_name(env):
    from backend.services.import_common import resolve_account

    acc = _mk("Meridian Bank Savings")
    assert resolve_account(acc["id"], None) == acc["id"]
    assert resolve_account(None, "meridian bank savings") == acc["id"]
    # A chosen account_id that is itself a name also resolves.
    assert resolve_account("Meridian Bank Savings", None) == acc["id"]


def test_import_resolve_account_refuses_ambiguity(env):
    """This path used to return whichever namesake it hit FIRST — a whole statement's rows
    could import against an arbitrary one of two same-named accounts."""
    from backend.services.import_common import ImportError_, resolve_account

    _mk("Trading", currency="USD")
    _mk("Trading", currency="SGD")
    with pytest.raises(ImportError_) as exc:
        resolve_account(None, "Trading")
    assert "ambiguous" in str(exc.value).lower()


def test_import_resolve_account_unknown_name_hints(env):
    from backend.services.import_common import ImportError_, resolve_account

    _mk("Meridian Bank Savings")
    with pytest.raises(ImportError_) as exc:
        resolve_account(None, "Meridian Bank Savngs")
    assert "Meridian Bank Savings" in str(exc.value)


# ── Wiring: REST inherits it from the choke (no route-level resolution) ──────


def test_rest_post_transactions_accepts_names_and_refuses_ambiguity(env):
    """The point of resolving AT the choke rather than in the tool layer: REST gets it for
    free, with no per-route code. Pins both halves — the resolve and the refusal."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.ledger import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    a = _mk("Meridian Bank Savings")
    b = _mk("Lighthouse Bank 360")
    _mk("Trading", currency="USD", institution="Vantage Brokerage")
    _mk("Trading", currency="SGD", institution="Tiger")

    ok = client.post(
        "/api/transactions",
        json={
            "kind": "transfer",
            "date": TODAY,
            "description": "rest transfer",
            "from_account_id": "meridian bank savings",
            "from_amount": 250,
            "from_currency": "SGD",
            "to_account_id": "Lighthouse Bank 360",
            "to_amount": 250,
            "to_currency": "SGD",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["from_account_id"] == a["id"]
    assert ok.json()["to_account_id"] == b["id"]

    bad = client.post(
        "/api/transactions",
        json={
            "kind": "expense",
            "date": TODAY,
            "description": "fee",
            "from_account_id": "Trading",
            "from_amount": 10,
            "from_currency": "SGD",
        },
    )
    assert bad.status_code == 422
    assert "ambiguous" in bad.json()["detail"].lower()


# ── Wiring: the AI tool layer (used to reject a name BEFORE the choke) ────────


def test_tool_validator_resolves_a_name(env):
    from backend.ai.tools._shared import _validate_account_id

    acc = _mk("Meridian Bank Savings")
    assert _validate_account_id("Meridian Bank Savings") == acc["id"]
    assert _validate_account_id(acc["id"]) == acc["id"]


def test_tool_validator_refuses_ambiguity_and_hints_on_miss(env):
    from backend.ai.tools._shared import _validate_account_id

    _mk("Trading", currency="USD")
    _mk("Trading", currency="SGD")
    with pytest.raises(ValueError) as exc:
        _validate_account_id("Trading")
    assert "ambiguous" in str(exc.value).lower()

    with pytest.raises(ValueError) as miss:
        _validate_account_id("Tradnig")
    assert "Trading" in str(miss.value)


def test_add_transaction_tool_accepts_an_account_name(env):
    """End-to-end through the tool the forensics measured at a 14% error rate."""
    from backend.ai.tools.ledger import add_transactions

    acc = _mk("Meridian Bank Savings")
    out, _ = asyncio.run(
        add_transactions(
            {
                "account_id": "Meridian Bank Savings",
                "amount": 12.5,
                "currency": "SGD",
                "description": "lunch",
                "date": TODAY,
            }
        )
    )
    payload = json.loads(out)
    assert payload.get("ok") is not False, payload
    stored = [t for t in svc.transactions.all() if not t.get("deleted")]
    assert len(stored) == 1
    assert stored[0]["from_account_id"] == acc["id"]


# ── Two-leg SHAPE errors (a different failure class from resolution) ─────────


def test_missing_to_amount_names_the_field_and_the_same_currency_shortcut(env):
    a = _mk("Meridian Bank Savings", currency="SGD")
    b = _mk("Lighthouse Bank 360", currency="SGD")
    with pytest.raises(ValueError) as exc:
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": TODAY,
                "description": "move",
                "from_account_id": a["id"],
                "from_amount": 500,
                "from_currency": "SGD",
                "to_account_id": b["id"],
            }
        )
    msg = str(exc.value)
    assert "to_amount" in msg
    # Both legs are SGD, so the exact value to send is stated outright.
    assert "500" in msg and "SGD" in msg


def test_missing_to_amount_on_a_cross_currency_transfer_names_the_conversion(env):
    a = _mk("Vantage Brokerage SGD", currency="SGD")
    b = _mk("Vantage Brokerage USD", currency="USD")
    with pytest.raises(ValueError) as exc:
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": TODAY,
                "description": "fund usd wallet",
                "from_account_id": a["id"],
                "from_amount": 500,
                "from_currency": "SGD",
                "to_account_id": b["id"],
            }
        )
    msg = str(exc.value)
    assert "to_amount" in msg
    # Names the destination's own currency — the leg is NOT simply the same number.
    assert "USD" in msg and "SGD" in msg


def test_self_transfer_error_names_the_account_and_the_alternatives(env):
    a = _mk("Meridian Bank Savings")
    with pytest.raises(ValidationError) as exc:
        svc.create_transaction(
            {
                "kind": "transfer",
                "date": TODAY,
                "description": "oops",
                "from_account_id": a["id"],
                "from_amount": 10,
                "from_currency": "SGD",
                "to_account_id": "Meridian Bank Savings",  # same account, named differently
                "to_amount": 10,
                "to_currency": "SGD",
            }
        )
    msg = str(exc.value)
    assert "Meridian Bank Savings" in msg
    # Points at the two real fixes rather than just restating the rule.
    assert "expense" in msg or "income" in msg


def test_missing_from_amount_on_an_expense_names_the_account_currency(env):
    a = _mk("Amex USD", currency="USD")
    with pytest.raises(ValueError) as exc:
        svc.create_transaction(
            {
                "kind": "expense",
                "date": TODAY,
                "description": "lunch",
                "from_account_id": a["id"],
                "from_currency": "USD",
            }
        )
    msg = str(exc.value)
    assert "from_amount" in msg and "USD" in msg
