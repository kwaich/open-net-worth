"""watch_reader — Lucius's read on EVERY live watch story. Runs in the scheduler's
``watch_signals`` job right after the explained alerts (``watch_analyst.explain_material``), on
the same context, so it costs no extra quote fetch.

The deterministic watch story restates the pulse flags ("Earnings in 5 days", "Reclaimed its
200-day average"). The read adds one or two sentences on what that means for someone watching the
stock, and one or two things to watch next — grounded in the same closed facts the explained alert
uses, today's price-movement note if it is already written, and the asset brief's earlier
research. It stays within the user's opinion level (``ai_opinion``).

In-app only: never pushed, never marks the story unread, never adds a timeline entry — an opinion
refresh is not a development. Re-read only when the story's findings, the session or the opinion
level change; at most ``watch_reads_per_day`` a day; skipped while ``watch_reads_enabled`` is off,
for a story the explained alert already covered this session, and when no LLM is registered.
ADR 2026-09-27-watch-story-reads-and-opinion-level.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from backend.monitoring import ai_opinion
from backend.monitoring import watch_reader_prompt as rp

logger = logging.getLogger(__name__)

MAX_TOKENS = 600
FIELD = "lucius_read"
_LOG_FIELD = "lucius_read_log"
_LOG_KEEP = 50  # ≥ the settings cap (services.watchlist.WATCH_READS_MAX_PER_DAY)


def _reads_today(stories: list[dict], today: str) -> int:
    return sum(1 for s in stories for at in s.get(_LOG_FIELD) or [] if str(at).startswith(today))


def read_key(ticker: str, kinds: list[str], session: str, level: str) -> str:
    return f"{ticker}:{'+'.join(sorted(kinds))}:{session}:{level}"


def _live_watch_stories(stories: list[dict]) -> dict[str, dict]:
    from backend.services.watch_signals import SYNTHETIC_PREFIX

    out: dict[str, dict] = {}
    for s in stories:
        key = s.get("synthetic_key") or ""
        if not key.startswith(f"{SYNTHETIC_PREFIX}:"):
            continue
        if s.get("dismissed") or s.get("resolved") or s.get("archived"):
            continue
        out[key[len(SYNTHETIC_PREFIX) + 1 :]] = s
    return out


def _candidates(ctx: dict, stories: list[dict], level: str) -> list[dict]:
    """Sync (run in a worker thread): every live watch story whose read is missing or stale, the
    loudest first. A story the explained alert covered this session is left to it."""
    from backend.services.price_movements import local_today
    from backend.services.watch_context import evaluate_item

    live = _live_watch_stories(stories)
    out: list[dict] = []
    for item in ctx.get("items") or []:
        key = item["value"].strip().upper()
        story = live.get(key)
        if story is None:
            continue
        evaluated = evaluate_item(item, ctx)
        if evaluated is None:
            continue
        snap, findings = evaluated
        session = local_today(snap.get("exchange_tz"), ctx["now"])
        if str((story.get("explained") or {}).get("trigger_key") or "").endswith(f":{session}"):
            continue
        rk = read_key(key, story.get("watch_kinds") or [], session, level)
        if (story.get(FIELD) or {}).get("read_key") == rk:
            continue
        out.append(
            {
                "item": item,
                "key": key,
                "snap": snap,
                "findings": findings,
                "story": story,
                "read_key": rk,
                "session": session,
            }
        )
    out.sort(
        key=lambda c: (
            c["story"].get("severity") != "high",
            -abs(c["story"].get("metric_pct") or 0.0),
        )
    )
    return out


def _grounding(instrument_id: str | None, session: str) -> tuple[dict | None, list[str]]:
    """Sync: today's price-movement note (only if already written — never narrated on demand, so
    no second model call) and the asset brief's key issues."""
    if not instrument_id:
        return None, []
    note = research = None
    try:
        from backend.db.price_movement_store import PriceMovementStore

        note = PriceMovementStore().get_note(instrument_id, session)
    except Exception:
        logger.debug("watch_reader: note unavailable for %s", instrument_id, exc_info=True)
    try:
        from backend.db.asset_brief_store import AssetBriefStore

        research = rp.research_lines(AssetBriefStore().get(instrument_id))
    except Exception:
        logger.debug("watch_reader: brief unavailable for %s", instrument_id, exc_info=True)
    if not (note or {}).get("summary"):
        note = None
    return note, research or []


async def _write(
    headline: str, facts: dict, note: dict | None, research: list[str], level: str
) -> dict | None:
    from backend.core import llm_seam

    n_sources = len((note or {}).get("sources") or [])
    grounded = rp.grounded_text(note, research)
    try:
        result = await llm_seam.complete_json(
            rp.build_prompt(headline, facts, note, research, level),
            system=rp.system_for(level),
            max_tokens=MAX_TOKENS,
            source="watch_reader",
            schema=rp.schema_for(level),
            validate=lambda r: rp.check_read(r, facts, n_sources, grounded, level),
            max_reasks=1,
        )
    except llm_seam.LLMUnavailable:
        raise
    except Exception:
        logger.warning("watch_reader: model call failed", exc_info=True)
        return None
    if result is None or rp.check_read(result, facts, n_sources, grounded, level) is not None:
        return None
    return result


def _persist(c: dict, result: dict, note: dict | None, level: str, now: str) -> None:
    """Story fields only — no timeline entry, no unread flip (an opinion is not a development)."""
    from backend.services.intel_stories import apply_story_change

    sources = (note or {}).get("sources") or []
    cited = [sources[i] for i in sorted(set(result.get("source_ids") or []))]
    read = {
        "text": result["read"].strip(),
        "watch_for": [str(w).strip() for w in result["watch_for"] if str(w).strip()],
        "sources": cited,
        "read_key": c["read_key"],
        "at": now,
        **ai_opinion.persisted(result, level),
    }
    story = c["story"]
    log = [*(story.get(_LOG_FIELD) or []), now][-_LOG_KEEP:]
    apply_story_change({**story, FIELD: read, _LOG_FIELD: log})


async def read_stories(ctx: dict, *, store=None) -> int:
    """Write Lucius's read onto this pass's live watch stories. Returns how many were written."""
    from backend.core import llm_seam
    from backend.db.intel_stories_store import IntelStoriesStore
    from backend.monitoring import watch_analyst as wa

    config = await asyncio.to_thread(wa.settings)
    budget_cap = int(config.get("watch_reads_per_day", 20) or 0)
    if not config.get("watch_reads_enabled", True) or budget_cap <= 0:
        return 0
    level = ai_opinion.level_of(config)
    store = store or IntelStoriesStore()
    now: datetime = ctx["now"]
    now_iso = now.isoformat()
    stories = await asyncio.to_thread(store.all)
    budget = budget_cap - _reads_today(stories, now.date().isoformat())
    written = 0
    for c in await asyncio.to_thread(_candidates, ctx, stories, level):
        if written >= budget:
            break
        note, research = await asyncio.to_thread(
            _grounding, c["item"].get("instrument_id"), c["session"]
        )
        analyst = await asyncio.to_thread(wa._analyst, c["key"])
        facts = wa._facts(c["item"], c["snap"], c["findings"], ctx, analyst)
        headline = str(c["story"].get("label") or c["key"])
        try:
            result = await _write(headline, facts, note, research, level)
        except llm_seam.LLMUnavailable:  # no provider — nothing else will succeed this pass
            logger.debug("watch_reader: no LLM provider — skipping reads")
            break
        if result is None:
            continue
        await asyncio.to_thread(_persist, c, result, note, level, now_iso)
        written += 1
    if written:
        logger.info("watch_reader: %d read(s)", written)
    return written
