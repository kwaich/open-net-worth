"""Query surface for the per-tool-call telemetry ring (``tool_call_log.json``).

A log nobody reads is dead weight. This is the reader — it answers, from evidence, the
five questions the log exists for:

  1. ``usage``     — which tools were called, how often, from which surface, over N days.
  2. ``core``      — which ``CORE_TOOL_NAMES`` were NEVER called (the membership question:
                     ~19 tools ride every turn at ~6,900 prompt tokens; a core tool used in
                     2% of turns pays rent it does not earn, and a repeatedly-searched-for
                     non-core tool has earned a seat).
  3. ``results``   — which tools produced the largest results (a single 15,000-token result
                     rides in context for the rest of the session; 58 of 79 read tools have
                     never been shrunk, so this prioritises which ones to shrink first).
  4. ``turns``     — median/percentile tool calls per turn, and the longest sequences.
  5. ``discovery`` — ``find_tools`` queries followed by a call the query did not return.

Usage::

    .venv/bin/python3 -m backend.scripts.tool_telemetry_report            # all sections
    .venv/bin/python3 -m backend.scripts.tool_telemetry_report usage --days 7
    .venv/bin/python3 -m backend.scripts.tool_telemetry_report core
    .venv/bin/python3 -m backend.scripts.tool_telemetry_report --json     # machine-readable

Read-only: it never writes to the ring (beyond flushing this process's own empty buffer)
and never mutates any store.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# A find_tools query is credited with a call if the call lands within this window. The
# join is by TIME, not session id, because tool_discovery_log.json does not record one —
# a deliberate heuristic, and the reason `discovery` reports "candidate" misses for a human
# to read rather than a hard number to act on blindly.
_DISCOVERY_WINDOW_SEC = 180.0


def _parse_ts(value: Any) -> Optional[datetime]:
    """Tolerant ISO8601 parse → aware UTC datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _load_rows(days: Optional[int]) -> list[dict]:
    """Telemetry rows, newest first, optionally filtered to the last ``days``."""
    from backend.ai.tools import call_log
    from backend.db.tool_call_log_store import ToolCallLogStore

    call_log.flush_now()  # make this process's own buffered rows visible
    rows = ToolCallLogStore().all_rows()
    if not days:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept = []
    for row in rows:
        ts = _parse_ts(row.get("ts"))
        if ts is None or ts >= cutoff:
            kept.append(row)
    return kept


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Empty → 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[idx]


# ── 1. usage ─────────────────────────────────────────────────────────────────────────


def usage(rows: list[dict]) -> dict:
    """Call counts per tool, split by origin, with error and alias detail."""
    by_tool: dict[str, dict] = {}
    for row in rows:
        tool = row.get("tool", "")
        entry = by_tool.setdefault(
            tool,
            {
                "tool": tool,
                "calls": 0,
                "errors": 0,
                "mutating": 0,
                "origins": Counter(),
                "error_classes": Counter(),
                "aliases": Counter(),
                "_durations": [],
            },
        )
        entry["calls"] += 1
        if not row.get("ok", True):
            entry["errors"] += 1
            if row.get("error_class"):
                entry["error_classes"][row["error_class"]] += 1
        if row.get("mutated"):
            entry["mutating"] += 1
        entry["origins"][row.get("origin") or "unknown"] += 1
        if row.get("alias_used"):
            entry["aliases"][row["alias_used"]] += 1
        entry["_durations"].append(float(row.get("duration_ms") or 0.0))

    out = []
    for entry in by_tool.values():
        durations = entry.pop("_durations")
        entry["origins"] = dict(entry["origins"].most_common())
        entry["error_classes"] = dict(entry["error_classes"].most_common())
        entry["aliases"] = dict(entry["aliases"].most_common())
        entry["median_ms"] = round(statistics.median(durations), 1) if durations else 0.0
        out.append(entry)
    out.sort(key=lambda e: (-e["calls"], e["tool"]))
    return {
        "total_calls": len(rows),
        "distinct_tools": len(out),
        "by_origin": dict(Counter(r.get("origin") or "unknown" for r in rows).most_common()),
        "tools": out,
    }


# ── 2. core membership ───────────────────────────────────────────────────────────────


def core_membership(rows: list[dict]) -> dict:
    """Every CORE tool with its call count, plus non-core tools that outrank the weakest.

    ``share_of_turns`` is the fraction of DISTINCT turns in which the tool was called at
    least once — the honest denominator for "does this tool earn a seat on every turn",
    since a tool called five times in one turn has still only been useful once.
    """
    from backend.ai.tools._groups import CORE_TOOL_NAMES

    turns = {
        (r.get("session_id"), r.get("turn_index"))
        for r in rows
        if r.get("session_id") and r.get("turn_index", -1) >= 0
    }
    total_turns = len(turns)

    turns_by_tool: dict[str, set] = defaultdict(set)
    calls_by_tool: Counter = Counter()
    for row in rows:
        tool = row.get("tool", "")
        calls_by_tool[tool] += 1
        if row.get("session_id") and row.get("turn_index", -1) >= 0:
            turns_by_tool[tool].add((row["session_id"], row["turn_index"]))

    core_rows = []
    for tool in sorted(CORE_TOOL_NAMES):
        n_turns = len(turns_by_tool.get(tool, ()))
        core_rows.append(
            {
                "tool": tool,
                "calls": calls_by_tool.get(tool, 0),
                "turns": n_turns,
                "share_of_turns": round(n_turns / total_turns, 4) if total_turns else 0.0,
            }
        )
    core_rows.sort(key=lambda e: (e["calls"], e["tool"]))

    non_core = [
        {"tool": tool, "calls": count, "turns": len(turns_by_tool.get(tool, ()))}
        for tool, count in calls_by_tool.most_common()
        if tool not in CORE_TOOL_NAMES
    ]
    return {
        "total_turns": total_turns,
        "core_size": len(CORE_TOOL_NAMES),
        "never_called": [e["tool"] for e in core_rows if e["calls"] == 0],
        "core": core_rows,
        "top_non_core": non_core[:15],
    }


# ── 3. result size ───────────────────────────────────────────────────────────────────


def result_sizes(rows: list[dict]) -> dict:
    """Which tools return the most context-consuming results.

    ``total_chars`` is the ranking metric, not ``max_chars``: a tool returning 15,000 chars
    once costs less than one returning 4,000 chars on every turn, and the latter is the
    better shrink target. ~4 chars ≈ 1 token is the rough conversion used for the estimate.
    """
    by_tool: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        by_tool[row.get("tool", "")].append(int(row.get("result_chars") or 0))

    out = []
    for tool, sizes in by_tool.items():
        total = sum(sizes)
        out.append(
            {
                "tool": tool,
                "calls": len(sizes),
                "total_chars": total,
                "median_chars": int(statistics.median(sizes)) if sizes else 0,
                "p95_chars": int(_percentile([float(s) for s in sizes], 95)),
                "max_chars": max(sizes) if sizes else 0,
                "est_total_tokens": total // 4,
            }
        )
    out.sort(key=lambda e: -e["total_chars"])
    grand_total = sum(e["total_chars"] for e in out)
    return {
        "total_result_chars": grand_total,
        "est_total_tokens": grand_total // 4,
        "tools": out,
    }


# ── 4. turns ─────────────────────────────────────────────────────────────────────────


def turn_shape(rows: list[dict], top: int = 10) -> dict:
    """Tool calls per turn, and the longest call sequences.

    Rows with turn_index < 0 (inbound MCP, connector confirms, REST applies — surfaces with
    no turn concept) are excluded from the distribution and counted separately, so they
    cannot drag the median toward 1.
    """
    per_turn: dict[tuple, list[dict]] = defaultdict(list)
    standalone = 0
    for row in rows:
        if row.get("session_id") and row.get("turn_index", -1) >= 0:
            per_turn[(row["session_id"], row["turn_index"])].append(row)
        else:
            standalone += 1

    counts = [len(calls) for calls in per_turn.values()]
    sequences = []
    for key, calls in per_turn.items():
        ordered = sorted(calls, key=lambda r: r.get("call_index", 0))
        sequences.append(
            {
                "session_id": (key[0] or "")[:14],
                "turn_index": key[1],
                "n_calls": len(ordered),
                "sequence": [r.get("tool", "") for r in ordered],
            }
        )
    sequences.sort(key=lambda s: -s["n_calls"])
    return {
        "turns": len(counts),
        "standalone_calls": standalone,
        "mean_calls_per_turn": round(statistics.mean(counts), 2) if counts else 0.0,
        "median_calls_per_turn": statistics.median(counts) if counts else 0,
        "p95_calls_per_turn": _percentile([float(c) for c in counts], 95),
        "max_calls_per_turn": max(counts) if counts else 0,
        "longest_sequences": sequences[:top],
    }


# ── 5. discovery waste ───────────────────────────────────────────────────────────────


def discovery_waste(rows: list[dict], days: Optional[int]) -> dict:
    """find_tools queries that returned nothing, plus queries followed by a call they missed.

    A "candidate miss" = after a find_tools query, a tool was called that (a) the query did
    not return and (b) is not a CORE tool the model already had. That is a wasted
    round-trip at full context price: the model searched, did not get what it needed, and
    reached the tool some other way.

    The join is EXACT when both sides carry a session id (discovery_log records one for
    this purpose): only calls in the SAME session, after the query, are credited. It falls
    back to a ``_DISCOVERY_WINDOW_SEC`` time window only for events with no session — a
    headless or pre-session-id row — where concurrent sessions can cross-credit. Each row
    reports which join produced it so a reader can weigh it.
    """
    from backend.ai.tools import discovery_log
    from backend.ai.tools._groups import CORE_TOOL_NAMES

    events = discovery_log.recent(1000)
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        events = [e for e in events if (_parse_ts(e.get("ts")) or cutoff) >= cutoff]

    calls = sorted(
        ((_parse_ts(r.get("ts")), r) for r in rows if _parse_ts(r.get("ts"))),
        key=lambda pair: pair[0],
    )

    misses = []
    for event in events:
        at = _parse_ts(event.get("ts"))
        if at is None:
            continue
        hits = set(event.get("hits") or [])
        session = event.get("session_id")
        join = "session" if session else "time-window"
        window_end = at + timedelta(seconds=_DISCOVERY_WINDOW_SEC)
        followed = [
            row
            for ts, row in calls
            if ts >= at
            and (row.get("session_id") == session if session else ts <= window_end)
            and row.get("tool") not in hits
            and row.get("tool") not in CORE_TOOL_NAMES
            and row.get("tool") != "find_tools"
        ]
        if event.get("kind") == "miss" or followed:
            misses.append(
                {
                    "ts": event.get("ts"),
                    "query": event.get("query"),
                    "kind": event.get("kind"),
                    "n_hits": len(hits),
                    "join": join,
                    "called_but_not_returned": sorted({r.get("tool", "") for r in followed}),
                }
            )

    by_kind = Counter(e.get("kind", "ok") for e in events)
    repeated = [q for q, n in Counter(e.get("query", "") for e in events).most_common() if n > 1]
    return {
        "queries": len(events),
        "by_kind": dict(by_kind),
        "miss_rate": round(by_kind.get("miss", 0) / len(events), 4) if events else 0.0,
        "repeated_queries": repeated[:10],
        "candidate_misses": misses[:25],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────────────


def _print_usage(data: dict) -> None:
    print(f"\n── USAGE ── {data['total_calls']} calls · {data['distinct_tools']} distinct tools")
    print(f"   by origin: {data['by_origin'] or '(none)'}")
    if not data["tools"]:
        print("   (no calls recorded yet)")
        return
    print(f"\n   {'tool':<34}{'calls':>6}{'err':>5}{'mut':>5}{'p50 ms':>9}  origins")
    for entry in data["tools"][:30]:
        origins = ",".join(f"{k}={v}" for k, v in entry["origins"].items())
        print(
            f"   {entry['tool']:<34}{entry['calls']:>6}{entry['errors']:>5}"
            f"{entry['mutating']:>5}{entry['median_ms']:>9.1f}  {origins}"
        )


def _print_core(data: dict) -> None:
    print(f"\n── CORE MEMBERSHIP ── {data['core_size']} core tools · {data['total_turns']} turns")
    never = data["never_called"]
    print(f"\n   NEVER CALLED ({len(never)}/{data['core_size']}): {', '.join(never) or '(none)'}")
    print(f"\n   {'core tool':<34}{'calls':>6}{'turns':>7}{'share of turns':>16}")
    for entry in data["core"]:
        print(
            f"   {entry['tool']:<34}{entry['calls']:>6}{entry['turns']:>7}"
            f"{entry['share_of_turns'] * 100:>15.1f}%"
        )
    if data["top_non_core"]:
        print("\n   TOP NON-CORE (candidates for promotion):")
        for entry in data["top_non_core"]:
            print(f"   {entry['tool']:<34}{entry['calls']:>6}{entry['turns']:>7}")


def _print_results(data: dict) -> None:
    print(
        f"\n── RESULT SIZE ── {data['total_result_chars']:,} chars "
        f"(~{data['est_total_tokens']:,} tokens) returned into context"
    )
    if not data["tools"]:
        print("   (no calls recorded yet)")
        return
    print(f"\n   {'tool':<34}{'calls':>6}{'total':>10}{'p50':>8}{'p95':>8}{'max':>9}{'~tok':>9}")
    for entry in data["tools"][:20]:
        print(
            f"   {entry['tool']:<34}{entry['calls']:>6}{entry['total_chars']:>10}"
            f"{entry['median_chars']:>8}{entry['p95_chars']:>8}{entry['max_chars']:>9}"
            f"{entry['est_total_tokens']:>9}"
        )


def _print_turns(data: dict) -> None:
    print(f"\n── TURNS ── {data['turns']} turns · {data['standalone_calls']} standalone calls")
    print(
        f"   calls/turn: mean {data['mean_calls_per_turn']} · median "
        f"{data['median_calls_per_turn']} · p95 {data['p95_calls_per_turn']} · "
        f"max {data['max_calls_per_turn']}"
    )
    if data["longest_sequences"]:
        print("\n   LONGEST SEQUENCES:")
        for seq in data["longest_sequences"]:
            print(
                f"   [{seq['session_id']} t{seq['turn_index']}] {seq['n_calls']:>3} calls: "
                f"{' → '.join(seq['sequence'][:12])}"
            )


def _print_discovery(data: dict) -> None:
    print(f"\n── DISCOVERY ── {data['queries']} find_tools queries · {data['by_kind']}")
    print(f"   miss rate: {data['miss_rate'] * 100:.1f}%")
    if data["repeated_queries"]:
        print(f"   repeated queries: {', '.join(repr(q) for q in data['repeated_queries'])}")
    if data["candidate_misses"]:
        print("\n   CANDIDATE MISSES (query → tool called that it did not return):")
        for miss in data["candidate_misses"]:
            called = ", ".join(miss["called_but_not_returned"]) or "(nothing followed)"
            print(f"   [{miss['join']:<12}] {miss['kind']:<10} {miss['query']!r:<34} → {called}")
    else:
        print("   (no candidate misses)")


_SECTIONS = ("usage", "core", "results", "turns", "discovery")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("section", nargs="?", default="all", choices=("all",) + _SECTIONS)
    parser.add_argument("--days", type=int, default=None, help="only the last N days")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    rows = _load_rows(args.days)
    wanted = _SECTIONS if args.section == "all" else (args.section,)

    report: dict[str, Any] = {"rows_considered": len(rows), "days": args.days}
    if "usage" in wanted:
        report["usage"] = usage(rows)
    if "core" in wanted:
        report["core"] = core_membership(rows)
    if "results" in wanted:
        report["results"] = result_sizes(rows)
    if "turns" in wanted:
        report["turns"] = turn_shape(rows)
    if "discovery" in wanted:
        report["discovery"] = discovery_waste(rows, args.days)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    window = f"last {args.days}d" if args.days else "full ring"
    print(f"\nTOOL-CALL TELEMETRY · {len(rows)} rows · {window}")
    if "usage" in wanted:
        _print_usage(report["usage"])
    if "core" in wanted:
        _print_core(report["core"])
    if "results" in wanted:
        _print_results(report["results"])
    if "turns" in wanted:
        _print_turns(report["turns"])
    if "discovery" in wanted:
        _print_discovery(report["discovery"])
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
