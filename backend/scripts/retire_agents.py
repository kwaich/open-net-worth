"""One-shot data migration for the Agent retirement (2026-08-02).

Idempotent. Run BEFORE the code deletions land, so ``agents.json`` is still readable for the
safety assertion.

    python -m backend.scripts.retire_agents [--dry-run] [--data-dir PATH]

What it does, and why each step exists:

1. Strips agent-management tools from every Automation's ``allowed`` entries. Those tools are
   deleted by the retirement, so a standing grant naming one would dangle. The ``allowed`` KEY
   ITSELF IS KEPT — it is the Always-allow store behind the confirm card, not a restriction.
2. Sets any Automation named by ``--skip <id>`` to ``approval: "skip"`` (owner decision: the
   email/trade logger must run fully unattended). Passed by id, never matched by name: this
   install has five similarly-named logger Automations, and turning off every confirm prompt
   on the wrong one is a security-relevant misconfiguration, not a typo.
3. Drops any ``scope: "session"`` grant and any ``session_allowed_tools`` key — the
   per-conversation tier was removed the same day.
4. Deletes ``agents.json``, after asserting no Automation still depends on an Agent.

Every mutated file is copied to a dated ``.pre-retire-agents-<stamp>`` sibling first.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tools that only ever existed to manage Agents.
AGENT_TOOLS = frozenset(
    {
        "get_agent_metrics",
        "get_agent_findings",
        "get_agent_runs",
        "list_agents",
        "get_self_improvements",
        "review_agent_finding",
        "review_self_improvement",
        "run_agent_now",
        "dispatch_to_agent",
        "enable_agent",
        "disable_agent",
        "create_agent",
        "update_agent",
        "delete_agent",
        "apply_agent_change",
    }
)

# A call to one of these in an Automation's instructions means its work still lives in an
# Agent record, so deleting agents.json would silently break it.
AGENT_DISPATCH_MARKERS = ("run_agent_now", "dispatch_to_agent")

_MUTATED_FILES = (
    "automations.json",
    "agents.json",
    "tool_grants.json",
    "automation_runtime.json",
)


def _read(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _backup(path: Path, stamp: str) -> Optional[str]:
    if not path.exists():
        return None
    target = path.with_name(f"{path.stem}.pre-retire-agents-{stamp}{path.suffix}")
    shutil.copy2(path, target)
    return target.name


def _assert_no_agent_dependency(automations: list[dict]) -> None:
    """Refuse to delete agents.json while an Automation still dispatches to an Agent.

    Aborting loudly is the only safe direction: the Agent's prompt is the work, and once the
    record is gone there is nothing to inline it from except a backup.
    """
    for row in automations:
        blob = f"{row.get('instructions') or ''} {json.dumps(row.get('trigger') or {})}"
        for marker in AGENT_DISPATCH_MARKERS:
            if marker in blob:
                raise RuntimeError(
                    f"Automation {row.get('name') or row.get('id')!r} references an agent via "
                    f"{marker!r} — inline its logic into the instructions before retiring Agents."
                )


def _strip_agent_grants(row: dict) -> int:
    """Drop agent-management tools from one Automation's grants. Returns entries changed."""
    changed = 0
    cleaned: list[dict] = []
    for item in row.get("allowed") or []:
        if not isinstance(item, dict):
            continue
        original = list(item.get("tools") or [])
        tools = [t for t in original if t not in AGENT_TOOLS]
        if tools != original:
            changed += 1
        if tools:
            cleaned.append({**item, "tools": tools})
    row["allowed"] = cleaned
    return changed


def migrate(
    data_dir: Path,
    *,
    dry_run: bool = False,
    skip_ids: tuple[str, ...] = (),
    clear_allowed: bool = False,
) -> dict:
    data_dir = Path(data_dir)
    wanted_skip = {s.strip() for s in skip_ids if s and s.strip()}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    summary = {
        "grants_stripped": 0,
        "allowed_cleared": 0,
        "session_grants_removed": 0,
        "runtime_keys_removed": 0,
        "approval_set": 0,
        "agents_deleted": False,
        "backups": [],
    }

    automations = _read(data_dir / "automations.json") or []
    agents = _read(data_dir / "agents.json")

    _assert_no_agent_dependency(automations)

    unknown = wanted_skip - {str(r.get("id") or "") for r in automations}
    if unknown:
        raise RuntimeError(f"--skip named unknown Automation id(s): {sorted(unknown)}")

    for row in automations:
        if clear_allowed:
            # Owner decision 2026-08-03: the migration should not have carried the agents'
            # tool lists into ``allowed`` at all. There they only ever meant "skip the confirm
            # card", which ``approval: auto|skip`` already says — so a 101-entry list bought
            # nothing and made the detail page unreadable. Emptied; a real grant is written
            # again the next time the owner taps Always allow on a card.
            if row.get("allowed"):
                summary["allowed_cleared"] += 1
            row["allowed"] = []
            continue
        summary["grants_stripped"] += _strip_agent_grants(row)
        if str(row.get("id") or "") in wanted_skip and row.get("approval") != "skip":
            row["approval"] = "skip"
            summary["approval_set"] += 1

    grants = _read(data_dir / "tool_grants.json")
    if grants is not None:
        kept = [g for g in grants if g.get("scope") != "session"]
        summary["session_grants_removed"] = len(grants) - len(kept)
        grants = kept

    runtime = _read(data_dir / "automation_runtime.json")
    if runtime is not None:
        for row in runtime:
            if isinstance(row, dict) and row.pop("session_allowed_tools", None) is not None:
                summary["runtime_keys_removed"] += 1

    if dry_run:
        # Report what a real run WOULD do — a dry run that under-reports is worse than none.
        summary["agents_deleted"] = agents is not None
        summary["backups"] = [n for n in _MUTATED_FILES if (data_dir / n).exists()]
        logger.info("retire_agents (dry run): %s", summary)
        return summary

    for name in _MUTATED_FILES:
        backup = _backup(data_dir / name, stamp)
        if backup:
            summary["backups"].append(backup)

    _write(data_dir / "automations.json", automations)
    if grants is not None:
        _write(data_dir / "tool_grants.json", grants)
    if runtime is not None:
        _write(data_dir / "automation_runtime.json", runtime)
    if agents is not None:
        (data_dir / "agents.json").unlink()
        summary["agents_deleted"] = True

    logger.info("retire_agents: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Retire Agents — data migration")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument(
        "--clear-allowed",
        action="store_true",
        help="Empty every Automation's allowed list instead of only stripping agent tools.",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="AUTOMATION_ID",
        help="Set this Automation's approval to 'skip'. Repeatable. By id, never by name.",
    )
    args = parser.parse_args()

    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        from backend.core.paths import DATA_DIR

        data_dir = DATA_DIR

    summary = migrate(
        data_dir,
        dry_run=args.dry_run,
        skip_ids=tuple(args.skip),
        clear_allowed=args.clear_allowed,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
