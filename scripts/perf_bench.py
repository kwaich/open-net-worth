#!/usr/bin/env python3
"""Read-flow performance benchmark for the tabs (net worth, ledger, accounts, positions,
commitments, budget).

Measures each flow's warm median latency AND a concurrent tab-load burst — the case that
actually bites, because opening a tab fires several requests at once and Python serializes CPU
work on the GIL. A flow that is fine alone but blows up super-linearly under the burst is a
contention bug (a hot path fetching far more rows than it returns, churning the connection pool
+ GIL), not "Python being slow".

Read-only and server-free (calls the service layer directly), so it is hang-safe and cannot
mutate data. The sync read path reads prices from the DB, not the network. Point it at a COPY of
real data — never against the live tree:

    ONW_DATABASE_URL=sqlite:///</path/to/copy>/onw.db \
    DATA_DIR=/tmp/bench/data CACHE_DIR=/tmp/bench/cache LOGS_DIR=/tmp/bench/logs \
    .venv/bin/python3 scripts/perf_bench.py

`make perf-bench DB=/path/to/copy/onw.db` wires the same thing up.
"""

from __future__ import annotations

import logging
import signal
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as a plain script

logging.disable(logging.CRITICAL)  # keep FX/price warnings out of the table
signal.signal(
    signal.SIGALRM, lambda *a: (print("TIMEOUT — a flow exceeded the alarm"), sys.exit(99))
)
signal.alarm(300)

from backend.services import commitments as commitments_svc
from backend.services import ledger as ledger_svc
from backend.services import planning_summary as planning_summary_svc
from backend.services import positions as positions_svc

# name → (callable, how many copies of it go into the concurrent burst). The burst weights the
# flows a real tab-load fires together.
FLOWS: dict[str, tuple] = {
    "net_worth (Net Worth)": (lambda: ledger_svc.net_worth(), 2),
    "transactions (Ledger)": (lambda: ledger_svc.transactions.all(), 1),
    "accounts (Accounts)": (lambda: ledger_svc.accounts.query_accounts(include_archived=False), 1),
    "hydrated (Positions)": (lambda: positions_svc.hydrated(), 2),
    "coverage (Commitments)": (lambda: commitments_svc.coverage(months=40), 1),
    "budget (Budget)": (lambda: planning_summary_svc.budget_aggregates(), 1),
}


def _median_ms(fn, n: int = 5) -> float | str:
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — a bench must survive one bad flow
            return f"ERR:{type(exc).__name__}"
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def main() -> int:
    # Warm caches once so the first flow doesn't pay everyone's import/connect cost.
    for fn, _ in FLOWS.values():
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass

    print("== warm median latency, per flow ==")
    for name, (fn, _) in FLOWS.items():
        ms = _median_ms(fn)
        shown = ms if isinstance(ms, str) else f"{ms:8.1f} ms"
        print(f"  {name:28s} {shown}")

    burst = [fn for fn, weight in FLOWS.values() for _ in range(weight)]
    print(f"\n== concurrent tab-load burst ({len(burst)} requests at once) ==")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(burst)) as pool:
        list(pool.map(lambda f: f(), burst))
    wall = (time.perf_counter() - t0) * 1000
    seq_sum = sum(
        m for m in (_median_ms(fn, 1) for fn, _ in FLOWS.values()) if isinstance(m, float)
    )
    print(f"  wall:            {wall:8.1f} ms")
    print(
        f"  ideal (1 pass):  {seq_sum:8.1f} ms   ratio {wall / seq_sum:4.1f}×  (~1 = GIL floor; >1.5 = contention)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
