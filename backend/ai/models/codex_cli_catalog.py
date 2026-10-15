"""The Codex CLI's OWN model catalog — read from the CLI, never hardcoded here.

The Codex CLI maintains ``$CODEX_HOME/models_cache.json`` (default ``~/.codex``): the
model list it fetched from OpenAI for THIS install, with each model's real reasoning
ladder. That file is the only honest answer to "what can this CLI run", because the CLI
has no ``--list-models`` command and no /models endpoint to call.

We used to ship a hand-written list instead. It rotted completely: by 2026-07-31 every one
of its five ids (``gpt-5.1-codex``, ``gpt-5.1-codex-mini``, ``gpt-5.1``, ``gpt-5-codex``,
``gpt-5``) had been retired, so the picker offered five models and the CLI could run none
of them. A curated list for a tool that publishes its own list is a list that is always
one release from being wrong — so the static list here is a floor for when the cache is
unreadable, not the source of truth.

Read-only: this module never writes the CLI's file.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Used only when the CLI's cache is missing/unreadable (fresh install that has not fetched
# yet, a sandbox with no HOME). Deliberately tiny — a floor, not a catalog.
#
# Verified 2026-08-24 against a real `~/.codex/models_cache.json` (CLI 0.147.0): it offered
# terra, luna, 5.5 and 5.4-mini. The floor's own lead entry was `gpt-5.6-sol`, which that
# install could NOT run — the exact rot the module docstring above describes, returned after
# one release. Ordered to match the CLI's own ordering, which leads with its default.
#
# If this needs updating again, read the cache rather than guessing:
#   python3 -c "import json,pathlib;print([m['slug'] for m in
#   json.loads((pathlib.Path.home()/'.codex'/'models_cache.json').read_text())['models']])"
FALLBACK_MODELS: tuple[str, ...] = ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5")

_CACHE_FILENAME = "models_cache.json"


def codex_home() -> Path:
    """``$CODEX_HOME`` when set, else ``~/.codex`` — the CLI's own resolution order."""
    override = (os.getenv("CODEX_HOME") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".codex"


def _read_cache() -> Optional[dict]:
    try:
        return json.loads((codex_home() / _CACHE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("codex models cache unavailable (%s)", exc)
        return None


def available_models() -> list[dict]:
    """``[{id, display_name, effort_levels}]`` in the CLI's own priority order.

    Hidden models are dropped (``visibility`` other than ``"list"`` — e.g. the internal
    ``codex-auto-review``, which is not a model a user should be able to pick for chat).
    ``supported_in_api`` is deliberately NOT filtered on: we drive the CLI, not the API,
    so a model the API cannot serve is still perfectly runnable here.

    Returns ``[]`` when the cache is absent or malformed — callers fall back to
    ``FALLBACK_MODELS``. Never raises.
    """
    raw = _read_cache()
    rows = (raw or {}).get("models")
    if not isinstance(rows, list):
        return []

    out: list[tuple[int, dict]] = []
    for i, m in enumerate(rows):
        if not isinstance(m, dict):
            continue
        slug = (m.get("slug") or "").strip()
        if not slug or m.get("visibility") != "list":
            continue
        # priority is the CLI's own ordering (1 = flagship). Missing → sort last, but keep
        # the file's relative order among such rows rather than collapsing them together.
        priority = m.get("priority")
        rank = priority if isinstance(priority, int) else 10_000 + i
        out.append(
            (
                rank,
                {
                    "id": slug,
                    "display_name": (m.get("display_name") or "").strip() or slug,
                    "effort_levels": _levels(m.get("supported_reasoning_levels")),
                },
            )
        )
    out.sort(key=lambda pair: pair[0])
    return [row for _, row in out]


def _levels(raw: object) -> list[str]:
    """``supported_reasoning_levels`` → our effort ladder, clamped to the rungs we render.

    The CLI's list is ``[{effort, description}, …]`` and can carry rungs beyond ours (it
    already ships ``ultra``); ``_ladder_intersect`` drops anything we cannot present rather
    than surfacing a rung the effort dial has no option for.
    """
    from backend.ai.models.capabilities import _ladder_intersect

    if not isinstance(raw, list):
        return []
    efforts = [
        (e.get("effort") or "").strip() for e in raw if isinstance(e, dict) and e.get("effort")
    ]
    return _ladder_intersect(efforts)


def effort_levels_for(model_id: str) -> Optional[list[str]]:
    """This model's real ladder per the CLI's cache, or ``None`` when it isn't listed."""
    target = (model_id or "").strip().lower()
    if not target:
        return None
    for row in available_models():
        if row["id"].lower() == target:
            return row["effort_levels"]
    return None


def model_ids() -> list[str]:
    """The pickable model ids, CLI order — falling back to the static floor."""
    ids = [m["id"] for m in available_models()]
    return ids or list(FALLBACK_MODELS)
