"""runner.py — the bare-subprocess sandbox backend (script-bundling-skills S2).

``run_sandboxed(bundle_dir, version, entry, input_json, limits)`` runs a bundle's
declared python script as an isolated SUBPROCESS and returns a typed result. It is
the MVP "Option A" backend from the RFC §2.6: pure stdlib, works on every self-host
(incl. macOS), strong against secret/data exfiltration + resource exhaustion *by
construction*, weaker against syscall-level escape on a bare POSIX host (no seccomp
on macOS, no hard network deny — documented below; the nsjail/container backends in
S5 close that gap).

ISOLATION MECHANISM (maps to the RFC §2.1 threat model):

  * T1/T2 (secret + data exfiltration). The child runs with a CONSTRUCTED env
    (``_build_env``) — a tiny PATH/HOME/TMPDIR/locale only, NEVER ``os.environ`` —
    so ``ANTHROPIC_API_KEY``/``AUTH_*``/any ``*_API_KEY`` is absent from the child.
    The cwd is a fresh throwaway scratch dir and ``DATA_DIR`` is never passed or
    referenced. The script gets ONLY the JSON its caller hands it (``input.json``
    via the ``SKILL_INPUT`` env path).

    **CORRECTION (2026-08-28) — this is NOT a filesystem jail.** This paragraph used
    to end "so ``auth.json`` / ``transactions.json`` are unreachable by construction
    (file-not-found, not a late denial)". That is false, and the false version is the
    dangerous one because it invites callers to treat sandbox output as non-sensitive.
    Not passing ``DATA_DIR`` in the env hides nothing: the path is deterministic and
    public (``~/.open-net-worth/data/onw.db``), and ``HOME=scratch`` is defeated by
    ``pwd.getpwuid()``, which reads ``/etc/passwd`` rather than the env. There is no
    chroot, no mount namespace, no seccomp and no uid drop anywhere in this module —
    grep it. The child is a full CPython process running as the SAME OS user as the
    backend, with an unrestricted view of the filesystem, so it can open the database
    (and since Wave 5D that database holds every connector credential in plaintext).

    What T1/T2 genuinely buy: no secrets inherited through the ENVIRONMENT, and no
    accidental cwd-relative access to app data. That is worth having and is what the
    env construction actually delivers. Real containment needs the deferred
    container/nsjail backend, or a platform sandbox profile (Linux: mount namespace +
    seccomp/landlock; macOS: ``sandbox-exec``). Until one ships, treat anything this
    module returns as potentially carrying on-disk secrets. See
    ``docs/security/2026-08-25-pentest-audit.md`` FINDING 6.

  * T3 (network). Network is OFF by intent. On Linux, if ``unshare`` is available we
    run the child in a fresh, EMPTY network namespace (``--net``) so socket+connect
    truly fail. On macOS / where ``unshare`` is unavailable, a bare subprocess CANNOT
    hard-deny network at the syscall level — this is documented honestly and surfaced
    to the operator (the UI in S4 + ``network_hard_denied`` on the result). The MVP
    never grants network to a script anyway; this is defence in depth.

  * T4 (resource exhaustion). A ``preexec_fn`` (``_apply_rlimits``) sets
    ``RLIMIT_CPU`` (SIGXCPU then SIGKILL), ``RLIMIT_AS`` (address space), ``RLIMIT_NPROC``
    (no fork/spawn — fork bomb denied), ``RLIMIT_NOFILE``, and ``RLIMIT_FSIZE`` (scratch
    write cap). The parent also enforces a WALL-CLOCK timeout: the child is launched in
    its own session/process group (``start_new_session=True``) and on overrun the parent
    ``os.killpg(SIGKILL)``s the whole group so a stuck child + any descendants die.

  * T5/T6 (host escape / cross-run poisoning). The bundle dir's write bits are
    stripped for the run (best-effort read-only — ``_make_bundle_readonly``; HONESTLY
    a same-uid script could chmod them back, so the HARD read-only bundle is a
    container/nsjail-backend property, S5 — here the load-bearing tamper control is
    the pre-exec sha verify, T7); the scratch dir is the only writable space and is
    deleted in ``finally``; ``RLIMIT_NPROC`` blocks spawning a helper.

  * T7 (tamper-then-run). ``_verify_bundle_sha`` recomputes the version dir's
    content-addressed sha and refuses to exec on a mismatch — before any code runs.

  * T8 (prompt injection via stdout). The caller (the S3 ``run_script`` tool) treats
    stdout as UNTRUSTED model-facing data; here we only size-cap it and never ``eval``.

Limits come from ``SandboxLimits`` and are CLAMPED to ``HARD_CEILINGS`` the runner owns
— a caller can only *tighten*, never raise above the ceiling (mirrors the tighten-only
tier rule). A script that exceeds CPU/mem/time/output is KILLED and yields a typed
error result, never a partial.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from backend.core import paths as _paths
from backend.core.process_tree import kill_tree, spawn_kwargs

_WINDOWS = os.name == "nt"

if _WINDOWS:
    from backend.sandbox import win_job as _win_job

    _resource = None
else:
    import resource as _resource  # POSIX-only: the T4 caps are setrlimit in the child

logger = logging.getLogger(__name__)


# ── Limits (caller-supplied, clamped to the runner's hard ceilings) ───────────


@dataclasses.dataclass(frozen=True)
class SandboxLimits:
    """Per-run resource limits. A caller can only *tighten* — every field is clamped
    to ``HARD_CEILINGS`` in ``_clamp`` before enforcement (the manifest's ``timeout_s``
    flows in here, RFC §2.4)."""

    cpu_seconds: int = 10  # RLIMIT_CPU soft (a CPU-hog is SIGXCPU'd, then SIGKILL'd)
    wall_seconds: float = 15.0  # parent-enforced wall clock (process-group SIGKILL)
    memory_mb: int = 512  # RLIMIT_AS (address space)
    max_output_bytes: int = 1_000_000  # stdout cap (1 MB; oversize → killed + error)
    max_stderr_bytes: int = 64_000  # stderr tail kept for diagnostics
    max_open_files: int = 64  # RLIMIT_NOFILE
    max_file_size_mb: int = 16  # RLIMIT_FSIZE (scratch write cap)


# The ceilings the runner OWNS. A caller's limit above these is clamped down; it can
# never raise the sandbox's blast radius. (Tune once a real script-skill exists, RFC §8.4.)
HARD_CEILINGS = SandboxLimits(
    cpu_seconds=30,
    wall_seconds=60.0,
    memory_mb=1024,
    max_output_bytes=5_000_000,
    max_stderr_bytes=256_000,
    max_open_files=256,
    max_file_size_mb=64,
)

DEFAULT_LIMITS = SandboxLimits()


def _clamp(limits: SandboxLimits) -> SandboxLimits:
    """Return ``limits`` with every field clamped to ``HARD_CEILINGS`` (and floored at
    a sane minimum so a degenerate 0 can't disable a cap). Tighten-only."""
    c = HARD_CEILINGS
    return SandboxLimits(
        cpu_seconds=max(1, min(limits.cpu_seconds, c.cpu_seconds)),
        wall_seconds=max(0.5, min(limits.wall_seconds, c.wall_seconds)),
        memory_mb=max(32, min(limits.memory_mb, c.memory_mb)),
        max_output_bytes=max(1024, min(limits.max_output_bytes, c.max_output_bytes)),
        max_stderr_bytes=max(512, min(limits.max_stderr_bytes, c.max_stderr_bytes)),
        max_open_files=max(8, min(limits.max_open_files, c.max_open_files)),
        max_file_size_mb=max(1, min(limits.max_file_size_mb, c.max_file_size_mb)),
    )


# ── Result + error ────────────────────────────────────────────────────────────


class SandboxError(Exception):
    """A pre-exec refusal — the runner declined to launch the child at all (bad bundle,
    sha mismatch, missing entry, no POSIX ``resource``). Distinct from a run that LAUNCHED
    and then failed/timed-out (which is reported on ``SandboxResult``, never raised)."""


def _require_supported_host() -> None:
    if _paths.DESKTOP_MODE:
        raise SandboxError(
            "Sandbox execution is unavailable in the desktop app until "
            "child-process containment is supported."
        )


@dataclasses.dataclass(frozen=True)
class SandboxResult:
    """The outcome of one sandboxed run. ``run_sandboxed`` NEVER raises for a run that
    launched — a non-zero exit / timeout / oversize / non-JSON stdout all degrade to a
    populated result with ``ok=False`` + a reason (mirrors ``import_pdf``'s best-effort
    posture, RFC §2.5). ``data`` is the parsed stdout JSON when ``ok`` and parseable."""

    ok: bool
    exit_code: int | None  # None = killed before/without an exit (timeout, signal)
    data: object | None  # parsed JSON from stdout when ok + valid JSON, else None
    stdout: str  # raw stdout (size-capped), UTF-8 (lossy)
    stderr_tail: str  # tail of stderr for diagnostics (size-capped)
    duration_ms: int
    timed_out: bool = False
    killed: bool = False  # SIGKILL'd by the parent (timeout or output overrun)
    output_truncated: bool = False  # stdout hit the cap
    error: str | None = None  # human-readable reason when not ok
    # Honesty flag (RFC §2.3/§2.6): True only when network was hard-denied at the
    # syscall level (Linux netns). False on macOS/bare subprocess — surfaced so the UI
    # can tell the operator network is best-effort here.
    network_hard_denied: bool = False

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Integrity (content-addressed sha — same algorithm as services.skill_bundles) ─
#
# The sandbox MUST verify the bundle sha before exec (T7), but it must NOT import
# ``services`` (that layer reaches every secret store — the whole point of the leaf
# contract). So the deterministic content-address is RE-IMPLEMENTED here byte-for-byte
# identical to ``services.skill_bundles.compute_bundle_sha``: sha256 over every file in
# the version dir as (sorted relative posix path, NUL, bytes, NUL). A bundle written by
# S1 verifies here, and a sha computed here equals the one S1 stored. (A tiny, vetted
# duplication is the correct trade for keeping secrets off the runner's import path.)


def _assert_no_symlinks(version_dir_path: Path) -> None:
    """Refuse a bundle tree that contains a SYMLINK (MED-3). The hash/run path must
    not trust S1's author-time symlink-escape check blindly: a symlink in the on-disk
    bundle (``data -> /etc/passwd``) would otherwise have its target's content folded
    into the sha + read off the HOST at hash time. We reject any symlink — file OR dir
    — anywhere under the version dir BEFORE hashing/exec, so the runner reads only the
    bundle's own bytes. Raises ``SandboxError``."""
    for p in version_dir_path.rglob("*"):
        # is_symlink() does NOT follow the link, so a dangling/escaping link is caught.
        if p.is_symlink():
            rel = p.relative_to(version_dir_path).as_posix()
            raise SandboxError(
                f"bundle contains a symlink '{rel}' — refusing to run (a symlink could "
                "read a host file at hash time)"
            )


def _compute_bundle_sha(version_dir_path: Path) -> str:
    h = hashlib.sha256()
    files = sorted(
        (p for p in version_dir_path.rglob("*") if p.is_file() and not p.is_symlink()),
        key=lambda p: p.relative_to(version_dir_path).as_posix(),
    )
    for p in files:
        rel = p.relative_to(version_dir_path).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _verify_bundle_sha(version_dir_path: Path, expected_sha: str) -> bool:
    if not version_dir_path.is_dir():
        return False
    return _compute_bundle_sha(version_dir_path) == expected_sha


# ── Entry resolution (jailed inside the version dir; symlink-escape rejected) ──


def _resolve_entry(version_dir_path: Path, entry: str) -> Path:
    """Resolve the declared ``entry`` against the version dir and assert it stays INSIDE
    (realpath'd, so ``..`` and symlink-escape are rejected) — re-checked at run time per
    RFC §1.3 even though S1 checked it at author time. Raises ``SandboxError``."""
    base = Path(os.path.realpath(version_dir_path))
    candidate = Path(os.path.realpath(version_dir_path / entry))
    if candidate != base and base not in candidate.parents:
        raise SandboxError(f"entry '{entry}' resolves outside the bundle dir")
    if not candidate.is_file():
        raise SandboxError(f"entry '{entry}' does not exist in the bundle")
    return candidate


# ── Read-only bundle (best-effort, restored after the run) ────────────────────


def _make_bundle_readonly(version_dir_path: Path) -> list[tuple[Path, int]]:
    """Strip the WRITE bits off every file + dir in the bundle for the duration of
    the run (T6: a script can't `open(its-own-script, 'w')` casually), returning the
    saved modes so ``_restore_bundle_modes`` can put them back in ``finally``.

    HONEST LIMITATION (RFC §2.6 Option A on a bare subprocess): the child runs as the
    SAME uid as the parent, so a determined script could `os.chmod` the bundle back
    and then write. This chmod RAISES THE BAR (a plain write fails) but is NOT a hard
    guarantee — the hard read-only bundle is a property of the container/nsjail backend
    (S5). The load-bearing tamper control on this backend is the pre-exec ``bundle_sha``
    verify (T7), which a chmod-and-rewrite within one run cannot defeat for the NEXT run.
    """
    saved: list[tuple[Path, int]] = []
    try:
        entries = [version_dir_path] + list(version_dir_path.rglob("*"))
    except OSError:  # pragma: no cover
        return saved
    for p in entries:
        try:
            mode = p.stat().st_mode
            saved.append((p, mode))
            # Clear all write bits (owner/group/other); keep read + (dir) execute.
            os.chmod(p, mode & ~0o222)
        except OSError:  # pragma: no cover - best-effort
            continue
    return saved


def _restore_bundle_modes(saved: list[tuple[Path, int]]) -> None:
    # Restore in reverse (deepest first) so a dir is writable when we touch its kids.
    for p, mode in reversed(saved):
        try:
            os.chmod(p, mode)
        except OSError:  # pragma: no cover - best-effort
            continue


# ── Child environment (constructed — NEVER os.environ) ────────────────────────


def _build_env(scratch: Path, input_path: Path) -> dict[str, str]:
    """Construct the child's ENTIRE environment from scratch. This is the primary
    defense against env-var secret leakage (T1): ``os.environ`` (which holds
    ``ANTHROPIC_API_KEY``/``AUTH_*``/connector creds) is NEVER inherited. Only a minimal
    PATH (so the pinned interpreter resolves its own stdlib), HOME/TMPDIR pinned to the
    throwaway scratch, a deterministic locale, and the ``SKILL_INPUT`` path."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows") if _WINDOWS else ""
    return {
        # Windows needs SystemRoot for Winsock/crypto and the system DLL directory on PATH;
        # both are fixed OS locations, not user data.
        "PATH": str(Path(system_root) / "System32") if _WINDOWS else "/usr/bin:/bin",
        **({"SYSTEMROOT": system_root, "USERPROFILE": str(scratch)} if _WINDOWS else {}),
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "TEMP": str(scratch),
        "TMP": str(scratch),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        # Don't let the child read user/site config or write .pyc into the (ro) bundle.
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
        "SKILL_INPUT": str(input_path),
    }


# ── Resource caps (preexec, POSIX setrlimit) ──────────────────────────────────


def _make_preexec(limits: SandboxLimits):
    """Build the ``preexec_fn`` that runs in the FORKED CHILD before exec: a fresh
    process group + the rlimit caps. Returns None on Windows, which has no fork — the same
    caps are a Job Object there (``win_job``). Keep this function tiny and import-free —
    it runs post-fork."""
    if _WINDOWS:
        return None

    cpu = limits.cpu_seconds
    mem_bytes = limits.memory_mb * 1024 * 1024
    nofile = limits.max_open_files
    fsize = limits.max_file_size_mb * 1024 * 1024

    def _preexec() -> None:  # pragma: no cover - runs in the child, not measurable here
        # (The fresh session/process group is created by Popen(start_new_session=True),
        # which calls setsid() in the child BEFORE this preexec — so the parent can
        # SIGKILL the whole tree on timeout. Calling setsid() again here would EPERM.)
        # CPU seconds: soft = cpu (SIGXCPU), hard = cpu+2 (SIGKILL) — a tight CPU-hog dies.
        _resource.setrlimit(_resource.RLIMIT_CPU, (cpu, cpu + 2))
        # Address space (virtual memory) — a malloc balloon hits MemoryError / is killed.
        try:
            _resource.setrlimit(_resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except (ValueError, OSError):
            pass  # some platforms (macOS) cap AS oddly; RLIMIT_DATA below is the backup
        try:
            _resource.setrlimit(_resource.RLIMIT_DATA, (mem_bytes, mem_bytes))
        except (ValueError, OSError):
            pass
        # No fork / spawn — a fork bomb is denied (NPROC counts processes for the uid;
        # keep a tiny headroom so the exec itself succeeds, then further forks fail).
        try:
            _resource.setrlimit(_resource.RLIMIT_NPROC, (0, 0))
        except (ValueError, OSError):
            pass
        _resource.setrlimit(_resource.RLIMIT_NOFILE, (nofile, nofile))
        # Max bytes any single file the child writes — the scratch write cap (a runaway
        # disk-filler is stopped with SIGXFSZ).
        _resource.setrlimit(_resource.RLIMIT_FSIZE, (fsize, fsize))
        # Disable core dumps (no secret-bearing core in the scratch).
        try:
            _resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass

    return _preexec


# ── Linux network namespace (hard network deny when available) ────────────────


@functools.lru_cache(maxsize=1)
def _unshare_net_available() -> str | None:
    """Return the ``unshare`` path IFF ``unshare --net -r`` actually WORKS on this host,
    else ``None``.

    ``shutil.which("unshare")`` alone over-promises: some Linux hosts ship the binary but
    BLOCK unprivileged user-namespace creation (writing ``/proc/self/uid_map`` fails with
    EPERM) — e.g. GitHub Actions runners and restricted containers. There, prefixing the
    child with ``unshare --net -r`` makes ``unshare`` itself exit non-zero *before the
    script runs at all*, so every sandboxed launch fails. We instead PROBE once with a
    trivial ``true`` (the host's namespace capability is static for the process, so the
    verdict is cached) and only claim the hard network-namespace deny when the probe
    succeeds. Where it can't, the runner falls back to the same soft-deny posture macOS
    uses (``network_hard_denied=False``) so a script still RUNS under the no-net-config
    posture instead of erroring out.
    """
    if not sys.platform.startswith("linux"):
        return None
    unshare = shutil.which("unshare")
    if not unshare:
        return None
    try:
        probe = subprocess.run(
            [unshare, "--net", "-r", "true"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        probe = None
    if probe is not None and probe.returncode == 0:
        return unshare
    # Linux host that SHIPS unshare but can't create a user/net namespace (EPERM on
    # /proc/self/uid_map — restricted containers, CI runners). We fall back to the
    # macOS-style soft-deny so scripts still run; warn ONCE (lru_cache → single call)
    # so an operator who expected hard netns isolation can see the degradation in logs
    # rather than only in each result's network_hard_denied flag.
    logger.warning(
        "sandbox: `unshare --net -r` is present but non-functional on this host "
        "(no unprivileged user namespaces); network isolation degraded to soft-deny "
        "(network_hard_denied=False)."
    )
    return None


def _net_isolation_prefix() -> tuple[list[str], bool]:
    """Return ``(argv_prefix, hard_denied)``. On a Linux host where ``unshare --net -r``
    actually works, prefix the child with it so it runs in a fresh EMPTY network namespace
    — socket()/connect() then truly fail (T3 hard-denied). Elsewhere (macOS, no ``unshare``,
    or a host that blocks unprivileged user namespaces) return an empty prefix +
    ``hard_denied=False`` and rely on the no-net-config posture; the honest caveat is
    surfaced via ``network_hard_denied`` on the result.
    """
    unshare = _unshare_net_available()
    if unshare:
        # --net = new (empty) net namespace; -r maps current uid→root inside the user
        # ns so an unprivileged process may create the net ns (no setuid needed).
        return ([unshare, "--net", "-r"], True)
    return ([], False)


# ── Interpreter dispatch (python / bash / node — all in the SAME isolation) ───
# The Agent Skills standard leaves script languages implementation-dependent and names
# python, bash, and javascript as the common set. ONW runs all three through the IDENTICAL
# sandbox envelope (constructed env, no DATA_DIR, rlimits, network-off, output caps). python
# is always available (it is the interpreter running ONW); bash/node are resolved on the host
# and refused gracefully if absent. NOTE: RLIMIT_NPROC=0 (no fork) means a script runs as a
# single process — python/node work fully; bash works for builtins but cannot spawn external
# commands (cat/jq/…). That is the deliberate no-fork-bomb posture, documented for authors.
SANDBOX_INTERPRETERS = {"python", "bash", "node"}


def _read_shebang(script_path: Path) -> list[str] | None:
    """Return the shebang argv from the script's first line, or None if absent/invalid.
    Only the first line is read; the shebang must start with '#!'. The interpreter path
    is used as-is (no PATH lookup) — callers are responsible for validating the path."""
    try:
        first = script_path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].strip().split()
    return parts if parts else None


def _python_executable() -> str:
    """The interpreter the child runs. A Windows venv's ``python.exe`` is a redirector that
    starts the real interpreter as a SECOND process, which the one-process job cap would
    refuse; ``-I -S`` needs no site-packages, so the base interpreter runs it directly."""
    return getattr(sys, "_base_executable", sys.executable) if _WINDOWS else sys.executable


def _is_wsl_launcher(path: str) -> bool:
    """``C:\\Windows\\System32\\bash.exe`` starts a WSL VM, a different and unconfined host
    than the one this sandbox constrains."""
    return _WINDOWS and Path(path).parent.name.lower() in {"system32", "sysnative"}


def _interpreter_argv(interpreter: str, script_path: Path) -> list[str]:
    """Build the interpreter argv for an isolated run, or raise ``SandboxError`` if the
    interpreter is unsupported or its binary is not installed on the host.

    For named interpreters (python/bash/node) the canonical isolated flags are applied.
    For an empty/unknown interpreter the script's shebang line is honoured as a fallback
    (e.g. ``#!/usr/bin/env python3``). A missing shebang with an unknown interpreter raises
    SandboxError so the failure is explicit rather than silent.
    """
    interp = (interpreter or "").lower()
    if interp == "python":
        # -I isolated (ignore env-based switches + user site); -B no .pyc; -S no site customizations.
        return [_python_executable(), "-I", "-B", "-S", str(script_path)]
    if interp == "bash":
        bash = shutil.which("bash") or "/bin/bash"
        if not Path(bash).exists() or _is_wsl_launcher(bash):
            raise SandboxError("bash interpreter not found on this host")
        # --noprofile/--norc: no user config; --restricted ('r'): a tighter bash for untrusted
        # code (blocks cd, $PATH changes, absolute-path command names, and output redirection).
        return [bash, "--noprofile", "--norc", "--restricted", str(script_path)]
    if interp == "node":
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            raise SandboxError("node interpreter not found on this host")
        return [node, str(script_path)]
    # Unknown/empty interpreter — honour the script's shebang line.
    shebang = _read_shebang(script_path)
    if shebang:
        return shebang + [str(script_path)]
    if interp:
        raise SandboxError(
            f"unsupported interpreter '{interpreter}' (supported: {', '.join(sorted(SANDBOX_INTERPRETERS))})"
            " and no shebang found in the script"
        )
    raise SandboxError("no interpreter specified and no shebang found in the script")


# ── The public entrypoint ─────────────────────────────────────────────────────


def run_sandboxed(
    bundle_dir: Path | str,
    version: str,
    entry: str,
    input_json: object,
    *,
    bundle_sha: str = "",
    interpreter: str = "python",
    limits: SandboxLimits | None = None,
) -> SandboxResult:
    """Run a bundle's declared ``entry`` as an isolated subprocess under ``interpreter``
    (python | bash | node — all the same isolation envelope; see ``_interpreter_argv``).

    Args:
      bundle_dir: the bundle's per-skill dir (``SKILL_BUNDLES_DIR/<skill_id>``) OR a
        version dir directly; ``version`` selects ``bundle_dir/<version>`` when
        ``bundle_dir`` is the per-skill dir. (Both forms supported so a caller can pass
        either; the resolved version dir must contain ``entry``.)
      version: the immutable version name (a child dir of ``bundle_dir``); pass "" if
        ``bundle_dir`` is already the version dir.
      entry: the manifest-declared relative path to the script (e.g. ``scripts/x.py``),
        re-jailed inside the version dir at run time.
      input_json: JSON-serializable input — written to ``scratch/input.json`` and pointed
        at by ``SKILL_INPUT``. This is the ONLY data the script receives (RFC §2.5).
      bundle_sha: optional content-address to verify before exec (T7 tamper guard). When
        non-empty, a sha mismatch raises ``SandboxError`` before any code runs. When
        empty (the default), verification is skipped — the trust model is the sandbox
        isolation + the instance-level kill switches + the local user-data folder.
      limits: per-run resource limits (clamped to ``HARD_CEILINGS``); defaults applied.

    Returns a ``SandboxResult``. Raises ``SandboxError`` ONLY for a pre-exec refusal
    (non-POSIX host, bad/missing bundle, sha mismatch, out-of-tree entry). A run that
    launched then failed/timed-out is reported on the result with ``ok=False``.
    """
    _require_supported_host()

    bundle_dir = Path(bundle_dir)
    version_dir_path = (bundle_dir / version) if version else bundle_dir
    if not version_dir_path.is_dir():
        raise SandboxError(f"bundle version dir not found: {version_dir_path}")

    # MED-3: refuse a bundle tree containing a symlink BEFORE hashing it (so the sha
    # compute never reads a symlink target off the host).
    _assert_no_symlinks(version_dir_path)

    # T7: tamper guard — refuse to run a bundle whose bytes changed out-of-band.
    # Only active when the caller supplies a non-empty sha (explicit pin). Without a pin
    # the sandbox isolation + global kill switches are the trust boundary.
    if bundle_sha and not _verify_bundle_sha(version_dir_path, bundle_sha):
        raise SandboxError(
            f"bundle sha mismatch for {version_dir_path} — refusing to run (tampered "
            "or wrong version)"
        )

    script_path = _resolve_entry(version_dir_path, entry)
    eff = _clamp(limits or DEFAULT_LIMITS)

    scratch = Path(tempfile.mkdtemp(prefix="onw-sandbox-"))
    saved_modes = _make_bundle_readonly(version_dir_path)
    try:
        input_path = scratch / "input.json"
        try:
            input_path.write_text(json.dumps(input_json, ensure_ascii=False), encoding="utf-8")
        except (TypeError, ValueError) as e:
            raise SandboxError(f"input is not JSON-serializable: {e}")

        env = _build_env(scratch, input_path)
        net_prefix, net_hard_denied = _net_isolation_prefix()

        # Dispatch the requested interpreter (python | bash | node) through the IDENTICAL
        # isolation machinery. python is always available; bash/node refuse if not installed.
        argv = net_prefix + _interpreter_argv(interpreter, script_path)

        return _spawn_and_collect(
            argv=argv,
            env=env,
            cwd=scratch,
            input_path=input_path,
            limits=eff,
            net_hard_denied=net_hard_denied,
        )
    finally:
        _restore_bundle_modes(saved_modes)
        shutil.rmtree(scratch, ignore_errors=True)


def run_source(
    source: str,
    input_json: object,
    *,
    limits: SandboxLimits | None = None,
) -> SandboxResult:
    """Run model-authored ``source`` (ad-hoc code execution — RFC ad-hoc-code-execution)
    in the SAME isolated subprocess sandbox as ``run_sandboxed``.

    This is the ephemeral sibling of ``run_sandboxed``: instead of resolving an immutable,
    content-addressed bundle + verifying its ``bundle_sha`` (the tamper guard T7, which is
    moot here — we just wrote the bytes ourselves, there is no out-of-band tamper window),
    the source is materialized into a fresh throwaway dir and run through the IDENTICAL
    isolation machinery — constructed env (never ``os.environ``, T1), cwd-jailed scratch
    (no ``DATA_DIR`` in the child's ENV, T2 — note this is not the same as unreachable:
    see the T2 correction in this module's header),
    ``setrlimit`` CPU/mem/nproc/fsize caps (T4), process-group wall-clock SIGKILL,
    network-off namespace where available (T3), and bounded-incremental JSON-parsed stdout
    (untrusted, never eval'd, T8). The scratch dir is the only writable space and is deleted
    in ``finally``.

    Pure compute: the child gets ONLY ``input_json`` (written to ``scratch/input.json`` and
    pointed at by ``SKILL_INPUT``). Returns a ``SandboxResult``; a run that LAUNCHED never
    raises (it degrades to ``ok=False``). Raises ``SandboxError`` only for a pre-exec refusal
    (non-POSIX host, empty source, non-serializable input).
    """
    _require_supported_host()
    if not isinstance(source, str) or not source.strip():
        raise SandboxError("source must be a non-empty string")

    eff = _clamp(limits or DEFAULT_LIMITS)

    # scratch is the cwd + the only writable space; main.py + input.json live here, and the
    # whole dir is removed after the run, so there is no cross-run persistence to guard (T6).
    scratch = Path(tempfile.mkdtemp(prefix="onw-code-"))
    try:
        script_path = scratch / "main.py"
        script_path.write_text(source, encoding="utf-8")

        input_path = scratch / "input.json"
        try:
            input_path.write_text(json.dumps(input_json, ensure_ascii=False), encoding="utf-8")
        except (TypeError, ValueError) as e:
            raise SandboxError(f"input is not JSON-serializable: {e}")

        env = _build_env(scratch, input_path)
        net_prefix, net_hard_denied = _net_isolation_prefix()

        # Same pinned interpreter + isolated flags as the bundle path (RFC §3.1 / §8.1).
        argv = net_prefix + [_python_executable(), "-I", "-B", "-S", str(script_path)]

        return _spawn_and_collect(
            argv=argv,
            env=env,
            cwd=scratch,
            input_path=input_path,
            limits=eff,
            net_hard_denied=net_hard_denied,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


_READ_CHUNK = 65536


def _drain_to_cap(
    stream,
    cap: int,
    overflow_event: "threading.Event",
    on_overflow,
    *,
    keep: str = "head",
) -> bytearray:
    """Read ``stream`` in chunks into a buffer that NEVER grows past ``cap`` bytes
    (HIGH-1: parent memory is bounded to ~``cap`` regardless of how long / how much the
    child streams — we do NOT buffer the whole pipe like ``communicate()`` did). The
    instant cumulative bytes EXCEED ``cap`` we set ``overflow_event`` + fire
    ``on_overflow`` ONCE (the stdout reader passes the killpg trigger here, so the
    process group dies AT the cap), then keep draining-and-DISCARDING the rest so the
    child's writes don't block on a full pipe (a blocked writer can outlive the kill).
    ``keep="head"`` retains the FIRST ``cap`` bytes (stdout — it's parsed as JSON);
    ``keep="tail"`` retains the LAST ``cap`` bytes via a sliding window (stderr — the
    tail is the useful diagnostic), still capped so memory stays bounded. Runs in a
    reader thread; closes the stream on exit. Returns the (≤cap) buffer."""
    buf = bytearray()
    total = 0  # cumulative bytes the child produced (NOT just what we kept)
    try:
        while True:
            chunk = stream.read(_READ_CHUNK)  # b"" once the pipe closes (child exits/killed)
            if not chunk:
                break
            total += len(chunk)
            if keep == "tail":
                buf += chunk
                if len(buf) > cap:  # slide the window — drop the oldest, keep memory ≤ cap+chunk
                    del buf[: len(buf) - cap]
            elif len(buf) < cap:
                buf += chunk[: cap - len(buf)]  # keep only up to the cap; never grow past it
            # Overflow = the child produced strictly MORE than the cap (exactly-cap is OK).
            if total > cap and not overflow_event.is_set():
                overflow_event.set()
                if on_overflow is not None:
                    on_overflow()
            # else: keep reading + discarding so the child's writer never blocks on a full pipe.
    except (OSError, ValueError):  # pragma: no cover - pipe torn down under us
        pass
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover
            pass
    return buf


def _pump_bounded(
    proc: subprocess.Popen,
    stdin_bytes: bytes,
    limits: SandboxLimits,
    kill: Callable[[], None],
) -> tuple[bytes, bytes, bool, bool]:
    """Feed stdin, read stdout/stderr with BOUNDED reader threads, and enforce the
    wall-clock — returning ``(stdout, stderr, timed_out, output_truncated)``. Memory is
    bounded to ~``max_output_bytes + max_stderr_bytes``: each reader caps its buffer and
    the stdout reader kills the whole process tree the moment it overflows the cap.
    The wall-clock is enforced by joining the readers with the remaining budget and, on
    expiry, killing the group (which closes the pipes → the readers finish)."""
    out_overflow = threading.Event()
    # stdout overflow is a hard violation → kill the group immediately at the cap.
    out_buf = bytearray()
    err_buf = bytearray()

    def _read_stdout() -> None:
        out_buf.extend(_drain_to_cap(proc.stdout, limits.max_output_bytes, out_overflow, kill))

    def _read_stderr() -> None:
        # stderr is diagnostic; cap it too (no kill — a noisy stderr isn't a violation),
        # keeping the TAIL (the useful end of a traceback), memory still bounded to ~cap.
        err_buf.extend(
            _drain_to_cap(
                proc.stderr, limits.max_stderr_bytes, threading.Event(), None, keep="tail"
            )
        )

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    # Feed stdin in a thread so a child that never reads it can't deadlock the parent.
    def _write_stdin() -> None:
        try:
            if proc.stdin:
                proc.stdin.write(stdin_bytes)
                proc.stdin.close()
        except (OSError, ValueError):  # pragma: no cover - child closed stdin early
            pass

    t_in = threading.Thread(target=_write_stdin, daemon=True)
    t_in.start()

    timed_out = False
    deadline = time.monotonic() + limits.wall_seconds
    # Wait for the child to exit within the wall-clock; on expiry kill the group.
    try:
        proc.wait(timeout=limits.wall_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill()

    # Drain the readers. Killing the group (timeout or overflow) closes the pipes, so
    # the reader threads return promptly; a small join budget guards a stuck reader.
    remaining = max(1.0, deadline - time.monotonic()) if not timed_out else 5.0
    t_out.join(timeout=remaining + 5.0)
    t_err.join(timeout=5.0)
    if proc.poll() is None:  # the readers couldn't finish because the child still lives
        kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - ignored SIGKILL
            pass
        t_out.join(timeout=5.0)
        t_err.join(timeout=5.0)

    return bytes(out_buf), bytes(err_buf), timed_out, out_overflow.is_set()


def _spawn_and_collect(
    *,
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
    input_path: Path,
    limits: SandboxLimits,
    net_hard_denied: bool,
) -> SandboxResult:
    """Launch the child, feed stdin = input JSON, enforce the wall-clock, and collect a
    size-capped stdout/stderr with BOUNDED INCREMENTAL READS (HIGH-1): we never buffer
    past ``max_output_bytes``/``max_stderr_bytes`` — reader threads read in chunks and
    the instant cumulative stdout exceeds the cap we SIGKILL the whole process group,
    so a script that streams output for the whole wall-clock window cannot OOM the
    parent. The wall-clock timeout + the existing process-group kill semantics hold."""
    start = time.monotonic()
    stdin_bytes = input_path.read_bytes()
    job = None
    guard = None

    try:
        if _WINDOWS:
            job = _win_job.JobObject(
                cpu_seconds=limits.cpu_seconds, memory_bytes=limits.memory_mb * 1024 * 1024
            )
            popen_kwargs: dict = {
                "creationflags": spawn_kwargs()["creationflags"] | _win_job.CREATE_SUSPENDED
            }
        else:
            popen_kwargs = {"preexec_fn": _make_preexec(limits), **spawn_kwargs()}
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            **popen_kwargs,
        )
        if job is not None:
            job.attach(proc)
    except (OSError, ValueError) as e:
        if job is not None:
            job.close()
        return SandboxResult(
            ok=False,
            exit_code=None,
            data=None,
            stdout="",
            stderr_tail="",
            duration_ms=int((time.monotonic() - start) * 1000),
            error=f"failed to launch sandbox child: {e}",
            network_hard_denied=net_hard_denied,
        )

    def kill() -> None:
        if job is not None:
            job.terminate()
        else:
            _killpg(proc)

    if job is not None:
        guard = _win_job.ScratchGuard(cwd, limits.max_file_size_mb * 1024 * 1024, kill)
        guard.start()
    try:
        out_bytes, err_bytes, timed_out, output_truncated = _pump_bounded(
            proc, stdin_bytes, limits, kill
        )
        killed = False

        # Make sure nothing is left running even on an unexpected path.
        if proc.poll() is None:  # pragma: no cover
            killed = True
            kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if guard is not None:
            guard.stop()
        if job is not None:
            job.terminate()
            job.close()

    duration_ms = int((time.monotonic() - start) * 1000)

    if timed_out or output_truncated:
        killed = True  # an overrun / oversize producer was SIGKILL'd (RFC §4.5)
    stdout = out_bytes.decode("utf-8", errors="replace")
    # err_bytes is already the bounded TAIL (≤ max_stderr_bytes) from the reader.
    stderr_tail = err_bytes.decode("utf-8", errors="replace")

    exit_code = proc.returncode

    # ── Classify the outcome (a launched run NEVER raises — RFC §2.5) ──────────
    if timed_out:
        return SandboxResult(
            ok=False,
            exit_code=None,
            data=None,
            stdout=stdout,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            timed_out=True,
            killed=True,
            output_truncated=output_truncated,
            error=f"script exceeded the {limits.wall_seconds:g}s wall-clock and was killed",
            network_hard_denied=net_hard_denied,
        )
    if output_truncated:
        return SandboxResult(
            ok=False,
            exit_code=exit_code,
            data=None,
            stdout=stdout,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            killed=True,
            output_truncated=True,
            error=f"script output exceeded the {limits.max_output_bytes}-byte cap",
            network_hard_denied=net_hard_denied,
        )
    if exit_code != 0:
        # Non-zero (incl. a signal — POSIX returncode is -signum) → killed-by-rlimit etc.
        sig = -exit_code if (exit_code is not None and exit_code < 0) else None
        if sig in _FATAL_SIGNALS:
            killed = True
        reason = (
            f"script killed by signal {sig}"
            if sig is not None
            else f"script exited with code {exit_code}"
        )
        return SandboxResult(
            ok=False,
            exit_code=exit_code,
            data=None,
            stdout=stdout,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            killed=killed,
            error=reason,
            network_hard_denied=net_hard_denied,
        )

    # Exit 0 — try to parse stdout as JSON (the script's contract). Non-JSON degrades.
    data: object | None = None
    parse_error: str | None = None
    text = stdout.strip()
    if text:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            parse_error = f"script stdout is not valid JSON: {e}"
    else:
        parse_error = "script produced no stdout"

    if parse_error is not None:
        return SandboxResult(
            ok=False,
            exit_code=exit_code,
            data=None,
            stdout=stdout,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            error=parse_error,
            network_hard_denied=net_hard_denied,
        )

    return SandboxResult(
        ok=True,
        exit_code=0,
        data=data,
        stdout=stdout,
        stderr_tail=stderr_tail,
        duration_ms=duration_ms,
        network_hard_denied=net_hard_denied,
    )


# The signals a resource cap or the parent delivers; SIGXCPU/SIGXFSZ are POSIX-only.
_FATAL_SIGNALS = frozenset(
    getattr(signal, name)
    for name in ("SIGKILL", "SIGXCPU", "SIGSEGV", "SIGXFSZ")
    if hasattr(signal, name)
)


def _killpg(proc: subprocess.Popen) -> None:
    """SIGKILL the child's whole process group (the child + any descendants it spawned
    before NPROC bit). Best-effort — a race where the group is already gone is fine."""
    try:
        kill_tree(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
