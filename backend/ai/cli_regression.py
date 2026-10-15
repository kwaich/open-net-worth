"""What a headless CLI looks like from outside — versions, feature flags, the tool list it
reports — and the diff of that against a reviewed baseline.

Codex and Claude Code self-update. Twice on 2026-10-03 a silent default change broke Lucius
(Codex's connected ``apps`` leaked the operator's Gmail into turns; ``code_mode_host`` routed
tool use around our text protocol). Both were found by the owner, in use. This module is the
observable half of the regression net — pure functions over what the CLI prints — so the live
suite (``tests/cli_live``) and its unit tests share one definition of "a default changed that
matters". The model-calling half is :mod:`backend.ai.cli_probe`. Doc: ``docs/ai/CLI_REGRESSION.md``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backend.ai.models.codex_app_server import child_env as codex_child_env

# A feature whose NAME says it can add tools, change how tool use is routed, or reach outside
# the host's own tool surface. Matching on the name is deliberate: the flag that breaks us next
# will not be one we have heard of, but it will probably be called something like these.
RISKY_FEATURE = re.compile(
    r"apps|code_mode|mcp|plugin|connector|tool_search|deferred|tool_suggest|browser|computer_use"
    r"|multi_agent|skill|hooks",
    re.IGNORECASE,
)

# Flags our managed CODEX_HOME turns off. Whatever else a snapshot says, these two are an
# invariant of the transport (backend/ai/models/codex_home.py), not a baseline to drift from.
CODEX_REQUIRED_OFF = ("apps", "code_mode_host")

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_VERSION_TIMEOUT_SECONDS = 15
_FEATURES_TIMEOUT_SECONDS = 30
_INIT_TIMEOUT_SECONDS = 60


@dataclass
class SnapshotDiff:
    """``failures`` are changes that can break tool use or leak data; ``notes`` are the rest."""

    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


# ── versions ──────────────────────────────────────────────────────────────────


def cli_version(binary: str) -> str:
    """``0.160.0`` from ``<binary> --version``, or ``""`` when it cannot be run or read."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    match = _VERSION.search(out.stdout or "") if out.returncode == 0 else None
    return match.group(0) if match else ""


def version_changed(last_tested: str, installed: str) -> bool:
    """Is there an installed version, and is it not the one last proven good?"""
    return bool(installed) and installed != (last_tested or "")


# ── Codex feature flags ───────────────────────────────────────────────────────


def parse_features(text: str) -> dict[str, dict]:
    """``codex features list`` → ``{name: {"stage": str, "enabled": bool}}``.

    Rows are ``<name>  <stage, possibly two words>  <true|false>``; the stage may contain a
    space ("under development"), so the name is the first column and the flag the last.
    """
    out: dict[str, dict] = {}
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 3 or cols[-1] not in ("true", "false"):
            continue
        out[cols[0]] = {"stage": " ".join(cols[1:-1]), "enabled": cols[-1] == "true"}
    return out


def codex_features(binary: str) -> dict[str, dict]:
    """The flags Codex resolves under OUR managed ``CODEX_HOME`` — what a Lucius turn gets."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [binary, "features", "list"],
        capture_output=True,
        text=True,
        timeout=_FEATURES_TIMEOUT_SECONDS,
        env=codex_child_env(""),
    )
    features = parse_features(out.stdout)
    if out.returncode != 0 or not features:
        raise RuntimeError(f"`codex features list` gave nothing usable (exit {out.returncode})")
    return features


def required_off_violations(features: dict[str, dict]) -> list[str]:
    """Flags the transport REQUIRES off that are on — or missing, which is a rename."""
    problems = []
    for name in CODEX_REQUIRED_OFF:
        row = features.get(name)
        if row is None:
            problems.append(f"{name}: no longer listed — renamed or removed; re-verify isolation")
        elif row["enabled"]:
            problems.append(f"{name}: ON under our managed config (must be off)")
    return problems


def diff_features(baseline: dict[str, dict], current: dict[str, dict]) -> SnapshotDiff:
    """What changed in ``codex features list`` since the reviewed baseline.

    A risky-named feature that is newly ON, or newly listed ON, is a failure; anything else
    that moved is a note — worth a look at the next ``make cli-smoke-update`` review, not worth
    paging for.
    """
    diff = SnapshotDiff()
    for name in sorted(set(baseline) | set(current)):
        before, after = baseline.get(name), current.get(name)
        if before == after:
            continue
        risky = bool(RISKY_FEATURE.search(name))
        if after is None:
            diff.notes.append(f"{name}: no longer listed (was {_describe(before)})")
        elif before is None:
            line = f"{name}: new feature, {_describe(after)}"
            (diff.failures if risky and after["enabled"] else diff.notes).append(line)
        else:
            line = f"{name}: {_describe(before)} -> {_describe(after)}"
            turned_on = after["enabled"] and not before["enabled"]
            (diff.failures if risky and turned_on else diff.notes).append(line)
    return diff


def _describe(row: dict | None) -> str:
    if row is None:
        return "absent"
    return f"{row['stage']}={'on' if row['enabled'] else 'off'}"


# ── Claude Code init event ────────────────────────────────────────────────────

_CLAUDE_INIT_KEYS = ("tools", "mcp_servers", "plugins", "agents", "skills")


def claude_init_view(event: dict) -> dict:
    """The parts of a ``system/init`` event worth pinning, reduced to sorted name lists.

    ``tools`` and ``mcp_servers`` are the ones that matter — under our flags both are empty.
    Plugins, agents and skills ride along as notes: they are the CLI's own built-ins and
    change release to release without touching what the model can call.
    """

    def names(value: object) -> list[str]:
        items = value if isinstance(value, list) else []
        return sorted(str(i.get("name") or i) if isinstance(i, dict) else str(i) for i in items)

    return {key: names(event.get(key)) for key in _CLAUDE_INIT_KEYS}


def diff_claude_init(baseline: dict, current: dict) -> SnapshotDiff:
    """``tools`` / ``mcp_servers`` growing past the baseline is a failure; the rest are notes."""
    diff = SnapshotDiff()
    for key in _CLAUDE_INIT_KEYS:
        before, after = set(baseline.get(key) or []), set(current.get(key) or [])
        if before == after:
            continue
        added, removed = sorted(after - before), sorted(before - after)
        line = f"{key}: +{added} -{removed}"
        (diff.failures if key in ("tools", "mcp_servers") and added else diff.notes).append(line)
    return diff


async def capture_claude_init(provider) -> dict:
    """The CLI's ``system/init`` event under the exact flags a chat turn uses.

    The process is killed the moment init arrives, so the model call it was about to make is
    abandoned rather than billed — init is written before any request goes out.
    """
    from backend.ai.models.claude_cli_provider import _kill

    proc, workdir = await provider._spawn(provider._argv("", "", effort="low"), "ping")
    try:
        assert proc.stdout is not None
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=_INIT_TIMEOUT_SECONDS)
            if not line:
                raise RuntimeError("claude exited before emitting a system/init event")
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                return event
    finally:
        if proc.returncode is None:
            await _kill(proc)
        shutil.rmtree(workdir, ignore_errors=True)


# ── snapshots ─────────────────────────────────────────────────────────────────


def load_snapshot(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_snapshot(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
