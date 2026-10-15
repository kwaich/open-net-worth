"""Extension install/uninstall service (P4-EXT phase-1).

``install_extension(zip_bytes)`` is the single entry point for installing an
extension from a zip archive (the portability-format bundle). It:

  1. Zip-bomb + zip-slip defence — reuses ``portability.inspect_bundle`` caps +
     ``_is_safe_ext_name`` allowlist + ``skill_bundles._resolve_in_tree`` jail.
  2. Manifest validation — reads + validates ``manifest.json`` via
     ``extensions.manifest.validate_extension_manifest``.
  3. SHA verification — ``bundle_sha`` field vs computed content-address.
  4. TYPE GATE — ``compute_plugin`` and ``ui_contribution`` are REFUSED with
     "not yet supported" error; only ``data_pack`` (and ``skill`` / ``agent``
     for future riding-for-free) proceed to disk.
  5. Permission review payload — returns the review summary BEFORE writing, so
     the router can return it to the operator for confirmation.
  6. Atomic write — unpacks to a staging dir, validates, then ``os.replace``
     moves the whole tree into ``USER_PACKS_DIR/<type>/<id>/<version>/`` (the
     immutable version-dir discipline from skill_bundles).
  7. ``verified=False`` is always set on user packs (RFC §3.5).

``uninstall_extension(ext_type, ext_id, version)`` removes one version dir (or
the whole pack id dir when ``version`` is omitted).

``inspect_extension(zip_bytes)`` is a dry-run that returns the permission review
payload without writing anything — used by the router before the operator
confirms installation.

SSRF is guarded by callers (``api/extensions.py`` calls
``services.import_common.fetch_url_bytes`` which uses ``core.net_guard``); this
module accepts already-fetched bytes.

SECURITY-CRITICAL — /security-review required before merge.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from backend.core.io import atomic_write_text
from backend.core.paths import USER_PACKS_DIR
from backend.models.extension import ExtensionManifest
from backend.services.extensions.manifest import _SLUG_RE, validate_extension_manifest
from backend.services.extensions.registry import _SHA_SIDECAR, _compute_pack_sha

logger = logging.getLogger(__name__)

# ── Zip-bomb caps (mirroring portability.py — same threat model) ──────────────
_MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB across all entries
_MAX_ENTRY_UNCOMPRESSED = 50 * 1024 * 1024  # 50 MB for any single entry
_MAX_ENTRY_COUNT = 500  # total entry count

# Chunk size for streaming zip extraction (Fix 4: cap actual decompressed bytes).
_EXTRACT_CHUNK_SIZE = 64 * 1024  # 64 KB per chunk

# Extension types refused at install time in phase-1.
# data_pack is the ONLY supported type; skill + agent are wired through the
# existing choke points (apply_agent_change / skill_store.create) — we refuse
# them here too for now (future work §2.3).
_UNSUPPORTED_INSTALL_TYPES = frozenset({"compute_plugin", "ui_contribution", "skill", "agent"})

# Files allowed inside an extension zip. We accept:
#   manifest.json  — required
#   data/**        — data pack payloads
#   scripts/**     — compute plugin scripts (refused to run in phase-1, but valid to store)
#   ui/**          — ui schema files
#   resources/**   — static resources
# Anything else is rejected (zip-slip / unknown file guard).
_ALLOWED_PREFIXES = ("manifest.json", "data/", "scripts/", "ui/", "resources/")


def _is_safe_ext_name(name: str) -> bool:
    """A zip entry name is safe iff it matches one of the allowed prefixes and
    contains no path traversal components."""
    if ".." in name or name.startswith("/"):
        return False
    for prefix in _ALLOWED_PREFIXES:
        if name == prefix.rstrip("/") or name.startswith(prefix):
            return True
    return False


# ── Public surface ────────────────────────────────────────────────────────────


def inspect_extension(zip_bytes: bytes) -> dict:
    """Dry-run validate a zip bundle and return the permission review payload.

    Returns a dict suitable for the API response:
    ``{ok, manifest_summary, permission_review, errors, warnings}``

    Does NOT write anything to disk — use ``install_extension`` to commit.
    """
    _sweep_stale_staging()
    result = _validate_zip(zip_bytes)
    if not result["ok"]:
        return result

    manifest: ExtensionManifest = result["_manifest"]
    pack_dir: Path = result["_staging"]  # staging dir for path-in-tree checks

    problems = validate_extension_manifest(manifest.model_dump(), pack_dir)
    if problems:
        _cleanup(result.get("_staging"))
        return {
            "ok": False,
            "manifest_summary": None,
            "permission_review": None,
            "errors": problems,
            "warnings": [],
        }

    type_error = _check_supported_type(manifest.type)
    _cleanup(result.get("_staging"))

    return {
        "ok": type_error is None,
        "manifest_summary": {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "type": manifest.type,
            "author": manifest.author,
            "description": manifest.description,
        },
        "permission_review": manifest.permission_summary(),
        "errors": [type_error] if type_error else [],
        "warnings": result.get("warnings", []),
    }


def install_extension(zip_bytes: bytes) -> dict:
    """Validate + install an extension from a zip bundle.

    Returns a dict:
    ``{ok, installed, id, name, version, type, pack_dir, permission_review, errors, warnings}``

    The caller (router) MUST:
      1. Call ``inspect_extension`` first and show ``permission_review`` to the operator.
      2. Only call ``install_extension`` after explicit operator confirmation.

    Raises ``ValidationError`` for structural failures; returns ``ok=False`` for
    content/type failures without raising (same as portability.inspect_bundle posture).
    """
    _sweep_stale_staging()
    result = _validate_zip(zip_bytes)
    if not result["ok"]:
        _cleanup(result.get("_staging"))
        return {**result, "installed": False, "pack_dir": None}

    manifest: ExtensionManifest = result["_manifest"]
    staging: Path = result["_staging"]

    # Full manifest validation.
    problems = validate_extension_manifest(manifest.model_dump(), staging)
    if problems:
        _cleanup(staging)
        return {
            "ok": False,
            "installed": False,
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "type": manifest.type,
            "pack_dir": None,
            "permission_review": manifest.permission_summary(),
            "errors": problems,
            "warnings": result.get("warnings", []),
        }

    # Type gate: refuse unsupported types.
    type_error = _check_supported_type(manifest.type)
    if type_error:
        _cleanup(staging)
        return {
            "ok": False,
            "installed": False,
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "type": manifest.type,
            "pack_dir": None,
            "permission_review": manifest.permission_summary(),
            "errors": [type_error],
            "warnings": result.get("warnings", []),
        }

    # Strip self-asserted verified=True before writing (RFC §3.5).
    if manifest.verified:
        logger.info(
            "install_extension: stripping self-asserted verified=True from %s/%s",
            manifest.id,
            manifest.version,
        )
        # Patch the on-disk manifest.json to remove the self-assertion.
        mf_path = staging / "manifest.json"
        raw = json.loads(mf_path.read_text(encoding="utf-8"))
        raw["verified"] = False
        atomic_write_text(mf_path, json.dumps(raw, indent=2, ensure_ascii=False))
        manifest = manifest.model_copy(update={"verified": False})

    # Compute sha OVER THE FINAL STAGING CONTENT (excluding the sidecar itself —
    # see registry._compute_pack_sha) and write it to ``bundle.sha``. The sidecar
    # is NOT included in the hash computation (no circular reference).
    computed_sha = _compute_pack_sha(staging)
    atomic_write_text(staging / _SHA_SIDECAR, computed_sha)

    # Atomic promotion into USER_PACKS_DIR/<type>/<id>/<version>/.
    #
    # FIX 1b — realpath jail: belt-and-suspenders check that the computed dest
    # truly lives inside USER_PACKS_DIR.  IMPORTANT: the jail root MUST be the
    # FIXED realpath(USER_PACKS_DIR), NOT a path derived from manifest.id or
    # manifest.type.  If an attacker pre-plants a symlink at
    # USER_PACKS_DIR/<type>/<id> → /evil, then realpath(jail_root) would also
    # resolve to /evil, causing both dest and jail_root to share the same prefix
    # and letting the proper-descendant check pass — a symlink jail escape.
    # Jailing against the fixed USER_PACKS_DIR root closes this.
    dest = USER_PACKS_DIR / manifest.type / manifest.id / manifest.version
    try:
        _realpath_jail(dest, USER_PACKS_DIR)
    except ValueError as exc:
        _cleanup(staging)
        logger.error("install_extension: jail check failed for %r: %s", str(dest), exc)
        return {
            "ok": False,
            "installed": False,
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "type": manifest.type,
            "pack_dir": None,
            "permission_review": manifest.permission_summary(),
            "errors": ["path traversal detected — install refused"],
            "warnings": result.get("warnings", []),
        }

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Version already installed — refuse (versions are immutable).
        _cleanup(staging)
        return {
            "ok": False,
            "installed": False,
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "type": manifest.type,
            "pack_dir": str(dest),
            "permission_review": manifest.permission_summary(),
            "errors": [
                f"version {manifest.version!r} of {manifest.id!r} is already installed "
                "(versions are immutable)"
            ],
            "warnings": result.get("warnings", []),
        }

    # FIX 5 — EXDEV: staging is in a temp dir which may be on a different
    # filesystem from USER_PACKS_DIR (e.g. /tmp vs a mounted data volume).
    # os.replace() across filesystems raises EXDEV. Fallback to shutil.move
    # which copies + removes when the rename fails.
    try:
        os.replace(staging, dest)
    except OSError as exc:
        import errno

        if exc.errno == errno.EXDEV:
            shutil.move(str(staging), str(dest))
        else:
            _cleanup(staging)
            raise
    logger.info(
        "install_extension: installed %s/%s (%s) → %s",
        manifest.id,
        manifest.version,
        manifest.type,
        dest,
    )

    return {
        "ok": True,
        "installed": True,
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "type": manifest.type,
        "pack_dir": str(dest),
        "permission_review": manifest.permission_summary(),
        "errors": [],
        "warnings": result.get("warnings", []),
    }


def uninstall_extension(ext_type: str, ext_id: str, version: Optional[str] = None) -> dict:
    """Remove a user-installed extension.

    If ``version`` is given, removes only that version directory.
    If ``version`` is None, removes the entire pack id directory (all versions).

    Returns ``{ok, removed_path, errors}``.

    FIX 3 — path traversal: validate each URL-path-param against the slug
    regex and realpath-jail the resolved target inside USER_PACKS_DIR before
    calling rmtree.
    """
    from backend.models.extension import EXTENSION_TYPES

    # Validate type is a known extension type (closed-world).
    if ext_type not in EXTENSION_TYPES:
        return {
            "ok": False,
            "removed_path": None,
            "errors": [f"unknown extension type {ext_type!r} (valid: {sorted(EXTENSION_TYPES)})"],
        }

    # Validate id and version against the slug regex — same discipline as manifest.
    if not _SLUG_RE.match(ext_id):
        return {
            "ok": False,
            "removed_path": None,
            "errors": [f"extension id {ext_id!r} contains unsafe characters"],
        }
    if version is not None and not _SLUG_RE.match(version):
        return {
            "ok": False,
            "removed_path": None,
            "errors": [f"extension version {version!r} contains unsafe characters"],
        }

    target = USER_PACKS_DIR / ext_type / ext_id
    if version:
        target = target / version

    # Realpath jail: refuse if target escapes USER_PACKS_DIR or is USER_PACKS_DIR itself.
    real_jail = Path(os.path.realpath(USER_PACKS_DIR))
    real_target = Path(os.path.realpath(target))
    if real_target == real_jail or real_jail not in real_target.parents:
        logger.error("uninstall_extension: path %r escapes USER_PACKS_DIR — refused", str(target))
        return {
            "ok": False,
            "removed_path": None,
            "errors": ["path traversal detected — uninstall refused"],
        }

    if not target.exists():
        return {
            "ok": False,
            "removed_path": None,
            "errors": [
                f"extension {ext_type}/{ext_id}"
                + (f"/{version}" if version else "")
                + " is not installed"
            ],
        }

    removed = str(target)
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"ok": False, "removed_path": None, "errors": [str(exc)]}

    logger.info("uninstall_extension: removed %s", removed)
    return {"ok": True, "removed_path": removed, "errors": []}


# ── Internal helpers ──────────────────────────────────────────────────────────


def _validate_zip(zip_bytes: bytes) -> dict:  # noqa: C901
    """Open and validate a zip bundle; unpack to a temp staging dir.

    Returns a dict with keys:
      ``ok``, ``_manifest``, ``_staging``, ``warnings``, ``errors``

    The ``_staging`` dir is the caller's responsibility to clean up on failure.
    """
    warnings: list[str] = []
    errors: list[str] = []

    # ── Parse zip ──────────────────────────────────────────────────────────────
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return {"ok": False, "errors": ["not a valid zip bundle"], "warnings": []}

    with zf:
        infos = zf.infolist()

        # ── Zip-bomb guard ────────────────────────────────────────────────────
        if len(infos) > _MAX_ENTRY_COUNT:
            return {
                "ok": False,
                "errors": [f"zip has {len(infos)} entries — exceeds cap of {_MAX_ENTRY_COUNT}"],
                "warnings": [],
            }
        total_uncompressed = sum(zi.file_size for zi in infos)
        if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED:
            return {
                "ok": False,
                "errors": [
                    f"zip declares {total_uncompressed} bytes uncompressed — "
                    f"exceeds cap {_MAX_TOTAL_UNCOMPRESSED}"
                ],
                "warnings": [],
            }
        oversized = [zi.filename for zi in infos if zi.file_size > _MAX_ENTRY_UNCOMPRESSED]
        if oversized:
            return {
                "ok": False,
                "errors": [
                    f"entry {name!r} declares {_MAX_ENTRY_UNCOMPRESSED}+ bytes uncompressed"
                    for name in oversized
                ],
                "warnings": [],
            }

        # ── Check manifest.json present ───────────────────────────────────────
        names = zf.namelist()
        if "manifest.json" not in names:
            return {
                "ok": False,
                "errors": ["missing manifest.json"],
                "warnings": [],
            }

        # ── Zip-slip guard: reject unsafe entry names ─────────────────────────
        unsafe = [n for n in names if not _is_safe_ext_name(n)]
        if unsafe:
            return {
                "ok": False,
                "errors": [f"rejected unsafe zip entry: {n!r}" for n in unsafe],
                "warnings": [],
            }

        # ── Read + parse manifest.json ────────────────────────────────────────
        try:
            manifest_raw = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "errors": [f"manifest.json is not valid JSON: {exc}"],
                "warnings": [],
            }

        try:
            manifest = ExtensionManifest.model_validate(manifest_raw)
        except Exception as exc:
            return {
                "ok": False,
                "errors": [f"manifest.json schema error: {exc}"],
                "warnings": [],
            }

        # ── Unpack to staging dir ─────────────────────────────────────────────
        # FIX 4 — zip-bomb streaming: we extract entry-by-entry via zf.open()
        # and count ACTUAL decompressed bytes as we go. This closes the gap where
        # a malicious zip declares file_size=0 (passing the header check) but
        # produces gigabytes of data after decompression.
        #
        # FIX 5 — EXDEV: staging is created inside USER_PACKS_DIR so that the
        # final os.replace() / shutil.move() is always on the same filesystem.
        USER_PACKS_DIR.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="onw-ext-staging-", dir=USER_PACKS_DIR))
        cumulative_bytes = 0
        try:
            for name in names:
                # Re-assert safety (defense in depth).
                if not _is_safe_ext_name(name):
                    shutil.rmtree(staging, ignore_errors=True)
                    return {
                        "ok": False,
                        "errors": [f"zip entry {name!r} rejected at extract time"],
                        "warnings": [],
                    }
                dest_path = staging / name
                # Jail the path inside the staging dir (zip-slip via realpath).
                real_staging = Path(os.path.realpath(staging))
                real_dest = Path(os.path.realpath(dest_path.parent))
                if real_staging not in [real_dest] + list(real_dest.parents):
                    shutil.rmtree(staging, ignore_errors=True)
                    return {
                        "ok": False,
                        "errors": [
                            f"zip entry {name!r} would escape the extraction dir (zip-slip refused)"
                        ],
                        "warnings": [],
                    }
                if name.endswith("/"):
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    # FIX 4: stream-extract with actual byte accounting.
                    entry_bytes = 0
                    with zf.open(name) as src_fp, dest_path.open("wb") as dst_fp:
                        while True:
                            chunk = src_fp.read(_EXTRACT_CHUNK_SIZE)
                            if not chunk:
                                break
                            entry_bytes += len(chunk)
                            cumulative_bytes += len(chunk)
                            if entry_bytes > _MAX_ENTRY_UNCOMPRESSED:
                                shutil.rmtree(staging, ignore_errors=True)
                                return {
                                    "ok": False,
                                    "errors": [
                                        f"entry {name!r} exceeds per-entry decompressed "
                                        f"cap of {_MAX_ENTRY_UNCOMPRESSED} bytes (zip bomb)"
                                    ],
                                    "warnings": [],
                                }
                            if cumulative_bytes > _MAX_TOTAL_UNCOMPRESSED:
                                shutil.rmtree(staging, ignore_errors=True)
                                return {
                                    "ok": False,
                                    "errors": [
                                        f"zip total decompressed size exceeds "
                                        f"cap of {_MAX_TOTAL_UNCOMPRESSED} bytes (zip bomb)"
                                    ],
                                    "warnings": [],
                                }
                            dst_fp.write(chunk)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return {
                "ok": False,
                "errors": [f"failed to extract zip: {exc}"],
                "warnings": [],
            }

    return {
        "ok": True,
        "_manifest": manifest,
        "_staging": staging,
        "warnings": warnings,
        "errors": errors,
    }


def _realpath_jail(path: Path, jail_root: Path) -> None:
    """Assert that ``path`` (once realpath-resolved) is strictly inside ``jail_root``.

    Used as a belt-and-suspenders check on the install destination after manifest
    slug validation has already run. Raises ``ValueError`` if the path escapes.
    """
    real_root = Path(os.path.realpath(jail_root))
    real_path = Path(os.path.realpath(path))
    if real_path == real_root or real_root not in real_path.parents:
        raise ValueError(
            f"destination {path!r} resolves outside the allowed directory "
            f"{jail_root!r} — path traversal refused"
        )


def _check_supported_type(ext_type: str) -> Optional[str]:
    """Return an error message if ``ext_type`` is not supported in phase-1, else None."""
    if ext_type in _UNSUPPORTED_INSTALL_TYPES:
        return (
            f"extension type {ext_type!r} is not yet supported — "
            "only 'data_pack' can be installed in this version. "
            "compute_plugin and ui_contribution are deferred to later phases."
        )
    return None


def _cleanup(staging: Optional[Path]) -> None:
    if staging and staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def _sweep_stale_staging() -> None:
    """Best-effort removal of any leftover ``onw-ext-staging-*`` dirs under
    USER_PACKS_DIR.  These accumulate when a previous install crashed between
    the ``mkdtemp`` and the ``os.replace`` promotion step.  They are harmless
    to the registry (they live outside the version-dir layout) but waste disk
    space and may confuse directory listings.  Called at the start of every
    ``install_extension`` and ``inspect_extension`` invocation; failures are
    silently swallowed — staging debris is cosmetic, never a correctness issue.
    """
    try:
        if not USER_PACKS_DIR.is_dir():
            return
        for entry in USER_PACKS_DIR.iterdir():
            if entry.name.startswith("onw-ext-staging-") and entry.is_dir():
                try:
                    shutil.rmtree(entry, ignore_errors=True)
                    logger.debug("_sweep_stale_staging: removed %s", entry)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
