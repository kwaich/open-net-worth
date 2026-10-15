"""`onw start` and `onw upgrade`.

`start` is the same boot as `python3 -m backend.main` (fail-closed migrations, the working-folder
claim, one uvicorn process serving API + SPA + docs) with two conveniences on top: flag overrides
for host/port and `--open`, which launches the browser once /api/health answers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from backend.core.version import APP_VERSION, DISTRIBUTION


def run_server(*, host: str | None, port: int | None, open_browser: bool) -> int:
    # Settings are read from the environment once, at first import — the overrides must land
    # BEFORE anything under backend.* other than core.version is imported.
    if host:
        os.environ["HOST"] = host
    if port:
        os.environ["PORT"] = str(port)
    from backend.core.config import get_settings

    settings = get_settings()
    url = browser_url(settings.host, settings.port)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    from backend.main import app

    print(f"==> Open Net Worth {APP_VERSION} — {url}")
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


def browser_url(host: str, port: int) -> str:
    """The address a browser on this machine should open for a server bound to host:port."""
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{browser_host}:{port}/"


def _open_when_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.5)


def upgrade() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print(
            "uv is not on your PATH. Install it (https://docs.astral.sh/uv/) and run:\n"
            f"  uv tool upgrade {DISTRIBUTION}",
            file=sys.stderr,
        )
        return 1
    if not installed_with_uv_tool(uv):
        print(
            f"This copy runs from {sys.prefix}, which `uv tool` does not manage.\n"
            "  A git checkout updates with:   git pull && make setup\n"
            f"  A uv tool install updates with: uv tool upgrade {DISTRIBUTION}",
            file=sys.stderr,
        )
        return 1
    return subprocess.call([uv, "tool", "upgrade", DISTRIBUTION])


def installed_with_uv_tool(uv: str, prefix: str = sys.prefix) -> bool:
    """True when the running interpreter lives inside uv's managed tools directory."""
    try:
        probe = subprocess.run([uv, "tool", "dir"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    tools_dir = probe.stdout.strip()
    if not tools_dir:
        return False
    return Path(prefix).resolve().is_relative_to(Path(tools_dir).resolve())
