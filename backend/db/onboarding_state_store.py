"""First-run completion stamp — ONE server-side answer to "is this instance set up?".

A namespace row on ``runtime_state`` (checkpoint-EXCLUDED, non-portable), beside
``harness_state``/``app_config``. Replaces two drifting signals: ``is_fresh_setup()`` sniffing
``profile.name`` (a field first run never wrote — name/nickname belong to Lucius now) and the
frontend's ``localStorage['onboarding-dismissed']`` (per-browser, so first run reappeared on
another device). Checkpoint-EXCLUDED deliberately: a ``/rewind`` must not re-open first run.

Design: docs/superpowers/specs/2026-08-22-first-run-and-onboarding-design.md §5.5.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state

_COMPLETED_AT = "completed_at"
_BOOK_ID = "book_id"


class OnboardingStateStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("onboarding", table=runtime_state, engine=engine)

    def completed_at(self) -> Optional[str]:
        val = (self.get() or {}).get(_COMPLETED_AT)
        return val if isinstance(val, str) and val else None

    def restore_pending(self) -> bool:
        doc = self.get() or {}
        return doc.get("restore_pending") is True and not doc.get(_COMPLETED_AT)

    def clear(self) -> None:
        """Forget completion, any pending restore and the book id — a data reset must re-open
        first run, and the next ``book_id()`` read mints the new book's identity."""
        self.save({})

    def book_id(self) -> str:
        """This book's opaque identity, minted on first read and kept until the book is reset
        (``clear``) or replaced by an import (``rotate_book_id``). Browser caches are keyed by
        it, so a tab still open on the old book notices the swap and drops what it cached. The
        read-or-mint composes in ONE transaction: two tabs booting together agree on one id."""
        with self._use_conn(None) as conn:
            doc = self.get(conn=conn) or {}
            existing = doc.get(_BOOK_ID)
            if isinstance(existing, str) and existing:
                return existing
            doc[_BOOK_ID] = uuid.uuid4().hex
            self.save(doc, conn=conn)
            return doc[_BOOK_ID]

    def rotate_book_id(self) -> str:
        """Give the book a new identity — a whole-book import replaces every row under it."""
        with self._use_conn(None) as conn:
            doc = self.get(conn=conn) or {}
            doc[_BOOK_ID] = uuid.uuid4().hex
            self.save(doc, conn=conn)
            return doc[_BOOK_ID]

    def mark_restored(self) -> None:
        with self._use_conn(None) as conn:
            doc = self.get(conn=conn) or {}
            if not doc.get(_COMPLETED_AT):
                doc["restore_pending"] = True
                self.save(doc, conn=conn)

    def mark_complete(self) -> str:
        """Stamp first run as finished. Idempotent — a second call never moves the timestamp,
        so a duplicated tab finishing twice cannot rewrite when setup actually happened. Read
        and write compose in ONE transaction."""
        with self._use_conn(None) as conn:
            doc = self.get(conn=conn) or {}
            existing = doc.get(_COMPLETED_AT)
            if isinstance(existing, str) and existing:
                return existing
            stamp = datetime.now(timezone.utc).isoformat()
            doc[_COMPLETED_AT] = stamp
            doc.pop("restore_pending", None)
            self.save(doc, conn=conn)
            return stamp
