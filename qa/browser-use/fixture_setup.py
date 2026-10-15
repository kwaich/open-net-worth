"""Fail-closed execution for deterministic browser-QA fixture scripts."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FixtureResult:
    ok: bool
    detail: str
    output: str = ""


def attest_qa_backend(backend_url: str, qa_nonce: str) -> FixtureResult:
    """Prove the target is this run's QA-mode backend before permitting fixture writes."""
    parsed = urlparse(backend_url)
    if (
        not qa_nonce
        or parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.port not in range(8090, 8100)
    ):
        return FixtureResult(False, "QA attestation requires a nonce and local port 8090-8099")
    request = Request(
        f"{backend_url.rstrip('/')}/api/qa/attest",
        headers={"X-ONW-QA-Nonce": qa_nonce},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - URL is constrained above
            if response.status != 200:
                return FixtureResult(False, f"QA attestation returned HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return FixtureResult(False, f"QA attestation failed: {exc}")
    return FixtureResult(True, "QA backend attested")


def run_fixture_script(
    script_name: str,
    *,
    fixtures_dir: Path,
    backend_url: str,
    qa_nonce: str,
    timeout_s: float = 60,
    attest=attest_qa_backend,
) -> FixtureResult:
    """Run one Python fixture from ``fixtures_dir`` with no shell involved."""
    root = fixtures_dir.resolve()
    script = (root / script_name).resolve()
    if not script.is_relative_to(root) or not script.is_file():
        return FixtureResult(False, f"invalid setup_script: {script_name}")

    attestation = attest(backend_url, qa_nonce)
    if not attestation.ok:
        return attestation

    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--backend-url",
                backend_url,
                "--qa-nonce",
                qa_nonce,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return FixtureResult(False, f"setup failed: {exc}")
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "fixture setup failed").strip()
        return FixtureResult(False, f"setup failed: {detail[:300]}", output)
    return FixtureResult(True, "fixture setup passed", output)
