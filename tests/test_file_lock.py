"""core/file_lock.py — shared/exclusive cross-handle locks, identical on POSIX and Windows.

Two descriptors opened on the same file contend exactly as two processes do (flock locks belong
to the open file description; LockFileEx locks belong to the handle), so one process proves
the semantics without a subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from backend.core import file_lock


@pytest.fixture
def lock_path(tmp_path):
    path = tmp_path / "lock"
    path.write_bytes(b"")
    return path


def _open(path):
    return os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))


def test_exclusive_lock_excludes_every_other_holder(lock_path):
    first, second = _open(lock_path), _open(lock_path)
    try:
        file_lock.lock_fd(first, exclusive=True, blocking=False)
        with pytest.raises(OSError):
            file_lock.lock_fd(second, exclusive=True, blocking=False)
        with pytest.raises(OSError):
            file_lock.lock_fd(second, exclusive=False, blocking=False)
        file_lock.unlock_fd(first)
        file_lock.lock_fd(second, exclusive=True, blocking=False)
        file_lock.unlock_fd(second)
    finally:
        os.close(first)
        os.close(second)


def test_shared_locks_coexist_and_block_an_exclusive_one(lock_path):
    first, second, third = _open(lock_path), _open(lock_path), _open(lock_path)
    try:
        file_lock.lock_fd(first, exclusive=False, blocking=False)
        file_lock.lock_fd(second, exclusive=False, blocking=False)
        with pytest.raises(OSError):
            file_lock.lock_fd(third, exclusive=True, blocking=False)
        file_lock.unlock_fd(first)
        file_lock.unlock_fd(second)
        file_lock.lock_fd(third, exclusive=True, blocking=False)
        file_lock.unlock_fd(third)
    finally:
        for descriptor in (first, second, third):
            os.close(descriptor)


def test_closing_the_descriptor_releases_the_lock(lock_path):
    first = _open(lock_path)
    file_lock.lock_fd(first, exclusive=True, blocking=False)
    os.close(first)
    second = _open(lock_path)
    try:
        file_lock.lock_fd(second, exclusive=True, blocking=False)
    finally:
        os.close(second)


_PROBE = """
import json, sys
from pathlib import Path
from backend.core import file_lock

path = Path(sys.argv[1])
with open(path, "a+", encoding="utf-8") as handle:
    try:
        file_lock.lock_exclusive(handle)
        locked = True
    except OSError:
        locked = False
    record = json.loads(path.read_text(encoding="utf-8"))
print(json.dumps({"locked": locked, "record": record}))
"""


def test_owner_record_stays_readable_while_another_process_holds_the_lock(tmp_path):
    path = tmp_path / "instance.lock"
    with open(path, "a+", encoding="utf-8") as handle:
        file_lock.lock_exclusive(handle)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "port": 8080}))
        handle.flush()
        probe = subprocess.run(
            [sys.executable, "-c", _PROBE, str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    seen = json.loads(probe.stdout)
    assert seen["locked"] is False
    assert seen["record"] == {"pid": os.getpid(), "port": 8080}
