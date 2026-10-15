from __future__ import annotations

from backend.db.automation_store import AutomationStore
from backend.services.mentions.registry import REGISTRY
from backend.services.mentions.types import SearchQuery


def test_mentions_expose_only_the_unified_automation_entity(db_isolated):
    from backend.services.mentions.providers import search_automation
    from backend.services.mentions.resolve import _resolve_automation

    saved = AutomationStore().create(
        {
            "name": "Monthly close",
            "description": "Reviews the books.",
            "instructions": "Review the books and notify me about exceptions.",
            "trigger": {"kind": "schedule", "type": "monthly", "day": 1, "time": "09:00"},
            "execution": {"kind": "lucius"},
            "next_run_at": "2026-09-01T01:00:00+00:00",
        }
    )

    assert "automation" in REGISTRY
    assert "rule" not in REGISTRY
    assert "scheduled_task" not in REGISTRY

    rows = search_automation(
        SearchQuery(
            q="close",
            types=frozenset({"automation"}),
            surfaces=frozenset({"entity"}),
            limit=25,
            cursor=None,
        )
    )
    assert [(row["type"], row["value"], row["label"]) for row in rows] == [
        ("automation", saved["id"], "Monthly close")
    ]
    assert rows[0]["sublabel"] == "Monthly on day 1 at 09:00"

    resolved = _resolve_automation(saved["id"])
    assert resolved == {
        "name": "Monthly close",
        "description": "Reviews the books.",
        "instructions": "Review the books and notify me about exceptions.",
        "trigger": "Monthly on day 1 at 09:00",
        "next_run_at": "2026-09-01T01:00:00+00:00",
        "status": "active",
    }
