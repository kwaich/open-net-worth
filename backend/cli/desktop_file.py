"""Private, one-shot descriptor-anchored reads for native watched-folder grants."""

from __future__ import annotations

import json
import math
import os
import stat
import sys
import threading
import types
from pathlib import Path

# This one-shot helper must start with the standard library alone: importing the ``backend.core``
# package runs its __init__, which loads settings and third-party dependencies and creates the
# data folders. Expose the package as a bare namespace so only the two stdlib-only modules load.
if "backend.core" not in sys.modules:
    _core = types.ModuleType("backend.core")
    _core.__path__ = [str(Path(__file__).resolve().parent.parent / "core")]
    sys.modules["backend.core"] = _core

from backend.core.anchored_dir import open_dir  # noqa: E402
from backend.core.platform_fs import IS_WINDOWS  # noqa: E402

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_BOOTSTRAP_BYTES = 8192


class WatchedFileError(ValueError):
    """A file no longer matches the native grant and stabilized file identity."""


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise WatchedFileError("Invalid watched-file identity.")
    return value


def _validated(request: object) -> dict:
    if not isinstance(request, dict) or not isinstance(request.get("file"), dict):
        raise WatchedFileError("Invalid watched-file request.")
    directory, name = request.get("directory"), request.get("name")
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise WatchedFileError("Invalid watched folder.")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 1024
        or name in (".", "..")
        or any(character in name for character in ("/", "\\", "\x00"))
    ):
        raise WatchedFileError("Invalid watched filename.")
    for identity in (request, request["file"]):
        for key in ("device", "inode"):
            _integer(identity.get(key))
    size = _integer(request["file"].get("size"))
    modified = request["file"].get("modifiedAt")
    if not 0 < size <= MAX_FILE_BYTES:
        raise WatchedFileError("Choose a regular file smaller than 25 MB.")
    if type(modified) not in (float, int) or not math.isfinite(modified):
        raise WatchedFileError("Invalid watched-file timestamp.")
    return request


def _same_number(observed: int, expected: int) -> bool:
    """Node reports a Windows file index as a double, which cannot hold every 64-bit id, so the
    observed id is compared after the same rounding. POSIX ids are compared exactly."""
    return observed == expected or (IS_WINDOWS and float(observed) == float(expected))


def _same_device(observed: int, expected: int) -> bool:
    """Python reads a Windows volume as a 64-bit serial; Node reports its low 32 bits."""
    return observed == expected or (IS_WINDOWS and observed & 0xFFFFFFFF == expected)


def _matches_file(observed: os.stat_result, expected: dict) -> bool:
    return (
        stat.S_ISREG(observed.st_mode)
        and _same_device(observed.st_dev, expected["device"])
        and _same_number(observed.st_ino, expected["inode"])
        and observed.st_size == expected["size"]
        and abs(observed.st_mtime_ns / 1_000_000 - expected["modifiedAt"]) <= 0.001
    )


def read_watched_file(request: object) -> tuple[str, bytes]:
    request = _validated(request)
    with open_dir(request["directory"]) as folder:
        observed = folder.fstat()
        if not _same_device(observed.st_dev, request["device"]) or not _same_number(
            observed.st_ino, request["inode"]
        ):
            raise WatchedFileError("The watched folder changed. Choose the folder again.")
        descriptor = folder.open_file(request["name"], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        try:
            expected = request["file"]
            if not _matches_file(os.fstat(descriptor), expected):
                raise WatchedFileError("The file changed after it was selected.")
            content = bytearray()
            while len(content) <= expected["size"]:
                chunk = os.read(descriptor, min(65536, expected["size"] + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) != expected["size"] or not _matches_file(
                os.fstat(descriptor), expected
            ):
                raise WatchedFileError("The file changed while being read.")
            return request["name"], bytes(content)
        finally:
            os.close(descriptor)


def _exit_when_host_closes() -> None:
    while os.read(sys.stdin.fileno(), 1):
        pass
    os._exit(1)


def main() -> int:
    try:
        line = sys.stdin.buffer.readline(MAX_BOOTSTRAP_BYTES + 1)
        if not line.endswith(b"\n") or len(line) > MAX_BOOTSTRAP_BYTES:
            raise WatchedFileError("Invalid watched-file bootstrap.")
        request = json.loads(line)
        threading.Thread(target=_exit_when_host_closes, daemon=True).start()
        name, content = read_watched_file(request)
        header = json.dumps({"type": "file", "size": len(content), "name": name}).encode()
        sys.stdout.buffer.write(header + b"\n" + content)
        sys.stdout.buffer.flush()
        return 0
    except WatchedFileError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    except (OSError, ValueError, TypeError):
        sys.stderr.write("The watched folder or file changed, or cannot be read safely.\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
