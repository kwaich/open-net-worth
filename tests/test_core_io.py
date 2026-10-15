"""Unit tests for backend.core.io (RFC P1-A1 §3.7).

Covers the atomic-write round-trip + overwrite, and the write-hook registry's
fire/clear/swallow semantics.
"""

from __future__ import annotations

import os
import stat
import threading

import pytest

from backend.core import io


@pytest.fixture(autouse=True)
def _reset_hook():
    """Each test starts and ends with no registered hook (module-global)."""
    io.register_write_hook(None)
    yield
    io.register_write_hook(None)


def test_atomic_write_round_trip(tmp_path):
    p = tmp_path / "x.json"
    io.atomic_write_text(p, '{"a": 1}')
    assert p.read_text(encoding="utf-8") == '{"a": 1}'


def test_atomic_write_overwrites(tmp_path):
    p = tmp_path / "x.txt"
    io.atomic_write_text(p, "first")
    io.atomic_write_text(p, "second")
    assert p.read_text(encoding="utf-8") == "second"


def test_atomic_write_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "deep" / "x.txt"
    io.atomic_write_text(p, "ok")
    assert p.read_text(encoding="utf-8") == "ok"


def test_atomic_write_leaves_no_tmp_files(tmp_path):
    p = tmp_path / "x.txt"
    io.atomic_write_text(p, "ok")
    leftovers = [f.name for f in tmp_path.iterdir() if ".tmp-" in f.name]
    assert leftovers == []


def test_fire_write_hook_invokes_registered(tmp_path):
    seen = []
    io.register_write_hook(lambda path: seen.append(path))
    target = tmp_path / "y.txt"
    io.fire_write_hook(target)
    assert seen == [target]


def test_fire_write_hook_noop_when_unregistered(tmp_path):
    # No hook registered (autouse fixture cleared it) → must not raise.
    io.fire_write_hook(tmp_path / "z.txt")


def test_fire_write_hook_swallows_exceptions(tmp_path):
    def boom(_path):
        raise RuntimeError("hook blew up")

    io.register_write_hook(boom)
    # Must not propagate — a failing hook can never block a write.
    io.fire_write_hook(tmp_path / "z.txt")


def test_register_none_clears_hook(tmp_path):
    seen = []
    io.register_write_hook(lambda path: seen.append(path))
    io.register_write_hook(None)
    io.fire_write_hook(tmp_path / "z.txt")
    assert seen == []


def test_exclusive_maintenance_lock_blocks_an_ordinary_atomic_writer(tmp_path):
    target = tmp_path / "data.json"
    started = threading.Event()
    finished = threading.Event()

    def writer() -> None:
        started.set()
        io.atomic_write_text(target, "[]")
        finished.set()

    with io.data_maintenance_lock(tmp_path, exclusive=True):
        thread = threading.Thread(target=writer)
        thread.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.1)

    thread.join(timeout=1)
    assert finished.is_set()
    assert target.read_text(encoding="utf-8") == "[]"


def test_atomic_write_inside_exclusive_context_does_not_downgrade_lock(tmp_path):
    contender_finished = threading.Event()

    with io.data_maintenance_lock(tmp_path, exclusive=True):
        io.atomic_write_text(tmp_path / "first.json", "[]")

        thread = threading.Thread(
            target=lambda: (
                io.atomic_write_text(tmp_path / "second.json", "[]"),
                contender_finished.set(),
            )
        )
        thread.start()
        assert not contender_finished.wait(timeout=0.1)

    thread.join(timeout=1)
    assert contender_finished.is_set()


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode bits")
def test_atomic_write_can_force_private_file_mode(tmp_path):
    target = tmp_path / "secret.json"

    io.atomic_write_text(target, "{}", mode=0o600)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_durable_replace_fsyncs_both_affected_parent_directories(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    source = source_dir / "value.json"
    destination = destination_dir / "value.json"
    source.write_text("{}", encoding="utf-8")
    fsynced = []
    monkeypatch.setattr(io, "fsync_directory", lambda path: fsynced.append(path))

    io.durable_replace(source, destination)

    assert destination.read_text(encoding="utf-8") == "{}"
    assert fsynced == [destination_dir, source_dir]


def test_atomic_write_propagates_parent_directory_fsync_failure(tmp_path, monkeypatch):
    target = tmp_path / "value.json"

    def fail_fsync(_path):
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(io, "fsync_directory", fail_fsync)

    with pytest.raises(OSError, match="directory fsync"):
        io.atomic_write_text(target, "{}")

    assert not list(tmp_path.glob("*.tmp-*"))
