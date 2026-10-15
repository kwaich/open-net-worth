#!/usr/bin/env python3
"""Run the deterministic no-network component fixture for empty vest history."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    # Accepted for the shared fixture-runner contract. This fixture performs no writes.
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--qa-nonce", required=True)
    parser.parse_args()

    repo = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            "npm",
            "test",
            "--",
            "--run",
            "src/components/tabs/investments/EquityTab.history.test.tsx",
        ],
        cwd=repo / "frontend",
        check=False,
    )
    if completed.returncode:
        return completed.returncode
    print("EquityTab empty-state component fixture passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
