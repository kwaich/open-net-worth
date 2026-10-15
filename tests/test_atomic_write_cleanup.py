import os

import pytest


def test_atomic_write_removes_its_temp_on_failure(tmp_path, monkeypatch):
    from backend.core import io as core_io

    target = tmp_path / "x.json"
    real_replace = os.replace

    def exploding_replace(src, dst):
        raise OSError("simulated disk full at replace")

    monkeypatch.setattr(os, "replace", exploding_replace)
    with pytest.raises(OSError):
        core_io.atomic_write_text(target, "{}")
    monkeypatch.setattr(os, "replace", real_replace)
    assert list(tmp_path.glob("*.tmp-*")) == [], "orphaned temp file left behind"


def test_boot_sweep_removes_stale_tmp_siblings(tmp_path):
    from backend.core.io import sweep_stale_tmp_files

    (tmp_path / "a.json").write_text("[]")
    stale = tmp_path / "a.json.tmp-1234-deadbeef"
    stale.write_text("{")
    old = tmp_path / "b.json.tmp-9-aabbccdd"
    old.write_text("")
    os_time = 10_000
    os.utime(stale, (os_time, os_time))
    os.utime(old, (os_time, os_time))
    removed = sweep_stale_tmp_files(tmp_path, older_than_seconds=3600)
    assert removed == 2
    assert not stale.exists() and not old.exists()
    assert (tmp_path / "a.json").exists()


def test_boot_sweep_ignores_real_files_that_merely_contain_tmp_dash(tmp_path):
    """A real data file whose name merely contains `.tmp-` must survive the sweep —
    only the exact `.tmp-<pid>-<8hex>` suffix `_atomic_write_bytes` produces is
    eligible, never a `.tmp-` substring match."""
    from backend.core.io import sweep_stale_tmp_files

    decoy_short_hex = tmp_path / "decoy.tmp-1.json"
    decoy_short_hex.write_text("{}")
    decoy_no_digits = tmp_path / "report.tmp-notes.txt"
    decoy_no_digits.write_text("notes")
    genuine = tmp_path / "data.json.tmp-999-deadbeef"
    genuine.write_text("{")

    os_time = 10_000
    os.utime(decoy_short_hex, (os_time, os_time))
    os.utime(decoy_no_digits, (os_time, os_time))
    os.utime(genuine, (os_time, os_time))

    removed = sweep_stale_tmp_files(tmp_path, older_than_seconds=3600)

    assert removed == 1
    assert decoy_short_hex.exists()
    assert decoy_no_digits.exists()
    assert not genuine.exists()
