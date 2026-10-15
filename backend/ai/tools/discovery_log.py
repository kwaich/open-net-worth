"""find_tools search-quality telemetry (tool-discovery RFC follow-up).

Records one event per ``find_tools(query=…)`` call so we can answer empirically
"is the lexical search good enough?" — without guessing. Each event is classified:

  * ``miss``      — the query matched NOTHING in the registry (a vocabulary gap; the
                    actionable signal that may justify synonym/alias expansion).
  * ``core_only`` — the query matched ONLY already-loaded CORE tools (the model
                    searched for something it already had; mild confusion signal).
  * ``ok``        — the query loaded one or more non-core tools (the happy path).

Events go to TWO places so they're easy to find later:
  1. The standard logger, with greppable markers ``find_tools_miss`` (WARNING) and
     ``find_tools_query`` (INFO) — e.g. ``grep find_tools_miss backend/logs/app.log``.
  2. A capped ring in the ``tool_discovery_log`` table (WAVE 5B: JSON->DB — was a JSONL
     ring at ``TOOL_DISCOVERY_LOG_JSON``) that survives log rotation, queryable like
     ``cost_log`` (see ``recent`` / ``miss_rate`` below). Each event also carries the
     owning ``session_id``, which is what lets the per-tool-call telemetry ring
     (``tool_call_log``) join a query to the calls that followed it and report DISCOVERY
     WASTE — a query answered by reaching for a tool it never returned. See
     ``backend/scripts/tool_telemetry_report.py``.

Best-effort throughout: telemetry must NEVER break the find_tools call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _classify(raw_hits: list[str], kept_hits: list[str]) -> str:
    """miss = nothing matched; core_only = matched only already-loaded core; ok = loaded something."""
    if not raw_hits:
        return "miss"
    if not kept_hits:
        return "core_only"
    return "ok"


def record_query(query: str, raw_hits: list[str], kept_hits: list[str]) -> None:
    """Log + persist one find_tools query event. Never raises.

    ``raw_hits``  = everything ``_search_tools`` returned (before the core filter).
    ``kept_hits`` = the non-core tools actually newly loaded for the session.
    """
    kind = _classify(raw_hits, kept_hits)
    try:
        if kind == "miss":
            logger.warning("find_tools_miss query=%r returned 0 tools (vocabulary gap)", query)
        elif kind == "core_only":
            logger.info(
                "find_tools_query query=%r kind=core_only matched only loaded core %s",
                query,
                raw_hits,
            )
        else:
            logger.info("find_tools_query query=%r kind=ok loaded=%s", query, kept_hits)
    except Exception:  # noqa: BLE001 — logging must never break the tool
        pass

    _append_event(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "kind": kind,
            "hits": list(raw_hits),
            # The owning conversation, so per-tool-call telemetry can join a query to the
            # calls that ACTUALLY FOLLOWED it ("which queries were followed by a call the
            # query never returned" — the discovery-waste question). Without it the join
            # has to fall back to a time window, which cross-credits concurrent sessions.
            # Best-effort: None outside a chat turn, exactly like every other reader of
            # this ContextVar.
            "session_id": _session_id(),
        }
    )


def _session_id():
    """The active chat session id, or None. Never raises (telemetry-grade)."""
    try:
        from backend.core.chat_session import get_session_id

        return get_session_id()
    except Exception:  # noqa: BLE001
        return None


def _append_event(event: dict) -> None:
    """Append ``event`` to the capped ring (best-effort — the store's own append does
    its insert-then-evict cycle atomically; nothing extra to lock here)."""
    try:
        from backend.db.tool_discovery_log_store import ToolDiscoveryLogStore

        ToolDiscoveryLogStore().append(event)
    except Exception:  # noqa: BLE001 — telemetry must never break find_tools
        logger.debug("discovery_log: could not persist event", exc_info=True)


def recent(limit: int = 50) -> list[dict]:
    """Return the most recent query events (newest last). Empty on any error."""
    try:
        from backend.db.tool_discovery_log_store import ToolDiscoveryLogStore

        return ToolDiscoveryLogStore().recent(limit)
    except Exception:
        return []


def miss_rate() -> dict:
    """Summarize the persisted ring: counts by kind + miss fraction. For quick debugging."""
    from backend.db.tool_discovery_log_store import MAX_EVENTS

    events = recent(MAX_EVENTS)
    total = len(events)
    by_kind: dict[str, int] = {"ok": 0, "core_only": 0, "miss": 0}
    for e in events:
        by_kind[e.get("kind", "ok")] = by_kind.get(e.get("kind", "ok"), 0) + 1
    return {
        "total": total,
        "by_kind": by_kind,
        "miss_rate": (by_kind["miss"] / total) if total else 0.0,
    }
