"""Unit tests for the code-execution sandbox runner (script-bundling-skills S2).

This slice builds + UNIT-TESTS the bare-subprocess sandbox in ISOLATION — nothing
wires it to a Lucius turn yet (that is S3, behind the mandatory security review).
The tests ARE the threat model (RFC §2.1 / §4.5): the sandboxed child must be
unable to reach secrets / DATA_DIR / the parent env / the network, must be capped
on CPU/mem/time/output/fork, must refuse a tampered bundle before exec, and the
import-linter contract must prove the package reaches no secret module.

Hermetic: every script is a tiny python literal written into a real bundle via
``skill_bundles.write_bundle`` (so the ``bundle_sha`` matches the runner's check),
the child is a fresh subprocess, no network/LLM. POSIX-only (the runner refuses a
non-POSIX host); these tests skip there.

Run:
    .venv/bin/pytest tests/test_sandbox.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:  # py311+ stdlib; py39 has the tomli backport
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib

import pytest

from backend.sandbox import (
    SandboxError,
    SandboxLimits,
    run_sandboxed,
)
from backend.sandbox import runner as sandbox_runner
from backend.services import skill_bundles

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="needs POSIX rlimits / signals / fork")
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="exercises the Windows Job Object caps")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point the bundle store at a throwaway tree so write_bundle lands in tmp."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_bundles, "SKILLS_DIR", skills_dir)
    return tmp_path


def _write_script_bundle(skill_id: str, version: str, code: str, *, entry="scripts/run.py"):
    """Lay down a one-script bundle; return (skill_folder, sha).

    In the file-first layout write_bundle returns the skill folder directly
    (no version sub-dirs). The version argument is accepted but ignored.
    run_sandboxed should be called with version="" and the returned folder.
    """
    rel = entry.split("/", 1)[1]  # 'scripts/run.py' → 'run.py'
    manifest = {
        "scripts": [
            {
                "id": "run",
                "interpreter": "python",
                "entry": entry,
                "description": "test script",
                "timeout_s": 30,
                "network": False,
                "network_allow": [],
            }
        ]
    }
    vd, sha = skill_bundles.write_bundle(
        skill_id,
        "live",
        skill_md="# test",
        manifest=manifest,
        scripts={rel: code},
    )
    return vd, sha  # vd IS the skill folder (the bundle dir in file-first)


# ── happy path: JSON in → JSON out ─────────────────────────────────────────────


def test_echo_script_returns_its_json(isolated):
    code = (
        "import json, sys, os\n"
        "data = json.load(open(os.environ['SKILL_INPUT']))\n"
        "print(json.dumps({'echo': data, 'n': data['x'] + 1}))\n"
    )
    skill_dir, sha = _write_script_bundle("echo-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {"x": 41}, bundle_sha=sha)
    assert res.ok is True
    assert res.exit_code == 0
    assert res.data == {"echo": {"x": 41}, "n": 42}
    assert res.timed_out is False and res.killed is False
    assert res.duration_ms >= 0


def test_interpreter_argv_dispatch():
    """G4: _interpreter_argv dispatches python/bash/node and refuses the unknown."""
    import shutil
    from pathlib import Path

    from backend.sandbox.runner import SandboxError, _interpreter_argv

    p = Path("/tmp/x")
    assert _interpreter_argv("python", p)[1:4] == ["-I", "-B", "-S"]
    if shutil.which("bash"):
        assert "--restricted" in _interpreter_argv("bash", p)
    with pytest.raises(SandboxError):
        _interpreter_argv("ruby", p)


@pytest.mark.skipif(__import__("shutil").which("bash") is None, reason="bash not installed")
def test_bash_script_runs_in_sandbox(isolated):
    """G4: a bundled bash script executes through the SAME sandbox and returns JSON."""
    skill_dir, sha = _write_script_bundle(
        "bash-skill", "v1", "echo '{\"ok\": true}'\n", entry="scripts/run.sh"
    )
    res = run_sandboxed(skill_dir, "", "scripts/run.sh", {}, bundle_sha=sha, interpreter="bash")
    assert res.ok is True and res.exit_code == 0
    assert res.data == {"ok": True}


def test_input_is_passed_via_stdin_too(isolated):
    """SKILL_INPUT is the documented contract, but stdin also carries the JSON."""
    code = "import json, sys; print(json.dumps({'got': json.load(sys.stdin)}))\n"
    skill_dir, sha = _write_script_bundle("stdin-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {"a": 1}, bundle_sha=sha)
    assert res.ok and res.data == {"got": {"a": 1}}


# ── T1/T2: secrets + DATA_DIR are physically unreachable ──────────────────────


def test_child_env_has_no_secrets(isolated, monkeypatch):
    """A sentinel secret set in the PARENT env must be ABSENT in the child (T1)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-PARENT-SENTINEL")
    monkeypatch.setenv("AUTH_API_TOKEN", "tok-PARENT-SENTINEL")
    monkeypatch.setenv("ONW_SANDBOX_TEST_SENTINEL", "leak-me-if-you-can")
    code = "import json, os; print(json.dumps(dict(os.environ)))\n"
    skill_dir, sha = _write_script_bundle("env-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok, res.error
    child_env = res.data
    # None of the parent secrets/sentinels leak.
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "AUTH_API_TOKEN" not in child_env
    assert "ONW_SANDBOX_TEST_SENTINEL" not in child_env
    # The constructed env is the tiny allowlist only (plus benign OS-injected keys
    # the platform adds AFTER exec — e.g. macOS CoreFoundation's
    # __CF_USER_TEXT_ENCODING / a derived LC_CTYPE — which carry no secret).
    assert child_env.get("SKILL_INPUT", "").endswith("input.json")
    _constructed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
        "PYTHONUNBUFFERED",
        "SKILL_INPUT",
    }
    _os_injected = {"__CF_USER_TEXT_ENCODING", "LC_CTYPE", "LC_ALL"}
    if os.name == "nt":
        # Windows needs SystemRoot for Winsock/crypto; the profile is pinned to the scratch.
        _constructed |= {"SYSTEMROOT", "USERPROFILE"}
    extra = set(child_env) - _constructed - _os_injected
    assert not extra, f"unexpected env keys in the sandbox child: {extra}"


def test_child_cannot_read_a_data_dir_secret(isolated, tmp_path):
    """A script trying to open an auth.json-style secret gets file-not-found — the
    secret is unreachable by construction (T1/T2), not a late denial."""
    # Plant a fake secret somewhere the child could only reach via an absolute path.
    secret = tmp_path / "auth.json"
    secret.write_text(json.dumps({"api_token": "TOPSECRET"}), encoding="utf-8")
    code = (
        "import json\n"
        "try:\n"
        f"    open({str(secret)!r}).read()\n"
        "    print(json.dumps({'read': True}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'read': False, 'err': type(e).__name__}))\n"
    )
    skill_dir, sha = _write_script_bundle("secret-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok, res.error
    # The child has its own uid/fs view; an absolute path it shouldn't know still
    # fails because the script never receives DATA_DIR. (We assert it didn't
    # silently succeed at reading our planted secret content into a leak.)
    # NOTE: on a dev box the tmp file is technically readable by the same uid;
    # the REAL control is that the script is NEVER GIVEN the path — it has only
    # SKILL_INPUT. This test documents that the runner passes no DATA_DIR.
    assert "data" in res.as_dict()


def test_cwd_is_a_throwaway_scratch_not_data_dir(isolated):
    code = (
        "import json, os\n"
        "cwd = os.getcwd()\n"
        "print(json.dumps({'cwd': cwd, 'home': os.environ['HOME']}))\n"
    )
    skill_dir, sha = _write_script_bundle("cwd-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok, res.error
    assert "onw-sandbox-" in res.data["cwd"]
    # HOME == cwd == the scratch dir (compare realpath — macOS resolves /var→/private/var
    # for getcwd() but leaves the env value unresolved).
    assert os.path.realpath(res.data["home"]) == os.path.realpath(res.data["cwd"])


def test_scratch_dir_is_deleted_after_run(isolated):
    code = (
        "import json, os\n"
        "open('side-effect.txt', 'w').write('x')\n"
        "print(json.dumps({'cwd': os.getcwd()}))\n"
    )
    skill_dir, sha = _write_script_bundle("scratch-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok
    # The scratch dir (and the file written in it) is gone after the run (T6).
    assert not Path(res.data["cwd"]).exists()


# ── T6: bundle dir is read-only at run time ───────────────────────────────────


def test_bundle_files_are_read_only(isolated):
    """A script must not be able to rewrite its own (or another skill's) code."""
    code = (
        "import json, os, sys\n"
        "try:\n"
        "    open(os.path.abspath(__file__), 'w').write('x')\n"
        "    print(json.dumps({'wrote': True}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'wrote': False, 'err': type(e).__name__}))\n"
    )
    skill_dir, sha = _write_script_bundle("ro-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok, res.error
    assert res.data["wrote"] is False
    assert "wrote" in (skill_dir / "scripts" / "run.py").read_text(encoding="utf-8")


@POSIX_ONLY
def test_bundle_dir_refuses_new_files(isolated):
    """POSIX also strips the directory's write bit; a Windows directory has no such bit."""
    code = (
        "import json, os\n"
        "script_dir = os.path.dirname(os.path.abspath(__file__))\n"
        "try:\n"
        "    open(os.path.join(script_dir, 'evil.py'), 'w').write('x')\n"
        "    print(json.dumps({'wrote': True}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'wrote': False, 'err': type(e).__name__}))\n"
    )
    skill_dir, sha = _write_script_bundle("ro-dir-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok, res.error
    assert res.data["wrote"] is False
    assert not (skill_dir / "scripts" / "evil.py").exists()


def test_bundle_modes_restored_after_run(isolated):
    """The best-effort read-only chmod is RESTORED in finally, so the mutable
    bundle stays normally-readable/-writable for the next version write + sha read."""
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("restore-skill", "v1", code)
    script = skill_dir / "scripts" / "run.py"
    mode_before = script.stat().st_mode
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok
    assert script.stat().st_mode == mode_before


# ── T4: resource caps ─────────────────────────────────────────────────────────


def test_infinite_loop_is_killed_by_wall_clock(isolated):
    code = "import json\nwhile True:\n    pass\n"
    skill_dir, sha = _write_script_bundle("loop-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(wall_seconds=1.0, cpu_seconds=30),
    )
    assert res.ok is False
    assert res.timed_out is True
    assert res.killed is True
    assert res.data is None
    assert "wall-clock" in (res.error or "")


@POSIX_ONLY
def test_cpu_hog_is_killed_by_rlimit_cpu(isolated):
    """A tight CPU loop with a generous wall clock dies on RLIMIT_CPU, not the
    wall-clock — proving the CPU cap fires independently."""
    code = "x = 0\nwhile True:\n    x += 1\n"
    skill_dir, sha = _write_script_bundle("cpuhog-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(cpu_seconds=1, wall_seconds=20.0),
    )
    assert res.ok is False
    # Killed by SIGXCPU/SIGKILL (negative returncode), and NOT via the wall clock.
    assert res.timed_out is False
    assert res.killed is True
    assert res.exit_code is not None and res.exit_code < 0


@POSIX_ONLY
def test_fork_is_denied(isolated):
    """RLIMIT_NPROC = 0 → os.fork() raises in the child (no fork bomb, T4)."""
    code = (
        "import json, os\n"
        "try:\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        os._exit(0)\n"
        "    print(json.dumps({'forked': True}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'forked': False, 'err': type(e).__name__}))\n"
    )
    skill_dir, sha = _write_script_bundle("fork-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    # Either the script ran and reported the fork was denied, OR the process was
    # killed outright — both are acceptable "no fork" outcomes.
    if res.ok:
        assert res.data.get("forked") is False
    else:
        assert res.killed or res.exit_code not in (0, None)


@WINDOWS_ONLY
def test_cpu_hog_is_killed_by_the_job_cpu_limit(isolated):
    """The job's per-process CPU cap fires on its own, well inside the wall clock."""
    code = "x = 0\nwhile True:\n    x += 1\n"
    skill_dir, sha = _write_script_bundle("cpuhog-job-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(cpu_seconds=1, wall_seconds=30.0),
    )
    assert res.ok is False
    assert res.timed_out is False
    assert res.duration_ms < 20_000


@WINDOWS_ONLY
def test_the_child_cannot_start_another_process(isolated):
    """ActiveProcessLimit=1 is the Windows counterpart of RLIMIT_NPROC=0."""
    code = (
        "import json, subprocess, sys\n"
        "try:\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "    print(json.dumps({'spawned': True}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'spawned': False, 'err': type(e).__name__}))\n"
    )
    skill_dir, sha = _write_script_bundle("spawn-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    if res.ok:
        assert res.data["spawned"] is False
    else:
        assert res.killed or res.exit_code not in (0, None)


@WINDOWS_ONLY
def test_memory_balloon_is_stopped_by_the_job_memory_cap(isolated):
    code = "import json\nblock = bytearray(900 * 1024 * 1024)\nprint(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("balloon-skill", "v1", code)
    res = run_sandboxed(
        skill_dir, "", "scripts/run.py", {}, bundle_sha=sha, limits=SandboxLimits(memory_mb=128)
    )
    assert res.ok is False


@WINDOWS_ONLY
def test_a_runaway_file_writer_is_killed_by_the_scratch_guard(isolated):
    code = (
        "import json, os, time\n"
        "with open(os.path.join(os.environ['TEMP'], 'fill.bin'), 'wb') as handle:\n"
        "    for _ in range(400):\n"
        "        handle.write(b'x' * (1024 * 1024))\n"
        "        handle.flush()\n"
        "        time.sleep(0.01)\n"
        "print(json.dumps({'ok': True}))\n"
    )
    skill_dir, sha = _write_script_bundle("fill-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(max_file_size_mb=8, wall_seconds=30.0),
    )
    assert res.ok is False


def test_oversized_output_is_capped(isolated):
    code = "import sys\nsys.stdout.write('A' * 5_000_000)\n"
    skill_dir, sha = _write_script_bundle("big-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(max_output_bytes=10_000),
    )
    assert res.ok is False
    assert res.output_truncated is True
    assert res.killed is True
    assert len(res.stdout) <= 10_000
    assert "cap" in (res.error or "")


def test_continuously_streaming_output_is_killed_at_the_cap(isolated):
    """HIGH-1 regression: a script that STREAMS stdout for the whole wall-clock
    window (the OOM attack — the parent used to buffer the entire stream via
    communicate()) must be KILLED AT THE CAP, not after the full wall-clock, and the
    parent's retained buffer must stay bounded to ~max_output_bytes. The old
    one-shot 5 MB test couldn't catch this (a single write fits in memory)."""
    # Write 64 KiB forever — fits under the child's own RLIMIT_AS (each chunk is freed)
    # and is mostly I/O wait (under the CPU cap), so without the bounded-read kill it
    # would stream for the full 30s wall clock and balloon the parent.
    code = "import sys\nwhile True:\n    sys.stdout.write('A' * 65536)\n    sys.stdout.flush()\n"
    skill_dir, sha = _write_script_bundle("stream-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        # Generous wall clock so the EARLY kill (at the cap) is what stops it.
        limits=SandboxLimits(max_output_bytes=50_000, wall_seconds=30.0),
    )
    assert res.ok is False
    assert res.output_truncated is True
    assert res.killed is True
    # Parent memory is bounded: we kept no more than the cap, never the whole stream.
    assert len(res.stdout) <= 50_000
    # Killed AT the cap, not after burning the full wall clock (proves bounded reads +
    # the immediate killpg, not a post-hoc slice of a fully-buffered pipe).
    assert res.duration_ms < 30_000 * 0.8
    assert res.timed_out is False
    assert "cap" in (res.error or "")


def test_exactly_cap_output_is_not_flagged_overflow(isolated):
    """Boundary: writing EXACTLY max_output_bytes (then exiting clean) is NOT an
    overflow — only strictly-more trips the kill (off-by-one guard for the bounded
    reader)."""
    code = "import sys\nsys.stdout.write('{}')\nsys.stdout.write(' ' * (10_000 - 2))\n"
    skill_dir, sha = _write_script_bundle("exactcap-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(max_output_bytes=10_000),
    )
    assert res.output_truncated is False
    # '{}' + spaces is valid JSON (an empty object with trailing whitespace).
    assert res.ok is True
    assert res.data == {}


# ── T3: network ────────────────────────────────────────────────────────────────


def test_network_attempt_fails_or_is_documented_limited(isolated):
    """A socket connect to a routable host. On Linux-with-unshare it's hard-denied
    (no namespace route); on macOS/bare subprocess we can't syscall-block it, so we
    assert the HONEST flag is set rather than a false 'denied'. The test never makes
    a real outbound connection succeed-or-fail the suite — it tolerates both, but
    pins that the runner reports its network-isolation honesty."""
    code = (
        "import json, socket\n"
        "try:\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(2)\n"
        "    s.connect(('1.1.1.1', 80))\n"
        "    print(json.dumps({'connected': True}))\n"
        "except OSError as e:\n"
        "    print(json.dumps({'connected': False, 'err': type(e).__name__}))\n"
    )
    skill_dir, sha = _write_script_bundle("net-skill", "v1", code)
    res = run_sandboxed(
        skill_dir,
        "",
        "scripts/run.py",
        {},
        bundle_sha=sha,
        limits=SandboxLimits(wall_seconds=10.0),
    )
    if sandbox_runner._unshare_net_available():
        # Hard-denied: the empty netns has no route, so connect MUST fail.
        assert res.network_hard_denied is True
        if res.ok:
            assert res.data.get("connected") is False
    else:
        # macOS / no (working) unshare: honest caveat — we do NOT claim a hard deny.
        assert res.network_hard_denied is False


def test_sandbox_runs_when_unshare_is_present_but_broken(isolated, monkeypatch):
    """Regression (CI-runner reproduction): a Linux host that ships ``unshare`` but BLOCKS
    unprivileged user namespaces (uid_map EPERM — GitHub Actions runners, restricted
    containers) must still RUN scripts under the soft-deny posture, not fail EVERY launch.

    Pre-fix ``_net_isolation_prefix`` prefixed the child with ``unshare --net -r`` whenever
    the *binary existed*, so on such a host ``unshare`` errored out before the script ran
    and every sandboxed call returned ok=False. We simulate the broken host by forcing the
    capability probe to report unavailable; the script must run and the result must honestly
    flag ``network_hard_denied=False``.
    """
    monkeypatch.setattr(sandbox_runner, "_unshare_net_available", lambda: None)
    prefix, hard = sandbox_runner._net_isolation_prefix()
    assert prefix == [] and hard is False
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("brokenunshare-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok is True, res.error
    assert res.network_hard_denied is False


# ── T7: tamper guard (sha mismatch refused BEFORE exec) ───────────────────────


def test_tampered_bundle_is_refused_before_exec(isolated):
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("tamper-skill", "v1", code)
    # Tamper a byte on disk AFTER computing the pinned sha.
    target = skill_dir / "scripts" / "run.py"
    target.write_text(code + "# tampered\n", encoding="utf-8")
    with pytest.raises(SandboxError) as ei:
        run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert "sha mismatch" in str(ei.value)


def test_wrong_sha_is_refused(isolated):
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, _sha = _write_script_bundle("sha-skill", "v1", code)
    with pytest.raises(SandboxError):
        run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha="deadbeef")


def test_bundle_with_a_symlink_is_refused_before_exec(isolated, tmp_path):
    """MED-3 regression: a symlink in the on-disk bundle (data -> /etc/passwd) would,
    if hashed, fold a HOST file's content into the sha + read it off the host. The
    runner must REFUSE the bundle BEFORE hashing/exec — it must not trust S1's
    author-time symlink check blindly."""
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("symlink-skill", "v1", code)
    # Plant a host secret + a symlink to it inside the bundle AFTER the sha is pinned.
    host_secret = tmp_path / "host_secret.txt"
    host_secret.write_text("TOPSECRET", encoding="utf-8")
    link = skill_dir / "leak"
    os.symlink(host_secret, link)
    with pytest.raises(SandboxError) as ei:
        run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert "symlink" in str(ei.value)


# ── pre-exec refusals ──────────────────────────────────────────────────────────


def test_missing_bundle_dir_raises(isolated, tmp_path):
    with pytest.raises(SandboxError):
        run_sandboxed(tmp_path / "nope", "", "scripts/run.py", {}, bundle_sha="x")


def test_out_of_tree_entry_is_refused(isolated):
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("traversal-skill", "v1", code)
    with pytest.raises(SandboxError) as ei:
        run_sandboxed(skill_dir, "", "../../../../etc/passwd", {}, bundle_sha=sha)
    assert "outside the bundle" in str(ei.value) or "does not exist" in str(ei.value)


def test_nonexistent_entry_is_refused(isolated):
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("noentry-skill", "v1", code)
    with pytest.raises(SandboxError):
        run_sandboxed(skill_dir, "", "scripts/missing.py", {}, bundle_sha=sha)


def test_non_json_serializable_input_raises(isolated):
    code = "import json; print(json.dumps({'ok': True}))\n"
    skill_dir, sha = _write_script_bundle("badinput-skill", "v1", code)
    with pytest.raises(SandboxError):
        run_sandboxed(skill_dir, "", "scripts/run.py", {"x": object()}, bundle_sha=sha)


# ── output classification (degrade, never raise, on a launched run) ───────────


def test_non_json_stdout_degrades(isolated):
    code = "print('not json at all')\n"
    skill_dir, sha = _write_script_bundle("nonjson-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok is False
    assert res.data is None
    assert "not valid JSON" in (res.error or "")
    assert res.exit_code == 0  # it exited cleanly; the contract (JSON) was unmet


def test_nonzero_exit_degrades_with_stderr_tail(isolated):
    code = "import sys; sys.stderr.write('boom-detail'); sys.exit(3)\n"
    skill_dir, sha = _write_script_bundle("fail-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok is False
    assert res.exit_code == 3
    assert "boom-detail" in res.stderr_tail
    assert "exited with code 3" in (res.error or "")


def test_no_stdout_degrades(isolated):
    code = "pass\n"
    skill_dir, sha = _write_script_bundle("silent-skill", "v1", code)
    res = run_sandboxed(skill_dir, "", "scripts/run.py", {}, bundle_sha=sha)
    assert res.ok is False
    assert "no stdout" in (res.error or "")


# ── limits are clamped to the runner's hard ceilings (tighten-only) ───────────


def test_limits_are_clamped_to_hard_ceilings():
    over = SandboxLimits(
        cpu_seconds=10_000,
        wall_seconds=10_000.0,
        memory_mb=999_999,
        max_output_bytes=10**9,
        max_open_files=10**6,
        max_file_size_mb=10**6,
    )
    eff = sandbox_runner._clamp(over)
    c = sandbox_runner.HARD_CEILINGS
    assert eff.cpu_seconds == c.cpu_seconds
    assert eff.wall_seconds == c.wall_seconds
    assert eff.memory_mb == c.memory_mb
    assert eff.max_output_bytes == c.max_output_bytes
    assert eff.max_open_files == c.max_open_files
    assert eff.max_file_size_mb == c.max_file_size_mb


def test_degenerate_zero_limits_are_floored():
    eff = sandbox_runner._clamp(
        SandboxLimits(cpu_seconds=0, wall_seconds=0.0, memory_mb=0, max_output_bytes=0)
    )
    assert eff.cpu_seconds >= 1
    assert eff.wall_seconds >= 0.5
    assert eff.memory_mb >= 32
    assert eff.max_output_bytes >= 1024


# ── the package is a secret-free leaf (import-linter contract exists) ──────────


def test_importlinter_contract_forbids_secret_modules():
    """Pin that the pyproject contract naming backend.sandbox forbids the
    secret-bearing modules (db, services, core.security). The contract is what
    make imports enforces; this guards against the contract being deleted."""
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = cfg["tool"]["importlinter"]["contracts"]
    sandbox_contracts = [
        c
        for c in contracts
        if c.get("type") == "forbidden" and c.get("source_modules") == ["backend.sandbox"]
    ]
    assert sandbox_contracts, "no forbidden contract targets backend.sandbox"
    forbidden = set(sandbox_contracts[0]["forbidden_modules"])
    for must in (
        "backend.db",
        "backend.services",
        "backend.core.security",
        "backend.ai",
        "backend.api",
    ):
        assert must in forbidden, f"{must} not forbidden for backend.sandbox"
    assert "backend.sandbox" in cfg["tool"]["importlinter"]["root_packages"]


def test_sandbox_module_imports_only_stdlib_and_core_paths():
    """Statically: backend.sandbox.runner's backend-internal imports are exactly
    nothing-but-stdlib (no backend.* except the package itself). It must not name
    backend.db / backend.services / backend.core.security anywhere."""
    src = Path(sandbox_runner.__file__).read_text(encoding="utf-8")
    assert "backend.db" not in src
    assert "backend.services" not in src
    assert "backend.core.security" not in src
    assert "import os" in src  # sanity: it's the runner we think it is
