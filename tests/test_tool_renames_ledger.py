"""Tool-name resilience: the regression suite whose absence caused a silent outage.

Incident (2026-07-18): the envelope⊥category split renamed two tools. Stored playbooks were
never migrated, so ``~/.open-net-worth/data/skills/monthly-close/SKILL.md`` kept listing
``set_category_target`` and ``move_category_to_group`` among its 156 allowed tools. Because
a skill is *degraded* when ANY listed tool is missing, and the prompt builder skipped
degraded skills outright, the whole 156-tool playbook became invisible to the model. Nothing
raised, nothing logged.

The fix at the time was an ALIAS map. That is gone (ADR ``2026-08-25-tool-names-are-final``):
nothing resolves an old name at runtime, so the guarantee moved one step earlier — every
stored name is either live or MIGRATABLE, and the migration is what makes it live again.
An un-migrated name is now an unknown tool, loudly, which is the opposite of the failure
above.

What is pinned here:
  1. every tool name in the fixture corpus is a live tool or one ``RENAMES`` can rewrite,
     and migrating the corpus leaves nothing dead;
  2. a stored name that no longer exists is REPORTED to the operator, never resolved away;
  3. no alias mechanism comes back;
  4. the migration itself: rewrite correctness (frontmatter AND body prose), idempotency,
     and that it never touches a record it has nothing to do.

These tests read ONLY repo fixtures and a tmp_path — never ``~/.open-net-worth``. Reading
the operator's live data dir from a test is exactly the coupling that would make this suite
pass on one machine and fail on another.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from backend.ai.tools import registry

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "playbooks"


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    """Import the tool package so the registry (and its alias map) is populated."""
    import backend.ai.tools  # noqa: F401


def _known() -> set[str]:
    return set(registry.registered_tool_names())


# ── 1. Every legacy name in the corpus is MIGRATABLE ───────────────────────────
def _iter_fixture_refs(root: Path | None = None):
    """Yield (source, tool_name) for every tool reference in a playbook corpus."""
    root = root or FIXTURE_DIR
    if not root.is_dir():
        return
    for path in sorted(root.glob("**/*.json")):
        rows = json.loads(path.read_text("utf-8"))
        for row in rows if isinstance(rows, list) else [rows]:
            for field in ("allowed_tools", "blocked_tools"):
                for name in row.get(field) or []:
                    yield f"{path.name}:{row.get('id')}:{field}", name
    for path in sorted(root.glob("**/SKILL.md")):
        text = path.read_text("utf-8")
        split = _split_front_matter(text)
        if not split:
            continue
        fm = yaml.safe_load(split[0]) or {}
        for name in (fm.get("allowed-tools") or "").split():
            yield f"{path.parent.name}/SKILL.md:allowed-tools", name
        meta = fm.get("metadata") or {}
        for name in (meta.get("onw-blocked-tools") or "").split():
            yield f"{path.parent.name}/SKILL.md:onw-blocked-tools", name


def _split_front_matter(text: str) -> tuple[str, str] | None:
    """Split a SKILL.md into (frontmatter_yaml, body). None if it has no frontmatter.

    Inlined here (2026-08-28) when the one-shot `migrate_tool_names.py` was deleted. It is a
    parsing utility, not migration logic, and the invariant below — no shipped playbook may
    reference a tool name that is neither live nor recorded in the rename ledger — is a LIVE
    check worth keeping. Ten sibling tests that exercised the deleted rewrite machinery went
    with the script; these did not.
    """
    if not text.startswith("---"):
        return None
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return None
    return rest[:end], rest[end + 4 :].lstrip("\n")


def test_the_registry_has_no_alias_mechanism():
    """The forever-guard for ADR 2026-08-25-tool-names-are-final.

    An alias is cheap to reintroduce one tool at a time and expensive to remove once 85 of
    them exist — so the absence is pinned, not merely documented."""
    for gone in ("_ALIASES", "_LEGACY_TOOL_ALIASES", "resolve_tool_name", "tool_aliases"):
        assert not hasattr(registry, gone), (
            f"registry.{gone} is back — a tool name is final; record the rename in "
            "backend/scripts/tool_renames.py instead of aliasing"
        )
    import inspect

    assert "aliases" not in inspect.signature(registry.tool).parameters, (
        "@tool(aliases=…) is back — see ADR 2026-08-25-tool-names-are-final"
    )


# ── 3. A stored name that no longer exists is REPORTED, never resolved away ────


def test_playbook_missing_tools_still_reports_a_truly_dead_name():
    from backend.services import _playbook

    missing = _playbook.playbook_missing_tools(["totally_gone_tool"], _known())
    assert missing == ["totally_gone_tool"]


# `skills_service.is_degraded` was removed along with skill `allowed_tools` enforcement:
# a skill no longer gates tool scope, so there is nothing left for a missing tool
# reference in it to degrade.


# ── 4. The migration — the ONLY thing that makes an old name work again ────────


def _write_copy(root: Path) -> Path:
    """A miniature data dir mirroring the live shapes (one agent, two skills)."""
    data = root / "data"
    (data / "skills" / "monthly-close").mkdir(parents=True)
    (data / "skills" / "clean-skill").mkdir(parents=True)

    (data / "agents.json").write_text(
        json.dumps(
            [
                {
                    "id": "monitor",
                    "allowed_tools": ["get_ledger_summary", "set_category_target"],
                    "blocked_tools": ["move_category_to_group"],
                },
                {"id": "clean", "allowed_tools": ["get_net_worth"]},
            ],
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    (data / "skills" / "monthly-close" / "SKILL.md").write_text(
        "---\n"
        "name: monthly-close\n"
        "description: Close the month\n"
        "allowed-tools: get_ledger_summary set_category_target move_category_to_group\n"
        "---\n\n"
        "# Body\n\nSome instructions that must survive verbatim.\n",
        "utf-8",
    )
    (data / "skills" / "clean-skill" / "SKILL.md").write_text(
        "---\nname: clean-skill\ndescription: d\nallowed-tools: get_net_worth\n---\n\n# Body\n",
        "utf-8",
    )
    return data


def _write_copy_2026_07_19(root: Path) -> Path:
    """A miniature data dir exercising the 2026-07-19 rename generation (category-group
    CRUD -> envelope-group; the legacy spending-cap trio -> set_spending_limit*)."""
    data = root / "data"
    (data / "skills" / "budget-groups-skill").mkdir(parents=True)

    (data / "agents.json").write_text(
        json.dumps(
            [
                {
                    "id": "budgeter",
                    "allowed_tools": [
                        "get_budget_month",
                        "create_category_group",
                        "update_spending_budgets",
                    ],
                    "blocked_tools": ["set_budget_rollover_start"],
                },
            ],
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    (data / "skills" / "budget-groups-skill" / "SKILL.md").write_text(
        "---\n"
        "name: budget-groups-skill\n"
        "description: Reorganize budget groups and caps\n"
        "allowed-tools: rename_category_group reorder_category_groups delete_category_group "
        "set_budget_rollover\n"
        "---\n\n"
        "# Body\n\nMust survive verbatim.\n",
        "utf-8",
    )
    return data
