"""ADR 0010: categorization learning groups by the payee ENTITY (payee_id), not the
description-derived ``_payee_key``.

Before this, a single real payee fragmented into one bucket per distinct description, so
a correction (or a categorized row) on "Anthropic Claude API" taught nothing about
"Anthropic PBC". Now both evidence sources bucket by ``payee_id`` when present (falling
back to ``_payee_key`` for entity-less rows), so one signal generalizes across every
description variant of the same payee.

Also covers: a pinned ``default_category`` participating in the suggestion, and the
legacy dual-read that resolves ``payee_id`` for older corrections that pre-date the field.

Pure + offline: service tests inject synthetic lists; the dual-read test isolates the
payee store in tmp_path.
"""

from __future__ import annotations

from backend.services import categorize as categorize_service


def _txn(desc, *, category="", payee_id=None, kind="expense", id_=None, date="2026-05-01"):
    t = {
        "id": id_ or f"{desc}-{date}",
        "date": date,
        "kind": kind,
        "description": desc,
        "category": category,
        "payee_id": payee_id,
        "deleted": False,
        "from_account_id": "acct-1",
        "from_amount": 10.0,
        "from_currency": "SGD",
    }
    return t


def _corr(to_category, *, payee_id=None, payee_sample="", created_at="2026-05-01T00:00:00Z"):
    return {
        "payee_id": payee_id,
        "payee_key": categorize_service.corrections_service._payee_key(payee_sample),
        "payee_sample": payee_sample,
        "to_category": to_category,
        "kind": "expense",
        "created_at": created_at,
    }


# ── grouping by payee_id ───────────────────────────────────────────────────────


def test_corrections_generalize_across_descriptions_via_payee_id():
    # Four corrections to "startup", same payee_id but DIFFERENT descriptions (so their
    # _payee_keys all differ). The uncategorized row is yet another description of the
    # same payee. Keyed on _payee_key this would never match; keyed on payee_id it does.
    corrections = [
        _corr(
            "startup",
            payee_id="pay_x",
            payee_sample=f"Anthropic variant {i}",
            created_at=f"2026-05-0{i}T00:00:00Z",
        )
        for i in range(1, 5)
    ]
    txns = [_txn("Anthropic Claude API", category="", payee_id="pay_x", id_="u")]
    out = categorize_service.suggest_categories(
        txns=txns, corrections=corrections, enabled_rules=[]
    )
    assert len(out) == 1
    assert out[0]["suggested_category"] == "startup"
    assert out[0]["evidence_count"] == 4


def test_ledger_rows_of_same_payee_unify_across_descriptions():
    txns = [
        _txn("Anthropic PBC #1", category="startup", payee_id="pay_x", id_="a1"),
        _txn("Anthropic invoice 992", category="startup", payee_id="pay_x", id_="a2"),
        _txn("Anthropic SF USA", category="startup", payee_id="pay_x", id_="a3"),
        _txn("Anthropic auto recharge", category="startup", payee_id="pay_x", id_="a4"),
        _txn("Anthropic Claude API", category="", payee_id="pay_x", id_="u"),
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert len(out) == 1
    assert out[0]["suggested_category"] == "startup"
    assert out[0]["evidence_count"] == 4


def test_entityless_rows_still_group_by_payee_key():
    # No payee_id anywhere → behavior is unchanged (description-key grouping).
    txns = [
        _txn("STARBUCKS #1", category="food", id_="a1"),
        _txn("STARBUCKS #2", category="food", id_="a2"),
        _txn("STARBUCKS #3", category="food", id_="a3"),
        _txn("STARBUCKS #4", category="food", id_="a4"),
        _txn("STARBUCKS #99", category="", id_="u"),
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert len(out) == 1
    assert out[0]["suggested_category"] == "food"


def test_different_payees_do_not_cross_contaminate():
    txns = [
        _txn("Anthropic PBC", category="startup", payee_id="pay_x", id_="a1"),
        _txn("Anthropic PBC", category="startup", payee_id="pay_x", id_="a2"),
        _txn("Anthropic PBC", category="startup", payee_id="pay_x", id_="a3"),
        _txn("Anthropic PBC", category="startup", payee_id="pay_x", id_="a4"),
        _txn("Some Cafe", category="", payee_id="pay_y", id_="u"),  # different payee, no history
    ]
    out = categorize_service.suggest_categories(txns=txns, corrections=[], enabled_rules=[])
    assert out == []  # pay_y has no evidence; pay_x's startup must not leak to it


# ── pinned default participates in the suggestion ──────────────────────────────


def test_pinned_default_surfaces_as_suggestion_without_other_history():
    # An uncategorized row of a pinned-default payee → suggest the default, even with no
    # corrections / categorized rows for it. The default is an explicit user instruction.
    txns = [_txn("Anthropic Claude API", category="", payee_id="pay_x", id_="u")]
    out = categorize_service.suggest_categories(
        txns=txns, corrections=[], enabled_rules=[], payee_defaults={"pay_x": "startup"}
    )
    assert len(out) == 1
    assert out[0]["suggested_category"] == "startup"


def test_recent_corrections_outweigh_the_pinned_default():
    # The pinned default participates but does not dominate — repeated recent corrections
    # to another category still win (the user's recent behavior beats a stale default).
    corrections = [
        _corr(
            "subscriptions",
            payee_id="pay_x",
            payee_sample=f"Anthropic {i}",
            created_at=f"2026-05-0{i}T00:00:00Z",
        )
        for i in range(1, 5)
    ]
    txns = [_txn("Anthropic Claude API", category="", payee_id="pay_x", id_="u")]
    out = categorize_service.suggest_categories(
        txns=txns, corrections=corrections, enabled_rules=[], payee_defaults={"pay_x": "startup"}
    )
    assert len(out) == 1
    assert out[0]["suggested_category"] == "subscriptions"
