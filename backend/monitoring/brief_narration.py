"""Level-5 pulse briefs — Lucius writes the Holdings brief or a watchlist tab's brief as a short
narrative instead of the template line (``holdings_brief_verbosity`` /
``watchlist_brief_verbosity`` = 5, Settings › Intelligence).

Grounded the same way as every other watch write-up: the model sees a closed set of FACTS (the
same figures the level-1–4 templates use), every number it writes must be one of them
(``watch_analyst_prompt.invents_figures``) and it stays within the user's opinion level
(``ai_opinion``). One reask, else None — the UI then shows the level-4 template, so the line never
goes blank. Cached in memory for 30 minutes per key, so reopening a tab never calls the model
again. Interactive-only: reached from the UI's API routes, never from a headless caller or tool.
"""

from __future__ import annotations

import asyncio
import logging
import time

from backend.core.user_time import user_today
from backend.monitoring import ai_opinion
from backend.monitoring.watch_analyst_prompt import invents_figures

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30 * 60
MAX_TOKENS = 500
MAX_CHARS = 900
MAX_ROWS = 25
_cache: dict[tuple, tuple[float, dict | None]] = {}

SYSTEM = (
    "You write a short daily brief for a personal-finance app: three to five plain sentences on "
    "what matters today across the positions or tickers listed in FACTS — the day's move, who "
    "drove it, what's coming up, and how it compares with the benchmark. Use ONLY the figures in "
    "FACTS, written as they appear there (you may drop decimals, never re-scale to 'k' or 'm'); "
    "never invent a number, a cause or a date. No headings, no bullet points."
)


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def holdings_facts(digest: dict) -> dict:
    """The Holdings brief's figures, from the Intel digest (the same the template reads)."""
    today = digest.get("today") or {}
    bench = (digest.get("market_backdrop") or {}).get("benchmark") or {}
    bench_twr = (bench.get("twr") or {}).get("period_return")
    port = (digest.get("portfolio_return") or {}).get("period_return")
    facts: dict = {"base_currency": digest.get("base_currency"), "period": digest.get("period")}
    if today.get("has_data"):
        facts["today"] = {
            "change": _r(today.get("pl_base"), 0),
            "change_pct": _r((today.get("pl_pct") or 0) * 100, 2),
        }
    facts["top_movers_today"] = [
        {
            "ticker": m.get("ticker") or m.get("label"),
            "change": _r(m.get("day_change_base"), 0),
            "change_pct": _r(m.get("day_change_pct")),
        }
        for m in (digest.get("top_movers") or [])[:5]
    ]
    facts["period_drivers"] = [
        {
            "ticker": d.get("ticker") or d.get("label"),
            "contribution": _r(d.get("contribution_base"), 0),
        }
        for d in (digest.get("return_drivers") or [])[:3]
        if d.get("contribution_base") is not None
    ]
    if port is not None:
        facts["your_return_pct"] = _r(port * 100)
    if bench_twr is not None and bench.get("symbol"):
        facts["benchmark"] = {"symbol": bench["symbol"], "return_pct": _r(bench_twr * 100)}
        if port is not None:
            facts["vs_benchmark_pts"] = _r((port - bench_twr) * 100)
    return facts


def watchlist_facts(list_name: str, rows: list[dict], period: str) -> dict:
    """A watchlist tab's figures, from its pulse rows (the same the template reads)."""
    out = []
    for r in rows[:MAX_ROWS]:
        trend = (r.get("trend") or {}).get(period) or {}
        move = r.get("move") or {}
        event = r.get("next_event")
        out.append(
            {
                "ticker": r.get("ticker"),
                "owned": bool(r.get("position")),
                "day_pct": _r(r.get("day_pct")),
                "times_usual_move": _r(abs(r["day_pct"]) / move["usual_pct"])
                if move.get("usual_pct") and r.get("day_pct") is not None
                else None,
                "move_driver": move.get("kind"),
                "period_pct": _r(trend.get("pct")),
                "vs_comparison_pts": _r(trend.get("vs_bench_pct")),
                "comparison": (r.get("compare") or {}).get("symbol"),
                "next_event": {"kind": event["kind"], "in_days": event["in_days"]}
                if event
                else None,
                "flags": [f.get("text") for f in (r.get("findings") or [])[:3]],
            }
        )
    return {"list": list_name, "period": period, "tickers": out}


def check_brief(result: object, facts: dict, level: str) -> str | None:
    """The validator handed to ``complete_json``: an error string makes it reask once."""
    if not isinstance(result, dict):
        return "Return a JSON object."
    brief = result.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        return "'brief' must be a non-empty string."
    if len(brief) > MAX_CHARS:
        return f"'brief' must be at most {MAX_CHARS} characters — three to five sentences."
    text = " ".join([brief, *ai_opinion.texts(result, level)])
    overstep = ai_opinion.error(result, text, level)
    if overstep:
        return overstep
    bad = invents_figures(text, facts)
    if bad:
        return f"The number {bad} is not in FACTS. Use only those figures."
    return None


def _prompt(title: str, facts: dict, level: str) -> str:
    return (
        f"BRIEF FOR: {title}\n\nFACTS (the only figures you may use):\n{facts}\n\n"
        "Return JSON with:\n- brief: three to five sentences.\n" + ai_opinion.instructions(level)
    )


def _schema(level: str) -> dict:
    return {
        "type": "object",
        "properties": {"brief": {"type": "string"}, **ai_opinion.schema_properties(level)},
        "required": ["brief"],
    }


async def narrate(key: tuple, title: str, facts: dict, level: str) -> dict | None:
    """``{narration, lean?, lean_reason?, suggestion?}`` or None. Cached per ``key`` (the caller
    includes the day, the period and the level) for CACHE_TTL_SECONDS — a None result is cached
    too, so a failing model isn't retried on every tab open."""
    from backend.core import llm_seam

    full_key = (*key, level)
    hit = _cache.get(full_key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    out: dict | None = None
    try:
        result = await llm_seam.complete_json(
            _prompt(title, facts, level),
            system=f"{SYSTEM} {ai_opinion.clause(level)}",
            max_tokens=MAX_TOKENS,
            source="pulse_brief",
            schema=_schema(level),
            validate=lambda r: check_brief(r, facts, level),
            max_reasks=1,
        )
        if result is not None and check_brief(result, facts, level) is None:
            out = {"narration": result["brief"].strip(), **ai_opinion.persisted(result, level)}
    except llm_seam.LLMUnavailable:
        out = None
    except Exception:
        logger.warning("brief_narration: model call failed for %s", key[0], exc_info=True)
    _cache[full_key] = (time.monotonic(), out)
    return out


def list_rows(list_id: str) -> tuple[str, list[dict]] | None:
    """Sync: a watchlist's name and its pulse rows — owned names from the held pulse (they carry
    the position), the rest from the watched pulse. None for an unknown list."""
    from backend.services.watch_pulse import build_pulse
    from backend.services.watchlist import is_topic, read_watchlist

    wl = read_watchlist()
    found = next((w for w in wl.get("watchlists") or [] if w.get("id") == list_id), None)
    if found is None:
        return None
    tickers = {
        str(i.get("value") or "").strip().upper()
        for i in wl.get("items") or []
        if list_id in (i.get("watchlist_ids") or []) and not is_topic(i)
    }
    rows: list[dict] = []
    seen: set[str] = set()
    for population in ("held", "watched"):
        for row in build_pulse(None, population).get("rows") or []:
            t = str(row.get("ticker") or "").upper()
            if t in tickers and t not in seen:
                rows.append(row)
                seen.add(t)
    return str(found.get("name") or "this list"), rows


async def holdings_brief(digest: dict, level: str) -> dict | None:
    facts = holdings_facts(digest)
    key = ("holdings", user_today().isoformat(), digest.get("period"))
    return await narrate(key, "your holdings", facts, level)


async def watchlist_brief(list_id: str, period: str, level: str) -> dict | None:
    """Raises NotFoundError for an unknown list; None when there is nothing to narrate."""
    from backend.core.errors import NotFoundError

    found = await asyncio.to_thread(list_rows, list_id)
    if found is None:
        raise NotFoundError(f"No watchlist {list_id!r}")
    name, rows = found
    if not rows:
        return None
    key = ("watchlist", list_id, user_today().isoformat(), period)
    return await narrate(key, f"the {name} watchlist", watchlist_facts(name, rows, period), level)
