"""
REST API for the skill registry (P2-SK1 — gallery surface).

Thin routes over ``SkillStore`` (storage) + ``skills_service`` (validation). A new
``SkillStore()`` is constructed per request — the store is
stateless (reads disk on every call), so this carries no cost and keeps the path
test-monkeypatchable. All writes funnel through ``SkillStore``'s own
``fire_write_hook`` + ``atomic_write_text`` calls (core.io's primitives, called
directly — SkillStore is a plain file store, not a JsonStore subclass) →
checkpoint pre-write hook + atomic write (skills.json IS checkpointed).

These REST create/update/delete are the **direct** (non-LLM) authoring path the
gallery editor uses — the human is already in the loop via the UI form, so they
write immediately (unlike the ``create_skill``/``update_skill`` *tools*, which
return a proposed_change for Lucius's confirm flow). Both ultimately call
``SkillStore`` and both are checkpoint-covered.

    GET    /api/skills           list (optional ?author= &enabled=). [] when none.
    GET    /api/skills/{id}      single; 404 if absent.
    POST   /api/skills           create; 422 on missing name; 400 on invalid instructions.
    PUT    /api/skills/{id}      patch top-level fields; re-validate; 404 if absent.
    DELETE /api/skills/{id}      delete; 404 if absent.

The skill **run** surface is the chat panel: the gallery sends the immutable skill id in
``ChatRequest.skills``. The host resolves and persists those direct invocations before the first
provider call; there is no synthesized "run the skill" message or preliminary ``activate_skill`` call.
The normal chat SSE path still renders confirm cards for mutations, so no separate /run route lives
here.
"""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from backend.core.errors import ValidationError
from backend.db.skill_store import SkillStore, validate_skill_id
from backend.services import skills as skills_service

router = APIRouter()

# Upload safety caps (zip-bomb / zip-slip defense for POST /api/skills/upload).
_MAX_ZIP_BYTES = 5 * 1024 * 1024  # 5 MB compressed input cap
_MAX_TOTAL_UNCOMPRESSED = 8 * 1024 * 1024  # 8 MB total uncompressed cap
_MAX_ENTRY_BYTES = 2 * 1024 * 1024  # 2 MB per file
_MAX_ENTRIES = 200


def _store() -> SkillStore:
    return SkillStore()


def _validated_skill_id(skill_id: str) -> str:
    try:
        return validate_skill_id(skill_id)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message or str(exc)) from exc


# ── Bodies ─────────────────────────────────────────────────────────────────────


class SkillCreate(BaseModel):
    name: str
    description: str
    body: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = {}
    claude: dict = {}
    openai: dict = {}
    enabled: bool = True


class SkillUpdate(BaseModel):
    changes: dict


# ── Collection ───────────────────────────────────────────────────────────────


@router.get("/api/skills")
def list_skills(
    author: Optional[str] = None,
    enabled: Optional[bool] = None,
):
    """List skills for the gallery. Returns ``[]`` when there are none."""
    rows = _store().query_skills(enabled=enabled, author=author)
    known = skills_service.tool_names()
    return [skills_service.to_list_row(s, known_tools=known) for s in rows]


@router.post("/api/skills")
def create_skill(body: SkillCreate):
    candidate = body.model_dump()
    problems = skills_service.validate(candidate, require_standard_fields=True)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    return {"ok": True, "skill": _store().create({**candidate, "author": "user"})}


# ── Starters (Browse-a-starter flow) ───────────────────────────────────────────
# NOTE: these static paths MUST be registered BEFORE the `/{skill_id}` param routes
# below, or "starters" would be captured as a skill_id (404). FastAPI matches in order.


@router.get("/api/skills/starters")
def list_starters():
    """Built-in starter skills NOT yet present in the library (so the Browse modal only
    offers ones the user can add). Returns ``[{id,name,description,category}]``."""
    from backend.db.skill_store import STARTER_SKILLS

    existing = {s.get("id") for s in _store().all()}
    return [
        {
            "id": s["id"],
            "name": s.get("name", ""),
            "description": s.get("description", ""),
        }
        for s in STARTER_SKILLS
        if s.get("id") not in existing
    ]


@router.post("/api/skills/starters/{starter_id}")
def add_starter(starter_id: str):
    """Seed ONE built-in starter by id (same path as ``seed_starters`` — author=builtin).
    Idempotent: if already present, returns the existing record. 404 for an unknown id."""
    from backend.db.skill_store import STARTER_SKILLS

    starter = next((s for s in STARTER_SKILLS if s.get("id") == starter_id), None)
    if starter is None:
        raise HTTPException(404, "Unknown starter skill")
    store = _store()
    existing = store.find_by_id(starter_id)
    # The managed skill-creator installer must run even when the skill record already exists:
    # it repairs/refreshes the body and reference bundle under the same exclusive transaction.
    if starter_id == "skill-creator":
        from backend.services.skill_creator_bundle import ensure_skill_creator_installed

        ensure_skill_creator_installed()
        created = store.find_by_id(starter_id)
        if created is None:
            raise HTTPException(500, "Managed skill-creator install did not persist the skill")
        return {"ok": True, "skill": created, **({"already": True} if existing else {})}
    if existing is not None:
        return {"ok": True, "skill": existing, "already": True}
    store.create({**starter, "author": "builtin"})
    from backend.services.starter_skill_files import write_starter_files

    write_starter_files(starter_id)
    return {"ok": True, "skill": store.find_by_id(starter_id)}


# ── Upload a skill bundle (.zip) ───────────────────────────────────────────────


def _safe_unzip(raw: bytes) -> dict[str, bytes]:
    """Read a .zip into ``{name: bytes}`` with zip-slip / zip-bomb / symlink defenses.

    Rejects (HTTP 400): absolute paths, ``..`` traversal, backslashes, symlink entries,
    too many entries, an oversize entry, or an oversize total. Directory entries are skipped.
    The names are validated here; the bundle WRITE path (``write_bundle``) additionally
    realpath-jails every script entry inside the bundle dir — defense in depth.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Not a valid .zip file")

    infos = zf.infolist()
    if len(infos) > _MAX_ENTRIES:
        raise HTTPException(400, f"Too many files in the zip (>{_MAX_ENTRIES})")

    out: dict[str, bytes] = {}
    total = 0
    for zi in infos:
        name = zi.filename
        if zi.is_dir():
            continue
        # Symlink reject (the high 16 bits of external_attr hold the unix mode).
        if stat.S_ISLNK((zi.external_attr >> 16) & 0xFFFF):
            raise HTTPException(400, f"Zip contains a symlink ('{name}') — refused")
        # Path-traversal / absolute-path reject.
        path = PurePosixPath(name)
        canonical_name = path.as_posix()
        if (
            not name
            or "\\" in name
            or name.startswith("/")
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or canonical_name != name
        ):
            raise HTTPException(400, f"Unsafe path in zip: '{name}'")
        if canonical_name in out:
            raise HTTPException(400, f"Duplicate path in zip: '{canonical_name}'")
        if zi.file_size > _MAX_ENTRY_BYTES:
            raise HTTPException(400, f"File '{name}' is too large (>{_MAX_ENTRY_BYTES} bytes)")
        total += zi.file_size
        if total > _MAX_TOTAL_UNCOMPRESSED:
            raise HTTPException(400, "Zip contents exceed the total size limit")
        with zf.open(zi) as fh:
            out[canonical_name] = fh.read(_MAX_ENTRY_BYTES + 1)
    return out


def _strip_common_wrapper(files: dict[str, bytes]) -> dict[str, bytes]:
    """If every entry lives under ONE top-level directory (the Agent Skills ``skill-name/``
    wrapper — the canonical way skills are distributed), strip that prefix so ``SKILL.md`` sits at
    the root and the rest of the importer works unchanged. No-op when files are already at the root
    or span multiple top-level dirs. Closes audit G1 (import unwrap)."""
    if not files:
        return files
    tops = {k.split("/", 1)[0] for k in files}
    if len(tops) == 1 and all("/" in k for k in files):
        prefix = next(iter(tops)) + "/"
        return {k[len(prefix) :]: v for k, v in files.items()}
    return files


def _sub_files(files: dict[str, bytes], prefix: str) -> dict[str, str]:
    """The ``{relative-to-prefix: text}`` map for zip entries under ``prefix/`` (e.g.
    'scripts/' → {'foo.py': '...'}). Skips the bare directory entry."""
    out: dict[str, str] = {}
    for path, content in files.items():
        if not path.startswith(prefix) or path == prefix:
            continue
        try:
            out[path[len(prefix) :]] = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, f"'{path}' must be UTF-8 text") from exc
    return out


@router.post("/api/skills/upload")
async def upload_skill(file: UploadFile = File(...)):
    """Install a skill from a **standard Agent-Skill** ``.zip``. ALWAYS created DISABLED.

    Format (pure-standard ``SKILL.md`` — the sole required file):
      - ``SKILL.md`` (REQUIRED): standard frontmatter (``name`` + ``description``) + body.
        The experimental standard ``allowed-tools`` field is dropped — a skill carries no
        tool list. Documented Claude Code frontmatter is preserved; ONW honors only invocation visibility
        and argument substitution, while Claude-only execution settings remain inert here.
      - ``agents/openai.yaml`` (optional): standard OpenAI interface, dependency, and
        implicit-invocation policy metadata.
      - ``references/*.md`` (optional): docs shown in the tree + loaded on demand.
      - ``scripts/*.py`` (optional): runnable code — a skill is "scripted" simply because
        it carries a ``scripts/`` dir. ``manifest.json`` optional — each ``.py`` is
        AUTO-DECLARED as a sandboxed entry.
    Born disabled (when it has scripts, code-exec stays off until enabled). Returns the skill.
    """
    raw = await file.read(_MAX_ZIP_BYTES + 1)
    if len(raw) > _MAX_ZIP_BYTES:
        raise HTTPException(400, f"Upload exceeds the {_MAX_ZIP_BYTES}-byte limit")
    if not raw:
        raise HTTPException(400, "Empty upload")

    files = _strip_common_wrapper(_safe_unzip(raw))  # G1: unwrap a `skill-name/` directory
    if "SKILL.md" not in files:
        raise HTTPException(
            400, "Zip must contain a SKILL.md (standard frontmatter + instructions)"
        )

    from backend.services.skill_bundles import (
        body_advisories,
        build_skill_md,
        parse_claude_extensions,
        parse_openai_yaml,
        parse_skill_md,
        validate_openai_yaml,
        write_bundle,
    )

    try:
        skill_md = files["SKILL.md"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "SKILL.md must be UTF-8 text") from exc
    meta, body = parse_skill_md(skill_md)

    from backend.services.skill_bundles import validate_skill_md_frontmatter

    frontmatter_problems = validate_skill_md_frontmatter(meta)
    if frontmatter_problems:
        raise HTTPException(400, "Invalid SKILL.md: " + "; ".join(frontmatter_problems))

    name = str(meta["name"]).strip()

    references = _sub_files(files, "references/")
    scripts = _sub_files(files, "scripts/")
    try:
        openai_text = files.get("agents/openai.yaml", b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "agents/openai.yaml must be UTF-8 text") from exc
    openai_problems = validate_openai_yaml(openai_text)
    if openai_problems:
        raise HTTPException(400, "Invalid agents/openai.yaml: " + "; ".join(openai_problems))
    openai = parse_openai_yaml(openai_text)
    # Old ONW-only sidecars are intentionally discarded during canonical import.
    _RESERVED = {"SKILL.md", "onw.yaml", "manifest.json", "agents/openai.yaml"}
    extra_files = {
        k: v
        for k, v in files.items()
        if k not in _RESERVED and not k.startswith("references/") and not k.startswith("scripts/")
    }
    has_scripts = bool(scripts)

    from backend.db.skill_files import _user_metadata

    candidate = {
        "name": name,
        "description": str(meta.get("description") or ""),
        "license": str(meta.get("license") or ""),
        "compatibility": str(meta.get("compatibility") or ""),
        "metadata": _user_metadata(meta),
        "body": body,
        "claude": parse_claude_extensions(meta),
        "openai": openai,
        "enabled": False,  # born disabled
    }
    problems = skills_service.validate(candidate, require_standard_fields=True)
    if problems:
        raise HTTPException(400, "Invalid skill: " + "; ".join(problems))

    store = _store()
    warnings = body_advisories(body)

    # Create the record first; this writes a canonical, portable SKILL.md.
    created = store.create(
        {**candidate, **({"has_scripts": True} if has_scripts else {}), "author": "user"}
    )
    sid = created["id"]

    # If only SKILL.md — no extra files — we're done
    if not references and not scripts and not extra_files:
        return {"ok": True, "skill": store.find_by_id(sid) or created, "warnings": warnings}

    # Write extra files (scripts/, references/, assets/, LICENSE.txt, …) into the folder
    # Manifest is fully derived from scripts/ — any manifest.json in the zip is ignored.
    manifest: dict = {"scripts": [], "references": sorted(references)}

    try:
        write_bundle(
            sid,
            "live",
            skill_md=build_skill_md(candidate),
            manifest=manifest,
            scripts=scripts or None,
            references=references or None,
            extra_files=extra_files or None,
        )
    except Exception as exc:  # noqa: BLE001 — roll back the dangling record on any bundle failure
        store.delete(sid)
        raise HTTPException(400, f"Invalid skill bundle: {exc}")
    updated = store.find_by_id(sid) or created
    return {"ok": True, "skill": updated, "warnings": warnings}


# A copy-paste starting point using only the portable Agent Skills contract.
_TEMPLATE_SKILL_MD = """\
---
name: my-skill
description: One line on what this does and when Lucius should use it.
---

# My Skill

Step-by-step instructions for Lucius. Read the user's request from the conversation.

1. Call get_spending_summary for the period requested by the user.
2. Summarize the result and report it.

<!-- Add references/*.md for docs Lucius loads on demand. To bundle a script add scripts/*.py
     (each .py is auto-declared as a sandboxed entry). -->
"""


@router.get("/api/skills/template")
def skill_template():
    """Download a portable Agent Skills starter ``.zip``. Auth-gated."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Nested under the skill-name dir (spec shape) — matches what export produces + upload unwraps.
        zf.writestr("my-skill/SKILL.md", _TEMPLATE_SKILL_MD)
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="skill-template.zip"'},
    )


def _build_skill_zip(skill: dict) -> bytes:
    """Export a skill as a spec-shaped .zip. All entries nested under ``<name>/``.
    Reads files verbatim from the skill folder (SKILL.md, scripts/*, etc.).
    Application bookkeeping is not embedded in the portable definition; the script
    manifest is fully derived from scripts/ on import."""
    from backend.db.skill_store import pinned_skill_folder, pinned_skill_tree, skill_store_lock
    from backend.services.skill_bundles import build_skill_md

    root = f"{skill.get('name') or skill.get('id') or 'skill'}/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        skill_md_content: str | bytes = ""
        extra_files: dict[str, bytes] = {}
        try:
            with skill_store_lock(exclusive=False):
                with pinned_skill_folder(skill["id"]) as pinned:
                    snapshot = pinned_skill_tree(pinned)
            skill_md_content = snapshot.pop("SKILL.md", b"")
            snapshot.pop("manifest.json", None)
            extra_files = snapshot
        except Exception:
            pass
        if not skill_md_content:
            skill_md_content = build_skill_md(skill)
        zf.writestr(root + "SKILL.md", skill_md_content)
        for rel, content in extra_files.items():
            zf.writestr(root + rel, content)
    return buf.getvalue()


# ── Bulk bundles (eager prefetch — one request, no per-skill loading spinner) ──
# MUST precede the `/{skill_id}` param routes below, or "bundles" is captured as a skill_id.


@router.get("/api/skills/bundles")
def all_skill_bundles():
    """Every skill's full virtual file tree keyed by skill id — so the UI can load all skills'
    structure in ONE request on mount instead of a lazy per-skill fetch (no loading spinner when
    a skill is tapped). Same per-skill shape as ``GET /api/skills/{id}/bundle``. Read-only."""
    return {s["id"]: _bundle_view(s) for s in _store().all()}


# ── Single ───────────────────────────────────────────────────────────────────


@router.get("/api/skills/{skill_id}")
def get_skill(skill_id: str):
    skill_id = _validated_skill_id(skill_id)
    s = _store().find_by_id(skill_id)
    if s is None:
        raise HTTPException(404, "Skill not found")
    known = skills_service.tool_names()
    return skills_service.to_list_row(s, known_tools=known)


@router.put("/api/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate):
    skill_id = _validated_skill_id(skill_id)
    existing = _store().find_by_id(skill_id)
    if existing is None:
        raise HTTPException(404, "Skill not found")
    changes = dict(body.changes)
    merged = {**existing, **changes}
    problems = skills_service.validate(merged, require_standard_fields=True)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    s = _store().update_skill(skill_id, changes)
    if s is None:
        raise HTTPException(404, "Skill not found")
    return {"ok": True, "skill": s}


def _bundle_view(s: dict) -> dict:
    """Build a skill's FULL file tree: ``{skill_md, manifest, files}``.

    SKILL.md and agents/openai.yaml are read from the portable skill folder.
    No application-specific definition file is synthesized or included.
    Falls back to a synthetic SKILL.md if the folder is absent.
    """
    from backend.services.skill_bundles import build_skill_md, read_bundle

    sid = s["id"]
    skill_md = ""
    manifest: dict = {}
    files: dict[str, str] = {}
    binary_files: list[str] = []
    try:
        bundle = read_bundle(sid, "live")
        skill_md = bundle.get("skill_md") or ""
        manifest = bundle.get("manifest") or {}
        files = dict(bundle.get("files") or {})
        binary_files = list(bundle.get("binary_files") or [])
    except Exception:  # noqa: BLE001 — folder absent → fall back to synthetic view
        pass
    if not skill_md:
        skill_md = build_skill_md(s)
    return {
        "skill_md": skill_md,
        "manifest": manifest,
        "files": files,
        "binary_files": binary_files,
    }


@router.get("/api/skills/{skill_id}/bundle")
def get_skill_bundle(skill_id: str):
    """One skill's full virtual file tree (see ``_bundle_view``). 404 only if the skill is absent.
    Read-only; auth-gated."""
    skill_id = _validated_skill_id(skill_id)
    s = _store().find_by_id(skill_id)
    if s is None:
        raise HTTPException(404, "Skill not found")
    return _bundle_view(s)


@router.get("/api/skills/{skill_id}/export")
def export_skill(skill_id: str):
    """Download a skill as a standard SKILL.md ``.zip`` (the interchange/template form), so users
    can share or back up a skill — and see exactly what the format looks like. Works for any
    skill incl. builtins (read-only, safe). 404 if absent. Auth-gated."""
    skill_id = _validated_skill_id(skill_id)
    s = _store().find_by_id(skill_id)
    if s is None:
        raise HTTPException(404, "Skill not found")
    data = _build_skill_zip(s)
    slug = (
        "".join(c if c.isalnum() else "-" for c in (s.get("name") or "skill").lower()).strip("-")[
            :40
        ]
        or "skill"
    )
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str):
    skill_id = _validated_skill_id(skill_id)
    if not _store().delete(skill_id):
        raise HTTPException(404, "Skill not found")
    return {"ok": True}
