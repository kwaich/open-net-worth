"""
Tests for the derive-only payee feature (cheap interim, see docs/rfc/payee.md).

Covers all three layers, kept small:
  * service — distinct-payee derivation (normalization groups spelling variants),
    base recompute (FX, never the stale stored base_amount), most-common category,
    last-seen, sort order, and the deleted/transfer exclusions.
  * normalization — card-processor prefix stripping (SQ *, TST*, PAYPAL *, DD *, etc.),
    known-variant alias expansion (amzn→amazon, mcd→mcdonalds), idempotency, and
    no-over-merging (distinct payees stay distinct).
  * subscriptions — delegates to payees._normalize_payee (single source of truth).
  * API     — GET /api/payees smoke (status + shape) via the in-process TestClient.
  * AI tool — get_payees is registered, is a pure read (proposed_change None), and
    returns the service shape.

Pure + offline: the service tests inject synthetic txn lists + base + rates, so
nothing reads disk or the network.
"""

from __future__ import annotations

import pytest

from backend.services import payees as payees_service


def _expense(d: str, desc: str, amount: float, currency: str = "SGD", **kw) -> dict:
    """A minimal non-deleted expense txn (from-leg carries the native amount)."""
    return {
        "id": kw.get("id", f"{desc}-{d}"),
        "date": d,
        "kind": "expense",
        "description": desc,
        "category": kw.get("category", "food"),
        "from_account_id": "acct-1",
        "from_amount": amount,
        "from_currency": currency,
        # A deliberately WRONG stale base snapshot with NO base_currency stamp — i.e. a
        # legacy / pre-stamp (or post-base-change) row. txn_base_amount's historical-rate
        # lock only trusts base_amount when base_currency matches, so an unstamped row
        # recomputes from the native leg and never trusts this snapshot.
        "base_amount": 999_999.0,
        "deleted": kw.get("deleted", False),
    }


def _income(d: str, desc: str, amount: float, currency: str = "SGD", **kw) -> dict:
    return {
        "id": kw.get("id", f"{desc}-{d}"),
        "date": d,
        "kind": "income",
        "description": desc,
        "category": kw.get("category", "income"),
        "to_account_id": "acct-1",
        "to_amount": amount,
        "to_currency": currency,
        # Stale snapshot, NO base_currency stamp → recompute path (see _expense).
        "base_amount": 999_999.0,
        "deleted": kw.get("deleted", False),
    }


# fx_cache convention: to_base = amount * rate[currency] / rate[base] (rates relative to
# the base pivot, base anchored at 1.0). So 1 USD → 1.35 SGD here.
RATES = {"SGD": 1.0, "USD": 1.35}


# ── service: normalization + grouping ────────────────────────────────────────


def test_normalize_groups_spelling_variants():
    # Dates and #chips are stripped — same merchant normalizes to same key.
    a = payees_service._normalize_payee("Netflix 2026-05-01 #subscriptions")
    b = payees_service._normalize_payee("NETFLIX 2026-05-08 #sub")
    assert a == b == "netflix"
    # Processor prefix "grab*" is stripped — leaving just the remainder.
    c = payees_service._normalize_payee("GRAB *Ride 2026-05-01 #work")
    assert c == "ride"  # "grab*" treated as a processor prefix (stripped)
    # Number IS kept — distinct phone/account numbers produce distinct payee keys.
    d = payees_service._normalize_payee("SEND MONEY TO 82220000")
    e = payees_service._normalize_payee("SEND MONEY TO 93330000")
    assert d == "send money to 82220000"
    assert e == "send money to 93330000"
    assert d != e


def test_list_payees_groups_distinct_payees():
    # Date-variant duplicates collapse to the same key; "fp*" prefix is stripped.
    txns = [
        _expense("2026-05-01", "fp*Kestrel Eats 2026-05-01", 18.0, category="food"),
        _expense("2026-05-08", "fp*Kestrel Eats 2026-05-08", 21.0, category="food"),
        _expense("2026-05-02", "NETFLIX.COM", 18.0, category="subscriptions"),
    ]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    keys = {r["payee_key"] for r in out["payees"]}
    assert keys == {"kestrel eats", "netflix com"}
    assert out["count"] == 2
    fp = next(r for r in out["payees"] if r["payee_key"] == "kestrel eats")
    assert fp["txn_count"] == 2
    assert fp["total_base"] == 39.0  # 18 + 21, native SGD (stale base_amount ignored)
    assert fp["last_seen"] == "2026-05-08"
    assert fp["sample_category"] == "food"


def test_total_base_recomputed_via_fx_not_stored_snapshot():
    # A USD payee: total must be recomputed from the native USD leg at the current rate,
    # NOT the (wrong) stored base_amount.
    txns = [_expense("2026-05-01", "AWS", 100.0, currency="USD", category="software")]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    aws = out["payees"][0]
    assert aws["payee_key"] == "aws"
    # 100 USD * 1.35 = 135.00 SGD (recomputed from the native leg, not the 999_999 snapshot)
    assert aws["total_base"] == 135.0


def test_total_base_locked_when_base_stamp_matches():
    # A row whose stored base_currency matches the reporting base is a historical fact:
    # txn_base_amount trusts its locked base_amount instead of revaluing the native leg.
    # Here the USD leg would recompute to 135 at today's rate, but the row was locked at 130.
    txn = _expense("2026-05-01", "AWS", 100.0, currency="USD", category="software")
    txn["base_amount"] = 130.0
    txn["base_currency"] = "SGD"
    out = payees_service.list_payees(txns=[txn], base="SGD", rates=RATES)
    assert out["payees"][0]["total_base"] == 130.0  # locked, not the 135 recompute


def test_sample_category_is_most_common():
    txns = [
        _expense("2026-05-01", "Tesco", 5.0, category="food"),
        _expense("2026-05-02", "Tesco", 6.0, category="food"),
        _expense("2026-05-03", "Tesco", 7.0, category="household"),
    ]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    assert out["payees"][0]["sample_category"] == "food"


def test_sorted_by_total_base_desc():
    txns = [
        _expense("2026-05-01", "Small", 5.0),
        _expense("2026-05-01", "Big", 500.0),
    ]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    assert [r["payee_key"] for r in out["payees"]] == ["big", "small"]


def test_excludes_deleted_and_transfers_and_empty_descriptions():
    txns = [
        _expense("2026-05-01", "Live", 10.0),
        _expense("2026-05-01", "Gone", 99.0, deleted=True),
        _expense("2026-05-01", "", 50.0),  # empty description → no payee key
        {  # a transfer has no payee
            "id": "t1",
            "date": "2026-05-01",
            "kind": "transfer",
            "description": "Move to savings",
            "from_account_id": "a",
            "from_amount": 200.0,
            "from_currency": "SGD",
            "to_account_id": "b",
            "to_amount": 200.0,
            "to_currency": "SGD",
            "deleted": False,
        },
    ]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    assert [r["payee_key"] for r in out["payees"]] == ["live"]


def test_income_payee_counted():
    txns = [_income("2026-05-01", "Acme Payroll", 3000.0)]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    assert out["payees"][0]["payee_key"] == "acme payroll"
    assert out["payees"][0]["total_base"] == 3000.0


def test_empty_book_returns_empty_list():
    out = payees_service.list_payees(txns=[], base="SGD", rates=RATES)
    assert out == {"payees": [], "base_currency": "SGD", "count": 0}


# ── normalization: card-processor prefix stripping ────────────────────────────
#
# Bank feeds inject card-processor / POS-network prefixes before the merchant name.
# The normalizer must strip them so charges at the same merchant group together
# regardless of which processor terminal was used on a given day.


def test_square_prefix_stripped():
    # "SQ *" is the Square POS prefix.
    assert payees_service._normalize_payee("SQ *Blue Bottle Coffee") == "blue bottle coffee"
    # Lower-case variant + trailing asterisk run together.
    assert payees_service._normalize_payee("sq* Blue Bottle Coffee") == "blue bottle coffee"


def test_toast_prefix_stripped():
    assert payees_service._normalize_payee("TST* The Diner") == "the diner"
    assert payees_service._normalize_payee("tst *the diner") == "the diner"


def test_paypal_prefix_stripped():
    assert payees_service._normalize_payee("PAYPAL *NETFLIX") == "netflix"
    assert payees_service._normalize_payee("PP *PAYEE") == "payee"


def test_doordash_prefix_stripped():
    assert payees_service._normalize_payee("DD *Pizza Place") == "pizza place"


def test_amazon_prefix_stripped():
    # "AMZN MKTP *" is Amazon Marketplace's bank-feed prefix.  After stripping the
    # prefix the remaining text is the merchant/product slug or a reference number.
    # Numbers are now KEPT — a pure-number slug retains the order/reference number.
    assert payees_service._normalize_payee("AMZN MKTP *12345") == "12345"
    # A named product leaves a non-empty key.
    assert payees_service._normalize_payee("AMZN MKTP *Books") == "books"
    # "AMZN" as a standalone token expands via the variant table.
    assert payees_service._normalize_payee("AMZN") == "amazon"
    # "AMZN*" prefix form (without MKTP) is stripped, leaving the trailing merchant.
    assert payees_service._normalize_payee("AMZN* Electronics") == "electronics"


def test_known_variant_alias_expansion():
    # Whole-word replacement: abbreviations map to canonical names.
    # Numbers are now preserved alongside the expanded name.
    assert payees_service._normalize_payee("MCD") == "mcdonalds"
    assert payees_service._normalize_payee("MCDS Store 42") == "mcdonalds store 42"
    assert payees_service._normalize_payee("WM SUPERCENTER") == "walmart supercenter"


def test_processor_prefix_alone_returns_empty():
    # A description that is ONLY a processor prefix with no merchant name behind it
    # should normalize to "".
    result = payees_service._normalize_payee("SQ *")
    assert result == ""


def test_normalize_idempotent():
    # Applying normalization twice must produce the same result as once.
    desc = "SQ *Blue Bottle Coffee #work 2026-05-01"
    once = payees_service._normalize_payee(desc)
    twice = payees_service._normalize_payee(once)
    assert once == twice


def test_no_over_merging_distinct_payees():
    # Two genuinely different payees must NOT collapse to the same key.
    k1 = payees_service._normalize_payee("Starbucks")
    k2 = payees_service._normalize_payee("Costa Coffee")
    assert k1 != k2

    k3 = payees_service._normalize_payee("Netflix")
    k4 = payees_service._normalize_payee("Spotify")
    assert k3 != k4


def test_processor_prefix_grouping_in_list():
    # Two charges at the same merchant via different processors must land in ONE bucket.
    txns = [
        _expense("2026-05-01", "SQ *Blue Bottle Coffee", 5.0, category="food"),
        _expense("2026-05-08", "Blue Bottle Coffee", 6.0, category="food"),
        _expense("2026-05-10", "TST* Blue Bottle Coffee", 4.50, category="food"),
    ]
    out = payees_service.list_payees(txns=txns, base="SGD", rates=RATES)
    assert out["count"] == 1
    row = out["payees"][0]
    assert row["payee_key"] == "blue bottle coffee"
    assert row["txn_count"] == 3
    assert row["total_base"] == pytest.approx(15.5)


# ── subscriptions: normalization delegation ───────────────────────────────────
#
# subscriptions._normalize_payee must now be the same function as
# payees._normalize_payee (imported, not duplicated) so both features cluster
# descriptions identically.


@pytest.mark.skip(reason="services.subscriptions removed from this branch")
def test_subscriptions_uses_payees_normalizer():
    """Confirm subscriptions imports the payees normalizer, not a local copy."""
    from backend.services import subscriptions as sub_svc

    assert sub_svc._normalize_payee is payees_service._normalize_payee


@pytest.mark.skip(reason="services.subscriptions removed from this branch")
def test_subscriptions_normalizer_strips_processor_prefix():
    """End-to-end: subscriptions detection clusters charges regardless of POS prefix."""
    from backend.services import subscriptions as sub_svc

    assert sub_svc._normalize_payee("SQ *Netflix") == "netflix"


# ── API smoke ────────────────────────────────────────────────────────────────


def test_get_payees_report_endpoint_smoke():
    """GET /api/payees/report — the derived spend report (split from GET /api/payees
    2026-07-06, mirroring the same split already done for the get_payees AI tool)."""
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/payees/report")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"payees", "base_currency", "count"}
    assert isinstance(body["payees"], list)


def test_get_payees_endpoint_smoke(db_isolated):
    """GET /api/payees returns the first-class PayeeStore entities (P4-PAYEE) — no flag
    needed since the 2026-07-06 split, it's the endpoint's only shape now. PayeeStore is
    DB-backed (plan 4d task 2) — db_isolated redirects it to a fresh, empty database."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services import payee_registry

    pid = payee_registry.resolve_payee_id("Anthropic, PBC")
    payee_registry.note_usage(pid, "2026-05-31")

    client = TestClient(app)
    resp = client.get("/api/payees?usage=true")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"payees", "count"}
    assert body["count"] == 1
    row = body["payees"][0]
    assert row["id"] == pid
    assert {"id", "name", "default_categories", "hidden", "txn_count", "last_used"} <= set(row)


# ── AI tool shape ────────────────────────────────────────────────────────────


def test_apply_payee_rules_endpoint_requires_confirm():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/payees/apply-rules", json={"confirm": False})
    assert resp.status_code == 400


def test_apply_payee_rules_endpoint_smoke(db_isolated):
    # PayeeStore is DB-backed (plan 4d task 2) — db_isolated redirects it (and every
    # other unpinned store apply_payee_rules_to_history touches) to a fresh, empty
    # database.
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/payees/apply-rules", json={"confirm": True})
    assert resp.status_code == 200
    body = resp.json()
    assert "updated" in body
    assert isinstance(body["updated"], int)


def test_get_payees_tool_registered_and_pure_read():
    from backend.ai.tools import TOOL_SCHEMAS

    assert "get_payees" in {t["name"] for t in TOOL_SCHEMAS}
    assert "get_payee_report" not in {t["name"] for t in TOOL_SCHEMAS}
