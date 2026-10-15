"""skill_files — file-level I/O helpers for the folder-first skill layout.

Each skill lives in DATA_DIR/skills/<skill_id>/ containing:
  SKILL.md          — Agent Skills frontmatter + Markdown instructions
  agents/openai.yaml — optional OpenAI UI, dependency, and invocation metadata
  scripts/          — runnable scripts; language inferred by extension or shebang
  references/       — docs read on demand (progressive disclosure)
  assets/           — static files preserved verbatim

ONW preserves the portable Agent Skills fields and the documented Claude Code
frontmatter extensions.  Claude arguments are rendered only when a skill is invoked.
Tool-list fields — the experimental standard ``allowed-tools`` and Claude Code's
``disallowed-tools`` — are DROPPED on read and never written: a skill carries no tool list,
and the rendered SKILL.md is what the model reads, so a list there reads as a tool policy
that ONW never enforces (ADR ``2026-09-26-skills-carry-no-allowed-tools``).

This module owns:
  - SKILL.md parse/build/validate (Agent Skills spec compliance)
  - agents/openai.yaml parse/build helpers
  - INTERPRETER_BY_EXT map + interpreter_for()
  - Path jail helper (_resolve_in_folder) used by the runner and the read-file tool
  - read_folder_file() — read one jailed file from a skill folder
  - sha computation over a scripts/ dir

Layering: ``db`` → ``core`` only. yaml/json are fine in db. No services import.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml

from backend.core.errors import ValidationError

# ── SKILL.md (standard Agent-Skill format) ────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---[ \t]*\n?(.*)$", re.DOTALL)


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into ``(frontmatter dict, markdown body)``.
    No/invalid frontmatter → ``({}, text)``. Never raises."""
    if not text:
        return {}, ""
    m = _FRONTMATTER_RE.match(text.lstrip("﻿"))
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, m.group(2).lstrip("\n")
    if not isinstance(meta, dict):
        meta = {}
    return meta, m.group(2).lstrip("\n")


def build_skill_md(record: dict) -> str:
    """Render portable Agent Skills fields plus documented Claude Code extensions."""
    fm: dict = {"name": record.get("name", ""), "description": record.get("description", "")}
    if record.get("license"):
        fm["license"] = record["license"]
    if record.get("compatibility"):
        fm["compatibility"] = record["compatibility"]
    metadata = {
        str(key): str(value)
        for key, value in (record.get("metadata") or {}).items()
        if isinstance(key, str) and isinstance(value, (str, int, float))
    }
    if metadata:
        fm["metadata"] = metadata

    claude = clean_claude_extensions(record.get("claude"))
    for record_key, frontmatter_key in _CLAUDE_RECORD_TO_FRONTMATTER.items():
        if record_key in claude:
            fm[frontmatter_key] = claude[record_key]

    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = (record.get("body") or "").strip()
    return f"---\n{front}\n---\n\n{body}\n"


_CLAUDE_RECORD_TO_FRONTMATTER = {
    "when_to_use": "when_to_use",
    "argument_hint": "argument-hint",
    "arguments": "arguments",
    "disable_model_invocation": "disable-model-invocation",
    "user_invocable": "user-invocable",
    "model": "model",
    "effort": "effort",
    "context": "context",
    "agent": "agent",
    "background": "background",
    "hooks": "hooks",
    "paths": "paths",
    "shell": "shell",
}
_CLAUDE_FRONTMATTER_TO_RECORD = {value: key for key, value in _CLAUDE_RECORD_TO_FRONTMATTER.items()}
_CLAUDE_STRING_FIELDS = frozenset(
    {"when_to_use", "argument_hint", "model", "effort", "context", "agent", "shell"}
)
_CLAUDE_BOOL_FIELDS = frozenset({"disable_model_invocation", "user_invocable", "background"})
_CLAUDE_LIST_FIELDS = frozenset({"arguments", "paths"})


def _string_list(value: object) -> list[str]:
    """Normalize a YAML string/list while preserving parenthesized tool patterns."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value.strip():
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if depth == 0 and (char.isspace() or char == ","):
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(char)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def clean_claude_extensions(value: object) -> dict:
    """Return the typed, allowlisted Claude Code extension record."""
    if not isinstance(value, dict):
        return {}
    clean: dict = {}
    for key in _CLAUDE_STRING_FIELDS:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            clean[key] = raw.strip()
    for key in _CLAUDE_BOOL_FIELDS:
        raw = value.get(key)
        if isinstance(raw, bool):
            clean[key] = raw
    for key in _CLAUDE_LIST_FIELDS:
        if key in value:
            items = _string_list(value.get(key))
            if items:
                clean[key] = items
    hooks = value.get("hooks")
    if isinstance(hooks, dict) and hooks:
        clean["hooks"] = hooks
    return clean


def validate_claude_extensions(value: object, *, frontmatter: bool = False) -> list[str]:
    """Reject malformed Claude invocation controls instead of silently enabling them."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["Claude Code metadata must be a map"]
    problems: list[str] = []
    for record_key, frontmatter_key in _CLAUDE_RECORD_TO_FRONTMATTER.items():
        key = frontmatter_key if frontmatter else record_key
        if key not in value:
            continue
        raw = value[key]
        if record_key in _CLAUDE_STRING_FIELDS and not isinstance(raw, str):
            problems.append(f"'{key}' must be a string")
        elif record_key in _CLAUDE_BOOL_FIELDS and not isinstance(raw, bool):
            problems.append(f"'{key}' must be a boolean")
        elif record_key in _CLAUDE_LIST_FIELDS and not (
            isinstance(raw, str)
            or (isinstance(raw, list) and all(isinstance(item, str) for item in raw))
        ):
            problems.append(f"'{key}' must be a string or list of strings")
        elif record_key == "hooks" and not isinstance(raw, dict):
            problems.append(f"'{key}' must be a map")
    return problems


def parse_claude_extensions(meta: dict) -> dict:
    """Read documented Claude Code-only keys from parsed ``SKILL.md`` frontmatter."""
    raw = {
        record_key: meta[frontmatter_key]
        for frontmatter_key, record_key in _CLAUDE_FRONTMATTER_TO_RECORD.items()
        if frontmatter_key in meta
    }
    return clean_claude_extensions(raw)


def allows_model_invocation(claude_metadata: object) -> bool:
    """Claude Code's model-invocation control (default true)."""
    if claude_metadata is None:
        return True
    if not isinstance(claude_metadata, dict):
        return False
    value = claude_metadata.get("disable_model_invocation")
    if value is not None and not isinstance(value, bool):
        return False
    return value is not True


def allows_user_invocation(claude_metadata: object) -> bool:
    """Claude Code's direct-user invocation control (default true)."""
    if claude_metadata is None:
        return True
    if not isinstance(claude_metadata, dict):
        return False
    value = claude_metadata.get("user_invocable")
    if value is not None and not isinstance(value, bool):
        return False
    return value is not False


def parse_openai_yaml(text: str | None) -> dict:
    """Parse optional ``agents/openai.yaml``; malformed metadata fails open like Codex."""
    if not text:
        return {}
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        key: value
        for key, value in parsed.items()
        if key in {"interface", "dependencies", "policy"} and isinstance(value, dict)
    }


def validate_openai_metadata(value: object) -> list[str]:
    """Validate the OpenAI sidecar shape at import/update security boundaries."""
    if value is None or value == {}:
        return []
    if not isinstance(value, dict):
        return ["OpenAI metadata must be a map"]
    problems: list[str] = []
    for key in ("interface", "dependencies", "policy"):
        if key in value and not isinstance(value[key], dict):
            problems.append(f"OpenAI '{key}' must be a map")
    policy = value.get("policy")
    if isinstance(policy, dict) and "allow_implicit_invocation" in policy:
        if not isinstance(policy["allow_implicit_invocation"], bool):
            problems.append("OpenAI 'policy.allow_implicit_invocation' must be a boolean")
    return problems


def validate_openai_yaml(text: str | None) -> list[str]:
    """Parse and validate ``agents/openai.yaml`` without silently dropping bad controls."""
    if not text:
        return []
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"agents/openai.yaml is invalid YAML: {exc}"]
    return validate_openai_metadata(parsed)


def build_openai_yaml(metadata: object) -> str:
    """Serialize only the OpenAI skill-metadata sections understood by Codex."""
    if not isinstance(metadata, dict):
        return ""
    clean = {
        key: value
        for key, value in metadata.items()
        if key in {"interface", "dependencies", "policy"} and isinstance(value, dict)
    }
    if not clean:
        return ""
    return yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)


def allows_implicit_invocation(openai_metadata: object) -> bool:
    """Return the standard OpenAI policy value (default true)."""
    if openai_metadata is None:
        return True
    if not isinstance(openai_metadata, dict):
        return False
    policy = openai_metadata.get("policy")
    if policy is None:
        return True
    if not isinstance(policy, dict):
        return False
    value = policy.get("allow_implicit_invocation")
    if value is None:
        return True
    return value if isinstance(value, bool) else False


def _user_metadata(meta: dict) -> dict:
    """Return the ``metadata`` map with ``onw-*`` keys stripped out (user-visible metadata)."""
    md = meta.get("metadata") or {}
    if not isinstance(md, dict):
        return {}
    return {k: v for k, v in md.items() if not k.startswith("onw-") and isinstance(v, str)}


_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_DESCRIPTION_LEN = 1024
_MAX_COMPATIBILITY_LEN = 500
_MAX_NAME_LEN = 64


def validate_skill_md_frontmatter(meta: dict) -> list[str]:
    """Validate parsed SKILL.md frontmatter against the Agent Skills spec.
    Returns problems list; empty = compliant. Pure; never raises."""
    problems: list[str] = []
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        problems.append("frontmatter 'name' is required")
    else:
        if len(name) > _MAX_NAME_LEN:
            problems.append(f"'name' exceeds {_MAX_NAME_LEN} characters")
        if not _SKILL_NAME_RE.match(name):
            problems.append(
                "'name' must be lowercase letters/numbers/hyphens with no leading, trailing, "
                "or consecutive hyphens"
            )
    desc = meta.get("description")
    if not isinstance(desc, str) or not desc.strip():
        problems.append("frontmatter 'description' is required and must be non-empty")
    elif len(desc) > _MAX_DESCRIPTION_LEN:
        problems.append(f"'description' exceeds {_MAX_DESCRIPTION_LEN} characters")
    license_ = meta.get("license")
    if license_ is not None and not isinstance(license_, str):
        problems.append("'license' must be a string")
    compat = meta.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str):
            problems.append("'compatibility' must be a string")
        elif len(compat) > _MAX_COMPATIBILITY_LEN:
            problems.append(f"'compatibility' exceeds {_MAX_COMPATIBILITY_LEN} characters")
    md = meta.get("metadata")
    if md is not None and not (
        isinstance(md, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in md.items())
    ):
        problems.append("'metadata' must be a map of string keys to string values")
    problems.extend(validate_claude_extensions(meta, frontmatter=True))
    return problems


_RECOMMENDED_MAX_BODY_LINES = 500
_RECOMMENDED_MAX_BODY_CHARS = 20_000


def body_advisories(body: str) -> list[str]:
    """Non-blocking size advisories (Agent Skills recommendations). Never raises."""
    out: list[str] = []
    if not isinstance(body, str) or not body:
        return out
    lines = body.count("\n") + 1
    if lines > _RECOMMENDED_MAX_BODY_LINES:
        out.append(
            f"SKILL.md body is {lines} lines (the standard recommends < {_RECOMMENDED_MAX_BODY_LINES}). "
            "Consider moving detail into references/*.md and loading it on demand."
        )
    if len(body) > _RECOMMENDED_MAX_BODY_CHARS:
        out.append(
            f"SKILL.md body is ~{len(body) // 4} tokens (the standard recommends < 5000). "
            "Consider splitting it into references/*.md (progressive disclosure)."
        )
    return out


# ── Interpreter resolution ────────────────────────────────────────────────────

INTERPRETER_BY_EXT = {
    ".py": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
}

SUPPORTED_INTERPRETERS = {"python", "bash", "node"}


def interpreter_for(rel_path: str) -> str:
    """Interpreter for a script path by extension; returns 'python' as default."""
    for ext, interp in INTERPRETER_BY_EXT.items():
        if rel_path.endswith(ext):
            return interp
    return "python"


# ── Path jail (resolve inside skill folder, reject traversal) ─────────────────


def _resolve_in_folder(folder: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``folder`` and assert it stays INSIDE it.
    Raises ValidationError on traversal or symlink-escape."""
    base = Path(os.path.realpath(folder))
    candidate = Path(os.path.realpath(folder / rel))
    if candidate != base and base not in candidate.parents:
        raise ValidationError(f"path '{rel}' resolves outside the skill folder")
    return candidate


def read_folder_file(folder: Path, rel: str) -> str | None:
    """Read ONE file's text from a skill folder, jailed (path-traversal rejected).
    Returns None if the path escapes or the file is missing. Read-only."""
    try:
        target = _resolve_in_folder(folder, rel)
    except ValidationError:
        return None
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return None


# ── scripts/ sha (for the enable-pin and tamper check) ────────────────────────


def compute_scripts_sha(folder: Path) -> str:
    """Deterministic sha256 over every file in ``folder/scripts/`` (sorted relative path +
    bytes). Returns empty string if scripts/ does not exist."""
    scripts_dir = folder / "scripts"
    if not scripts_dir.is_dir():
        return ""
    h = hashlib.sha256()
    files = sorted(
        (p for p in scripts_dir.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(folder).as_posix(),
    )
    for p in files:
        rel = p.relative_to(folder).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()
