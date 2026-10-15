"""Private pipe bootstrap for the desktop-owned, single-worker backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO

_MAX_BOOTSTRAP = 8192
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}")
_INSTANCE = re.compile(r"[A-Za-z0-9_-]{8,128}")
logger = logging.getLogger(__name__)


def read_bootstrap(stream: TextIO) -> dict:
    line = stream.readline(_MAX_BOOTSTRAP + 1)
    if len(line) > _MAX_BOOTSTRAP or not line.endswith("\n"):
        raise ValueError("invalid desktop bootstrap")
    try:
        config = json.loads(line)
    except ValueError as exc:
        raise ValueError("invalid desktop bootstrap") from exc
    if not isinstance(config, dict) or set(config) - {"token", "instance_id", "root", "qa_mode"}:
        raise ValueError("invalid desktop bootstrap")
    for key, pattern in (("token", _TOKEN), ("instance_id", _INSTANCE)):
        if not isinstance(config.get(key), str) or pattern.fullmatch(config[key]) is None:
            raise ValueError("invalid desktop bootstrap")
    root = config.get("root")
    if not isinstance(root, str) or not root or "\x00" in root or not Path(root).is_absolute():
        raise ValueError("desktop root must be absolute")
    if "qa_mode" in config and not isinstance(config["qa_mode"], bool):
        raise ValueError("invalid desktop bootstrap")
    return config


def configure_environment(config: dict, port: int) -> None:
    # Never adopt a developer/server book, credentials or network target from the GUI's env.
    for key in (
        "DATA_DIR",
        "CACHE_DIR",
        "LOGS_DIR",
        "BACKUPS_DIR",
        "ONW_DATABASE_URL",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
        "DEMO_MODE",
        "DEMO_OPEN_ACCESS",
    ):
        os.environ.pop(key, None)
    os.environ.update(
        ONW_DESKTOP_MODE="1",
        ONW_DESKTOP_INSTANCE_ID=config["instance_id"],
        ONW_ROOT=str(Path(config["root"]).resolve()),
        HOST="127.0.0.1",
        PORT=str(port),
        CORS_ORIGINS=f"http://127.0.0.1:{port}",
        AUTH_DISABLED="false",
        AUTH_TRUST_LOOPBACK="true",
        ONW_QA_MODE="1" if config.get("qa_mode") else "0",
    )


def _watch_host(stream: TextIO, server) -> None:
    # EOF means the host died: avoid an orphan scheduler after an Electron crash.
    for line in stream:
        try:
            if json.loads(line) != {"type": "shutdown"}:
                continue
        except ValueError:
            continue
        break
    from backend.core.data_events import mark_closing

    mark_closing()
    server.should_exit = True


def _startup_errors(app, config: dict, output: TextIO) -> None:
    from backend.core.instance_lock import LOCK_NAME, InstanceAlreadyRunning
    from backend.core.version import APP_VERSION

    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original(application) as state:
                yield state
        except InstanceAlreadyRunning:
            try:
                port = json.loads((Path(config["root"]) / LOCK_NAME).read_text())["port"]
            except (OSError, ValueError, KeyError, TypeError):
                port = None
            if type(port) is not int or not 1 <= port <= 65535:
                port = None
            output.write(
                json.dumps(
                    {
                        "type": "startup_error",
                        "protocol": 1,
                        "instance_id": config["instance_id"],
                        "version": APP_VERSION,
                        "code": "book_in_use",
                        "port": port,
                    }
                )
                + "\n"
            )
            output.flush()
            raise

    app.router.lifespan_context = lifespan


async def serve(config: dict, sock: socket.socket, output: TextIO, input_stream: TextIO) -> int:
    import uvicorn

    from backend.api.data_change_transport import DataChangeMiddleware
    from backend.api.desktop_transport import DesktopSession, DesktopTransportMiddleware
    from backend.core.version import APP_VERSION
    from backend.main import app

    _startup_errors(app, config, output)
    session = DesktopSession(config["token"], config["instance_id"], sock.getsockname()[1])
    app.add_middleware(DataChangeMiddleware)
    app.add_middleware(DesktopTransportMiddleware, session=session)
    uv_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=session.port,
        workers=1,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=15,
    )

    class DesktopServer(uvicorn.Server):
        async def startup(self, sockets=None) -> None:
            await super().startup(sockets)
            if self.started:
                output.write(
                    json.dumps(
                        {
                            "type": "ready",
                            "protocol": 1,
                            "port": session.port,
                            "instance_id": session.instance_id,
                            "version": APP_VERSION,
                        }
                    )
                    + "\n"
                )
                output.flush()

    server = DesktopServer(uv_config)
    if sys.platform == "win32":
        # After a graceful stop uvicorn re-raises the signal against the handler it found; Windows'
        # default for Ctrl-Break is a hard exit code, which would report a clean stop as a failure.
        signal.signal(signal.SIGBREAK, lambda *_: None)
    threading.Thread(target=_watch_host, args=(input_stream, server), daemon=True).start()
    try:
        await server.serve(sockets=[sock])
        return 0 if server.started else 1
    finally:
        from backend.core.instance_lock import release

        release()


def main() -> int:
    output = sys.stdout
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        config = read_bootstrap(sys.stdin)
        os.umask(0o077)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            configure_environment(config, sock.getsockname()[1])
            return asyncio.run(serve(config, sock, output, sys.stdin))
    except (ValueError, OSError):
        logger.error("desktop startup configuration failed")
        return 1
    except Exception:
        logger.exception("desktop backend stopped unexpectedly")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
