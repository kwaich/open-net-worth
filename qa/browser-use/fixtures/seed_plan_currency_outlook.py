#!/usr/bin/env python3
"""Seed deterministic VND Outlook currency facts through disposable QA REST APIs."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _request(base: str, method: str, path: str, body: dict | None = None, *, nonce=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            **({"Content-Type": "application/json"} if data is not None else {}),
            **({"X-ONW-QA-Nonce": nonce} if nonce else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - local URL checked below
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc.read().decode()}") from exc
    return json.loads(raw) if raw else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default="http://localhost:8091")
    parser.add_argument("--qa-nonce", required=True)
    args = parser.parse_args()
    parsed = urlparse(args.backend_url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("backend URL must be local HTTP")
    if parsed.port not in range(8090, 8100):
        parser.error("backend port must be 8090-8099")
    base = args.backend_url.rstrip("/")
    try:
        _request(base, "GET", "/api/qa/attest", nonce=args.qa_nonce)
    except RuntimeError as exc:
        parser.error(f"backend is not a QA-mode instance: {exc}")

    _request(base, "POST", "/api/reset")
    _request(
        base,
        "PUT",
        "/api/profile",
        {
            "name": "QA VND Outlook",
            "base_currency": "VND",
            "number_format": "comma",
            "date_of_birth": "1990-01-01",
            "retirement_target_age": 60,
            "monthly_net_income": 100000000,
            "monthly_expenses": 20000000,
            "fire_monthly_retirement_spending": 20000000,
            "fire_expected_return_pct": 7,
            "fire_withdrawal_rate_pct": 4,
            "fire_volatility_pct": 15,
            "fire_life_expectancy": 90,
        },
    )
    _request(
        base,
        "POST",
        "/api/qa/market-data/scenario",
        {"name": "yahoo_live", "fx_rates": {"VND": 1, "SGD": 20000}},
    )
    account = _request(
        base,
        "POST",
        "/api/accounts",
        {
            "name": "QA Billion Dong Cash",
            "type": "cash",
            "currency": "VND",
            "initial_balance": 2008437944,
            "initial_balance_date": "2026-01-01",
        },
    )
    portfolio = _request(base, "GET", "/api/portfolio")
    if portfolio.get("fx", {}).get("SGD") != 20000:
        raise RuntimeError(f"portfolio did not expose synthetic SGD/VND FX: {portfolio.get('fx')}")
    print(json.dumps({"account_id": account["id"], "fx": portfolio["fx"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
