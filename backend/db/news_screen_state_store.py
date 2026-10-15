"""NewsScreenStateStore — the news-significance-screen daily budget watermark.

A single tiny state row: ``{day: "YYYY-MM-DD", screened: int}`` — the rolling calendar-day
screen-count ``backend/monitoring/news_screen.py`` checks against ``_DAILY_SCREEN_CAP``
before running another LLM significance call (mirrors ``analyst.py``'s own daily-cap
pattern).

Runtime state, NOT user-authored data → checkpoint-EXCLUDED (a Lucius restore must not
roll today's screen count back and silently re-open budget that was already spent), NOT
portable.

WAVE 5F (JSON->DB): replaces the hand-rolled ``news_screen_state.json`` (CACHE_DIR) file
I/O with a row in the ``runtime_state`` table, namespace ``"news_screen_state"`` — mirrors
``HarnessStateStore`` exactly (same namespaced-singleton shape, same table; see
``docs/adr/2026-08-10-intentionally-file-backed-stores.md`` for why this is STATE, not a
cache that deserves to stay a file).
"""

from __future__ import annotations

from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state


class NewsScreenStateStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("news_screen_state", table=runtime_state, engine=engine)
