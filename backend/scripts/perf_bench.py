"""Benchmark the app's REAL page-load waves against a running server.

The unit of user experience is not an endpoint — it is the WAVE of requests a page fires
at once. This script replays each page's wave (captured from DevTools/PERF-REQ logs,
2026-08-12) with page-realistic concurrency and prints per-endpoint + wall times, so a
perf fix is validated against the case the owner actually feels. Read-only GETs only.

Usage:
    .venv/bin/python3 backend/scripts/perf_bench.py [--base http://127.0.0.1:8080] [wave ...]

Baselines live in docs/PERFORMANCE.md ("Benchmark baseline"). Update them when a fix lands.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import time
import urllib.request

WAVES: dict[str, list[str]] = {
    "overview": [
        "/api/accounts",
        "/api/payees",
        "/api/net-worth",
        "/api/net-worth/stats",
        "/api/transactions?limit=75&offset=0",
        "/api/categories",
        "/api/portfolio?live=0",
        "/api/intel/signals",
        "/api/matches/reviews",
        "/api/commitments/summary",
    ],
    "plan": [
        "/api/planning/month",
        "/api/planning/health",
        "/api/planning/summary",
        "/api/commitments",
        "/api/commitments/coverage?months=12",
        "/api/cashflow?months=40",
        "/api/forecast-scorecard?window=1Y",
        "/api/net-worth",
        "/api/net-worth/stats",
        "/api/categories?include_hidden=true",
    ],
    "investments": [
        "/api/portfolio?live=0",
        "/api/portfolio/analytics",
        "/api/portfolio/drivers",
        "/api/portfolio/value-series",
        "/api/returns",
        "/api/investments/brief",
        "/api/prices",
        "/api/intel/signals",
    ],
    "chat-open": [
        "/api/sessions",
        "/api/models",
        "/api/sessions/60fc4e06-6b7f-456a-b6e1-938f1052ce7e/messages?tail=30",
    ],
}


def _get(base: str, path: str) -> tuple[str, float, int, int]:
    req = urllib.request.Request(base + path, headers={"Accept-Encoding": "gzip"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 — localhost bench
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return path, time.perf_counter() - t0, resp.status, len(body)


def run_wave(base: str, name: str, paths: list[str], concurrency: int) -> None:
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda p: _get(base, p), paths))
    wall = time.perf_counter() - t0
    print(f"\n── {name}: wall={wall:.2f}s  ({len(paths)} requests, concurrency={concurrency})")
    for path, dt, status, size in sorted(results, key=lambda r: -r[1]):
        print(f"   {status} {dt:6.2f}s {size / 1024:8.1f}KB  {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--concurrency", type=int, default=6, help="browser-like: 6 per host")
    ap.add_argument("waves", nargs="*", default=list(WAVES))
    args = ap.parse_args()
    for name in args.waves:
        run_wave(args.base, name, WAVES[name], args.concurrency)


if __name__ == "__main__":
    main()
