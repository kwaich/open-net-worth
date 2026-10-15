"""
One-off backfill: replace instrument `name` values that the CSV migration set from a
*position label* with the real security name from the price source.

  yahoo    → yfinance longName (e.g. "ONWAI.DE" → "ONWAI Inc")
  coingecko→ /coins/list name  (e.g. "bitcoin" → "Bitcoin")
  manual   → left untouched (property / physical names are already meaningful)

Usage:
    python3 -m backend.scripts.backfill_instrument_names            # dry run
    python3 -m backend.scripts.backfill_instrument_names --commit   # apply
"""

from __future__ import annotations

import argparse

from backend.db.instrument_store import InstrumentStore
from backend.services import coingecko_client


def _yahoo_names(symbols: set[str]) -> dict[str, str]:
    from backend.api.prices import _fetch_name

    out: dict[str, str] = {}
    for sym in symbols:
        _, name, _ = _fetch_name(sym)
        if name:
            out[sym] = name
    return out


def _coingecko_names(ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        data = coingecko_client.get_json("/coins/list", {}, timeout=20)
    except Exception:
        return {}
    by_id = {c.get("id"): c.get("name") for c in data}
    return {i: by_id[i] for i in ids if by_id.get(i)}


def backfill(commit: bool = False) -> list[tuple]:
    ins = InstrumentStore()
    insts = ins.all()
    ynames = _yahoo_names(
        {
            i["price_source_id"]
            for i in insts
            if i.get("price_source") == "yahoo" and i.get("price_source_id")
        }
    )
    cgnames = _coingecko_names(
        {
            i["price_source_id"]
            for i in insts
            if i.get("price_source") == "coingecko" and i.get("price_source_id")
        }
    )

    changed: list[tuple] = []
    for i in insts:
        src, pid = i.get("price_source"), i.get("price_source_id")
        newname = (
            ynames.get(pid) if src == "yahoo" else cgnames.get(pid) if src == "coingecko" else None
        )
        if newname and newname != i.get("name"):
            changed.append((i.get("symbol"), i.get("name"), newname))
            if commit:
                ins.touch(i["id"], {"name": newname})
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="apply (default is a dry run)")
    args = ap.parse_args()
    changes = backfill(commit=args.commit)
    for symbol, old, new in changes:
        print(f"  {symbol:10} {old!r:40} → {new!r}")
    verb = "Updated" if args.commit else "Would update"
    print(
        f"\n{verb} {len(changes)} instrument name(s).{'' if args.commit else '  Re-run with --commit to apply.'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
