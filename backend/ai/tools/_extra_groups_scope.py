"""Per-session extra tool-group and tool-name scopes (Lane D — tool scoping).

Holds two parallel ContextVars for the current session:

* **Group scope** — rare-group names the session has unlocked via
  ``find_tools(groups=…)``.  Adding a group expands the offer by every tool
  in that group.
* **Name scope** — individual tool names loaded via ``find_tools(query=…)``
  keyword search.  Allows fine-grained loading without unlocking an entire
  group.

Both are *additive* and session-sticky: names/groups are only ever added,
never removed for a given session, and both sets survive turn boundaries
because ``assistant.run`` re-seeds them from persisted session meta at the
start of every turn.

This module is a leaf within ``ai/tools`` — it imports nothing internal.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# Frozen set of extra group names enabled for the current session.
# None = not yet seeded (first turn before assistant.run touches it);
# empty set = seeded but no groups unlocked.
_extra_groups: ContextVar[Optional[frozenset[str]]] = ContextVar("extra_tool_groups", default=None)


def get_extra_groups() -> frozenset[str]:
    """Return the currently enabled extra groups (empty frozenset if none)."""
    v = _extra_groups.get()
    return v if v is not None else frozenset()


def set_extra_groups(groups: frozenset[str]) -> None:
    """Seed the enabled groups for this turn (called by assistant.run at turn start)."""
    _extra_groups.set(groups)


def add_extra_groups(groups: frozenset[str]) -> frozenset[str]:
    """Union ``groups`` into the enabled set; return the new combined set."""
    current = get_extra_groups()
    updated = current | groups
    _extra_groups.set(updated)
    return updated


# Frozen set of extra individual tool names enabled for the current session.
# None = not yet seeded (first turn before assistant.run touches it);
# empty set = seeded but no individual tools loaded.
_extra_names: ContextVar[Optional[frozenset[str]]] = ContextVar("extra_tool_names", default=None)


def get_extra_names() -> frozenset[str]:
    """Return the currently enabled extra tool names (empty frozenset if none)."""
    v = _extra_names.get()
    return v if v is not None else frozenset()


def set_extra_names(names: frozenset[str]) -> None:
    """Seed the enabled tool names for this turn (called by assistant.run at turn start)."""
    _extra_names.set(names)


def add_extra_names(names: frozenset[str]) -> frozenset[str]:
    """Union ``names`` into the enabled set; return the new combined set."""
    current = get_extra_names()
    updated = current | names
    _extra_names.set(updated)
    return updated
