"""Back-compat alias for the prompt-encoded tool protocol.

The implementation moved to :mod:`backend.ai.models.cli_tool_protocol` when the Codex CLI
became a second headless-CLI transport using the identical protocol (2026-07-22). Kept as
a re-export because the name is referenced by the Claude CLI RFC/ADR and its tests.
"""

from __future__ import annotations

from .cli_tool_protocol import (
    CLOSE_TAG,
    OPEN_TAG,
    ToolCallScanner,
    flatten_messages,
    parse_tool_calls,
    render_tool_manifest,
)

__all__ = [
    "CLOSE_TAG",
    "OPEN_TAG",
    "ToolCallScanner",
    "flatten_messages",
    "parse_tool_calls",
    "render_tool_manifest",
]
