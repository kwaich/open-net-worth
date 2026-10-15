"""Rank the model-facing prose most likely to be teaching a RETIRED workflow.

WHY THIS IS A REPORT AND NOT A TEST
-----------------------------------
Workflow rot is the class the contract tests in ``tests/test_claim_contracts.py`` cannot
reach. A tool can be internally perfect — schema matches handler, every tool it names
exists, every category resolves — while describing a pipeline that was replaced weeks ago.
Nothing about the tool is inconsistent; the prose is simply about a world that moved on.

The signal is DIVERGENCE IN TIME: the service was rebuilt, the prose that steers the model
toward it was not. The audit that motivated this script found:

    services/statements/decisions.py     13 commits, 2026-07-16 -> 07-18
    services/matching.py                 11 commits, 2026-07-16 -> 07-17
      vs
    ai/prompts.py import protocol block  last changed 2026-06-11
    services/statement_import_bundle.py  last changed 2026-06-29
    ai/tools/matching.py workflow prose  last changed 2026-06-11

Five weeks and ~8 ADRs of pipeline rebuild while the steering prose sat still.

But staleness is a SUSPICION, not a defect. A service can be refactored heavily without
its contract changing, and a prose surface can be legitimately stable — which is exactly
why this is not a CI gate. A test that fails on every refactor gets disabled, and then
there is nothing at all. Run this periodically (it fits the ``hardening-loop`` skill's
turn as a review input) and triage the top of the list by hand.

The deterministic SUBSET of this problem — prose naming a service function that no longer
exists — IS enforced, as a real test, by
``tests/test_claim_contracts.py::test_tool_descriptions_name_only_live_tools_or_live_symbols``.

KNOWN LIMITATION — file-level dating is easily defeated
-------------------------------------------------------
An entry with no ``anchor`` is dated by its FILE's last commit, which any unrelated touch
resets. Measured live: on the night this script was written, six lanes were editing tool
modules for other reasons (adding ``mutates=`` tags, fixing descriptions), and every
file-dated entry collapsed to "0-1 days ago" while its actual workflow prose had not moved
in weeks. The ``anchor`` form (``git log -S`` on a distinctive phrase from the block) is
immune to this and is what surfaced the real 40-day gap in ``prompts.py``. TREAT A
FILE-DATED ENTRY'S SILENCE AS WEAK EVIDENCE, and add an anchor to any surface you actually
care about.

HOW THE MAP WAS BUILT
---------------------
``PROSE_MAP`` is explicit rather than inferred. Inference was tried and rejected: import
graphs connect a tool module to a dozen services it merely touches, which buries the one
it actually DESCRIBES. Each entry below was chosen by reading the prose surface and asking
"which service's workflow does this text tell the model how to drive?" — so the map is a
claim about narrative dependency, not about imports. Entries are cheap to add; a prose
surface with no real workflow guidance (pure CRUD tools) is deliberately absent.

USAGE
-----
    .venv/bin/python3 -m backend.scripts.prose_staleness            # ranked report
    .venv/bin/python3 -m backend.scripts.prose_staleness --json     # machine-readable
    .venv/bin/python3 -m backend.scripts.prose_staleness --top 5
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"

# prose surface → the service path(s) whose WORKFLOW it teaches.
# An optional `anchor` is a distinctive phrase from the prose block; when present the
# script dates that BLOCK via `git log -S` rather than the whole file, which matters for
# large multi-topic files like prompts.py where a file-level date is meaningless.
PROSE_MAP: list[dict] = [
    {
        "prose": "backend/ai/prompts.py",
        "anchor": "PROPOSED CHANGE PROTOCOL",
        "services": ["backend/services/statements/", "backend/services/ledger.py"],
        "note": "the change/approval protocol the model follows for every write",
        "topics": ["import", "ledger", "transaction", "txn", "permission", "approval", "dedup"],
    },
    {
        "prose": "backend/ai/tools/imports.py",
        "anchor": "same engine as Settings",
        "services": ["backend/services/statements/"],
        "note": "statement/CSV import pipeline guidance",
        "topics": ["import", "statement", "dedup", "batch"],
    },
    {
        "prose": "backend/ai/tools/statements.py",
        "services": ["backend/services/statements/"],
        "note": "statement lifecycle (upload → parse → diff → commit)",
        "topics": ["import", "statement", "pdf", "batch"],
    },
    {
        "prose": "backend/services/statement_import_bundle.py",
        "services": ["backend/services/statements/"],
        "note": "the bundled statement-import skill body + its allowlist",
        "topics": ["import", "statement", "dedup", "batch", "skill"],
    },
    {
        "prose": "backend/ai/tools/matching.py",
        "anchor": "the match engine's amount/date/description rules",
        "services": ["backend/services/matching.py", "backend/services/match_review.py"],
        "note": "dedup / match-review workflow",
        "topics": ["dedup", "match", "merge", "import"],
    },
    {
        "prose": "backend/ai/tools/ledger.py",
        "services": ["backend/services/ledger.py"],
        "note": "transaction create/update choke-point rules",
        "topics": ["ledger", "transaction", "txn", "category", "note", "memo", "description"],
    },
    {
        "prose": "backend/ai/tools/planning.py",
        "services": ["backend/services/planning.py", "backend/services/envelopes.py"],
        "note": "envelope/budget assignment workflow",
        "topics": ["envelope", "budget", "plan", "rta", "target", "category"],
    },
    {
        "prose": "backend/ai/tools/envelopes.py",
        "services": ["backend/services/envelopes.py"],
        "note": "envelope lifecycle tools",
        "topics": ["envelope", "budget", "category"],
    },
    {
        "prose": "backend/ai/tools/lots.py",
        "services": ["backend/services/lots.py"],
        "note": "tax-lot acquire/dispose workflow",
        "topics": ["lot", "tax", "rate-lock", "disposal", "position"],
    },
    {
        "prose": "backend/ai/tools/portfolio.py",
        "services": ["backend/services/positions.py"],
        "note": "position propose→apply workflow",
        "topics": ["position", "instrument", "holding", "preview", "portfolio"],
    },
    {
        "prose": "backend/ai/tools/equity.py",
        "services": ["backend/services/equity.py", "backend/services/equity_report.py"],
        "note": "grant/vest workflow",
        "topics": ["equity", "grant", "vest", "options", "tax"],
    },
    {
        "prose": "backend/ai/tools/options.py",
        "services": ["backend/services/options.py"],
        "note": "option open/close workflow",
        "topics": ["option", "tax", "premium"],
    },
    {
        "prose": "backend/ai/tools/rules.py",
        "services": ["backend/services/rules.py"],
        "note": "spend-rule authoring + apply-to-history",
        "topics": ["rule", "category", "import", "audit"],
    },
    {
        "prose": "backend/ai/tools/payees.py",
        "services": ["backend/services/payees.py", "backend/services/payee_registry.py"],
        "note": "payee normalization + rule application",
        "topics": ["payee", "category", "rule", "import"],
    },
    {
        "prose": "backend/ai/tools/skills.py",
        "services": ["backend/services/skills.py"],
        "note": "skill authoring/run workflow",
        "topics": ["skill", "tool", "permission", "scope"],
    },
    {
        "services": ["backend/services/agents.py"],
        "note": "agent authoring + scope rules",
        "topics": ["agent", "permission", "scope", "headless", "tool"],
    },
    {
        "prose": "backend/services/skill_creator_bundle.py",
        "services": ["backend/services/skills.py"],
        "note": "the skill-authoring guide handed to the model",
        "topics": ["skill", "tool", "permission", "scope"],
    },
    {
        "prose": "backend/db/skill_store.py",
        "services": ["backend/services/skills.py"],
        "note": "starter-skill bodies",
        "topics": ["skill", "tool"],
    },
]


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _last_change(path: str, anchor: str | None) -> tuple[str, str]:
    """(iso date, method) of the last commit touching this prose.

    With an ``anchor`` we use ``git log -S`` on that phrase, which dates the BLOCK rather
    than the file — far more precise for a multi-topic file, at the cost of one extra git
    call. Without one we fall back to the file's last commit and say so in the output.
    """
    if anchor:
        out = _git("log", "-1", "--format=%ad", "--date=short", "-S", anchor, "--", path)
        if out:
            return out, "block (-S anchor)"
    return _git("log", "-1", "--format=%ad", "--date=short", "--", path), "file"


def _commits_since(paths: list[str], since: str) -> tuple[int, str, str]:
    """(commit count, first date, last date) touching ``paths`` strictly after ``since``."""
    if not since:
        return 0, "", ""
    dates = _git(
        "log", f"--since={since}", "--format=%ad", "--date=short", "--", *paths
    ).splitlines()
    dates = [d for d in dates if d and d > since]
    if not dates:
        return 0, "", ""
    return len(dates), min(dates), max(dates)


_ADR_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")


def _adrs_since(since: str, topics: list[str]) -> list[str]:
    """ADRs dated after ``since`` whose SLUG mentions one of this surface's topics.

    Unfiltered, a 40-day gap returns every ADR in the repo, which buries the signal. The
    slug filter is a keyword match on the filename — cheap, since ADR filenames are
    date-prefixed and topic-descriptive by convention (ADR 2026-06-28). A surface with no
    declared topics gets no ADR evidence rather than all of it: silence beats noise.
    """
    if not since or not topics or not ADR_DIR.is_dir():
        return []
    out = []
    for path in sorted(ADR_DIR.glob("*.md")):
        match = _ADR_DATE.match(path.name)
        if not match or match.group(1) <= since:
            continue
        slug = match.group(2)
        if any(topic in slug for topic in topics):
            out.append(path.name)
    return out


@dataclass
class Suspect:
    prose: str
    anchor: str | None
    note: str
    prose_date: str
    dated_by: str
    services: list[str]
    commits: int
    first: str
    last: str
    adrs: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """commits-since × ADR multiplier. An ADR in the gap means a DECISION changed,
        which is far likelier to invalidate prose than routine churn."""
        return self.commits * (1 + 0.5 * min(len(self.adrs), 8))

    @property
    def gap_days(self) -> int:
        try:
            start = datetime.strptime(self.prose_date, "%Y-%m-%d").date()
        except ValueError:
            return 0
        return (date.today() - start).days


def collect() -> list[Suspect]:
    suspects = []
    for entry in PROSE_MAP:
        prose = entry["prose"]
        if not (REPO_ROOT / prose.rstrip("/")).exists():
            continue
        anchor = entry.get("anchor")
        prose_date, dated_by = _last_change(prose, anchor)
        services = [s for s in entry["services"] if (REPO_ROOT / s.rstrip("/")).exists()]
        if not services:
            # A mapped service path that no longer exists is itself map rot, and must NOT
            # silently degrade into `git log --` with no pathspec (which counts EVERY
            # commit in the repo and fabricates a top-ranked suspect). Surface it instead.
            print(
                f"WARNING: {prose} maps to service path(s) {entry['services']} that no "
                f"longer exist — fix PROSE_MAP in {Path(__file__).name}."
            )
            continue
        count, first, last = _commits_since(services, prose_date)
        suspects.append(
            Suspect(
                prose=prose,
                anchor=anchor,
                note=entry["note"],
                prose_date=prose_date,
                dated_by=dated_by,
                services=services,
                commits=count,
                first=first,
                last=last,
                adrs=_adrs_since(prose_date, entry.get("topics") or []) if count else [],
            )
        )
    return sorted(suspects, key=lambda s: (-s.score, -s.commits, s.prose))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--top", type=int, default=0, help="show only the top N suspects")
    parser.add_argument(
        "--min-commits", type=int, default=1, help="hide surfaces with fewer service commits"
    )
    args = parser.parse_args()

    suspects = [s for s in collect() if s.commits >= args.min_commits]
    if args.top:
        suspects = suspects[: args.top]

    if args.json:
        print(json.dumps([s.__dict__ | {"score": s.score} for s in suspects], indent=2))
        return 0

    if not suspects:
        print("No prose surface has drifted behind its service. Nothing to triage.")
        return 0

    print("PROSE STALENESS — model-facing text whose service moved on without it")
    print("=" * 78)
    print("A SUSPICION list, not a defect list. Triage from the top; a heavily refactored")
    print("service whose CONTRACT did not change is a legitimate no-op.\n")
    for rank, s in enumerate(suspects, 1):
        print(f"{rank}. {s.prose}" + (f"  [{s.anchor}]" if s.anchor else ""))
        print(f"     {s.note}")
        print(
            f"     prose last changed {s.prose_date or '(unknown)'} "
            f"({s.dated_by}) — {s.gap_days} days ago"
        )
        print(
            f"     since then: {s.commits} commit(s) to "
            f"{', '.join(s.services)}" + (f" ({s.first} → {s.last})" if s.first else "")
        )
        if s.adrs:
            print(f"     ADRs landed in the gap ({len(s.adrs)}):")
            for name in s.adrs[:8]:
                print(f"       - {name}")
            if len(s.adrs) > 8:
                print(f"       … and {len(s.adrs) - 8} more")
        print(f"     score {s.score:.1f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
