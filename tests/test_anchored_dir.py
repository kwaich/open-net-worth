"""core/anchored_dir.py — name-relative directory access that refuses links on both platforms."""

from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest

from backend.core.anchored_dir import open_dir


def _make_directory_link(link, target) -> None:
    """A symlink, or on Windows (where an unprivileged symlink may be refused) a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )


def test_files_round_trip_through_the_anchor(tmp_path):
    (tmp_path / "anchor").mkdir()
    with open_dir(tmp_path / "anchor") as root:
        descriptor = root.open_file("a.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.write(descriptor, b"line one\nline two\n")
        os.close(descriptor)
        root.replace("a.txt", "b.txt")
        assert sorted(root.listdir()) == ["b.txt"]
        descriptor = root.open_file("b.txt", os.O_RDONLY)
        try:
            assert os.read(descriptor, 100) == b"line one\nline two\n"
        finally:
            os.close(descriptor)
        assert stat.S_ISREG(root.stat("b.txt").st_mode)
        root.unlink("b.txt")
        assert root.listdir() == []


def test_children_are_directories_addressed_by_name(tmp_path):
    with open_dir(tmp_path) as root:
        root.mkdir("child")
        with pytest.raises(FileExistsError):
            root.mkdir("child")
        with root.open_child("child") as child:
            assert stat.S_ISDIR(child.fstat().st_mode)
        root.rmdir("child")
        with pytest.raises(FileNotFoundError):
            root.open_child("child")


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "a\x00b"])
def test_a_name_must_be_a_single_component(tmp_path, name):
    with open_dir(tmp_path) as root, pytest.raises(ValueError):
        root.stat(name)


def test_a_linked_directory_is_never_followed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_bytes(b"secret")
    base = tmp_path / "base"
    base.mkdir()
    _make_directory_link(base / "link", target)
    with open_dir(base) as root:
        assert stat.S_ISLNK(root.stat("link").st_mode)
        with pytest.raises(OSError):
            root.open_child("link")


def test_a_linked_file_is_never_followed(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    base = tmp_path / "base"
    base.mkdir()
    try:
        os.symlink(secret, base / "link.txt")
    except OSError:
        pytest.skip("this host refuses to create file symlinks")
    with open_dir(base) as root:
        assert stat.S_ISLNK(root.stat("link.txt").st_mode)
        with pytest.raises(OSError):
            root.open_file("link.txt", os.O_RDONLY)


def test_a_linked_root_is_refused(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    _make_directory_link(link, target)
    with pytest.raises(OSError):
        open_dir(link)
