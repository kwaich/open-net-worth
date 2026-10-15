"""golden_budget_snapshot — capture the FULL money surface of Planning for every month that
has data, in a form that is stable across the envelope-id-native refactor (docs/adr/
2026-07-05-envelope-id-native.md) AND the category-opaque-ids migration.

Why: the envelope-id-native refactor changed the budget-row `key` field from a category key
to the envelope id — a WIRE-FORMAT change, not a money change — every actual dollar figure
(assigned, activity, available, target, needed_this_month, RTA, totals) must be byte-identical
before and after. This script captures those figures indexed by `envelope_id` (a field that
exists on every row BOTH before and after that refactor), so a before/after diff proves "the
numbers never moved" independently of the key-format change.

The category-opaque-ids migration adds a SECOND capture (`category_integrity`): every category
row's own id/key is now ALSO the thing being rewritten (a natural key like "food" -> an opaque
"cat_xxx"), so the money-proof above isn't enough on its own — a per-category census keyed by
LABEL (the one field stable across that rename) proves no transaction silently changed which
category it belongs to, independent of the id-format change.

Usage (against an ISOLATED clone — never the live ~/.open-net-worth tree):

    DATA_DIR=<clone>/data CACHE_DIR=<clone>/cache LOGS_DIR=<clone>/logs \\
        .venv/bin/python3 -m backend.scripts.golden_budget_snapshot --out snapshot.json

Re-run this after any change to the Planning money layer and diff the two JSON files
(`diff before.json after.json` — keys are already sorted) to prove the refactor is
numerically invisible.
"""

from __future__ import annotations

import argparse
import json
import sys

# Rows in the derived "Uncategorized"/"Unbudgeted" groups carry no `envelope_id` (they aren't
# envelopes) — index them by their own stable key instead so they still compare cleanly.
_NO_ENVELOPE = "__no_envelope__"


def _row_index(row: dict) -> str:
    env_id = row.get("envelope_id")
    if env_id:
        return env_id
    return f"{_NO_ENVELOPE}:{row.get('key')}"


def _row_money(row: dict) -> dict:
    return {
        "assigned": row.get("assigned"),
        "activity": row.get("activity"),
        "available": row.get("available"),
        "target": row.get("target"),
        "needed_this_month": row.get("needed_this_month"),
        "status": row.get("status"),
    }


def snapshot_month(month: str) -> dict:
    from backend.services import planning

    table = planning.budget_month(month)
    rows_by_envelope: dict[str, dict] = {}
    groups_meta = []
    for g in table.get("groups", []):
        for c in g.get("categories", []):
            rows_by_envelope[_row_index(c)] = _row_money(c)
        groups_meta.append(
            {
                "id": g.get("id"),
                "unsorted": bool(g.get("unsorted")),
                "unbudgeted": bool(g.get("unbudgeted")),
                "synced": bool(g.get("synced")),
                "subtotal": g.get("subtotal"),
            }
        )
    health = planning.planning_health(month)
    return {
        "ready_to_assign": table.get("ready_to_assign"),
        "on_budget_cash": table.get("on_budget_cash"),
        "pre_budget_baseline": table.get("pre_budget_baseline"),
        "held_in_envelopes": table.get("held_in_envelopes"),
        "card_spend_not_covered": table.get("card_spend_not_covered"),
        "assigned_in_future_months": table.get("assigned_in_future_months"),
        "uncategorized_income_base": table.get("uncategorized_income_base"),
        "totals": table.get("totals"),
        "nav": table.get("nav"),
        "rows_by_envelope": rows_by_envelope,
        "groups_meta": sorted(groups_meta, key=lambda g: g["id"] or ""),
        "planning_health": {k: v for k, v in health.items() if k != "age_of_money"},
    }


def _category_label_by_ref() -> dict[str, str]:
    """{category ref: label} covering BOTH the pre-migration shape (rows keyed by ``key``)
    and the post-migration shape (rows keyed by ``id``), so the SAME script run against the
    SAME data dir before/after the category-opaque-ids migration resolves every stored
    category value to the identical label."""
    from backend.db.category_store import CategoryStore

    out: dict[str, str] = {}
    for row in CategoryStore().all():
        ref = row.get("id") or row.get("key")
        if ref:
            out[ref] = row.get("label") or ref
    return out


def capture_category_integrity() -> dict:
    """Per-category txn census + net spend, keyed by LABEL — the one field that does NOT
    change across a category re-key/rename (unlike the id/key itself). A before/after diff of
    this block proves the category-opaque-ids migration never moved a single transaction's
    category assignment, independent of the id-format change. ``__none__`` = unsorted/blank;
    ``split`` = the structural sentinel (its own lines are additionally censused by label)."""
    from backend.services import ledger as ledger_service
    from backend.services.fx_cache import load_fx_cache

    label_by_ref = _category_label_by_ref()

    def _label(cat) -> str:
        if not cat:
            return "__none__"
        if cat == "split":
            return "split"
        return label_by_ref.get(cat, f"__unresolved__:{cat}")

    base = ledger_service.base_currency()
    rates = load_fx_cache()
    txn_count: dict[str, int] = {}
    net_base: dict[str, float] = {}

    def _add(label: str, count: bool, amount: float) -> None:
        if count:
            txn_count[label] = txn_count.get(label, 0) + 1
        net_base[label] = round(net_base.get(label, 0.0) + amount, 2)

    for t in ledger_service.transactions.all():
        if t.get("deleted"):
            continue
        top_label = _label(t.get("category"))
        sign = -1.0 if t.get("kind") == "expense" else 1.0
        splits = t.get("splits") or []
        if splits:
            _add(top_label, True, 0.0)  # the top-level sentinel itself carries no amount
            for s in splits:
                amt = ledger_service.txn_split_base_amount(t, s, base, rates)
                _add(_label(s.get("category")), False, sign * amt)
        else:
            amt = ledger_service.txn_base_amount(t, base, rates)
            _add(top_label, True, sign * amt if t.get("kind") in ("expense", "income") else 0.0)

    return {"txn_count_by_label": txn_count, "net_base_by_label": net_base}


def build_snapshot() -> dict:
    from backend.db.budget_assignments_store import BudgetAssignmentsStore

    months = BudgetAssignmentsStore().months()
    return {
        "months": {m: snapshot_month(m) for m in months},
        "category_integrity": capture_category_integrity(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="write the snapshot JSON here")
    args = ap.parse_args()

    snap = build_snapshot()
    with open(args.out, "w") as f:
        json.dump(snap, f, indent=2, sort_keys=True)
        f.write("\n")
    print(
        f"wrote {args.out}: {len(snap['months'])} month(s) — {sorted(snap['months'].keys())}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
