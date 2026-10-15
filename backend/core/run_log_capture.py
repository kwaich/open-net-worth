"""ContextVar-based log capture for agent runs.

A single handler is registered once on the root `backend` logger at INFO level.
During a run, start_log_capture() arms a ContextVar list; every record from
backend.* loggers (except HTTP access noise) is appended to it.
collect_log_capture() harvests the list and resets the ContextVar.

Concurrent async runs are fully isolated — each has its own ContextVar binding.
The handler itself is stateless; the ContextVar does the routing.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone

_captured_logs: ContextVar[list | None] = ContextVar("_captured_logs", default=None)
_handler_installed = False

# HTTP access log lines emitted by concurrent frontend requests during the run —
# these are NOT agent activity and would pollute the run log. Skip them.
_HTTP_ACCESS_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /")


class _RunLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        captured = _captured_logs.get(None)
        if captured is None:
            return
        try:
            msg = record.getMessage()
            # Skip HTTP access log lines from concurrent frontend requests
            if _HTTP_ACCESS_RE.match(msg):
                return
            captured.append(
                {
                    "type": "log",
                    "t": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": msg,
                }
            )
        except Exception:
            pass  # never crash the logger


# Capture everything — DEBUG and above — from backend.* loggers.
_handler = _RunLogHandler(logging.DEBUG)


def _ensure_handler() -> None:
    global _handler_installed
    if not _handler_installed:
        logging.getLogger("backend").addHandler(_handler)
        _handler_installed = True


def start_log_capture() -> None:
    """Arm log capture for the current async context (called by a headless runner)."""
    _ensure_handler()
    _captured_logs.set([])


def collect_log_capture() -> list:
    """Harvest captured log entries and reset the ContextVar."""
    logs = _captured_logs.get(None) or []
    _captured_logs.set(None)
    return logs
