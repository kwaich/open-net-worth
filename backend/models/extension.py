"""Extension manifest schema — the unified community-extensions model (P4-EXT phase-1).

A leaf beside ``models/connector.py``: no imports from ``db``, ``services``, or
``api``. Pure Pydantic + stdlib — the manifest model is read at install time and
passed through service-layer validators that may not import ``api``.

An ``ExtensionManifest`` is a SUPERSET of the existing skill-bundle manifest
(``services/skill_bundles.py``). A skill bundle with ``type="skill"`` and a
``scripts`` block is a valid extension manifest. New top-level keys are additive
so an existing bundle never breaks.

The manifest schema mirrors RFC P4-EXT §1.2. All fields are validated here for
SHAPE only (types, literal choices, required fields). Cross-field validation
(path-in-tree checks, capability closed-world, etc.) lives in
``services/extensions/manifest.py``.

SECURITY-CRITICAL NOTE: this file and the entire extensions platform require
``/security-review`` before merge. This phase-1 implementation:
  - handles DATA PACK type only (compute_plugin/ui_contribution refused at install)
  - never auto-grants any capability (install-time review payload is returned to
    the human operator for confirmation)
  - never executes code (data packs are interpret-only)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import ONBaseModel

# The single supported manifest version in this implementation.
MANIFEST_VERSION = 1

# Extension types understood by the loader. Phase-1 supports data_pack installs
# only; compute_plugin and ui_contribution are REFUSED at install time with a
# clear "not yet supported" error. skill/agent ride for free once the manifest
# model exists (they reuse existing choke points: validate_agent / skills_service).
EXTENSION_TYPES = frozenset({"data_pack", "compute_plugin", "skill", "agent", "ui_contribution"})

# Contribution kinds shipped in phase-1. Compute/UI contributions are validated
# against the manifest schema but REFUSED by the installer (not-yet-supported).
CONTRIBUTION_KINDS = frozenset({"data_pack", "compute", "ui"})

# Engine names registered for the data_pack kind (the contracts §2.1). This set
# is checked at validation time; unknown engines are warned but not refused
# (forward-compat: a future phase may add more engines).
KNOWN_DATA_PACK_ENGINES = frozenset(
    {
        "category_taxonomy",  # spending category taxonomy overlay
        "payee_rules",  # payee-normalisation alias table
        "report_template",  # declarative report definition
        "sample",  # trivial shipped example for loader smoke tests
    }
)


class Contribution(ONBaseModel):
    """One contribution entry in ``manifest.contributions[]``.

    A single extension may bundle several contributions (e.g. a data pack +
    a UI schema). The ``kind`` field governs what the loader looks for:
      - ``data_pack``: ``engine`` + ``path`` (relative to the pack version dir)
      - ``compute``:   ``engine`` + ``entry`` + ``timeout_s`` (gated, not phase-1)
      - ``ui``:        ``tier`` + ``slot`` + ``schema`` (gated, not phase-1)
    """

    kind: str  # data_pack | compute | ui
    # data_pack fields
    engine: Optional[str] = None  # the named engine contract (e.g. "category_taxonomy")
    path: Optional[str] = None  # relative path inside the pack to the data file
    # compute fields (phase-2)
    entry: Optional[str] = None  # relative path to the sandboxed entry script
    timeout_s: Optional[float] = None
    # ui fields (phase-4)
    tier: Optional[int] = None  # 0 | 1 | 2 (3 is FORBIDDEN — never validates)
    slot: Optional[str] = None  # named mount point (e.g. "plan.breakdown")
    schema_path: Optional[str] = None  # relative path to the declarative schema file


class ExtensionPermissions(ONBaseModel):
    """The declared permissions block — the install-time review surface (RFC §3.3)."""

    # Named read surfaces the engine exposes to the pack (data packs only).
    reads: List[str] = []
    # RETIRED with the auto-apply capability model. Kept on the schema so an older manifest
    # still PARSES, but a non-empty value is now a validation error (see manifest.py) rather
    # than a grant request — there is nothing left for it to name.
    capabilities: List[str] = []
    # compute_plugin only: MUST be false in MVP (validator enforces).
    network: bool = False
    # ui_contribution only: 0 | 1 | 2 (3 FORBIDDEN).
    ui_tier: Optional[int] = None


class ExtensionDomain(ONBaseModel):
    """Free-form domain metadata for display + provenance (surfaced in the gallery).

    Keys are per-engine; common ones are country, year, currency, as_of, source_url.
    The loader uses this dict to match resolution queries (e.g. ``country=="SG"`` and
    ``year<=2025``).
    """

    model_config = {"extra": "allow"}  # open dict — engine-specific keys allowed

    country: Optional[str] = None
    year: Optional[int] = None
    currency: Optional[str] = None
    as_of: Optional[str] = None
    source_url: Optional[str] = None


class ExtensionManifest(ONBaseModel):
    """The unified extension manifest (RFC P4-EXT §1.2).

    A current skill-bundle manifest is a valid extension manifest with
    ``type="skill"`` and ``scripts`` populated. All new top-level keys are
    optional so the superset is backward-compatible.

    Field contracts
    ---------------
    ``id``              Slug, unique within its type namespace (enforced per install).
    ``type``            Extension type — one of EXTENSION_TYPES. Phase-1 installs
                        only data_pack; others are refused by the installer.
    ``manifest_version`` Loader refuses a version it doesn't understand.
    ``target_app_version`` Semver range (display only in phase-1; version-compat check
                        is phase-3 open work per RFC §8).
    ``bundle_sha``      SHA-256 over the version dir (same algo as skill_bundles).
                        Verified at install AND before every sandbox exec (T7).
    ``signature``       Optional detached signature over ``bundle_sha``; null = unsigned.
                        MVP ships the field + verify hook, not the key infra (RFC §3.4).
    ``verified``        NEVER self-asserted — loader strips any author-supplied ``True``
                        and sets it only from the registry index (RFC §3.5).
    ``contributions``   At least one entry is required for a data_pack.
    ``scripts``         Verbatim skill-bundle scripts block (for skill/compute types).
    """

    # ── Identity (required) ────────────────────────────────────────────────────
    manifest_version: int = MANIFEST_VERSION
    id: str
    type: str = "data_pack"
    name: str
    version: str
    author: str = ""
    description: str = ""

    # ── Compatibility ─────────────────────────────────────────────────────────
    target_app_version: str = "*"
    manifest_compat: int = MANIFEST_VERSION

    # ── Integrity / provenance ────────────────────────────────────────────────
    bundle_sha: str = ""
    signature: Optional[str] = None  # null = unsigned (MVP)
    source_url: Optional[str] = None
    # NEVER self-asserted: the loader strips any author-supplied True (RFC §3.5).
    verified: bool = False

    # ── Declared permissions (the install-review surface) ─────────────────────
    permissions: ExtensionPermissions = ExtensionPermissions()

    # ── Domain metadata (per-engine, for display + resolution) ────────────────
    domain: ExtensionDomain = ExtensionDomain()

    # ── Contributions (≥1 for data_pack, any for others) ─────────────────────
    contributions: List[Contribution] = []

    # ── Compute-only block (verbatim today's skill-bundle scripts[]) ──────────
    scripts: List[Dict[str, Any]] = []

    def permission_summary(self) -> str:
        """Plain-English one-liner for the install-time review UI.

        Surfaces WHAT the pack declares before the operator confirms. The
        summary is purposely conservative: errs toward more disclosure.
        """
        t = self.type
        has_code = bool(self.scripts) or any(c.kind == "compute" for c in self.contributions)
        has_network = self.permissions.network
        reads = self.permissions.reads or []
        caps = self.permissions.capabilities or []

        parts: list[str] = [f"type={t!r}  version={self.version!r}"]
        if t == "data_pack":
            engines = sorted({c.engine for c in self.contributions if c.engine})
            parts.append(f"engines={engines}")
            if reads:
                parts.append(f"reads={reads}")
            parts.append("runs no code, touches no money")
        if has_code:
            parts.append("runs custom Python in sandbox (no secrets, no ledger)")
        if has_network:
            parts.append("WARNING: requests network (MVP refuses this)")
        if caps:
            parts.append("WARNING: declares retired permissions.capabilities (refused)")
        if not self.verified:
            parts.append("unsigned / not registry-verified")
        return " | ".join(parts)
