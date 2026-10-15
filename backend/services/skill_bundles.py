"""skill_bundles — folder-first skill file I/O for script/reference bundles.

In the new file-first layout each skill IS a folder:
  DATA_DIR/skills/<skill_id>/
    SKILL.md          portable Agent Skills frontmatter + instructions
    agents/openai.yaml optional Codex interface and invocation policy
    scripts/          runnable scripts (language by extension or shebang)
    references/       docs read on demand
    assets/           static files

ONW keeps application bookkeeping (enabled state, timestamps, invocation count) outside
the portable definition. It preserves documented Claude Code invocation metadata and arguments,
but never turns skill tool lists into ONW permissions.

This module is the service-layer I/O surface for:
  - write_bundle():  write script/reference/extra files into a skill's folder
  - read_bundle():   read a skill folder back to the {skill_md, manifest, files} shape
                     the REST API and activate_skill expect
  - read_bundle_file(): read one jailed file from a skill folder (progressive disclosure)
  - validate_manifest(): validate the manifest dict shape (unchanged)
  - compute_bundle_sha / verify_bundle_sha: sha over the skill folder (for tamper-guard)
  - version_dir():   returns the skill folder itself (version concept is gone)
  - list_versions(): returns ["live"] if the folder has any content, else []

The parse/build/validate helpers (parse_skill_md etc.) are re-exported from
db.skill_files so callers that import them from here keep working.

Layering: ``services`` → ``db`` + ``core``. db.skill_files owns the pure helpers;
this module owns folder I/O + manifest logic.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

from backend.core.errors import NotFoundError, ValidationError
from backend.core.io import atomic_write_text
from backend.core.paths import SKILLS_DIR

# Re-export the pure helpers from db.skill_files so callers that import them from
# services.skill_bundles keep working without changes (back-compat shim).
from backend.db.skill_files import (  # noqa: F401 — re-export
    INTERPRETER_BY_EXT,
    SUPPORTED_INTERPRETERS,
    allows_implicit_invocation,
    body_advisories,
    build_openai_yaml,
    build_skill_md,
    compute_scripts_sha,
    interpreter_for,
    parse_claude_extensions,
    parse_openai_yaml,
    parse_skill_md,
    validate_openai_metadata,
    validate_openai_yaml,
    validate_skill_md_frontmatter,
)
from backend.db.skill_store import (
    PinnedSkillFolder,
    _complete_state_row,
    _load_state,
    _save_state,
    pinned_skill_folder,
    pinned_skill_tree,
    read_pinned_skill_text,
    skill_store_lock,
    validate_skill_id,
    write_pinned_skill_bytes,
    write_pinned_skill_text,
)

logger = logging.getLogger(__name__)

# Hard ceiling for manifest per-script timeouts (unchanged from old layout).
MAX_TIMEOUT_S = 600


# ── Folder location (replaces version_dir) ───────────────────────────────────


def version_dir(skill_id: str, version: str) -> Path:
    """Return the skill's live folder (version concept is gone — always the same folder).

    Back-compat: callers that use version_dir() still work. The version string is
    accepted and ignored (any non-empty string is valid). Empty version raises
    ValidationError for parity with the old API."""
    if not version:
        raise ValidationError("bundle version must not be empty")
    with skill_store_lock(exclusive=False):
        try:
            with pinned_skill_folder(skill_id) as pinned:
                pinned.verify_identity()
                return pinned.path
        except FileNotFoundError:
            return SKILLS_DIR / validate_skill_id(skill_id)


def list_versions(skill_id: str) -> list[str]:
    """Return ["live"] if the skill folder has content, else []. Version dirs are gone."""
    with skill_store_lock(exclusive=False):
        try:
            with pinned_skill_folder(skill_id) as pinned:
                return ["live"] if pinned_skill_tree(pinned) else []
        except FileNotFoundError:
            return []


def _resolve_in_tree(folder: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``folder`` with jail check. Same semantics as before."""
    base = Path(os.path.realpath(folder))
    candidate = Path(os.path.realpath(folder / rel))
    if candidate != base and base not in candidate.parents:
        raise ValidationError(f"bundle entry '{rel}' resolves outside the skill folder")
    return candidate


# ── Integrity ─────────────────────────────────────────────────────────────────


def compute_bundle_sha(folder: Path) -> str:
    """Deterministic sha256 over every file in the skill folder (sorted rel path + bytes)."""
    if not folder.is_dir():
        raise NotFoundError(f"skill folder not found: {folder}")
    h = hashlib.sha256()
    files = sorted(
        (p for p in folder.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(folder).as_posix(),
    )
    for p in files:
        rel = p.relative_to(folder).as_posix()
        resolved = _resolve_in_tree(folder, rel)
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(resolved.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def compute_pinned_bundle_sha(folder: PinnedSkillFolder) -> str:
    """Content hash for an already-open, identity-pinned skill folder."""
    h = hashlib.sha256()
    for rel, content in sorted(pinned_skill_tree(folder).items()):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return h.hexdigest()


def verify_bundle_sha(skill_id: str, version: str, expected_sha: str) -> bool:
    """Recompute the folder sha and compare to expected_sha. False on mismatch or missing."""
    with skill_store_lock(exclusive=False):
        try:
            with pinned_skill_folder(skill_id) as pinned:
                return compute_pinned_bundle_sha(pinned) == expected_sha
        except (OSError, NotFoundError, FileNotFoundError):
            return False


# ── Manifest validation ───────────────────────────────────────────────────────


def validate_manifest(manifest: dict, folder: Path) -> list[str]:
    """Return problems with a manifest dict. Empty = valid. Same logic as before."""
    problems: list[str] = []
    scripts = manifest.get("scripts")
    if scripts is None:
        scripts = []
    if not isinstance(scripts, list):
        return ["manifest 'scripts' must be a list"]
    seen_ids: set[str] = set()
    for i, script in enumerate(scripts):
        label = f"script #{i + 1}"
        if not isinstance(script, dict):
            problems.append(f"{label} is not an object")
            continue
        sid = script.get("id")
        if not (isinstance(sid, str) and sid):
            problems.append(f"{label} is missing an 'id'")
        elif sid in seen_ids:
            problems.append(f"duplicate script id '{sid}'")
        else:
            seen_ids.add(sid)
        if sid:
            label = f"script '{sid}'"
        interp = script.get("interpreter")
        if interp not in SUPPORTED_INTERPRETERS:
            problems.append(
                f"{label} has unsupported interpreter '{interp}' "
                f"(supported: {', '.join(sorted(SUPPORTED_INTERPRETERS))})"
            )
        entry = script.get("entry")
        if not (isinstance(entry, str) and entry):
            problems.append(f"{label} is missing an 'entry'")
        else:
            try:
                resolved = _resolve_in_tree(folder, entry)
            except ValidationError as e:
                problems.append(f"{label}: {e}")
            else:
                if not resolved.is_file():
                    problems.append(f"{label} entry '{entry}' does not exist in the skill folder")
        if script.get("network"):
            problems.append(
                f"{label} requests network access — network is OFF in this version "
                "(a later, separately-gated slice; never granted here)"
            )
        timeout = script.get("timeout_s")
        if timeout is not None:
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                problems.append(f"{label} 'timeout_s' must be a positive number")
            elif timeout > MAX_TIMEOUT_S:
                problems.append(f"{label} 'timeout_s' exceeds the {MAX_TIMEOUT_S}s ceiling")
    return problems


# ── Write / read ──────────────────────────────────────────────────────────────


def write_bundle(
    skill_id: str,
    version: str,
    *,
    skill_md: str,
    manifest: dict,
    scripts: dict[str, str] | None = None,
    resources: dict[str, str] | None = None,
    references: dict[str, str] | None = None,
    extra_files: dict[str, str | bytes] | None = None,
) -> tuple[Path, str]:
    """Write bundle files into the skill's folder and return ``(folder, bundle_sha)``.

    In the new layout all writes are in-place (mutable folder, no version dirs).
    The version argument is accepted for back-compat and ignored.
    SKILL.md is written only when skill_md is non-empty (caller may pre-write it).
    Validates the manifest before writing; raises ValidationError on problems.
    Returns the sha over the folder after writing.
    """
    skill_id = validate_skill_id(skill_id)
    with skill_store_lock(exclusive=True):
        state = _load_state()
        state_row = _complete_state_row(state.get(skill_id), enabled=False)
        final_enabled = state[skill_id]["enabled"] if skill_id in state else False
        with pinned_skill_folder(skill_id, create=True) as pinned:
            state[skill_id] = state_row
            _save_state(state)
            pinned.verify_identity()
            result = _write_bundle_unlocked(
                pinned,
                version,
                skill_md=skill_md,
                manifest=manifest,
                scripts=scripts,
                resources=resources,
                references=references,
                extra_files=extra_files,
            )
            pinned.verify_identity()
            state_row["enabled"] = final_enabled
            _save_state(state)
            return result


def _write_bundle_unlocked(
    folder: PinnedSkillFolder,
    version: str,
    *,
    skill_md: str,
    manifest: dict,
    scripts: dict[str, str] | None = None,
    resources: dict[str, str] | None = None,
    references: dict[str, str] | None = None,
    extra_files: dict[str, str | bytes] | None = None,
) -> tuple[Path, str]:
    skill_id = folder.skill_id
    scripts = scripts or {}
    resources = resources or {}
    references = references or {}
    extra_files = extra_files or {}

    # Write SKILL.md first (caller may have already written via _write_skill_folder)
    if skill_md:
        write_pinned_skill_text(folder, "SKILL.md", skill_md)

    # Validate script entries via staging before committing to the real folder.
    # The manifest is DERIVED from scripts/ — we validate the derived dict so callers
    # that pass an explicit manifest still get interpreter/network checks on their scripts.
    if scripts:
        staging = folder.path.parent / f".staging-{skill_id}-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            staging.mkdir(parents=True)
            current_skill_md = read_pinned_skill_text(folder, "SKILL.md")
            if current_skill_md is not None:
                atomic_write_text(staging / "SKILL.md", current_skill_md)
            scripts_staging = staging / "scripts"
            for rel, content in scripts.items():
                tgt = _resolve_in_tree(staging, f"scripts/{rel}")
                # Additional jail: script must land inside scripts/ subdirectory specifically.
                scripts_base = Path(os.path.realpath(scripts_staging))
                if scripts_base not in tgt.parents and tgt != scripts_base:
                    raise ValidationError(f"script path '{rel}' escapes the scripts/ directory")
                tgt.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(tgt, content)
            # Validate interpreter / network / entry presence using the derived manifest.
            derived = _derive_manifest_from_folder(staging)
            problems = validate_manifest(derived, staging)
            if problems:
                raise ValidationError("invalid bundle manifest: " + "; ".join(problems))
            # Write scripts to the real folder
            for rel, content in scripts.items():
                write_pinned_skill_text(folder, f"scripts/{rel}", content)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    for rel, content in resources.items():
        write_pinned_skill_text(folder, f"resources/{rel}", content)
    for rel, content in references.items():
        write_pinned_skill_text(folder, f"references/{rel}", content)
    for rel, content in (extra_files or {}).items():
        if rel in ("SKILL.md", "manifest.json"):
            continue
        if isinstance(content, bytes):
            write_pinned_skill_bytes(folder, rel, content)
        else:
            write_pinned_skill_text(folder, rel, content)

    folder.verify_identity()
    sha = compute_pinned_bundle_sha(folder)
    return folder.path, sha


def _derive_manifest_from_folder(folder: Path) -> dict:
    """Build the manifest dict by scanning scripts/ recursively — the single source of truth.

    id = filename stem (slug-safe), interpreter = interpreter_for(name),
    entry = "scripts/<rel>", timeout_s = 30, network = False.
    Returns {"scripts": []} when no scripts/ dir exists."""
    scripts_dir = folder / "scripts"
    if not scripts_dir.is_dir():
        return {"scripts": []}
    scripts = []
    for p in sorted(scripts_dir.rglob("*"), key=lambda x: x.relative_to(scripts_dir).as_posix()):
        if not p.is_file():
            continue
        rel = p.relative_to(scripts_dir).as_posix()
        stem = p.stem  # filename without extension — used as the script id
        scripts.append(
            {
                "id": stem,
                "interpreter": interpreter_for(p.name),
                "entry": f"scripts/{rel}",
                "timeout_s": MAX_TIMEOUT_S // 20,  # 30 s default
                "network": False,
            }
        )
    return {"scripts": scripts}


def read_bundle(skill_id: str, version: str) -> dict:
    """Read a skill folder back as ``{skill_md, manifest, files: {rel: text}}``.

    ``manifest`` is ALWAYS derived from the scripts/ folder — any on-disk
    manifest.json is silently ignored (never parsed, never raised on).
    ``files`` excludes SKILL.md and manifest.json.
    The version argument is accepted for back-compat and ignored.
    Raises NotFoundError if the folder has no SKILL.md."""
    with skill_store_lock(exclusive=False):
        try:
            with pinned_skill_folder(skill_id) as pinned:
                return read_pinned_bundle(pinned)
        except FileNotFoundError as exc:
            raise NotFoundError(f"skill folder '{skill_id}' not found") from exc


def read_pinned_bundle(folder: PinnedSkillFolder) -> dict:
    """Read one immutable-inode bundle snapshot from an already pinned skill folder."""
    snapshot = pinned_skill_tree(folder)
    skill_md_bytes = snapshot.get("SKILL.md")
    if skill_md_bytes is None:
        raise NotFoundError(f"skill folder '{folder.skill_id}' not found")
    skill_md = skill_md_bytes.decode("utf-8")
    manifest = _derive_manifest_from_names(snapshot)
    files: dict[str, str] = {}
    binary_files: list[str] = []
    _EXCLUDED = {"SKILL.md", "manifest.json"}
    for rel, content in sorted(snapshot.items()):
        if rel in _EXCLUDED:
            continue
        try:
            files[rel] = content.decode("utf-8")
        except UnicodeDecodeError:
            binary_files.append(rel)
    folder.verify_identity()
    return {
        "skill_md": skill_md,
        "manifest": manifest,
        "files": files,
        "binary_files": binary_files,
    }


def _derive_manifest_from_names(files: dict[str, bytes]) -> dict:
    scripts = []
    for rel in sorted(files):
        if not rel.startswith("scripts/"):
            continue
        path = Path(rel)
        scripts.append(
            {
                "id": path.stem,
                "interpreter": interpreter_for(path.name),
                "entry": rel,
                "timeout_s": MAX_TIMEOUT_S // 20,
                "network": False,
            }
        )
    return {"scripts": scripts}


def read_bundle_file(folder: Path, rel: str) -> str | None:
    """Read ONE file's text from a skill folder, jailed. Returns None if missing/escaped."""
    with skill_store_lock(exclusive=False):
        if folder.parent == SKILLS_DIR and folder.name:
            try:
                with pinned_skill_folder(folder.name) as pinned:
                    text = read_pinned_skill_text(pinned, rel)
                    pinned.verify_identity()
                    return text
            except (FileNotFoundError, ValidationError, UnicodeDecodeError):
                return None
        try:
            if folder.is_symlink():
                raise ValidationError("bundle folder must not be a symlink")
            target = _resolve_in_tree(folder, rel)
        except ValidationError:
            return None
        if not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except OSError:
            return None
