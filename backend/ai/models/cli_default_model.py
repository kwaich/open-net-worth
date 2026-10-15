"""The model a local CLI would run on its own — read from the CLI's config, never guessed.

A CLI subscription has no model of ours: Codex runs whatever ``model`` its ``config.toml``
names, Claude Code whatever its ``settings.json`` names. Suggesting anything else at setup (a
hint we hard-coded, or ``live[0]`` of an unrelated ordering) puts the user on a model they
never chose. So the suggestion is the CLI's own answer, in the CLI's own precedence order.

Read-only and never raises: an unreadable or malformed file just falls to the next source.
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from pathlib import Path

from backend.ai.models.codex_cli_catalog import FALLBACK_MODELS, available_models, codex_home

logger = logging.getLogger(__name__)

# Claude Code publishes no model list; its aliases track the CLI's current model per tier.
# `sonnet` is its own default tier for a subscription with no `model` setting.
_CLAUDE_FALLBACK = "sonnet"


def _claude_config_dir() -> Path:
    """``$CLAUDE_CONFIG_DIR`` when set, else ``~/.claude`` — Claude Code's own resolution."""
    override = (os.getenv("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".claude"


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _codex_configured_model() -> str:
    """``model`` of the profile the top-level ``profile`` selects, else the top-level
    ``model`` — a selected profile's own model wins, as it does in the CLI."""
    try:
        cfg = tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("codex config unavailable (%s)", exc)
        return ""
    profiles = cfg.get("profiles")
    name = _str(cfg.get("profile"))
    if name and isinstance(profiles, dict) and isinstance(profiles.get(name), dict):
        picked = _str(profiles[name].get("model"))
        if picked:
            return picked
    return _str(cfg.get("model"))


def _claude_configured_model() -> str:
    try:
        raw = json.loads((_claude_config_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("claude settings unavailable (%s)", exc)
        return ""
    return _str(raw.get("model")) if isinstance(raw, dict) else ""


def cli_default_model(provider_key: str) -> str:
    """The model this CLI runs when told nothing, or ``""`` for a CLI without a notion of one."""
    if provider_key == "codex_cli":
        configured = _codex_configured_model()
        if configured:
            return configured
        listed = available_models()
        return listed[0]["id"] if listed else FALLBACK_MODELS[0]
    if provider_key == "claude_cli":
        return _claude_configured_model() or _CLAUDE_FALLBACK
    return ""


def cli_default_models() -> dict[str, str]:
    """``{provider_key: default}`` for every CLI transport that has a notion of one."""
    return {key: cli_default_model(key) for key in ("claude_cli", "codex_cli")}
