"""CliProbeStateStore — which CLI versions the tool-reachability probe has already judged.

One ``runtime_state`` namespace row: ``{<provider_key>: {"version": str, "ok": bool}}`` — the
last CLI version probed per transport and whether Lucius's tools reached the model on it.
``ai/cli_probe.py`` reads it to probe a CLI once per version, not once per boot.

Runtime state, NOT user-authored data → checkpoint-EXCLUDED (a Lucius restore must not
re-announce a CLI problem the owner already saw), NOT portable. Same shape as
``MemoryConsolidateStateStore``.
"""

from __future__ import annotations

from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state


class CliProbeStateStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("cli_probe_state", table=runtime_state, engine=engine)

    def last_probed_version(self, provider_key: str) -> str:
        row = (self.get() or {}).get(provider_key)
        version = row.get("version") if isinstance(row, dict) else ""
        return version if isinstance(version, str) else ""

    def record(self, provider_key: str, version: str, ok: bool) -> None:
        with self._use_conn(None) as conn:
            doc = self.get(conn=conn) or {}
            doc[provider_key] = {"version": version, "ok": ok}
            self.save(doc, conn=conn)
