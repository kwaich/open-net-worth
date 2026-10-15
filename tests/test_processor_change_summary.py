"""Confirm-safety on the Telegram path: ``format_change_summary`` must produce a readable,
non-empty summary for EVERY change type — agents, skills/lots/income, and any unknown future
type — not a bare "Proposed change" string. Regression for the agent blind-confirm bug, which
also affected the Telegram inline-keyboard confirm.
"""

from __future__ import annotations

from backend.connectors.processor import format_change_summary


def test_agent_create_summary_lists_name_and_tools():
    summary = format_change_summary(
        {
            "type": "agent_create",
            "display": {
                "name": "Fraud & Scam Monitor",
                "description": "Watches for suspicious transactions",
                "prompt_preview": "Review recent transactions",
                "allowed_tools": ["get_transactions", "telegram_send_message"],
            },
        }
    )
    assert "Create agent: Fraud & Scam Monitor" in summary
    assert "Watches for suspicious transactions" in summary
    assert "get_transactions" in summary
    # The tool list IS what the agent can do — the confirm card must show it, since there is
    # no longer a separate grant line qualifying it.
    assert "tools: get_transactions, telegram_send_message" in summary


def test_agent_create_summary_shows_the_tools_it_will_get():
    summary = format_change_summary(
        {
            "type": "agent_create",
            "display": {"name": "Monitor", "allowed_tools": ["telegram_send_message"]},
        }
    )
    assert "tools: telegram_send_message" in summary


def test_agent_update_summary_lists_changed_fields():
    summary = format_change_summary(
        {
            "type": "agent_update",
            "display": {"name": "Monitor", "changed_fields": ["prompt"], "prompt_preview": "new"},
        }
    )
    assert "Update agent: Monitor" in summary
    assert "changing: prompt" in summary


def test_unknown_type_summary_is_readable_not_bare():
    # A skill_create previously fell through to the bare "Proposed change" string (blind).
    summary = format_change_summary(
        {
            "type": "skill_create",
            "display": {"name": "Monthly close", "tools_used": 3},
        }
    )
    assert summary != "Proposed change"
    assert "Skill create" in summary
    assert "Monthly close" in summary


def test_known_position_summaries_unchanged():
    # No regression on the existing add/update/remove formatting.
    assert format_change_summary(
        {"type": "add", "display": {"symbol": "AAPL", "quantity": 5, "asset_class": "equity"}}
    ).startswith("Add 5 AAPL (equity)")
    assert format_change_summary({"type": "remove", "display": {"name": "AAPL"}}).startswith(
        "Remove AAPL"
    )


# ── ledger confirm cards (txn_create / txn_update / txn_bulk) ──────────────────────────
#
# ``services.ledger_preview`` returns these FLAT (no ``display`` wrapper, unlike every type
# above) — before this fix, ``format_change_summary``'s generic fallback read ``change.get(
# "display", {})`` for these too, which is always empty for a ledger card, so a connector
# confirm rendered a blind "Txn create" with nothing underneath it.


def test_txn_create_summary_shows_resolved_category_and_account():
    summary = format_change_summary(
        {
            "type": "txn_create",
            "description": "Grab ride home",
            "category": {"id": "cat_transport", "label": "Transport", "source": "rule"},
            "from": {
                "account_id": "acc_chk",
                "account_name": "Checking",
                "amount": 13.8,
                "currency": "SGD",
            },
            "payee": None,
            "warnings": [],
        }
    )
    assert "SGD 13.80" in summary
    assert "Grab ride home" in summary
    assert "Transport" in summary
    assert "Checking" in summary


def test_txn_create_summary_flags_a_new_payee():
    summary = format_change_summary(
        {
            "type": "txn_create",
            "description": "NEW MERCHANT PTE LTD",
            "category": {"id": None, "label": "Uncategorized", "source": "none"},
            "from": {"account_name": "Checking", "amount": 9.0, "currency": "SGD"},
            "payee": {"id": None, "name": "NEW MERCHANT PTE LTD", "is_new": True},
            "warnings": [],
        }
    )
    assert "Uncategorized" in summary
    assert "(new)" in summary


def test_txn_update_summary_shows_the_before_after_diff():
    summary = format_change_summary(
        {
            "type": "txn_update",
            "transaction_id": "txn_1",
            "description": "Lunch",
            "fields": [
                {"field": "category", "label": "Category", "before": "Food", "after": "Transport"}
            ],
            "warnings": [],
        }
    )
    assert "Update transaction: Lunch" in summary
    assert "Category: Food → Transport" in summary


def test_txn_bulk_summary_shows_count_and_totals():
    summary = format_change_summary(
        {
            "type": "txn_bulk",
            "count": 3,
            "rows": [
                {
                    "date": "2026-07-19",
                    "description": "Coffee",
                    "category_label": "Food",
                    "amount": 5.0,
                    "currency": "SGD",
                    "account_name": "Checking",
                }
            ],
            "truncated": False,
            "totals": {"base_currency": "SGD", "inflow_base": 0.0, "outflow_base": 5.0},
            "warnings": [],
        }
    )
    assert "3 transactions" in summary
    assert "Coffee" in summary
    assert "Food" in summary
    assert "Checking" in summary


def test_txn_summary_surfaces_warnings():
    summary = format_change_summary(
        {
            "type": "txn_create",
            "description": "Old txn",
            "category": {"id": None, "label": "Uncategorized"},
            "from": {"account_name": "Checking", "amount": 1.0, "currency": "SGD"},
            "payee": None,
            "warnings": ["dated before tracking start"],
        }
    )
    assert "⚠ dated before tracking start" in summary
