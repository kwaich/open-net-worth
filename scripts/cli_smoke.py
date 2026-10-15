#!/usr/bin/env python3
"""Run the live CLI regression suite (tests/cli_live) and schedule it on this Mac.

    cli_smoke.py                 run now, notify on failure          (make cli-smoke)
    cli_smoke.py --update        refresh the reviewed snapshots      (make cli-smoke-update)
    cli_smoke.py --scheduled     run only if a CLI changed version or 24h passed (launchd)
    cli_smoke.py install|uninstall   the launchd job                 (make cli-smoke-install)

It spends the owner's own `claude` / `codex` logins (cents) and never touches the real book:
the suite's data dirs are throwaway (tests/conftest.py) and this script's own state — last
tested CLI versions, the last run's log — lives in ONW_CLI_SMOKE_STATE (default
~/.cache/open-net-worth-cli-smoke). Doc: docs/ai/CLI_REGRESSION.md.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing backend.core.paths creates the data dirs; point them at scratch FIRST so a
# bare `import backend...` here can never create or open ~/.open-net-worth.
_SCRATCH = tempfile.mkdtemp(prefix="onw-cli-smoke-")
for _var, _sub in (("DATA_DIR", "data"), ("CACHE_DIR", "cache"), ("LOGS_DIR", "logs")):
    os.environ[_var] = f"{_SCRATCH}/{_sub}"
os.environ["ONW_QA_MODE"] = "1"

from backend.ai.cli_regression import cli_version, version_changed  # noqa: E402
from backend.core.process_tree import kill_tree, spawn_kwargs  # noqa: E402

LABEL = "ai.opennetworth.cli-smoke"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
STATE_DIR = Path(
    os.environ.get("ONW_CLI_SMOKE_STATE") or Path.home() / ".cache" / "open-net-worth-cli-smoke"
)
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "last-run.log"
CLIS = ("claude", "codex")
RUN_TIMEOUT_SECONDS = 1200
SCHEDULE_INTERVAL_SECONDS = 3600
STALE_AFTER = timedelta(hours=24)


def _python() -> str:
    venv = ROOT / ".venv" / "bin" / "python3"
    return str(venv if venv.exists() else sys.executable)


def _installed() -> dict[str, str]:
    return {name: cli_version(path) for name in CLIS if (path := shutil.which(name))}


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _due(state: dict, installed: dict[str, str]) -> str:
    """Why a scheduled run should happen now, or "" when it should not."""
    for name, version in installed.items():
        if version_changed((state.get("tested") or {}).get(name, ""), version):
            return f"{name} is {version}, last tested {(state.get('tested') or {}).get(name) or 'never'}"
    try:
        last = datetime.fromisoformat(state["last_run"])
    except (KeyError, ValueError):
        return "never run"
    return "24h since the last run" if datetime.now(timezone.utc) - last > STALE_AFTER else ""


def _notify(title: str, message: str) -> None:
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, timeout=15)  # noqa: S603,S607


def _run_suite(update: bool) -> tuple[int, str]:
    """pytest over tests/cli_live; the whole process TREE is killed at the timeout."""
    env = {**os.environ, "ONW_CLI_LIVE": "1", "ONW_QA_MODE": "1"}
    if update:
        env["ONW_CLI_SNAPSHOT_UPDATE"] = "1"
    argv = [_python(), "-m", "pytest", "tests/cli_live"]
    argv += ["-v", "-rfEw", "--no-header", "-p", "no:cacheprovider"]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(  # noqa: S603
            argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, **spawn_kwargs()
        )
        try:
            code = proc.wait(timeout=RUN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            proc.wait()
            log.write(f"\nTIMED OUT after {RUN_TIMEOUT_SECONDS}s — process tree killed\n")
            code = 124
    return code, LOG_FILE.read_text(encoding="utf-8")


def run(update: bool, scheduled: bool) -> int:
    installed, state = _installed(), _load_state()
    if scheduled:
        why = _due(state, installed)
        if not why:
            print("cli-smoke: nothing due")
            return 0
        print(f"cli-smoke: running — {why}")
    code, output = _run_suite(update)
    print("\n".join(output.splitlines()[-40:]))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_state({"tested": installed, "last_run": now, "last_exit": code, "updated": update})
    if code != 0 and not update:
        versions = ", ".join(f"{n} {v}" for n, v in installed.items()) or "no CLI found"
        _notify("Open Net Worth: CLI regression failed", f"{versions}. Log: {LOG_FILE}")
        print(f"cli-smoke: FAILED (exit {code}) — full log {LOG_FILE}", file=sys.stderr)
    return code


def install() -> int:
    path_dirs = [Path.home() / ".local" / "bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]
    plist = {
        "Label": LABEL,
        "ProgramArguments": [_python(), str(ROOT / "scripts" / "cli_smoke.py"), "--scheduled"],
        "WorkingDirectory": str(ROOT),
        "StartInterval": SCHEDULE_INTERVAL_SECONDS,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": ":".join(map(str, [*path_dirs, "/bin"]))},
        "StandardOutPath": str(STATE_DIR / "launchd.log"),
        "StandardErrorPath": str(STATE_DIR / "launchd.log"),
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    target = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{target}/{LABEL}"], check=False)  # noqa: S603,S607
    subprocess.run(["launchctl", "bootstrap", target, str(PLIST)], check=True)  # noqa: S603,S607
    print(f"installed {PLIST} — checks hourly, runs the suite when a CLI changes version or daily")
    return 0


def uninstall() -> int:
    subprocess.run(  # noqa: S603,S607
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=False
    )
    PLIST.unlink(missing_ok=True)
    print(f"removed {PLIST}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "command", nargs="?", choices=("run", "install", "uninstall"), default="run"
    )
    parser.add_argument("--update", action="store_true", help="refresh snapshots, no diffing")
    parser.add_argument("--scheduled", action="store_true", help="only run when due")
    args = parser.parse_args()
    try:
        if args.command == "install":
            return install()
        if args.command == "uninstall":
            return uninstall()
        return run(args.update, args.scheduled)
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
