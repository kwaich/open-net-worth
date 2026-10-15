"""`onw mcp` — the stdio MCP bridge an AI app (Claude Desktop, Cursor, Cline…) spawns.

A thin launcher over ``backend.mcp.stdio_bridge``: same env contract (``ONW_BASE_URL`` /
``ONW_API_TOKEN``), but runnable from any install — a ``uv tool`` install, a source checkout,
or the desktop app's bundled Python (``<python> -m backend.cli mcp``) — with no ``cwd``.

With no ``ONW_BASE_URL``, the address is read from the instance lock of the working folder
(``ONW_ROOT``, else the recorded/default root). The desktop app binds a fresh port on every
launch, so a URL baked into a client config would go stale at the next restart; the lock is
re-read per message so a long-lived client follows the app across restarts.

stdout is the exclusive JSON-RPC channel, and the token is never printed anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)


def _lock_path() -> Optional[Path]:
    try:
        from backend.core.config import get_settings
        from backend.core.instance_lock import LOCK_NAME

        # The same folder main.py's lifespan claims: DATA_DIR's parent.
        return get_settings().data_dir.parent / LOCK_NAME
    except Exception:  # a broken config must not kill the bridge — fall back to the default URL
        logger.warning("onw mcp: could not resolve the working folder", exc_info=True)
        return None


def discovered_base_url() -> Optional[str]:
    """``http://127.0.0.1:<port>`` from the instance lock, or None when none is recorded."""
    path = _lock_path()
    if path is None:
        return None
    try:
        port = json.loads(path.read_text(encoding="utf-8"))["port"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    return f"http://127.0.0.1:{port}"


def resolve_target(explicit: Optional[str]) -> Union[str, Callable[[], str]]:
    """An explicit URL (flag, then env) is fixed; otherwise re-discover on every message."""
    from backend.mcp.stdio_bridge import DEFAULT_BASE_URL

    fixed = (explicit or "").strip() or os.environ.get("ONW_BASE_URL", "").strip()
    if fixed:
        return fixed
    return lambda: discovered_base_url() or DEFAULT_BASE_URL


def run_mcp(base_url: Optional[str] = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    from backend.mcp import stdio_bridge

    token = os.environ.get("ONW_API_TOKEN", "").strip() or None
    return stdio_bridge.run(sys.stdin.buffer, sys.stdout.buffer, resolve_target(base_url), token)
