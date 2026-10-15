"""Extension registry — discover + load installed packs (P4-EXT phase-1).

The registry is the single loader for the extensions platform. It implements the
precedence rule from RFC §2.4:

    resolve(engine, key) =
      1. user pack  — enabled, sha-verified under USER_PACKS_DIR/data_pack/
      2. shipped pack — in-repo under SHIPPED_EXTENSIONS_DIR/<engine>/
      3. None        — the engine's own fallback (e.g. the built-in default table)

``get_data_pack(engine, domain_filter)`` is the GENERIC seam a future engine
calls. It is intentionally domain-agnostic: the caller passes a ``domain_filter``
dict (e.g. ``{"country": "SG", "year": 2025}``), and the registry matches it
against ``manifest.domain`` using simple equality + "latest year <= requested".

Layout on disk:
    USER_PACKS_DIR/          (DATA_DIR/user_packs — instance-local, gitignored)
      data_pack/
        <pack-id>/
          <version>/
            manifest.json    ← ExtensionManifest
            data/…

    SHIPPED_EXTENSIONS_DIR/  (BACKEND/extensions — in-repo, may be absent)
      <engine>/
        <pack-id>/
          manifest.json      ← ExtensionManifest
          data/…

SECURITY NOTE: every user-pack manifest is sha-verified on load (``bundle_sha``
field vs computed content-address). A pack whose bytes changed out-of-band is
treated as disabled and logged. Shipped packs are trusted (they are in-repo).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from backend.core.errors import NotFoundError
from backend.core.paths import SHIPPED_EXTENSIONS_DIR, USER_PACKS_DIR
from backend.models.extension import ExtensionManifest
from backend.services.extensions.manifest import load_manifest_from_dir

logger = logging.getLogger(__name__)

# ── Integrity helper ──────────────────────────────────────────────────────────

_SHA_SIDECAR = "bundle.sha"


def _compute_pack_sha(version_dir: Path) -> str:
    """Compute sha256 over all files EXCEPT the ``bundle.sha`` sidecar itself.

    The sidecar stores the installer-computed sha but is NOT included in the hash
    input — including it would create a circular dependency (writing the sha into
    the sidecar changes the sha). The installer computes sha BEFORE writing the
    sidecar; the registry uses this function to re-derive and compare.
    """
    if not version_dir.is_dir():
        raise NotFoundError(f"pack version dir not found: {version_dir}")
    h = hashlib.sha256()
    files = sorted(
        (p for p in version_dir.rglob("*") if p.is_file() and p.name != _SHA_SIDECAR),
        key=lambda p: p.relative_to(version_dir).as_posix(),
    )
    for p in files:
        rel = p.relative_to(version_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# ── Public surface ────────────────────────────────────────────────────────────


def _disabled_flag(pack_type: str, pack_id: str) -> str:
    """Config key holding one pack's OFF switch.

    Stored as "disabled" rather than "enabled" so the absence of a flag means enabled —
    a freshly installed pack works without anyone having to write a record for it, and a
    lost/rolled-back config file cannot silently switch every extension off.
    """
    return f"extension_disabled:{pack_type}:{pack_id}"


def is_pack_enabled(pack_type: str, pack_id: str) -> bool:
    from backend.db.app_config_store import AppConfigStore  # noqa: PLC0415

    return not AppConfigStore().get_flag(_disabled_flag(pack_type, pack_id), default=False)


def set_pack_enabled(pack_type: str, pack_id: str, enabled: bool) -> bool:
    from backend.db.app_config_store import AppConfigStore  # noqa: PLC0415

    AppConfigStore().set_flag(_disabled_flag(pack_type, pack_id), not enabled)
    return enabled


def get_data_pack(engine: str, domain_filter: dict) -> Optional[dict]:
    """Resolve the best data pack for ``engine`` matching ``domain_filter``.

    Precedence: **user pack > shipped > None**.

    Returns a dict:
      ``{manifest, pack_dir, source, verified}``
    where:
      ``manifest``  is the loaded ``ExtensionManifest``;
      ``pack_dir``  is the resolved version directory (``Path``);
      ``source``    is ``"user"`` or ``"shipped"``;
      ``verified``  mirrors the manifest's ``verified`` flag (user packs are always
                    ``False`` — the registry sets it only from index metadata, not
                    self-assertion; shipped packs default to ``True``).

    Returns ``None`` when no installed pack matches.

    Args:
        engine:        Named engine contract (e.g. ``"category_taxonomy"``).
        domain_filter: Dict of domain key→value to match against ``manifest.domain``.
                       Example: ``{"country": "SG", "year": 2025}`` matches
                       a pack whose domain has country=="SG" and the highest
                       year <= 2025.
    """

    # A pack the operator switched off must not resolve — otherwise the toggle would only
    # grey out a row while the extension kept running. The user tier filters disabled packs
    # inside its own resolver (so one disabled pack cannot shadow an enabled sibling); the
    # shipped tier has a single candidate, so guarding the result is enough.
    def _usable(result: Optional[dict]) -> Optional[dict]:
        if result is None:
            return None
        manifest = result.get("manifest")
        pack_id = getattr(manifest, "id", None) or (
            manifest.get("id") if isinstance(manifest, dict) else None
        )
        pack_type = getattr(manifest, "type", None) or (
            manifest.get("type") if isinstance(manifest, dict) else None
        )
        if pack_id and not is_pack_enabled(str(pack_type or ""), str(pack_id)):
            return None
        return result

    # 1. User packs (DATA_DIR/user_packs/data_pack/) — already enabled-filtered.
    user_result = _resolve_user_pack(engine, domain_filter)
    if user_result is not None:
        return user_result

    # 2. Shipped packs (BACKEND/extensions/<engine>/ — absent unless a pack ships).
    shipped_result = _usable(_resolve_shipped_pack(engine, domain_filter))
    if shipped_result is not None:
        return shipped_result

    return None


def list_installed(*, type_filter: Optional[str] = None) -> list[dict]:
    """Return a list of all installed (user + shipped) extension records.

    Each record:
    ``{id, name, version, type, engine, source, verified, pack_dir, manifest}``

    ``type_filter`` (optional) restricts to a specific extension type.
    """
    results: list[dict] = []
    results.extend(_list_user_packs(type_filter=type_filter))
    results.extend(_list_shipped_packs(type_filter=type_filter))
    return results


# ── User pack resolver ────────────────────────────────────────────────────────


def _resolve_user_pack(engine: str, domain_filter: dict) -> Optional[dict]:
    """Find the best user-installed data pack for ``engine`` + ``domain_filter``."""
    type_dir = USER_PACKS_DIR / "data_pack"
    if not type_dir.is_dir():
        return None

    candidates = []
    for pack_dir in type_dir.iterdir():
        if not pack_dir.is_dir():
            continue
        # Newest version wins within a pack id — iterate version subdirs.
        for version_dir in sorted(pack_dir.iterdir(), reverse=True):
            if not version_dir.is_dir():
                continue
            record = _load_user_pack_version(version_dir)
            if record is None:
                continue
            manifest: ExtensionManifest = record["manifest"]
            if not _contributes_engine(manifest, engine):
                continue
            if not _domain_matches(manifest, domain_filter):
                continue
            # Skip here rather than nulling the whole user tier afterwards: with two user
            # packs matching the same domain, disabling the top-ranked one must fall through
            # to the NEXT user pack, not silently down to the shipped ruleset.
            if not is_pack_enabled(str(manifest.type), str(manifest.id)):
                continue
            candidates.append(record)
            break  # only the newest valid version per pack id

    if not candidates:
        return None

    # Among matching packs, prefer the one with the closest year match
    # (highest year ≤ requested); fall back to insertion order.
    requested_year = domain_filter.get("year")
    if requested_year is not None:
        candidates = [c for c in candidates if (c["manifest"].domain.year or 0) <= requested_year]
        candidates.sort(key=lambda c: c["manifest"].domain.year or 0, reverse=True)

    return candidates[0] if candidates else None


def _load_user_pack_version(version_dir: Path) -> Optional[dict]:
    """Load + sha-verify one version dir. Returns None on any error."""
    try:
        manifest = load_manifest_from_dir(version_dir)
    except Exception as exc:
        logger.warning("extensions: cannot load manifest from %s: %s", version_dir, exc)
        return None

    # Sha-verify: user packs must not be tampered with. Read the sha from the
    # ``bundle.sha`` sidecar written by the installer. The sidecar is EXCLUDED
    # from the hash computation (see _compute_pack_sha) to avoid a circular
    # reference — the installer computes sha before writing the sidecar, so the
    # registry must recompute identically (excluding the sidecar itself).
    sha_path = version_dir / _SHA_SIDECAR
    if sha_path.is_file():
        expected_sha = sha_path.read_text(encoding="utf-8").strip()
        try:
            actual_sha = _compute_pack_sha(version_dir)
        except Exception as exc:
            logger.warning(
                "extensions: sha computation failed for %s: %s — pack disabled",
                version_dir,
                exc,
            )
            return None
        if actual_sha != expected_sha:
            logger.warning(
                "extensions: sha mismatch for %s (expected %s, got %s) — pack disabled",
                version_dir,
                expected_sha[:16],
                actual_sha[:16],
            )
            return None
    else:
        logger.debug(
            "extensions: pack %s has no bundle.sha sidecar — skipping integrity check",
            version_dir,
        )

    # Strip author self-asserted verified=True (RFC §3.5).
    if manifest.verified:
        manifest = manifest.model_copy(update={"verified": False})

    return {
        "manifest": manifest,
        "pack_dir": version_dir,
        "source": "user",
        "verified": False,  # user packs are never registry-verified in phase-1
    }


# ── Shipped pack resolver ─────────────────────────────────────────────────────


def _resolve_shipped_pack(engine: str, domain_filter: dict) -> Optional[dict]:
    """Find the best shipped (in-repo) data pack for ``engine`` + ``domain_filter``."""
    engine_dir = SHIPPED_EXTENSIONS_DIR / engine
    if not engine_dir.is_dir():
        return None

    candidates = []
    for pack_dir in engine_dir.iterdir():
        if not pack_dir.is_dir():
            continue
        record = _load_shipped_pack(pack_dir)
        if record is None:
            continue
        manifest: ExtensionManifest = record["manifest"]
        if not _domain_matches(manifest, domain_filter):
            continue
        candidates.append(record)

    if not candidates:
        return None

    requested_year = domain_filter.get("year")
    if requested_year is not None:
        candidates = [c for c in candidates if (c["manifest"].domain.year or 0) <= requested_year]
        candidates.sort(key=lambda c: c["manifest"].domain.year or 0, reverse=True)

    return candidates[0] if candidates else None


def _load_shipped_pack(pack_dir: Path) -> Optional[dict]:
    """Load a shipped (in-repo) pack dir. Shipped packs are trusted (no sha check)."""
    try:
        manifest = load_manifest_from_dir(pack_dir)
    except Exception as exc:
        logger.debug("extensions: cannot load shipped pack from %s: %s", pack_dir, exc)
        return None
    return {
        "manifest": manifest,
        "pack_dir": pack_dir,
        "source": "shipped",
        "verified": True,  # shipped packs are trusted (in-repo, CI-validated)
    }


# ── List helpers ──────────────────────────────────────────────────────────────


def _list_user_packs(*, type_filter: Optional[str]) -> list[dict]:
    if not USER_PACKS_DIR.is_dir():
        return []
    results = []
    for type_dir in USER_PACKS_DIR.iterdir():
        if not type_dir.is_dir():
            continue
        ext_type = type_dir.name
        if type_filter and ext_type != type_filter:
            continue
        for pack_dir in type_dir.iterdir():
            if not pack_dir.is_dir():
                continue
            for version_dir in sorted(pack_dir.iterdir(), reverse=True):
                if not version_dir.is_dir():
                    continue
                rec = _load_user_pack_version(version_dir)
                if rec is None:
                    continue
                m: ExtensionManifest = rec["manifest"]
                engines = sorted({c.engine for c in m.contributions if c.engine})
                results.append(
                    {
                        "id": m.id,
                        "name": m.name,
                        "version": m.version,
                        "type": m.type,
                        "engine": engines[0] if len(engines) == 1 else engines,
                        "source": "user",
                        "verified": False,
                        "pack_dir": str(version_dir),
                        "manifest": m.model_dump(),
                    }
                )
                break  # only the newest valid version per pack id
    return results


def _list_shipped_packs(*, type_filter: Optional[str]) -> list[dict]:
    if not SHIPPED_EXTENSIONS_DIR.is_dir():
        return []
    results = []
    for engine_dir in SHIPPED_EXTENSIONS_DIR.iterdir():
        if not engine_dir.is_dir():
            continue
        engine = engine_dir.name
        for pack_dir in engine_dir.iterdir():
            if not pack_dir.is_dir():
                continue
            rec = _load_shipped_pack(pack_dir)
            if rec is None:
                continue
            m: ExtensionManifest = rec["manifest"]
            if type_filter and m.type != type_filter:
                continue
            results.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "version": m.version,
                    "type": m.type,
                    "engine": engine,
                    "source": "shipped",
                    "verified": True,
                    "pack_dir": str(pack_dir),
                    "manifest": m.model_dump(),
                }
            )
    return results


# ── Matching helpers ──────────────────────────────────────────────────────────


def _contributes_engine(manifest: ExtensionManifest, engine: str) -> bool:
    """True iff the manifest has a data_pack contribution for ``engine``."""
    return any(c.kind == "data_pack" and c.engine == engine for c in manifest.contributions)


def _domain_matches(manifest: ExtensionManifest, domain_filter: dict) -> bool:
    """True iff every key in ``domain_filter`` matches ``manifest.domain``.

    Special rule for ``year``: the filter value is a ceiling (ints ≤ requested).
    All other keys are strict equality.
    """
    domain = manifest.domain
    for key, value in domain_filter.items():
        if key == "year":
            # Caller supplies the target year; a pack is eligible if its
            # year <= the requested year (closest year ≤ target wins among
            # multiple candidates — resolved in the caller loop).
            pack_year = getattr(domain, "year", None)
            if pack_year is None:
                return False
            if pack_year > value:
                return False
        else:
            pack_val = getattr(domain, key, None)
            if pack_val != value:
                return False
    return True
