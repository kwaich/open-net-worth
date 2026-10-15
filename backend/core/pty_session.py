"""A shell on a pseudo-terminal: the POSIX-only seam behind the in-browser terminal.

``pty``, ``termios`` and ``fcntl`` exist only on POSIX, so they are imported inside the
functions that use them and ``available()`` says whether this platform has them at all. The
child is the root of its own session (``core.process_tree``), so stopping it stops everything the
shell started; a small launcher makes the pty its controlling terminal without forking this
multi-threaded process.

Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from backend.core import process_tree

_READ_CHUNK = 64 * 1024
_KILL_GRACE_SECONDS = 2.0
# Output waiting for a slow client: past this the shell is left to block on a full pty (flow
# control) instead of this process buffering without bound.
_MAX_QUEUED_CHUNKS = 64

# Run in the child (already a session leader, via start_new_session) before it becomes the shell:
# take the pty on stdin as the controlling terminal, no core dumps, then exec. ``-c`` keeps this out of the process table's argv.
_LAUNCHER = (
    "import fcntl, os, resource, sys, termios\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


def available() -> bool:
    """True where a pty exists (POSIX)."""
    return not process_tree.IS_WINDOWS


def _set_window_size(fd: int, cols: int, rows: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class PtySession:
    """One shell on a pty. ``read()`` returns what the shell prints; ``None`` marks its end."""

    def __init__(self, process: subprocess.Popen, master_fd: int) -> None:
        self._process = process
        self._pgid = process_tree.group_of(process.pid)
        self._fd = master_fd
        self._loop = asyncio.get_running_loop()
        self.output: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False
        self._paused = False
        self._loop.add_reader(master_fd, self._on_readable)

    @classmethod
    def open(
        cls,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        cols: int,
        rows: int,
    ) -> PtySession:
        import pty

        master, slave = pty.openpty()
        try:
            _set_window_size(slave, cols, rows)
            os.set_blocking(master, False)
            process = subprocess.Popen(
                [sys.executable, "-c", _LAUNCHER, *argv],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=dict(env),
                cwd=cwd,
                close_fds=True,
                **process_tree.spawn_kwargs(),
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        return cls(process, master)

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, _READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if data:
            self.output.put_nowait(data)
            if self.output.qsize() >= _MAX_QUEUED_CHUNKS:
                self._loop.remove_reader(self._fd)
                self._paused = True
            return
        self._loop.remove_reader(self._fd)
        self.output.put_nowait(None)

    async def read(self) -> bytes | None:
        """The next chunk the shell printed, or None once it has ended."""
        chunk = await self.output.get()
        if self._paused and not self._closed and self.output.qsize() < _MAX_QUEUED_CHUNKS // 2:
            self._paused = False
            self._loop.add_reader(self._fd, self._on_readable)
        return chunk

    def write(self, data: bytes) -> None:
        """Send keystrokes to the shell. A full pty buffer drops the rest rather than block."""
        view = memoryview(data)
        while view and not self._closed:
            try:
                written = os.write(self._fd, view)
            except BlockingIOError:
                return
            except OSError:
                return
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            with contextlib.suppress(OSError):
                _set_window_size(self._fd, cols, rows)

    def exit_code(self) -> int | None:
        return self._process.poll()

    async def close(self) -> None:
        """Stop the shell and everything it started, then release the pty. Idempotent."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(OSError):
            self._loop.remove_reader(self._fd)
        await self._stop_tree()
        with contextlib.suppress(OSError):
            os.close(self._fd)

    async def _stop_tree(self) -> None:
        # Signal the group recorded at spawn, and do it BEFORE the shell is reaped: a group id
        # stays reserved while its leader is a zombie, so no unrelated group can be hit, and
        # jobs the shell put in groups of their own get SIGHUP from the shell itself.
        pid, pgid = self._process.pid, self._pgid
        with contextlib.suppress(OSError):
            process_tree.hangup_group(pgid)
        deadline = self._loop.time() + _KILL_GRACE_SECONDS
        while not process_tree.has_exited(pid, self._process.poll) and self._loop.time() < deadline:
            await asyncio.sleep(0.05)
        with contextlib.suppress(OSError):
            process_tree.kill_group(pgid)
        await asyncio.to_thread(self._process.wait)
