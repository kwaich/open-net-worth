"""The Agent-retirement data migration (backend/scripts/retire_agents.py).

Properties that matter: it keeps ``allowed`` (the Always-allow store) while dropping only the
agent-management tools inside it, it refuses to delete agents.json while anything still
dispatches to an Agent, and running it twice changes nothing the second time.
"""

from __future__ import annotations

import json

import pytest

from backend.scripts.retire_agents import migrate


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload))


def _automations(tmp_path):
    return json.loads((tmp_path / "automations.json").read_text())


def test_strips_agent_tools_but_keeps_the_allowed_key(tmp_path):
    _write(
        tmp_path,
        "automations.json",
        [
            {
                "id": "a1",
                "name": "X",
                "allowed": [
                    {
                        "src": "Open Net Worth",
                        "desc": "d",
                        "tools": ["notify", "get_agent_metrics", "add_transactions"],
                    }
                ],
            }
        ],
    )
    _write(tmp_path, "agents.json", [])
    summary = migrate(tmp_path)

    row = _automations(tmp_path)[0]
    assert row["allowed"][0]["tools"] == ["notify", "add_transactions"]
    assert summary["grants_stripped"] == 1


def test_drops_an_entry_left_with_no_tools(tmp_path):
    _write(
        tmp_path,
        "automations.json",
        [
            {
                "id": "a1",
                "name": "X",
                "allowed": [{"src": "s", "desc": "d", "tools": ["list_agents"]}],
            }
        ],
    )
    _write(tmp_path, "agents.json", [])
    migrate(tmp_path)

    assert _automations(tmp_path)[0]["allowed"] == []


def test_an_automation_with_no_agent_tools_is_untouched(tmp_path):
    grants = [{"src": "s", "desc": "d", "tools": ["notify"]}]
    _write(tmp_path, "automations.json", [{"id": "a1", "name": "X", "allowed": grants}])
    _write(tmp_path, "agents.json", [])
    summary = migrate(tmp_path)

    assert _automations(tmp_path)[0]["allowed"] == grants
    assert summary["grants_stripped"] == 0


def test_skip_is_applied_by_id_only(tmp_path):
    """By id, never by name: this install has five similarly-named logger Automations, and
    turning off every confirm prompt on the wrong one is a security-relevant mistake."""
    _write(
        tmp_path,
        "automations.json",
        [
            {"id": "a1", "name": "Log transactions from emails", "approval": "auto", "allowed": []},
            {"id": "a2", "name": "Log transactions from email", "approval": "auto", "allowed": []},
        ],
    )
    _write(tmp_path, "agents.json", [])
    summary = migrate(tmp_path, skip_ids=("a1",))

    rows = {r["id"]: r for r in _automations(tmp_path)}
    assert rows["a1"]["approval"] == "skip"
    assert rows["a2"]["approval"] == "auto"
    assert summary["approval_set"] == 1


def test_no_skip_flag_changes_no_approval(tmp_path):
    _write(
        tmp_path,
        "automations.json",
        [{"id": "a1", "name": "Log transactions from emails", "approval": "auto", "allowed": []}],
    )
    _write(tmp_path, "agents.json", [])
    summary = migrate(tmp_path)

    assert _automations(tmp_path)[0]["approval"] == "auto"
    assert summary["approval_set"] == 0


def test_an_unknown_skip_id_aborts_rather_than_silently_doing_nothing(tmp_path):
    _write(tmp_path, "automations.json", [{"id": "a1", "name": "X", "allowed": []}])
    _write(tmp_path, "agents.json", [])

    with pytest.raises(RuntimeError, match="unknown Automation id"):
        migrate(tmp_path, skip_ids=("typo-id",))


def test_removes_session_grants_and_runtime_keys(tmp_path):
    _write(tmp_path, "automations.json", [])
    _write(tmp_path, "agents.json", [])
    _write(
        tmp_path,
        "tool_grants.json",
        [
            {"tool": "a", "scope": "session", "session_id": "s"},
            {"tool": "b", "scope": "always"},
        ],
    )
    _write(
        tmp_path,
        "automation_runtime.json",
        [{"automation_id": "a1", "session_allowed_tools": {"s": ["notify"]}}],
    )
    summary = migrate(tmp_path)

    assert json.loads((tmp_path / "tool_grants.json").read_text()) == [
        {"tool": "b", "scope": "always"}
    ]
    assert (
        "session_allowed_tools"
        not in json.loads((tmp_path / "automation_runtime.json").read_text())[0]
    )
    assert summary["session_grants_removed"] == 1
    assert summary["runtime_keys_removed"] == 1


def test_deletes_agents_json_and_backs_everything_up(tmp_path):
    _write(tmp_path, "automations.json", [])
    _write(tmp_path, "agents.json", [{"id": "monitor"}])
    summary = migrate(tmp_path)

    assert not (tmp_path / "agents.json").exists()
    assert summary["agents_deleted"] is True
    assert any(name.startswith("agents.pre-retire-agents-") for name in summary["backups"])


@pytest.mark.parametrize("marker", ["run_agent_now", "dispatch_to_agent"])
def test_aborts_when_an_automation_still_dispatches_to_an_agent(tmp_path, marker):
    """The Agent's prompt IS the work — deleting it while something calls it loses that work."""
    _write(
        tmp_path,
        "automations.json",
        [{"id": "a1", "name": "X", "allowed": [], "instructions": f"Call {marker} with monitor"}],
    )
    _write(tmp_path, "agents.json", [{"id": "monitor"}])

    with pytest.raises(RuntimeError, match="references an agent"):
        migrate(tmp_path)
    assert (tmp_path / "agents.json").exists()


def test_is_idempotent(tmp_path):
    _write(
        tmp_path,
        "automations.json",
        [
            {
                "id": "a1",
                "name": "Log transactions from emails",
                "approval": "auto",
                "allowed": [{"src": "s", "desc": "d", "tools": ["notify", "list_agents"]}],
            }
        ],
    )
    _write(tmp_path, "agents.json", [])

    first = migrate(tmp_path, skip_ids=("a1",))
    after_first = (tmp_path / "automations.json").read_text()
    second = migrate(tmp_path, skip_ids=("a1",))

    assert (tmp_path / "automations.json").read_text() == after_first
    assert (first["grants_stripped"], first["approval_set"]) == (1, 1)
    assert (second["grants_stripped"], second["approval_set"]) == (0, 0)


def test_dry_run_writes_nothing(tmp_path):
    _write(
        tmp_path,
        "automations.json",
        [
            {
                "id": "a1",
                "name": "X",
                "allowed": [{"src": "s", "desc": "d", "tools": ["list_agents"]}],
            }
        ],
    )
    _write(tmp_path, "agents.json", [{"id": "monitor"}])
    before = (tmp_path / "automations.json").read_text()

    summary = migrate(tmp_path, dry_run=True)

    assert (tmp_path / "automations.json").read_text() == before
    assert (tmp_path / "agents.json").exists()
    assert summary["grants_stripped"] == 1  # still REPORTS what it would do
    assert summary["agents_deleted"] is True  # ... including the delete it did not perform


def test_missing_optional_files_are_not_an_error(tmp_path):
    """A fresh install may have no grants or runtime state at all."""
    _write(tmp_path, "automations.json", [])
    summary = migrate(tmp_path)

    assert summary["agents_deleted"] is False
    assert summary["session_grants_removed"] == 0


def test_clear_allowed_empties_every_grant_list(tmp_path):
    """Owner decision 2026-08-03: the agents' tool lists should never have been carried into
    ``allowed``. There they only meant 'skip the confirm card', which approval already says."""
    _write(
        tmp_path,
        "automations.json",
        [
            {
                "id": "a1",
                "name": "Log transactions from emails",
                "allowed": [
                    {"src": "Open Net Worth", "desc": "d", "tools": ["notify", "calculate"]}
                ],
            },
            {"id": "a2", "name": "Other", "allowed": []},
        ],
    )
    _write(tmp_path, "agents.json", [])
    summary = migrate(tmp_path, clear_allowed=True)

    assert [r["allowed"] for r in _automations(tmp_path)] == [[], []]
    assert summary["allowed_cleared"] == 1  # only the one that HAD grants counts


def test_clear_allowed_is_idempotent(tmp_path):
    _write(
        tmp_path,
        "automations.json",
        [{"id": "a1", "name": "X", "allowed": [{"src": "s", "desc": "d", "tools": ["notify"]}]}],
    )
    _write(tmp_path, "agents.json", [])
    first = migrate(tmp_path, clear_allowed=True)
    second = migrate(tmp_path, clear_allowed=True)

    assert first["allowed_cleared"] == 1
    assert second["allowed_cleared"] == 0


def test_without_the_flag_grants_are_preserved(tmp_path):
    grants = [{"src": "s", "desc": "d", "tools": ["notify"]}]
    _write(tmp_path, "automations.json", [{"id": "a1", "name": "X", "allowed": grants}])
    _write(tmp_path, "agents.json", [])
    migrate(tmp_path)

    assert _automations(tmp_path)[0]["allowed"] == grants
