"""
Bank statement reconciliation: parse any uploaded bank statement and diff it
against system transactions for an account.

Parsing is delegated to the UNIFIED statement parser (``services/statements``, RFC
statement-import-unified) — the same tiered engine the import flow uses (tabular
schema-inference · text extraction · vision for scanned pages), so reconcile and import can
never disagree about what a statement says. This module keeps only the reconcile-specific
shape (flat ``{date, amount, direction, description}`` rows) and the differ.
"""

from __future__ import annotations

import logging
from collections import Counter

from backend.services.statements.canonical import StatementMeta
from backend.services.statements.parser import parse_statement

logger = logging.getLogger(__name__)


async def parse_bank_statement(
    upload: dict,
) -> tuple[list[dict], StatementMeta, dict | None, str]:
    """
    Parse any bank statement upload into ``(rows, meta, reconcile, tier)``:
      rows: [{date: "YYYY-MM-DD", amount: float, direction: "IN"|"OUT", description: str}]
      meta: the parse's ``StatementMeta`` — carries ``extra["parse_complete"]`` and, when the
            statement is a scan, ``extra["incomplete_pages"]``
      reconcile: the guard verdict ({"status": "ok"|"ok_if_flipped"|"off"|"unknown", ...})
      tier: which tier read it ("tabular-schema" | "tabular-profile" | "text" | "vision") —
            a tabular parse is zero-LLM and complete by construction even when its own meta
            carries no ``parse_complete`` key (a legacy record cached before that key existed)

    `upload` is the entry returned by backend.api.upload.get_upload().
    Supports kind="text" (CSV/xlsx or text-extracted PDF) and kind="pdf_pages"
    (scanned / image PDF).

    ``meta``/``reconcile``/``tier`` exist so a caller can apply the completeness gate (see
    ``ai/tools/statements.py::diff_statement``) BEFORE treating "a system row this
    statement doesn't mention" as guidance to delete it — a parse that never finished
    reading every page/chunk, or a statement whose own balances don't add up, has no basis
    for that claim (security review, ADR 2026-09-09-an-incomplete-parse-withholds-the-
    ledger-diff — same rule, this module's OWN differ instead of ``ledger_diff.py``'s)."""
    result = await parse_statement(upload)
    rows = [
        {
            "date": row.date,
            "amount": row.amount,
            "direction": row.direction,
            "description": row.description,
        }
        for row in result.rows
        if not row.skip
    ]
    return rows, result.meta, result.reconcile, result.tier


# ── Differ ──────────────────────────────────────────────────────────────────


def diff_against_system(
    bank_txns: list[dict],
    sys_txns: list[dict],
    account_id: str,
) -> dict:
    """
    Compare bank-statement rows against system transactions for `account_id`.

    Returns:
      {date_from, date_to, bank_count, system_count, matched, missing, extra}

    missing: in bank but not in system → should be added
    extra:   in system but not in bank → may need deletion
    """
    if not bank_txns:
        return {"error": "No transactions parsed from bank statement"}

    dates = [t["date"] for t in bank_txns]
    date_from = min(dates)
    date_to = max(dates)

    # Collect active system legs for this account within the statement date range
    sys_legs: list[dict] = []
    for t in sys_txns:
        if t.get("deleted"):
            continue
        d = t.get("date", "")
        if not (date_from <= d <= date_to):
            continue
        if t.get("from_account_id") == account_id:
            sys_legs.append(
                {
                    "date": d,
                    "amount": float(t.get("from_amount") or 0),
                    "direction": "OUT",
                    "description": t.get("description", ""),
                    "id": t["id"],
                }
            )
        elif t.get("to_account_id") == account_id:
            sys_legs.append(
                {
                    "date": d,
                    "amount": float(t.get("to_amount") or 0),
                    "direction": "IN",
                    "description": t.get("description", ""),
                    "id": t["id"],
                }
            )

    def _key(t: dict) -> tuple:
        return (t["date"], t["direction"], round(t["amount"], 2))

    bank_counter = Counter(_key(t) for t in bank_txns)
    sys_counter = Counter(_key(t) for t in sys_legs)

    missing_counter = bank_counter - sys_counter
    extra_counter = sys_counter - bank_counter

    def _expand(source: list[dict], counter: Counter) -> list[dict]:
        remaining = list(source)
        result: list[dict] = []
        for key, count in counter.items():
            found = 0
            keep = []
            for t in remaining:
                if found < count and _key(t) == key:
                    result.append(t)
                    found += 1
                else:
                    keep.append(t)
            remaining = keep
        return sorted(result, key=lambda t: t["date"])

    missing = _expand(bank_txns, missing_counter)
    extra = _expand(sys_legs, extra_counter)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "bank_count": len(bank_txns),
        "system_count": len(sys_legs),
        "matched": len(bank_txns) - len(missing),
        "missing": missing,
        "extra": extra,
    }
