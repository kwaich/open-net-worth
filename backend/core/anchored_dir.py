"""A directory that every lookup is made relative to, so a path component cannot be re-pointed.

Callers that must not follow a symlink an attacker planted mid-operation (skill folders, the
desktop's watched folders) open a directory once and then address children by NAME through it.

POSIX holds a directory file descriptor and uses the ``*at`` family (``dir_fd=``) with
``O_NOFOLLOW``, so the kernel resolves each name against the pinned directory and refuses links.

Windows has no ``openat``. ``_WindowsDir`` addresses children by path but refuses every reparse
point (symlink or junction) before touching a name, and proves the object it opened is the one it
inspected by comparing the descriptor's file identity with the identity seen beforehand. That
narrows the swap window to the instant between the inspection and the open and detects a swap
inside it; it does not make the pair atomic the way ``dir_fd`` does. Callers still re-verify the
directory's identity around a transaction (``fstat``), which on Windows is the same inspection.

Stdlib only — ``core`` is a leaf.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from backend.core import platform_fs

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class AnchoredDir:
    """Interface. ``name`` arguments are single path components, never paths."""

    def open_child(self, name: str) -> AnchoredDir:
        raise NotImplementedError

    def stat(self, name: str) -> os.stat_result:
        """``lstat`` of a child; a link of any kind reports ``S_ISLNK``."""
        raise NotImplementedError

    def fstat(self) -> os.stat_result:
        raise NotImplementedError

    def listdir(self) -> list[str]:
        raise NotImplementedError

    def mkdir(self, name: str) -> None:
        raise NotImplementedError

    def open_file(self, name: str, flags: int, mode: int = 0o666) -> int:
        """Open a child without following a link. Returns a binary-mode descriptor."""
        raise NotImplementedError

    def replace(self, source: str, destination: str) -> None:
        raise NotImplementedError

    def rename(self, source: str, destination: str) -> None:
        raise NotImplementedError

    def unlink(self, name: str) -> None:
        raise NotImplementedError

    def rmdir(self, name: str) -> None:
        raise NotImplementedError

    def fsync(self) -> None:
        raise NotImplementedError

    def dup(self) -> AnchoredDir:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> AnchoredDir:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _component(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"not a single path component: {name!r}")
    return name


class _PosixDir(AnchoredDir):
    _DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def __init__(self, fd: int) -> None:
        self._fd = fd

    @classmethod
    def open(cls, path: Path | str) -> _PosixDir:
        return cls(os.open(path, cls._DIRECTORY))

    def open_child(self, name: str) -> _PosixDir:
        return _PosixDir(os.open(_component(name), self._DIRECTORY, dir_fd=self._fd))

    def stat(self, name: str) -> os.stat_result:
        return os.stat(_component(name), dir_fd=self._fd, follow_symlinks=False)

    def fstat(self) -> os.stat_result:
        return os.fstat(self._fd)

    def listdir(self) -> list[str]:
        return os.listdir(self._fd)

    def mkdir(self, name: str) -> None:
        os.mkdir(_component(name), dir_fd=self._fd)

    def open_file(self, name: str, flags: int, mode: int = 0o666) -> int:
        return os.open(_component(name), platform_fs.open_flags(flags), mode=mode, dir_fd=self._fd)

    def replace(self, source: str, destination: str) -> None:
        os.replace(
            _component(source),
            _component(destination),
            src_dir_fd=self._fd,
            dst_dir_fd=self._fd,
        )

    def rename(self, source: str, destination: str) -> None:
        os.rename(
            _component(source),
            _component(destination),
            src_dir_fd=self._fd,
            dst_dir_fd=self._fd,
        )

    def unlink(self, name: str) -> None:
        os.unlink(_component(name), dir_fd=self._fd)

    def rmdir(self, name: str) -> None:
        os.rmdir(_component(name), dir_fd=self._fd)

    def fsync(self) -> None:
        os.fsync(self._fd)

    def dup(self) -> _PosixDir:
        return _PosixDir(os.dup(self._fd))

    def close(self) -> None:
        os.close(self._fd)


def _lstat(path: Path) -> os.stat_result:
    """``lstat`` in which a junction, like a symlink, reports ``S_ISLNK``.

    CPython reports a junction (a mount-point reparse point) as an ordinary directory; the
    attribute bit is what says it redirects."""
    observed = os.lstat(path)
    if getattr(observed, "st_file_attributes", 0) & _REPARSE_POINT and not stat.S_ISLNK(
        observed.st_mode
    ):
        fields = list(observed)
        fields[0] = stat.S_IFLNK | stat.S_IMODE(observed.st_mode)
        return os.stat_result(fields)
    return observed


class _WindowsDir(AnchoredDir):
    def __init__(self, path: Path) -> None:
        self._path = path
        observed = _lstat(path)
        if not stat.S_ISDIR(observed.st_mode):
            raise NotADirectoryError(str(path))
        self._identity = (observed.st_dev, observed.st_ino)

    @classmethod
    def open(cls, path: Path | str) -> _WindowsDir:
        return cls(Path(path))

    def _verify(self) -> None:
        """Prove this directory is still the one that was opened (not renamed, not re-pointed)."""
        observed = self.fstat()
        if (observed.st_dev, observed.st_ino) != self._identity or not stat.S_ISDIR(
            observed.st_mode
        ):
            raise OSError(f"the directory changed while it was in use: {self._path}")

    def _child(self, name: str) -> Path:
        self._verify()
        return self._path / _component(name)

    def open_child(self, name: str) -> _WindowsDir:
        return _WindowsDir(self._child(name))

    def stat(self, name: str) -> os.stat_result:
        return _lstat(self._child(name))

    def fstat(self) -> os.stat_result:
        return _lstat(self._path)

    def listdir(self) -> list[str]:
        self._verify()
        return os.listdir(self._path)

    def mkdir(self, name: str) -> None:
        os.mkdir(self._child(name))

    def open_file(self, name: str, flags: int, mode: int = 0o666) -> int:
        path = self._child(name)
        creating = bool(flags & (os.O_EXCL | os.O_CREAT))
        before = None
        if not creating:
            before = _lstat(path)
            if stat.S_ISLNK(before.st_mode):
                raise OSError(f"refusing to follow a link: {path}")
        descriptor = os.open(path, platform_fs.open_flags(flags), mode)
        try:
            opened = os.fstat(descriptor)
            if getattr(opened, "st_file_attributes", 0) & _REPARSE_POINT or (
                before is not None
                and (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise OSError(f"the file changed while it was opened: {path}")
        except OSError:
            os.close(descriptor)
            raise
        return descriptor

    def replace(self, source: str, destination: str) -> None:
        platform_fs.replace(self._child(source), self._child(destination))

    def rename(self, source: str, destination: str) -> None:
        os.rename(self._child(source), self._child(destination))

    def unlink(self, name: str) -> None:
        os.unlink(self._child(name))

    def rmdir(self, name: str) -> None:
        os.rmdir(self._child(name))

    def fsync(self) -> None:
        """NTFS journals directory entries itself; a directory cannot be opened for fsync."""

    def dup(self) -> _WindowsDir:
        return _WindowsDir(self._path)

    def close(self) -> None:
        """Nothing is held open on Windows."""


def open_dir(path: Path | str) -> AnchoredDir:
    """Open ``path`` as an anchor. Raises ``FileNotFoundError`` / ``NotADirectoryError``; a
    symlink as the final component is refused on POSIX (``O_NOFOLLOW``) and on Windows."""
    if platform_fs.IS_WINDOWS:
        return _WindowsDir.open(path)
    return _PosixDir.open(path)
