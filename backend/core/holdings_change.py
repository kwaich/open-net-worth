"""Dependency-inverted seam: a `positions` table write notifies whoever reconciles
holdings-derived state, without `db/` importing `services/` (the dependency rule — `db`
sits below `services`, so it may never import upward).

Mirrors `core.automation_runner`'s register/fire shape (a single registrant, wired up as a
load-time side effect of importing the module that owns the behavior) rather than
`core.io`'s write-hook pair — this fires POST-commit for one specific table, not pre-write
for every table, and there is exactly one consumer (watchlist reconciliation), not a
fan-out.

`db.position_store.PositionStore` calls `notify_holdings_changed()` after a REAL quantity
change (create/touch/delete) commits; `services.watchlist` registers the actual
reconciliation function at import time. Neither side has to know the other's layer.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

_log = logging.getLogger(__name__)

_hook: Optional[Callable[[], None]] = None


def register_holdings_changed_hook(fn: Optional[Callable[[], None]]) -> None:
    """Install (or clear, with `None`) the positions-changed hook. Last registration wins —
    there is only ever one real registrant (`services.watchlist`), so this mirrors
    `core.io.register_write_hook`'s single-slot shape rather than `core.channels`'s
    multi-registrant fan-out."""
    global _hook
    _hook = fn


def notify_holdings_changed() -> None:
    """Invoke the registered hook, if any. Best-effort — NEVER raises and never blocks the
    write it follows: a reconciliation failure must not turn a successful position write
    into a failed request, and must not delay one either. Logged at error level (not
    swallowed to debug like `core.io`'s hooks) because this guards a real, user-visible
    feature gap — a silently-stale "My holdings" watchlist — not a pure defensive backstop."""
    hook = _hook
    if hook is None:
        return
    try:
        hook()
    except Exception:
        _log.exception("holdings-changed hook failed")
