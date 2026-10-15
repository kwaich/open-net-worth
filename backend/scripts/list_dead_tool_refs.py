"""List every stored automation/skill that names a RETIRED AI tool — read-only.

RFC ``connectors-as-data-sources`` §D: a tool rename/retirement is a DATA migration
(``backend.scripts.tool_renames.RENAMES``, ADR ``2026-08-25-tool-names-are-final``) — there
is no alias, so a stored automation's ``instructions``/skill's ``body`` naming a retired
tool is now an instruction the harness cannot fulfil. Unlike ``tool_inventory.py --lint``
(which checks live TOOL DESCRIPTIONS, enforced in CI via ``make lint``), this script checks
USER DATA — an owner's own automations and skills — which this repo's data-safety rules
forbid mutating automatically (CLAUDE.md: "never bypass a write choke point"; the owner
edits their own playbooks by hand). This script only lists what needs a look; it never
writes.

Usage
-----
    .venv/bin/python3 -m backend.scripts.list_dead_tool_refs          # human-readable
    .venv/bin/python3 -m backend.scripts.list_dead_tool_refs --json   # machine-readable

Exit code is always 0 — this is a report, not a gate (nothing here is CI-enforced; the
09-05 connectors retirement's own dead names are already caught by
``tool_inventory.py --lint`` for any tool DESCRIPTION, and by
``tests/test_tools_aggregation.py``'s registration-name assertions for the registry
itself). Run it after any tool rename to see what a real instance's own data would need to
be told to fix.
"""

from __future__ import annotations

import argparse
import json
import re
import sys


def _dead_names() -> dict[str, str]:
    from backend.scripts.tool_renames import RENAMES

    return dict(RENAMES)


def _mentions(text: str, dead: dict[str, str]) -> list[str]:
    """Every dead name appearing in ``text`` as a whole word (never a substring hit inside
    a longer identifier — e.g. ``connector_sync`` must not match
    ``connector_sync_result``)."""
    if not text:
        return []
    hits = []
    for name in dead:
        if re.search(rf"\b{re.escape(name)}\b", text):
            hits.append(name)
    return hits


def _scan_automations(dead: dict[str, str]) -> list[dict]:
    from backend.db.automation_store import AutomationStore

    findings = []
    for row in AutomationStore().all():
        fields = {
            "name": row.get("name") or "",
            "description": row.get("description") or "",
            "instructions": row.get("instructions") or "",
        }
        blob = " ".join(fields.values())
        hits = _mentions(blob, dead)
        if hits:
            findings.append(
                {
                    "kind": "automation",
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "dead_tools": sorted(hits),
                    "suggested": {h: dead[h] for h in hits},
                }
            )
    return findings


def _scan_skills(dead: dict[str, str]) -> list[dict]:
    from backend.db.skill_store import SkillStore

    findings = []
    for row in SkillStore().all():
        # A skill names tools only in its body — it carries no tool list.
        hits = _mentions(row.get("body") or "", dead)
        if hits:
            findings.append(
                {
                    "kind": "skill",
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "dead_tools": sorted(hits),
                    "suggested": {h: dead[h] for h in hits},
                }
            )
    return findings


def find_dead_tool_refs() -> list[dict]:
    """Every automation/skill naming a retired tool. Read-only — never mutates a store."""
    dead = _dead_names()
    if not dead:
        return []
    return _scan_automations(dead) + _scan_skills(dead)


def _render_human(findings: list[dict]) -> str:
    if not findings:
        return "No automations or skills name a retired tool."
    lines = [f"{len(findings)} finding(s):"]
    for f in findings:
        suggestion = ", ".join(
            f"'{old}' -> '{new}'" if new else f"'{old}' (no replacement — retired outright)"
            for old, new in f["suggested"].items()
        )
        lines.append(f"  {f['kind']} {f['id']!r} ({f['name']!r}): {suggestion}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    findings = find_dead_tool_refs()
    if args.json:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
    else:
        print(_render_human(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
