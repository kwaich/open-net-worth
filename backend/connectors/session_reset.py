"""``reset_session`` — split out of ``session_map.py`` (security review 2026-08-17).

The ONLY session_map operation that needs ``ai.assistant`` (to actually clear the old
session's transcript/fold/proactive state, not just mint a fresh id). Keeping it here
instead of in ``session_map.py`` itself is what lets ``services.connectors.remove_connector``
import ``session_map`` directly to forget a removed connector's mappings without
``services`` transitively reaching ``backend.ai`` — which would break import-linter's
layers contract (``services`` must not import ``ai``, even indirectly). Nothing under
``backend.services`` may import this module.
"""

from __future__ import annotations

from backend.ai import assistant
from backend.connectors import session_map


def reset_session(connector: str, user_id: str) -> str:
    """Clear the current session and assign a new UUID."""
    old_session, new_session = session_map.rotate_session(connector, user_id)
    if old_session:
        assistant.clear_session(old_session)
    return new_session
