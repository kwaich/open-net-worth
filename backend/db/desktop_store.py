"""Device-local resource decisions and file intake identities; no host paths or money."""

from __future__ import annotations

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import desktop_access_grants, desktop_ingest_jobs


class DesktopGrantStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(desktop_access_grants, engine=engine, checkpointed=False)


class DesktopIngestStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(desktop_ingest_jobs, engine=engine, checkpointed=False)

    def find_content(self, grant_id: str, content_hash: str) -> dict | None:
        rows = self.query(grant_id=grant_id, content_hash=content_hash)
        return rows[0] if rows else None

    def recent(self, limit: int = 200) -> list[dict]:
        return list(reversed(self.all()))[:limit]
