"""
Tests for the learned per-row category suggester (competitor gap #1, L1).

Covers:
  * service (services/categorize.py) — the deterministic confidence model:
      - a payee categorized N× historically → a high-confidence suggestion;
      - a never-seen payee → NO suggestion (empty/young book → []);
      - a barely-seen payee (one observation) → below threshold, no suggestion;
      - a payee already covered by an ENABLED rule → EXCLUDED (no duplication);
      - corrections + already-categorized rows BOTH feed the history;
      - only uncategorized expense/income rows get a suggestion; sorted by confidence.
  * AI tool — get_category_suggestions is registered and a pure read (proposed_change
    None).

Pure + offline: the service tests inject synthetic txn/correction/rule lists, so
nothing reads disk or the network. Mirrors test_payees.py / test_corrections_service.py.
"""

from __future__ import annotations

import asyncio
import json

from backend.services import categorize as categorize_service


def _txn(
    desc: str, *, category="", kind="expense", deleted=False, id_=None, date="2026-05-01"
) -> dict:
    """A minimal two-leg expense/income txn. category="" means uncategorized."""
    t = {
        "id": id_ or f"{desc}-{date}",
        "date": date,
        "kind": kind,
        "description": desc,
        "category": category,
        "deleted": deleted,
    }
    if kind == "income":
        t["to_account_id"] = "acct-1"
        t["to_amount"] = 10.0
        t["to_currency"] = "SGD"
    else:
        t["from_account_id"] = "acct-1"
        t["from_amount"] = 10.0
        t["from_currency"] = "SGD"
    return t


def _correction(payee_key: str, to_category: str, *, created_at="2026-05-01T00:00:00Z") -> dict:
    return {
        "payee_key": payee_key,
        "payee_sample": payee_key,
        "to_category": to_category,
        "kind": "expense",
        "created_at": created_at,
    }


def _rule(keyword: str, category: str) -> dict:
    return {
        "id": f"automation-{keyword}",
        "name": keyword,
        "status": "active",
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"description_contains": keyword},
            "limit": "each",
        },
        "execution": {
            "kind": "transaction_transform",
            "set": {"category": category},
            "stop": True,
        },
    }


# ── confidence model ──────────────────────────────────────────────────────────


def test_payee_categorized_many_times_yields_high_confidence_suggestion():
    # 4 already-categorized STARBUCKS rows → "food"; one NEW uncategorized STARBUCKS row.
    txns = [
        _txn("STARBUCKS #1", category="food", id_="a1"),
        _txn("STARBUCKS #2", category="food", id_="a2"),
        _txn("STARBUCKS #3", category="food", id_="a3"),
        _txn("STARBUCKS #4", category="food", id_="a4"),
        _txn("STARBUCKS #99", category="", id_="new"),  # uncategorized
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert len(out) == 1
    s = out[0]
    assert s["transaction_id"] == "new"
    assert s["suggested_category"] == "food"
    assert s["evidence_count"] == 4
    # 4/4 consistency * 4/5 evidence = 0.8 → above the 0.6 default threshold.
    assert s["confidence"] >= 0.6


def test_never_seen_payee_yields_no_suggestion():
    # The only row is an uncategorized payee with NO history anywhere.
    txns = [_txn("MYSTERY MART", category="", id_="m")]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert out == []


def test_single_observation_is_below_threshold():
    # One categorized row + one uncategorized row of the same payee. Evidence=1 →
    # confidence 1/1 * 1/2 = 0.5, below the 0.6 default → no fabricated suggestion.
    txns = [
        _txn("RARE SHOP", category="shopping", id_="c1"),
        _txn("RARE SHOP", category="", id_="u1"),
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert out == []


def test_a_payee_an_automation_handles_is_still_suggested():
    """This asserted the OPPOSITE until 2026-08-02, when categorization rules became
    event-triggered Automations.

    A rule used to be a declarative row this function could match against, so it could tell
    "the rules engine already categorizes this payee" and stay quiet. A rule is now a Lucius
    run that judges the match itself, AFTER the row commits — nothing here can answer that
    without running the model. So the suppressor is gone and the suggestion stands.

    The cost is a redundant suggestion for a payee an Automation would also have handled.
    That is the right side to be wrong on: a suggestion is offered, never applied.
    """
    txns = [
        _txn("UBER trip", category="transport", id_="r1"),
        _txn("UBER trip", category="transport", id_="r2"),
        _txn("UBER trip", category="transport", id_="r3"),
        _txn("UBER trip", category="transport", id_="r4"),
        _txn("UBER trip", category="", id_="newu"),
    ]
    rules = [_rule("uber", "transport")]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=rules)
    assert [row["transaction_id"] for row in out] == ["newu"]


def test_corrections_feed_the_history():
    # No categorized ledger rows, but the user corrected this payee to "food" 4×. The
    # correction's payee_key is the _payee_key of the real description (how the recorder
    # stores it), and the uncategorized row derives the same key.
    key = categorize_service.corrections_service._payee_key("STARBUCKS @ ION")
    corrections = [
        _correction(key, "food", created_at=f"2026-05-0{i}T00:00:00Z") for i in range(1, 5)
    ]
    txns = [_txn("STARBUCKS @ ION", category="", id_="cu")]
    out = categorize_service.suggest_categories(
        txns=txns, corrections=corrections, enabled_rules=[]
    )
    assert len(out) == 1
    assert out[0]["suggested_category"] == "food"
    assert out[0]["evidence_count"] == 4


def test_other_counts_as_uncategorized():
    txns = [
        _txn("Cafe", category="food", id_="o1"),
        _txn("Cafe", category="food", id_="o2"),
        _txn("Cafe", category="food", id_="o3"),
        _txn("Cafe", category="other", id_="needs"),  # "other" == uncategorized
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert [s["transaction_id"] for s in out] == ["needs"]
    assert out[0]["suggested_category"] == "food"


def test_transfers_and_deleted_get_no_suggestion():
    txns = [
        _txn("Salary", category="income", kind="income", id_="i1"),
        _txn("Salary", category="income", kind="income", id_="i2"),
        _txn("Salary", category="income", kind="income", id_="i3"),
        # a deleted uncategorized row of the same payee — never suggested
        _txn("Salary", category="", kind="income", id_="del", deleted=True),
        # a transfer is not categorizable
        {
            "id": "x",
            "date": "2026-05-01",
            "kind": "transfer",
            "description": "Salary",
            "category": "",
            "deleted": False,
            "from_account_id": "a",
            "from_amount": 1.0,
            "from_currency": "SGD",
            "to_account_id": "b",
            "to_amount": 1.0,
            "to_currency": "SGD",
        },
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert out == []


def test_sorted_by_confidence_desc():
    txns = [
        # payee A: 4/4 consistent → 0.8
        _txn("AAA", category="food", id_="a1"),
        _txn("AAA", category="food", id_="a2"),
        _txn("AAA", category="food", id_="a3"),
        _txn("AAA", category="food", id_="a4"),
        _txn("AAA", category="", id_="ua"),
        # payee B: 3 food / 1 shopping → modal food, consistency 3/4 → 0.75*0.8=0.6
        _txn("BBB", category="food", id_="b1"),
        _txn("BBB", category="food", id_="b2"),
        _txn("BBB", category="food", id_="b3"),
        _txn("BBB", category="shopping", id_="b4"),
        _txn("BBB", category="", id_="ub"),
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    ids = [s["transaction_id"] for s in out]
    assert ids[0] == "ua"  # higher confidence first
    assert set(ids) == {"ua", "ub"}
    assert out[0]["confidence"] >= out[1]["confidence"]


def test_empty_book_returns_empty_list():
    assert categorize_service.suggest_categories(txns=[], corrections=[], enabled_rules=[]) == []


def test_suggestions_skip_a_category_that_no_longer_exists():
    """The module docstring claims a suggestion's category is never stale (corrections in
    particular are never scrubbed when a category is later deleted/merged) — this proves it:
    a payee whose entire evidence points at a since-deleted category yields NO suggestion
    rather than one naming a dead id."""
    txns = [
        _txn("GHOST CO", category="dead_cat", id_="g1"),
        _txn("GHOST CO", category="dead_cat", id_="g2"),
        _txn("GHOST CO", category="dead_cat", id_="g3"),
        _txn("GHOST CO", category="dead_cat", id_="g4"),
        _txn("GHOST CO", category="", id_="new"),
    ]
    out = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], category_ids={"food"}
    )
    assert out == []
    # The same evidence, with "dead_cat" among the live ids, suggests normally — proves the
    # empty result above is the live-category filter, not some other change to the model.
    out_live = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], category_ids={"dead_cat"}
    )
    assert [s["transaction_id"] for s in out_live] == ["new"]


def test_limit_caps_results():
    txns = []
    for n in range(5):
        # each payee: 3 categorized + 1 uncategorized → a suggestion
        for i in range(3):
            txns.append(_txn(f"P{n}", category="food", id_=f"p{n}-{i}"))
        txns.append(_txn(f"P{n}", category="", id_=f"p{n}-u"))
    out = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], limit=2
    )
    assert len(out) == 2


def _five_payee_txns() -> list[dict]:
    txns = []
    for n in range(5):
        # each payee: 3 categorized + 1 uncategorized → a suggestion
        for i in range(3):
            txns.append(_txn(f"P{n}", category="food", id_=f"p{n}-{i}"))
        txns.append(_txn(f"P{n}", category="", id_=f"p{n}-u"))
    return txns


def test_offset_pages_through_further_suggestions():
    """A page smaller than the true count must be reachable via offset — the caller can walk
    the whole suggestion list, not just the first `limit` (F-bound-tool-results ladder)."""
    txns = _five_payee_txns()
    total = categorize_service.count_category_suggestions(
        txns=txns, corrections=[], enabled_rules=[]
    )
    assert total == 5

    page1 = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], limit=2, offset=0
    )
    page2 = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], limit=2, offset=2
    )
    page3 = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], limit=2, offset=4
    )
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1

    seen = {s["transaction_id"] for s in page1 + page2 + page3}
    full = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], limit=100
    )
    assert seen == {s["transaction_id"] for s in full}
    assert len(seen) == 5


def test_count_category_suggestions_matches_unpaged_length():
    txns = _five_payee_txns()
    assert categorize_service.count_category_suggestions(
        txns=txns, corrections=[], enabled_rules=[]
    ) == len(
        categorize_service.suggest_categories(
            txns=txns, corrections=[], enabled_rules=[], limit=100
        )
    )


# ── AI tool shape ─────────────────────────────────────────────────────────────


def test_get_category_suggestions_tool_registered_and_pure_read(monkeypatch):
    from backend.ai.tools import TOOL_SCHEMAS, execute_tool

    assert "get_category_suggestions" in {t["name"] for t in TOOL_SCHEMAS}

    monkeypatch.setattr(
        categorize_service,
        "suggest_categories",
        lambda **kw: [{"transaction_id": "x", "suggested_category": "food", "confidence": 0.9}],
    )
    monkeypatch.setattr(categorize_service, "count_category_suggestions", lambda **kw: 1)
    text, proposed = asyncio.run(execute_tool("get_category_suggestions", {}))
    assert proposed is None
    body = json.loads(text)
    assert body["ok"] is True
    assert body["showing"] == 1
    assert body["total"] == 1
    assert "note" not in body  # nothing truncated
    assert body["suggestions"][0]["suggested_category"] == "food"


def test_get_category_suggestions_passes_through_params(monkeypatch):
    from backend.ai.tools import execute_tool

    captured = {}

    def fake(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(categorize_service, "suggest_categories", fake)
    monkeypatch.setattr(categorize_service, "count_category_suggestions", lambda **kw: 0)
    asyncio.run(execute_tool("get_category_suggestions", {"min_confidence": 0.8, "limit": 5}))
    assert captured == {"min_confidence": 0.8, "limit": 5, "offset": 0}


def test_get_category_suggestions_tool_notes_truncation_and_offset_reaches_rest(monkeypatch):
    """End to end (no mocking of suggest_categories itself): a real 5-suggestion book, capped
    to 2, reports showing/total and a note whose offset actually reaches the rest."""
    from backend.ai.tools import execute_tool

    txns = _five_payee_txns()
    real_suggest = categorize_service.suggest_categories
    real_count = categorize_service.count_category_suggestions

    def fake_suggest(*, limit, offset=0, **kw):
        return real_suggest(txns=txns, corrections=[], enabled_rules=[], limit=limit, offset=offset)

    def fake_count(**kw):
        return real_count(txns=txns, corrections=[], enabled_rules=[])

    monkeypatch.setattr(categorize_service, "suggest_categories", fake_suggest)
    monkeypatch.setattr(categorize_service, "count_category_suggestions", fake_count)

    text, _ = asyncio.run(execute_tool("get_category_suggestions", {"limit": 2}))
    body = json.loads(text)
    assert body["showing"] == 2
    assert body["total"] == 5
    assert "offset" in body["note"]

    seen = {s["transaction_id"] for s in body["suggestions"]}
    text2, _ = asyncio.run(execute_tool("get_category_suggestions", {"limit": 2, "offset": 2}))
    body2 = json.loads(text2)
    seen |= {s["transaction_id"] for s in body2["suggestions"]}
    text3, _ = asyncio.run(execute_tool("get_category_suggestions", {"limit": 2, "offset": 4}))
    body3 = json.loads(text3)
    assert "note" not in body3
    seen |= {s["transaction_id"] for s in body3["suggestions"]}
    assert len(seen) == 5


# ── REST smoke ────────────────────────────────────────────────────────────────


def test_get_category_suggestions_endpoint_smoke():
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/category-suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"suggestions", "count"}
    assert isinstance(body["suggestions"], list)
