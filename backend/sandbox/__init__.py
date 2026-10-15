"""backend.sandbox — the code-execution sandbox for script-bundling skills.

This is the load-bearing SECURITY control of the script-bundling-skills feature
(RFC ``docs/rfc/script-bundling-skills.md``, slice S2). A *script* skill bundles
runnable python that the agent executes; the old "skills grant ZERO execution
power" invariant is gone, and the sandbox's isolation is what replaces it.

**This package is a LEAF by construction.** The runner imports ONLY the standard
library — it does not even import ``backend.core.paths`` (the S3 caller resolves
the bundle dir and passes it in). It must NEVER import ``backend.db``,
``backend.services``, ``backend.core.security``, ``backend.core.config`` (which
DOES read ``AUTH_*`` secrets from env), or any ``*_config_store`` — i.e. nothing
that can reach ``DATA_DIR`` secrets, the ledger, or auth. An import-linter
contract (pyproject Contract 5) asserts this. The secret-bearing layers are
physically off the sandbox's import path, so a bug in the runner cannot leak
credentials by import.

The runner (``runner.py``) executes a bundle's declared python script as a
SUBPROCESS with hard isolation: a *constructed* env (never ``os.environ``),
a cwd jailed to a fresh throwaway scratch dir, the bundle dir read-only,
``setrlimit`` CPU/RSS/NPROC/FSIZE caps, a wall-clock timeout that SIGKILLs the
whole process group on overrun, and network OFF (best-effort — see the macOS
caveat in ``runner.py``). The script is a PURE compute black box: JSON in →
JSON out, with NO path to the ledger / DB / secrets by construction.

NOTHING wires this to a Lucius turn yet — the ``run_script`` tool that calls it
is slice S3, and the whole feature MUST get a ``/security-review`` before S3
exposes it live. S2 builds + unit-tests the runner in isolation only.
"""

from __future__ import annotations

from backend.sandbox.runner import (
    DEFAULT_LIMITS,
    HARD_CEILINGS,
    SandboxError,
    SandboxLimits,
    SandboxResult,
    run_sandboxed,
    run_source,
)

__all__ = [
    "run_sandboxed",
    "run_source",
    "SandboxResult",
    "SandboxLimits",
    "SandboxError",
    "DEFAULT_LIMITS",
    "HARD_CEILINGS",
]
