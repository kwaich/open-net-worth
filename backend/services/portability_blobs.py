"""The file half of a portable bundle: skills, attachments, receipts, statement source files and
generated documents (``core.portable.portable_trees``), carried as ``blobs/<tree>/<relative path>``.

Everything here streams — a file is copied in fixed chunks between the zip and the disk and is
never held whole in memory, so a book with gigabytes of documents exports and imports on a small
machine. Import is two-phase: ``stage`` copies and verifies every file into a directory beside
its live tree (nothing live is touched, so a corrupt bundle changes nothing), and ``commit``
swaps the staged files in once the stores have been restored.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from backend.core import platform_fs, portable
from backend.core.errors import ValidationError
from backend.core.io import fire_write_hook

logger = logging.getLogger(__name__)

BLOBS_PREFIX = "blobs/"
_CHUNK = 1024 * 1024
_DISK_MARGIN = 256 * 1024 * 1024
# A statement's parsed-rows sidecar is inlined into statements.json on export and re-written by
# the money import, so it is neither exported as a file nor deleted when the tree is replaced.
_SIDECAR_SUFFIX = ".parsed.json"
_SKIPPED_DIRS = {"__pycache__"}


def is_blob_name(name: str) -> bool:
    return name.startswith(BLOBS_PREFIX)


def parse_blob_name(name: str, trees: dict[str, Path]) -> tuple[str, Path] | None:
    """``(tree, relative path)`` for a safe ``blobs/<known tree>/<path>`` entry, else None."""
    parts = name[len(BLOBS_PREFIX) :].split("/")
    if len(parts) < 2 or parts[0] not in trees:
        return None
    rel = parts[1:]
    if any(p in ("", ".", "..") or "\\" in p or "\0" in p or ":" in p for p in rel):
        return None
    return parts[0], Path(*rel)


def _is_sidecar(tree: str, rel: Path) -> bool:
    return tree == "statements" and len(rel.parts) == 1 and rel.name.endswith(_SIDECAR_SUFFIX)


def _walk_files(root: Path):
    """Every regular file under ``root`` (symlinks and special files are never exported)."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIPPED_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_file() and not path.is_symlink():
                yield path


def write_blobs(zf: zipfile.ZipFile) -> tuple[dict[str, dict], list[str]]:
    """Stream every portable file into ``zf``. Returns the manifest entries and tree names."""
    entries: dict[str, dict] = {}
    trees = portable.portable_trees()
    for tree, root in trees.items():
        for path in _walk_files(root):
            rel = path.relative_to(root)
            if _is_sidecar(tree, rel):
                continue
            arcname = f"{BLOBS_PREFIX}{tree}/{rel.as_posix()}"
            digest = hashlib.sha256()
            size = 0
            try:
                with path.open("rb") as src, zf.open(arcname, "w", force_zip64=True) as dest:
                    while chunk := src.read(_CHUNK):
                        digest.update(chunk)
                        size += len(chunk)
                        dest.write(chunk)
            except FileNotFoundError:
                continue
            entries[arcname] = {"kind": "blob", "sha256": digest.hexdigest(), "bytes": size}
    return entries, sorted(trees)


def sha256_of(zf: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with zf.open(name) as src:
        while chunk := src.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Staged:
    """Verified files waiting beside their live trees: ``(tree, staging dir)`` per tree."""

    dirs: dict[str, Path] = field(default_factory=dict)

    def discard(self) -> None:
        for directory in self.dirs.values():
            shutil.rmtree(directory, ignore_errors=True)
        self.dirs.clear()


def stage(zf: zipfile.ZipFile, manifest: dict) -> Staged:
    """Copy every blob into a staging directory next to its live tree, verifying size and
    sha256 as it goes. Raises ``ValidationError`` (after discarding) on any mismatch; touches
    nothing live. A tree is staged when the manifest lists it or the bundle has a file for it."""
    trees = portable.portable_trees()
    infos = [zi for zi in zf.infolist() if is_blob_name(zi.filename) and not zi.is_dir()]
    wanted = {t for t in manifest.get("blob_trees") or [] if t in trees}
    for zi in infos:
        parsed = parse_blob_name(zi.filename, trees)
        if parsed:
            wanted.add(parsed[0])
    if not wanted:
        return Staged()
    _require_disk(infos, trees, wanted)
    staged = Staged()
    try:
        for tree in sorted(wanted):
            live = trees[tree]
            live.parent.mkdir(parents=True, exist_ok=True)
            staged.dirs[tree] = Path(
                tempfile.mkdtemp(prefix=f".{live.name}.import-", dir=live.parent)
            )
        entries = manifest.get("entries") or {}
        for zi in infos:
            parsed = parse_blob_name(zi.filename, trees)
            if parsed:
                _copy_verified(
                    zf, zi, staged.dirs[parsed[0]] / parsed[1], (entries.get(zi.filename) or {})
                )
    except BaseException:
        staged.discard()
        raise
    return staged


def _require_disk(infos: list[zipfile.ZipInfo], trees: dict[str, Path], wanted: set[str]) -> None:
    need = sum(zi.file_size for zi in infos)
    probe = trees[sorted(wanted)[0]].parent
    probe.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(probe).free
    if need + _DISK_MARGIN > free:
        raise ValidationError(
            f"not enough free disk space to restore the bundle's files: needs {need} bytes "
            f"plus a safety margin, {free} free"
        )


def _copy_verified(zf: zipfile.ZipFile, zi: zipfile.ZipInfo, dest: Path, entry: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with zf.open(zi) as src, dest.open("wb") as out:
        while chunk := src.read(_CHUNK):
            size += len(chunk)
            if size > zi.file_size:
                raise ValidationError(f"{zi.filename}: larger than its declared size")
            digest.update(chunk)
            out.write(chunk)
    os.chmod(dest, 0o600)
    expected = entry.get("sha256")
    if expected is not None and digest.hexdigest() != expected:
        raise ValidationError(f"{zi.filename}: sha256 mismatch (tampered or corrupt)")


def commit(staged: Staged) -> int:
    """Make each staged tree the live one by renaming it into place, so a tree is never seen
    half-replaced and nothing is ever written THROUGH a link that was sitting in the live tree
    (the old tree is renamed away whole and deleted without following links; a live directory
    that is itself a link is replaced, not entered). A statement's parsed-rows sidecars are moved
    across. Returns the file count restored."""
    trees = portable.portable_trees()
    restored = 0
    for tree, staging in staged.dirs.items():
        live = trees[tree]
        restored += sum(1 for _ in _walk_files(staging))
        _swap_in(tree, live, staging)
    staged.dirs.clear()
    return restored


def _swap_in(tree: str, live: Path, staging: Path) -> None:
    retired: Path | None = None
    if live.is_symlink():
        live.unlink()
    elif live.exists():
        for path in _walk_files(live):
            fire_write_hook(path)
        retired = live.with_name(f".{live.name}.retired-{os.getpid()}")
        shutil.rmtree(retired, ignore_errors=True)
        platform_fs.replace(live, retired)
    platform_fs.replace(staging, live)
    if retired is None:
        return
    for sidecar in _walk_files(retired):
        rel = sidecar.relative_to(retired)
        if _is_sidecar(tree, rel):
            platform_fs.replace(sidecar, live / rel)
    shutil.rmtree(retired, ignore_errors=True)
