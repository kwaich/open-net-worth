from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.services.automations.executors import execute_deterministic


def _transaction_automation() -> dict:
    return {
        "id": "automation_rent",
        "name": "Monthly rent",
        "instructions": "Post monthly rent.",
        "next_run_at": "2026-08-02T08:00:00+00:00",
        "execution": {
            "kind": "transaction",
            "transaction": {
                "kind": "expense",
                "from_account_id": "account_cash",
                "amount": 1200,
                "currency": "SGD",
                "description": "Rent",
                "category": "cat_housing",
                "tags": ["home"],
            },
        },
    }


@pytest.mark.asyncio
async def test_recurring_transaction_uses_the_ledger_write_choke(monkeypatch):
    import backend.services.ledger as ledger

    calls: list[dict] = []
    monkeypatch.setattr(ledger.transactions, "has_external_ref", lambda source, ref: False)
    monkeypatch.setattr(
        ledger,
        "create_transaction",
        lambda data: calls.append(data) or {"id": "txn_rent"},
    )

    result = await execute_deterministic(_transaction_automation(), payload={})

    assert result == {
        "transaction_id": "txn_rent",
        "external_ref": "automation_rent:2026-08-02T08:00:00+00:00",
    }
    assert calls == [
        {
            "kind": "expense",
            "from_account_id": "account_cash",
            "to_account_id": None,
            "description": "Rent",
            "memo": "",
            "category": "cat_housing",
            "tags": ["home"],
            # WRITER = automation (RFC 2026-08-02); the external_ref NAMESPACE stays
            # "recurring" — that is WHICH automation, and it is the idempotency key.
            "source": "automation",
            "external_refs": [
                {
                    "source": "recurring",
                    "ref": "automation_rent:2026-08-02T08:00:00+00:00",
                }
            ],
            "date": "2026-08-02",
            "from_amount": 1200,
            "from_currency": "SGD",
        }
    ]


@pytest.mark.asyncio
async def test_transaction_executor_deduplicates_the_same_scheduled_occurrence(monkeypatch):
    import backend.services.ledger as ledger

    monkeypatch.setattr(ledger.transactions, "has_external_ref", lambda source, ref: True)
    monkeypatch.setattr(
        ledger,
        "create_transaction",
        lambda data: pytest.fail("duplicate occurrence must not reach the ledger write"),
    )

    result = await execute_deterministic(_transaction_automation(), payload={})

    assert result == {
        "skipped": "duplicate",
        "external_ref": "automation_rent:2026-08-02T08:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_executor_refuses_lucius_and_transform_variants():
    for kind in ("lucius", "transaction_transform", "shell"):
        with pytest.raises(ValidationError, match="deterministic execution kind"):
            await execute_deterministic(
                {"id": "automation_a", "name": "Unsafe", "execution": {"kind": kind}},
                payload={},
            )


# The agent executor went with Agents (2026-08-02). It pinned that dispatching to an agent
# passed the AUTOMATION's trigger through rather than inventing one, so a run's provenance
# named what actually fired it. `execution.kind` is `lucius` and nothing else now — the
# store refuses any other value — so there is no second executor to keep honest.
