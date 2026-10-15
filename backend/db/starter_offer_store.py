"""Which built-in starter skills this book has been OFFERED — so a starter shipped after an
install already existed reaches it exactly once.

Boot seeds starters only into an empty library, so a starter added in a later release would
never reach an existing install. Installing every missing starter on each boot is not the fix
either: a starter the owner deleted would come back on every restart. This records each id the
install has been offered; a new starter is installed once, and a deleted one stays deleted.

A namespace row on ``runtime_state`` (checkpoint-EXCLUDED, non-portable), like
``BudgetLensIntroStore`` — a ``/rewind`` must not re-offer a starter the owner already removed.
"""

from __future__ import annotations

from typing import Iterable, Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

_IDS = "ids"


class StarterOfferStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("starter_skills_offered", table=runtime_state, engine=engine)

    def offered(self) -> set[str]:
        return {str(sid) for sid in (self.get() or {}).get(_IDS) or []}

    def record(self, ids: Iterable[str]) -> None:
        self.save({_IDS: sorted(set(ids))})
