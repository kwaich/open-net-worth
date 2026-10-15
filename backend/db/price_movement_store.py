"""
PriceMovementStore — per-(instrument, session) "Notable Price Movement" cache.

One row per trading session an instrument has been observed or narrated for, keyed
``"{instrument_id}:{session_date}"`` so a single prefix scan lists a ticker's days newest-first.
A derived cache (checkpoint-EXCLUDED, like ``asset_brief``): the numbers are re-derivable from
the price provider's daily bars and the live quote, the paragraph from the ``evidence`` table.

Note payload (every field optional except the three identity ones):
    id, instrument_id, session_date : identity (session_date is exchange-local YYYY-MM-DD)
    currency                        : the instrument's quote currency
    close, prev_close, change_pct   : the completed session's regular-close facts
    final                           : True once the regular session is over (POST/CLOSED seen)
    live_price, live_change_pct, live_as_of, live_phase : the newest intraday observation
    after_hours                     : {price, change_pct, as_of} | None — the post-market print
    summary, sources                : the LLM paragraph + server-built {url,title,domain} triples
    narrated_at, narrated_change_pct, narrated_phase, narrated_final, narration_checked_at
    updated_at
"""

from __future__ import annotations

from sqlalchemy import select

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import price_movement_notes


def note_id(instrument_id: str, session_date: str) -> str:
    return f"{instrument_id}:{session_date}"


# `for_instrument` — this cache's ONE reader — never asks for more than this many sessions
# (its own callers pass `limit<=200`, per the Asset Detail page's own lookback window), so a
# session note beyond it is pure growth with no reader: never GC'd before this fix.
_MAX_SESSIONS_PER_INSTRUMENT = 200


class PriceMovementStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(price_movement_notes, engine=engine, id_field="id")

    def get_note(self, instrument_id: str, session_date: str) -> dict | None:
        return self.find_by_id(note_id(instrument_id, session_date))

    def for_instrument(self, instrument_id: str, limit: int | None = None) -> list[dict]:
        """This instrument's notes, newest session first (the id embeds the ISO date, so a
        prefix scan ordered by id descending IS date order)."""
        q = (
            select(self._table.c.payload)
            .where(self._table.c.id.like(f"{instrument_id}:%"))
            .order_by(self._table.c.id.desc())
        )
        if limit:
            q = q.limit(limit)
        with self._use_conn(None) as conn:
            return [dict(row[0]) for row in conn.execute(q)]

    def upsert_note(self, note: dict) -> dict:
        iid = note["instrument_id"]
        day = note["session_date"]
        result = self.upsert({**note, "id": note_id(iid, day)})
        self._trim_to_cap(iid)
        return result

    def _trim_to_cap(self, instrument_id: str) -> None:
        """Delete every session note for `instrument_id` beyond the newest
        `_MAX_SESSIONS_PER_INSTRUMENT` (id-descending == date-descending — `for_instrument`'s
        own ordering). A separate, best-effort pass rather than composed into `upsert`'s own
        write transaction: this is a derived, checkpoint-EXCLUDED cache, so a crash between
        the two leaves at most one row over cap, self-healing on the next note."""
        with self._eng().connect() as conn:
            stale_ids = [
                row.id
                for row in conn.execute(
                    select(self._table.c.id)
                    .where(self._table.c.id.like(f"{instrument_id}:%"))
                    .order_by(self._table.c.id.desc())
                    .offset(_MAX_SESSIONS_PER_INSTRUMENT)
                )
            ]
        for stale_id in stale_ids:
            self.delete(stale_id)
