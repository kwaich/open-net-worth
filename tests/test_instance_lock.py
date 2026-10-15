"""One instance per working folder — but many workers of one instance.

Two processes on one folder does not corrupt SQLite (WAL serialises writes across processes);
everything above it diverges instead — two turn registries, two caches, interleaved write-log
checkpoints, and a ``/rewind`` in one reverting rows the other just wrote. The failure is an
app that behaves oddly for no visible reason, which is the worst kind to own a money ledger.

The subtlety these pin: this app legitimately runs SEVERAL uvicorn workers over one folder
(``main.py``'s scheduler lock exists because they do), so the lock identifies the INSTANCE by
the port it serves, not the process.
"""

from __future__ import annotations

import json

import pytest

from backend.core import instance_lock
from backend.core.instance_lock import LOCK_NAME, InstanceAlreadyRunning


@pytest.fixture(autouse=True)
def _release_between_tests():
    yield
    instance_lock.release()


def test_the_first_instance_claims_the_folder(tmp_path):
    assert instance_lock.acquire(tmp_path, 8080) is True
    recorded = json.loads((tmp_path / LOCK_NAME).read_text())
    assert recorded["port"] == 8080
    assert recorded["pid"] > 0


def test_releasing_lets_the_next_instance_in(tmp_path):
    assert instance_lock.acquire(tmp_path, 8080) is True
    instance_lock.release()
    assert instance_lock.acquire(tmp_path, 8081) is True


def test_the_lock_file_is_created_with_the_folder(tmp_path):
    """A never-started instance has no folder yet; claiming it must not require one."""
    folder = tmp_path / "not-there-yet"
    assert instance_lock.acquire(folder, 8080) is True
    assert (folder / LOCK_NAME).exists()


def test_a_second_instance_on_a_different_port_is_refused(tmp_path):
    """The real hazard: your live app on 8080 and a test instance pointed at the same folder."""
    import subprocess
    import sys

    # A real second PROCESS, because flock is per-process — one process re-locking its own
    # file succeeds and would prove nothing.
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import json,time;"
            f"h=open({str(tmp_path / LOCK_NAME)!r},'a+');"
            f"lock_exclusive(h);"
            f"h.seek(0);h.truncate();h.write(json.dumps({{'pid':1,'port':8080}}));h.flush();"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(InstanceAlreadyRunning) as excinfo:
            instance_lock.acquire(tmp_path, 8095)

        assert "already in use" in str(excinfo.value)
        assert "port 8080" in str(excinfo.value)
        assert "ONW_ROOT" in str(excinfo.value)
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_a_sibling_worker_on_the_same_port_is_allowed(tmp_path):
    """Several uvicorn workers share one folder by design — refusing them would break a
    legitimate deployment, which is why the lock keys off the PORT and not the process."""
    import subprocess
    import sys

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import json,time;"
            f"h=open({str(tmp_path / LOCK_NAME)!r},'a+');"
            f"lock_exclusive(h);"
            f"h.seek(0);h.truncate();h.write(json.dumps({{'pid':1,'port':8080}}));h.flush();"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        # Same port -> a sibling worker. False means "someone else owns it", not a refusal.
        assert instance_lock.acquire(tmp_path, 8080) is False
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_held_by_other_is_false_when_nothing_has_ever_claimed_the_folder(tmp_path):
    assert instance_lock.held_by_other(tmp_path) is False


def test_held_by_other_is_false_when_this_process_owns_the_lock(tmp_path):
    assert instance_lock.acquire(tmp_path, 8080) is True
    assert instance_lock.held_by_other(tmp_path) is False


def test_held_by_other_is_true_when_a_different_process_holds_it(tmp_path):
    """The migration guard's whole reason to exist: a real OTHER process (not this one)
    has the folder open."""
    import subprocess
    import sys

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import json,time;"
            f"h=open({str(tmp_path / LOCK_NAME)!r},'a+');"
            f"lock_exclusive(h);"
            f"h.seek(0);h.truncate();h.write(json.dumps({{'pid':1,'port':8080}}));h.flush();"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        assert instance_lock.held_by_other(tmp_path) is True
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_held_by_other_rejects_the_same_port_sibling_excuse(tmp_path):
    """Unlike ``acquire()``, ``held_by_other`` must NOT let a same-port "sibling worker"
    through — a migration is exactly the operation two workers of one instance must not
    race on, so the caller (``ensure_migrated``) needs to know for certain nobody else has
    the file open, sibling or not."""
    import subprocess
    import sys

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import json,time;"
            f"h=open({str(tmp_path / LOCK_NAME)!r},'a+');"
            f"lock_exclusive(h);"
            f"h.seek(0);h.truncate();h.write(json.dumps({{'pid':1,'port':8080}}));h.flush();"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        # Same port this probe "would" use if it were `acquire` -- held_by_other takes no
        # port argument at all, so there is no port to match; it must still say True.
        assert instance_lock.held_by_other(tmp_path) is True
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_held_by_other_releases_its_own_probe_lock(tmp_path):
    """A probe that succeeded (nobody else holds it) must close its handle so it doesn't
    itself become the "other" holder for the NEXT probe or a real ``acquire()``."""
    assert instance_lock.held_by_other(tmp_path) is False
    assert instance_lock.held_by_other(tmp_path) is False
    assert instance_lock.acquire(tmp_path, 8080) is True


def test_an_unreadable_lock_file_still_refuses_rather_than_guessing(tmp_path):
    """A truncated or hand-edited lock file must not read as "free". Unknown holder, unknown
    port — the safe answer is to refuse, not to assume it is a sibling."""
    import subprocess
    import sys

    (tmp_path / LOCK_NAME).write_text("not json at all")
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from backend.core.file_lock import lock_exclusive;import time;"
            f"h=open({str(tmp_path / LOCK_NAME)!r},'a+');"
            f"lock_exclusive(h);"
            f"print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(InstanceAlreadyRunning):
            instance_lock.acquire(tmp_path, 8080)
    finally:
        holder.kill()
        holder.wait(timeout=10)
