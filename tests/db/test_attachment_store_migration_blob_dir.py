"""Regression test for the revision-0041 blob-directory move
(``backend.db.alembic.versions.202608161716_attachment_store._move_blob_dir``).

The bug this guards against: on the codebase as it stood when 0041 was authored,
``backend.core.paths`` unconditionally did ``ATTACHMENTS_DIR.mkdir(exist_ok=True)`` at
MODULE-IMPORT time (not inside any function) pointing straight at ``DATA_DIR/"attachments"``
— the exact flat name 0041's own ``_move_blob_dir("uploads", "attachments")`` targets — and
that module sits on the import path of the migration itself (via ``backend.db.engine`` /
``alembic/env.py``). So by the time ``upgrade()`` ran, the destination directory had ALREADY
been created, fresh and empty. A first version of ``_move_blob_dir`` gated the move on
``not new_dir.exists()``, which was therefore always False in practice: the move silently
never fired and every pre-existing attachment's payload file was stranded at the old
``DATA_DIR/uploads/{id}.json`` path forever.

2026-08-21 (``docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md``):
``ATTACHMENTS_DIR`` itself moved to ``FILES_DIR/"attachments"`` — no longer the same
directory 0041's hardcoded ``DATA_DIR/"attachments"`` targets. This test still pins 0041's
OWN merge behavior against that literal flat directory (its target is unchanged; only where
the LATER-migrated store ultimately ends up, via revision 0045, is different) — it just no
longer gets the "already exists" precondition for free from ``paths.py``'s own module-level
mkdir (that now only auto-creates the NESTED destination, which is 0045's job to populate),
so this test manufactures the identical precondition explicitly instead.

Every other test touching ``ATTACHMENTS_DIR`` (e.g. ``test_attachment_store_db.py``,
``test_misc_cutover.py``) isolates it via ``monkeypatch``/``tmp_path``, which is exactly the
setup that hides this bug. This test deliberately runs the real ordering in a fresh
subprocess — set ``DATA_DIR`` env var → import ``backend.core.paths`` → THEN populate
``DATA_DIR/uploads`` with pre-existing payload files AND create ``DATA_DIR/attachments``
empty (simulating the precondition) → THEN call ``_move_blob_dir`` — so the destination
directory provably already exists before the move runs, just like it did on a real install
back when 0041 alone owned the ``ATTACHMENTS_DIR`` constant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "db"
    / "alembic"
    / "versions"
    / "202608161716_attachment_store.py"
)

# Executed in a fresh subprocess (via `python3 -c`) so DATA_DIR is set BEFORE
# `backend.core.paths` is ever imported in that process — the only way to genuinely
# reproduce "the destination dir already exists when the migration runs" instead of
# monkeypatching the constant after the fact, which would hide the exact bug being tested.
_SCRIPT = """
import importlib.util
import json
import os
import sys

data_dir = sys.argv[1]
os.environ["DATA_DIR"] = data_dir
os.environ["CACHE_DIR"] = data_dir + "_cache"

import backend.core.paths as paths  # noqa: E402

# 0041's own target is this literal FLAT name -- unchanged since 0041 was authored, even
# though paths.ATTACHMENTS_DIR itself has since moved (2026-08-21) to FILES_DIR/"attachments"
# and is no longer the same directory. Manufacture the "already exists, freshly created,
# empty" precondition explicitly -- paths.py's own module-level mkdir no longer does this
# for the flat name (only for the nested one it now points ATTACHMENTS_DIR at).
new_dir = paths.DATA_DIR / "attachments"
new_dir.mkdir(parents=True)
assert not any(new_dir.iterdir()), "sanity: should start empty"

# NOW populate the OLD uploads/ dir with pre-existing payload files -- simulating an
# install that has real attachments from before this migration ever ran.
old_dir = paths.DATA_DIR / "uploads"
old_dir.mkdir()
(old_dir / "att_aaa111111111.json").write_text('{"kind": "text", "text": "hello"}')
(old_dir / "att_bbb222222222.json").write_text('{"kind": "image", "content_b64": "Zm9v"}')

spec = importlib.util.spec_from_file_location("_migration_0041_under_test", sys.argv[2])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mod._move_blob_dir("uploads", "attachments")

result = {
    "old_dir_exists": old_dir.exists(),
    "new_dir_contents": sorted(p.name for p in new_dir.iterdir()),
    "aaa_text": (new_dir / "att_aaa111111111.json").read_text(),
    "bbb_text": (new_dir / "att_bbb222222222.json").read_text(),
}
print(json.dumps(result))
"""


def _run_migration_against_populated_data_dir(tmp_path: Path) -> dict:
    data_dir = tmp_path / "onw_data"
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(data_dir), str(_MIGRATION_FILE)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"subprocess failed (rc={proc.returncode})\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_move_blob_dir_relocates_files_even_though_destination_already_exists(tmp_path):
    """The real-boot ordering 0041 itself was written against: its flat destination
    ``DATA_DIR/"attachments"`` already exists, empty, before ``_move_blob_dir`` runs. Every
    pre-existing uploads/ file must still end up there with byte-identical content, and the
    old directory must be left empty/removed -- not silently skipped because the
    destination "already exists"."""
    result = _run_migration_against_populated_data_dir(tmp_path)

    assert result["old_dir_exists"] is False, "old uploads/ dir should be removed once emptied"
    assert result["new_dir_contents"] == ["att_aaa111111111.json", "att_bbb222222222.json"]
    assert result["aaa_text"] == '{"kind": "text", "text": "hello"}'
    assert result["bbb_text"] == '{"kind": "image", "content_b64": "Zm9v"}'


def test_move_blob_dir_never_clobbers_a_file_already_at_the_destination(tmp_path):
    """If a name collides at the destination (shouldn't happen in practice, but the fix must
    not assume it can't), the existing destination file wins -- never silently overwritten."""
    data_dir = tmp_path / "onw_data"
    repo_root = Path(__file__).resolve().parents[2]
    script = _SCRIPT.replace(
        'mod._move_blob_dir("uploads", "attachments")',
        (
            '(new_dir / "att_aaa111111111.json")'
            '.write_text(\'{"already": "there"}\')\n'
            'mod._move_blob_dir("uploads", "attachments")'
        ),
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(data_dir), str(_MIGRATION_FILE)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    # The colliding file at the destination is left untouched (never clobbered)...
    assert result["aaa_text"] == '{"already": "there"}'
    # ...but the non-colliding file still gets relocated.
    assert result["bbb_text"] == '{"kind": "image", "content_b64": "Zm9v"}'
    # The old dir is NOT removed -- it still holds the un-relocated colliding source file,
    # so silently deleting it here would lose data.
    assert result["old_dir_exists"] is True


def test_move_blob_dir_is_a_noop_when_the_old_directory_never_existed(tmp_path):
    """A fresh install / a store that never wrote to the old path must not fail -- the
    fail-closed boot gate would refuse the whole boot over a directory that simply never
    existed."""
    data_dir = tmp_path / "onw_data"
    repo_root = Path(__file__).resolve().parents[2]
    script = _SCRIPT.replace(
        (
            'old_dir = paths.DATA_DIR / "uploads"\n'
            "old_dir.mkdir()\n"
            '(old_dir / "att_aaa111111111.json").write_text(\'{"kind": "text", '
            '"text": "hello"}\')\n'
            '(old_dir / "att_bbb222222222.json").write_text(\'{"kind": "image", '
            '"content_b64": "Zm9v"}\')'
        ),
        'old_dir = paths.DATA_DIR / "uploads"',
    )
    script = script.replace(
        '"aaa_text": (new_dir / "att_aaa111111111.json").read_text(),\n'
        '    "bbb_text": (new_dir / "att_bbb222222222.json").read_text(),\n',
        "",
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(data_dir), str(_MIGRATION_FILE)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["old_dir_exists"] is False
    assert result["new_dir_contents"] == []
