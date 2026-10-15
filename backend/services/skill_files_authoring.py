"""Supporting files a skill author (Lucius, through the skill-creator) may put in a skill folder.

The Agent Skills guidance (agentskills.io) keeps ``SKILL.md`` to what the model needs on every
run and moves the rest out: detail into ``references/`` (read at a named step) and fixed output
templates into ``assets/``. The built-in report skills work that way, so a skill Lucius authors
must be able to as well (owner, 2026-10-05) — ``create_skill`` / ``update_skill`` accept
``files``, validated here and written through the skill store's jailed bundle writer.

Deliberately narrow: TEXT files under ``references/`` or ``assets/`` only, a short list of
types, size and count caps, no hidden or traversing paths. Scripts never come this way —
``save_code_as_skill`` has its own review and a separate enable switch. A value of ``None``
removes that file.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from backend.core.errors import ValidationError

ALLOWED_DIRS = ("references", "assets")
ALLOWED_SUFFIXES = (".md", ".html", ".json", ".csv", ".txt")
MAX_FILES = 10
MAX_FILE_BYTES = 100_000  # read_skill_file returns at most this much of one file


def file_problems(files: object) -> list[str]:
    """Human-readable problems with a proposed ``files`` map; empty means valid."""
    if files is None:
        return []
    if not isinstance(files, dict):
        return ["files must be an object of {relative path: text content (or null to remove)}"]
    if len(files) > MAX_FILES:
        return [f"at most {MAX_FILES} files per change"]
    problems = []
    for raw, content in files.items():
        problem = _path_problem(raw)
        if problem:
            problems.append(problem)
        elif content is not None and not isinstance(content, str):
            problems.append(f"{raw}: content must be text (or null to remove the file)")
        elif isinstance(content, str) and len(content.encode("utf-8")) > MAX_FILE_BYTES:
            problems.append(f"{raw}: over {MAX_FILE_BYTES // 1000} KB — split it")
    return problems


def _path_problem(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return "every file needs a relative path"
    path = PurePosixPath(raw)
    parts = path.parts
    if path.is_absolute() or "\\" in raw or any(p in ("", ".", "..") for p in parts):
        return f"{raw}: use a plain relative path like references/guide.md"
    if len(parts) < 2 or parts[0] not in ALLOWED_DIRS:
        return f"{raw}: files go under {' or '.join(d + '/' for d in ALLOWED_DIRS)}"
    if any(p.startswith(".") for p in parts):
        return f"{raw}: hidden files are not allowed"
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return f"{raw}: only {', '.join(ALLOWED_SUFFIXES)} files"
    return ""


def write_files(skill_id: str, files: dict | None) -> None:
    """Write (or, for ``None``, remove) the validated files in the skill's folder."""
    if not files:
        return
    problems = file_problems(files)
    if problems:
        raise ValidationError("; ".join(problems))
    from backend.db.skill_store import (
        pinned_skill_folder,
        remove_pinned_skill_file,
        skill_store_lock,
    )
    from backend.services.skill_bundles import write_bundle

    writes = {path: text for path, text in files.items() if text is not None}
    if writes:
        write_bundle(skill_id, "live", skill_md="", manifest={"scripts": []}, extra_files=writes)
    removals = [path for path, text in files.items() if text is None]
    if removals:
        with skill_store_lock(exclusive=True), pinned_skill_folder(skill_id) as pinned:
            for path in removals:
                remove_pinned_skill_file(pinned, path)
            pinned.verify_identity()


def describe(files: dict | None) -> list[dict]:
    """What the confirm card shows: each path and whether it is written or removed."""
    return [
        {"path": path, "action": "remove" if text is None else "write", "chars": len(text or "")}
        for path, text in sorted((files or {}).items())
    ]
