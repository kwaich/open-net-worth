"""Filesystem behaviour that differs between POSIX and Windows, in one place.

Callers state the intent (``private_open_flags``, ``fsync_directory``, ``replace``) and this
module decides what it means on the host. Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import getpass
import logging
import os
import stat
import subprocess
import time
from pathlib import Path

_log = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

# Windows opens descriptors in text mode unless told otherwise, which rewrites "\n" on every
# read and write through the descriptor. Every os.open of file content must carry this.
O_BINARY = getattr(os, "O_BINARY", 0)
_NOFOLLOW_CLOEXEC = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

# A replace races a reader/scanner holding the destination open without FILE_SHARE_DELETE.
_REPLACE_RETRY_ERRORS = (5, 32, 33)  # access denied, sharing violation, lock violation
_REPLACE_ATTEMPTS = 20
_REPLACE_BACKOFF_SECONDS = 0.05


def open_flags(*flags: int) -> int:
    """Combine ``flags`` with binary mode, close-on-exec and no-follow where the OS has them."""
    combined = O_BINARY | _NOFOLLOW_CLOEXEC
    for flag in flags:
        combined |= flag
    return combined


def is_current_owner(metadata: os.stat_result) -> bool:
    """True if ``metadata`` is owned by this user. Windows has no uid: ownership is the ACL."""
    return IS_WINDOWS or metadata.st_uid == os.geteuid()


def fchmod(descriptor: int, mode: int) -> None:
    """Best-effort ``fchmod``; Windows only models the read-only bit, which a 0o600/0o666
    mode leaves clear, and ``os.fchmod`` is absent before Python 3.13 there."""
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)


def fsync_directory(path: Path) -> None:
    """Durably flush directory-entry changes, propagating any failure.

    Windows cannot open a directory as a file descriptor and journals NTFS metadata itself,
    so there is nothing to flush from user space."""
    if IS_WINDOWS:
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_file(path: Path) -> None:
    """Durably flush an existing regular file without following its final symlink.

    Windows ``fsync`` needs a descriptor opened for writing."""
    descriptor = os.open(path, open_flags(os.O_RDWR if IS_WINDOWS else os.O_RDONLY))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"not a regular file: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace(source: Path | str, destination: Path | str) -> None:
    """``os.replace``, retrying briefly on Windows where a transient reader of the
    destination (indexer, antivirus, another process mid-read) fails the rename."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if not IS_WINDOWS or getattr(exc, "winerror", None) not in _REPLACE_RETRY_ERRORS:
                raise
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS)


def restrict_to_owner(directory: Path) -> None:
    """Make ``directory`` (and everything later created inside it) owner-only.

    POSIX: mode 0o700. Windows has no mode bits; access is the ACL, so the inherited entries are
    dropped and only this user and SYSTEM are granted, with the grant inherited by new children.
    Best-effort on Windows: a refusal is logged and the directory keeps the ACL it inherited,
    which under a user profile already excludes other users."""
    if not IS_WINDOWS:
        os.chmod(directory, 0o700)
        return
    grants = (f"{getpass.getuser()}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F")
    command = ["icacls", str(directory), "/inheritance:r"]
    for grant in grants:
        command += ["/grant:r", grant]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        _log.warning("could not restrict %s to its owner", directory, exc_info=True)
        return
    if result.returncode != 0:
        _log.warning(
            "icacls refused to restrict %s: %s",
            directory,
            result.stderr.decode(errors="replace").strip(),
        )
