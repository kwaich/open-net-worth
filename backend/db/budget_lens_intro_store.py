"""Plan → Budget introduction dismissal — ONE server-side "has this book seen the intro?"
flag (owner, 2026-08-23 — the naming-collision incident that also drove the
zero_based/envelope rename in ``services/budget_engines/``).

Mirrors ``OnboardingStateStore``'s own shape exactly: a namespace row on ``runtime_state``
(checkpoint-EXCLUDED, non-portable) rather than frontend ``localStorage`` — localStorage is
per-BROWSER, so the introduction would reappear on another device even after the user
already picked a method and moved on (the same drifting-signal problem first run's own
docstring names). Checkpoint-EXCLUDED deliberately: a ``/rewind`` must not re-open an
introduction the user already dismissed, same reasoning as first run.
"""

from __future__ import annotations

from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

_DISMISSED = "dismissed"


class BudgetLensIntroStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("budget_lens_intro", table=runtime_state, engine=engine)

    def dismissed(self) -> bool:
        return bool((self.get() or {}).get(_DISMISSED))

    def dismiss(self) -> bool:
        """Idempotent — a second call re-persists the same ``{"dismissed": True}`` doc,
        a harmless no-op write."""
        self.save({_DISMISSED: True})
        return True
