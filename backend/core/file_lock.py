"""Cross-process file locks on POSIX and Windows, using the OS lock API.

POSIX takes an advisory ``flock``. Windows takes a byte-range ``LockFileEx`` lock, which
(unlike ``msvcrt.locking``) has a shared mode, so the data-maintenance gate keeps its
shared-writer / exclusive-maintenance semantics on both platforms. Windows byte locks are
mandatory: callers that must read the file's content while it is locked keep the locked byte
beyond that content (``offset``).

Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import os
from typing import IO

_WINDOWS = os.name == "nt"

# The instance lock stores its JSON owner record from byte 0; lock a byte far past it so a
# second process can still read the record while the lock is held.
RECORD_LOCK_OFFSET = 4096

if _WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _LOCKFILE_FAIL_IMMEDIATELY = 0x1
    _LOCKFILE_EXCLUSIVE_LOCK = 0x2

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    _kernel32.UnlockFileEx.restype = wintypes.BOOL

    def _overlapped(offset: int) -> _Overlapped:
        overlapped = _Overlapped()
        overlapped.Offset = offset & 0xFFFFFFFF
        overlapped.OffsetHigh = offset >> 32
        return overlapped


def lock_fd(fd: int, *, exclusive: bool, blocking: bool = True, offset: int = 0) -> None:
    """Lock ``fd``. Raises ``OSError`` when ``blocking`` is False and another holder has it."""
    if _WINDOWS:
        flags = _LOCKFILE_EXCLUSIVE_LOCK if exclusive else 0
        if not blocking:
            flags |= _LOCKFILE_FAIL_IMMEDIATELY
        overlapped = _overlapped(offset)
        if not _kernel32.LockFileEx(
            msvcrt.get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(overlapped)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        import fcntl

        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, operation if blocking else operation | fcntl.LOCK_NB)


def unlock_fd(fd: int, *, offset: int = 0) -> None:
    if _WINDOWS:
        overlapped = _overlapped(offset)
        if not _kernel32.UnlockFileEx(msvcrt.get_osfhandle(fd), 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def lock_exclusive(handle: IO) -> None:
    """Non-blocking exclusive process ownership of ``handle``'s file."""
    lock_fd(
        handle.fileno(),
        exclusive=True,
        blocking=False,
        offset=RECORD_LOCK_OFFSET if _WINDOWS else 0,
    )
