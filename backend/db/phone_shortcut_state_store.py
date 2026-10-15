"""PhoneShortcutStateStore — the address the owner's phone reaches this instance on.

One ``runtime_state`` namespace row: ``{"phone_base_url": str}``. An instance is routinely
opened at ``localhost`` (the desktop app, a browser on the same machine) while the phone
reaches it through a tunnel or Tailscale, so the address a phone needs cannot be detected from
the request that opens Settings. It is remembered once known instead — see
``services.shortcuts.phone_address``.

Runtime state, NOT user-authored data → checkpoint-EXCLUDED, NOT portable (a book moved to a
new host has a new address).
"""

from __future__ import annotations

from typing import Optional

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import runtime_state


class PhoneShortcutStateStore(NamespacedSettingsStore):
    def __init__(self, engine: Optional[object] = None):
        super().__init__("phone_shortcuts", table=runtime_state, engine=engine)

    def phone_base_url(self) -> str:
        value = (self.get() or {}).get("phone_base_url")
        return value if isinstance(value, str) else ""

    def set_phone_base_url(self, url: str) -> None:
        with self._use_conn(None) as conn:
            doc = self.get(conn=conn) or {}
            doc["phone_base_url"] = url
            self.save(doc, conn=conn)
