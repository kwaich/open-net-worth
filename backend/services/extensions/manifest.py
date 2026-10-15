"""Extension manifest validation (P4-EXT phase-1).

``validate_extension_manifest(raw, version_dir_path)`` is the single authority
for checking a manifest dict before the installer writes anything to disk.

It delegates the ``scripts[]`` block to the existing
``services.skill_bundles.validate_manifest`` (byte-identical compatibility) so a
skill bundle is a valid extension manifest with zero fork.

Per-type contribution checks added here:
  - ``data_pack``: at least one contribution with kind="data_pack" + engine +
    path-in-tree check via ``skill_bundles._resolve_in_tree``.
  - ``compute`` / ``ui``: shape-checked but REFUSED (not-yet-supported) — the
    manifest is still valid structurally, the installer rejects the type.
  - ``permissions.network``: MUST be False (validator enforces for phase-1).
  - ``ui_tier != 3``: Tier-3 is FORBIDDEN, never validates.

Layering: ``services`` → ``models`` + ``services.skill_bundles`` + ``core``.
No ``db`` or ``api`` imports.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from backend.core.errors import ValidationError
from backend.models.extension import (
    CONTRIBUTION_KINDS,
    EXTENSION_TYPES,
    MANIFEST_VERSION,
    ExtensionManifest,
)
from backend.services.skill_bundles import validate_manifest as _validate_scripts

logger = logging.getLogger(__name__)

# Slug discipline for id and version — must start with an alphanumeric char,
# contain only alphanumerics plus dots, hyphens, underscores. No slashes, no
# dots at the start (which would make the name hidden on Unix), no absolute paths.
#
# NOTE: always call via re.fullmatch() (not .match()) — "$" would pass "v1\n"
# because Python's $ matches before a trailing newline; \Z does not.
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*\Z")


def validate_extension_manifest(raw: dict, version_dir_path: Path) -> list[str]:  # noqa: C901
    """Return a list of human-readable problems with a raw manifest dict.

    Empty list = valid.  Checks (per RFC §1.2 + §5):

    IDENTITY
      - ``manifest_version`` must be ``MANIFEST_VERSION`` (1).
      - ``id`` must be a non-empty slug (letters, digits, hyphens).
      - ``type`` must be in EXTENSION_TYPES.
      - ``name`` and ``version`` must be non-empty strings.

    PERMISSIONS
      - ``permissions.network`` must be False (phase-1 enforces this for all types).
      - ``permissions.ui_tier`` 3 is FORBIDDEN (never validates).
      - ``verified`` self-asserted as True is stripped (not an error; warned).

    CONTRIBUTIONS (per ``type``)
      - ``data_pack``: requires at least one contribution with kind="data_pack";
        each must have engine + path; path is validated in-tree.
      - ``compute`` / ``ui`` contributions: shape-checked (warned) but not executed.

    SCRIPTS (skill/compute types)
      - Delegates to ``skill_bundles.validate_manifest`` for the scripts[] block.

    Args:
        raw:               The parsed manifest dict (from ``manifest.json``).
        version_dir_path:  The directory the bundle lives in (for path-in-tree checks).

    Returns:
        A list of problem strings; empty = valid.
    """
    problems: list[str] = []

    # ── Parse into the typed model (shape check) ─────────────────────────────
    try:
        manifest = ExtensionManifest.model_validate(raw)
    except Exception as exc:
        return [f"manifest does not match the expected schema: {exc}"]

    # ── Identity ──────────────────────────────────────────────────────────────
    if manifest.manifest_version != MANIFEST_VERSION:
        problems.append(
            f"manifest_version {manifest.manifest_version!r} is not supported "
            f"(expected {MANIFEST_VERSION})"
        )

    if not manifest.id or not manifest.id.strip():
        problems.append("'id' must be a non-empty string")
    else:
        if not re.match(r"^[a-z0-9][a-z0-9\-]*\Z", manifest.id):
            problems.append(
                f"'id' {manifest.id!r} must be a lowercase slug "
                "(letters, digits, hyphens; must start with a letter or digit)"
            )

    if manifest.type not in EXTENSION_TYPES:
        problems.append(
            f"'type' {manifest.type!r} is not a known extension type "
            f"(valid: {sorted(EXTENSION_TYPES)})"
        )

    if not manifest.name or not manifest.name.strip():
        problems.append("'name' must be a non-empty string")

    if not manifest.version or not manifest.version.strip():
        problems.append("'version' must be a non-empty string")
    elif not _SLUG_RE.match(manifest.version):
        problems.append(
            f"'version' {manifest.version!r} must be a safe version slug "
            "(alphanumerics, dots, hyphens, underscores; must start with alphanumeric; "
            "no slashes, no leading dots, no absolute paths)"
        )

    # ── Permissions ───────────────────────────────────────────────────────────
    if manifest.permissions.network:
        problems.append(
            "permissions.network=true is not supported in this phase "
            "(network access for compute plugins is a later gated slice)"
        )

    if manifest.permissions.capabilities:
        problems.append(
            "permissions.capabilities is no longer supported — the auto-apply capability "
            "model it named was removed. An extension's power comes from what it contributes, "
            "not from a granted capability list."
        )

    if manifest.permissions.ui_tier == 3:
        problems.append(
            "permissions.ui_tier=3 is FORBIDDEN — inline third-party JS "
            "runs in the app origin with full session access"
        )

    # Warn if the author self-asserted verified=True (we strip it, non-fatal).
    if raw.get("verified") is True:
        logger.warning(
            "extension manifest %r self-asserts verified=True — "
            "the loader will strip this; 'verified' is set only by the registry",
            manifest.id,
        )

    # ── Contribution checks ────────────────────────────────────────────────────
    if manifest.contributions:
        for i, contrib in enumerate(manifest.contributions):
            label = f"contribution #{i + 1} (kind={contrib.kind!r})"

            if contrib.kind not in CONTRIBUTION_KINDS:
                problems.append(
                    f"{label}: unknown kind {contrib.kind!r} (valid: {sorted(CONTRIBUTION_KINDS)})"
                )
                continue

            if contrib.kind == "data_pack":
                if not contrib.engine:
                    problems.append(f"{label}: missing 'engine'")
                if not contrib.path:
                    problems.append(f"{label}: missing 'path'")
                else:
                    # Validate that the declared path stays inside the bundle tree.
                    try:
                        from backend.services.skill_bundles import _resolve_in_tree

                        resolved = _resolve_in_tree(version_dir_path, contrib.path)
                        if not resolved.is_file():
                            problems.append(
                                f"{label}: data file {contrib.path!r} does not exist in the bundle"
                            )
                    except ValidationError as exc:
                        problems.append(f"{label}: path validation failed — {exc}")

            elif contrib.kind == "compute":
                # Shape-check only (installer refuses compute_plugin type in phase-1).
                if not contrib.engine:
                    problems.append(f"{label}: missing 'engine'")
                if not contrib.entry:
                    problems.append(f"{label}: missing 'entry'")

            elif contrib.kind == "ui":
                if contrib.tier == 3:
                    problems.append(
                        f"{label}: ui tier 3 is FORBIDDEN (inline third-party JS in the app origin)"
                    )

    # A data_pack MUST declare at least one data_pack contribution.
    if manifest.type == "data_pack":
        dp_contribs = [c for c in manifest.contributions if c.kind == "data_pack"]
        if not dp_contribs:
            problems.append(
                "a 'data_pack' extension must declare at least one contribution "
                "with kind='data_pack'"
            )

    # ── Scripts block (skill type only in phase-1) ────────────────────────────
    # Delegate the scripts[] block to the existing skill-bundle validator ONLY
    # when the manifest actually declares scripts (to avoid forcing an empty-
    # scripts error on a compute_plugin manifest that is already refused by the
    # type gate). For data_pack types scripts[] is empty by design.
    if manifest.scripts:
        script_problems = _validate_scripts(raw, version_dir_path)
        problems.extend(script_problems)

    return problems


def load_manifest_from_dir(pack_dir: Path) -> ExtensionManifest:
    """Read and parse ``manifest.json`` from a pack version directory.

    Raises ``ValidationError`` on missing or unparseable manifest.
    Does NOT validate the full manifest contents — call
    ``validate_extension_manifest`` for that.
    """
    mf_path = pack_dir / "manifest.json"
    if not mf_path.is_file():
        raise ValidationError(f"manifest.json not found in {pack_dir}")
    try:
        raw = json.loads(mf_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValidationError(f"cannot read manifest.json in {pack_dir}: {exc}") from exc
    try:
        return ExtensionManifest.model_validate(raw)
    except Exception as exc:
        raise ValidationError(f"manifest.json schema error in {pack_dir}: {exc}") from exc
