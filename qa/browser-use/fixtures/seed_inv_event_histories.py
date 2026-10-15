#!/usr/bin/env python3
"""Seed deterministic investment-history facts through REST on a disposable QA backend.

The script refuses every host/port except localhost:8090-8099. The QA harness has already
safety-checked DATA_DIR and set ONW_QA_MODE before this runs; all writes still travel through
the production account/position/equity/income choke points rather than editing store files.
"""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ACCOUNT_GRANT = "QA Event History Equity"
ACCOUNT_INCOME = "QA Event History Income"
GRANT_NOTE = "QA_INV_EVENT_HISTORY_GRANT_V1"
INCOME_NOTE_PREFIX = "QA_INV_EVENT_HISTORY_INCOME_V2_"
INSTRUMENT_SOURCE_ID = "qa-inv-event-history-v1"
SYMBOL = "QAHIST"


def _request(
    base: str, method: str, path: str, body: dict | None = None, *, qa_nonce: str | None = None
):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            **({"Content-Type": "application/json"} if data is not None else {}),
            **({"X-ONW-QA-Nonce": qa_nonce} if qa_nonce else {}),
        },
    )
    try:
        with urlopen(req, timeout=30) as response:  # noqa: S310 - localhost is validated below
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else None


def _account(base: str, name: str) -> dict:
    existing = next(
        (row for row in _request(base, "GET", "/api/accounts") if row["name"] == name), None
    )
    if existing:
        return existing
    return _request(
        base,
        "POST",
        "/api/accounts",
        {"name": name, "kind": "asset", "type": "brokerage", "currency": "USD"},
    )


def _instrument(base: str) -> dict:
    rows = _request(base, "GET", "/api/instruments")
    existing = next(
        (row for row in rows if row.get("price_source_id") == INSTRUMENT_SOURCE_ID), None
    )
    if existing:
        return existing
    result = _request(
        base,
        "POST",
        "/api/instruments/resolve",
        {
            "query": SYMBOL,
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": INSTRUMENT_SOURCE_ID,
        },
    )
    if not result.get("resolved"):
        raise RuntimeError(f"could not resolve QA instrument: {result}")
    return result["instrument"]


def _income_position(base: str, instrument_id: str, account_id: str) -> dict:
    positions = _request(base, "GET", "/api/portfolio/raw")
    existing = next(
        (
            row
            for row in positions
            if row.get("instrument_id") == instrument_id
            and row.get("account_id") == account_id
            and row.get("note") == "QA_INV_EVENT_HISTORY_POSITION_V1"
        ),
        None,
    )
    if existing:
        return {**existing, "id": existing.get("position_id") or existing.get("id")}
    result = _request(
        base,
        "POST",
        "/api/positions",
        {
            "instrument_id": instrument_id,
            "account_id": account_id,
            "quantity": 100,
            "cost_basis": 10,
            "cost_currency": "USD",
            "note": "QA_INV_EVENT_HISTORY_POSITION_V1",
        },
    )
    return result["position"]


def _seed_vests(base: str, instrument_id: str, account_id: str) -> dict:
    grants = _request(base, "GET", "/api/equity/grants")["grants"]
    grant = next((row for row in grants if row.get("note") == GRANT_NOTE), None)
    if grant is None:
        tranches = [{"vest_date": f"2026-01-{day:02d}", "units": 10} for day in range(1, 10)]
        result = _request(
            base,
            "POST",
            "/api/equity/grants",
            {
                "instrument_id": instrument_id,
                "account_id": account_id,
                "grant_date": "2025-01-01",
                "total_units": 90,
                "currency": "USD",
                "tax_event": "deferred",
                "release_required": True,
                "tranches": tranches,
                "note": GRANT_NOTE,
            },
        )
        grant_id = result["grant_id"]
        grant = next(
            row
            for row in _request(base, "GET", "/api/equity/grants")["grants"]
            if row["id"] == grant_id
        )

    for tranche in grant["tranches"]:
        if tranche.get("vested"):
            continue
        _request(
            base,
            "POST",
            f"/api/equity/grants/{grant['id']}/tranches/{tranche['id']}/vest",
            {
                "units": tranche["units"],
                "fmv_per_unit": 20,
                "fmv_currency": "USD",
                "vest_date": tranche["vest_date"],
                "expected_release_date": "2026-01-20",
                "idempotency_key": f"qa-inv-history-vest-{tranche['id']}",
                "note": GRANT_NOTE,
            },
        )

    items = [
        row
        for row in _request(base, "GET", "/api/equity/vest-history")["items"]
        if row.get("grant_id") == grant["id"]
    ]
    if not any(row["release_status"] == "released" for row in items):
        oldest = min(items, key=lambda row: row["vest_date"])
        _request(
            base,
            "POST",
            f"/api/equity/vest-events/{oldest['vest_event_id']}/release",
            {"release_date": "2026-01-21", "note": GRANT_NOTE},
        )
        items = [
            row
            for row in _request(base, "GET", "/api/equity/vest-history")["items"]
            if row.get("grant_id") == grant["id"]
        ]

    statuses = {row["release_status"] for row in items}
    if len(items) < 9 or statuses != {"released", "awaiting_release"}:
        raise RuntimeError(f"vest fixture incomplete: count={len(items)} statuses={statuses}")
    return {"count": len(items), "statuses": sorted(statuses)}


def _seed_income(base: str, position_id: str, account_id: str) -> dict:
    existing = {
        row.get("note")
        for row in _request(base, "GET", "/api/income")["events"]
        if (row.get("note") or "").startswith(INCOME_NOTE_PREFIX)
    }
    kinds = ("dividend", "interest", "distribution")
    for index in range(1, 10):
        note = f"{INCOME_NOTE_PREFIX}{index:02d}"
        if note in existing:
            continue
        is_drp = index == 9
        body = {
            "position_id": position_id,
            "paid_at": f"2026-02-{index:02d}",
            "income_kind": kinds[(index - 1) % len(kinds)],
            "gross_amount": 20 + index,
            "tax_withheld": 1,
            # XTS is ISO 4217's testing currency. One deterministic row therefore proves
            # missing-FX display/provenance without relying on the operator's real rates.
            "currency": "XTS" if index == 9 else "USD",
            "is_drp": is_drp,
            "note": note,
        }
        if is_drp:
            body.update({"drp_quantity": 3.1, "drp_unit_cost": 9.03})
        else:
            body["account_id"] = account_id
        _request(base, "POST", "/api/income", body)

    rows = [
        row
        for row in _request(base, "GET", "/api/income")["events"]
        if (row.get("note") or "").startswith(INCOME_NOTE_PREFIX)
    ]
    observed_kinds = {row["income_kind"] for row in rows}
    delivery = {"reinvested" if row.get("is_drp") else "cash" for row in rows}
    if len(rows) < 9 or observed_kinds != set(kinds) or delivery != {"cash", "reinvested"}:
        raise RuntimeError(
            f"income fixture incomplete: count={len(rows)} kinds={observed_kinds} delivery={delivery}"
        )
    return {"count": len(rows), "kinds": sorted(observed_kinds), "delivery": sorted(delivery)}


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

    # This route exists only when the server booted with ONW_QA_MODE=1. A high
    # localhost port alone is not sufficient proof that writes are disposable.
    try:
        _request(base, "GET", "/api/qa/attest", qa_nonce=args.qa_nonce)
    except RuntimeError as exc:
        parser.error(f"backend is not a QA-mode instance: {exc}")

    grant_account = _account(base, ACCOUNT_GRANT)
    income_account = _account(base, ACCOUNT_INCOME)
    instrument = _instrument(base)
    position = _income_position(base, instrument["id"], income_account["id"])
    result = {
        "vests": _seed_vests(base, instrument["id"], grant_account["id"]),
        "income": _seed_income(base, position["id"], income_account["id"]),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
