"""The tools a screen brings with it.

The interactive offer is CORE plus whatever the session has loaded (ADR
2026-07-19-core-tool-offer-diet); anything else must be loaded with ``find_tools``. A model that
treats its declared tool list as its whole toolset never does. On 2026-09-26 Lucius, asked on
Plan → Budget to set caps from the Zero-Based targets, answered for twenty turns from
``get_profile`` alone — pairing target amounts with envelope names by guesswork — because no
budget tool was ever in its offer, and the one that does the job (``apply_budget_operation``'s
``copy_targets_to_caps``) was never loaded.

A chat turn sent from a screen names it (the snapshot's ``title``). The tools that screen is
about load into the session exactly like a ``find_tools`` load: additive, sticky for the
session, and never a permission — every write still stages its confirm card (ADR
2026-09-26-a-screen-loads-its-own-tools). Leaf: imports nothing internal.
"""

from __future__ import annotations

from typing import Optional

# Keyed by the exact ``data-ctx-title`` the screen renders (``Plan.tsx``).
SCREEN_TOOLS: dict[str, frozenset[str]] = {
    # Both budget methods: the month's view, each envelope by name with its target, cap and
    # note, and the writes the screen itself offers (caps, targets, assigning).
    "Plan — Budget": frozenset(
        {
            "get_budget_view",
            "list_envelopes",
            "apply_budget_operation",
            "set_envelope_targets",
            "assign_budget",
        }
    ),
}


def tools_for_screen(title: Optional[str]) -> frozenset[str]:
    """The tool names the screen titled ``title`` loads — empty for any other screen."""
    return SCREEN_TOOLS.get((title or "").strip(), frozenset())
