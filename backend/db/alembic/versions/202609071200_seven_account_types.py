"""Seven account types on one axis (ADR 2026-09-07-seven-account-types-one-axis).

Remaps every stored account `type` to the seven-value vocabulary, re-derives `kind`, strips the
retired payload keys (asset_class / locked / on_budget / group), and converts property-like
accounts (real_estate / vehicle / collectible / other_asset, plus any non-liability row whose
`asset_class` is "property") into owned assets carrying one mark per leg date, so the net-worth
HISTORY keeps the same ladder rather than only today's figure. Fail-closed: an unknown type
aborts before any write. Downgrade is lossy (coarse reverse map; owned assets stay).

Revision ID: 202609071200_seven_account_types
Revises: 202609061000_owned_assets
Create Date: 2026-09-07
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "202609071200_seven_account_types"
down_revision = "202609061000_owned_assets"
branch_labels = None
depends_on = None
logger = logging.getLogger(__name__)

_CASH = {"checking", "savings", "cash", "ewallet", "debit_card", "money_market", "bank_transfer"}
_INVESTMENT = {"brokerage", "crypto_exchange", "custody"}
_RETIREMENT = {"pension", "retirement_acct", "govt_fund"}
_CREDIT = {"credit_card", "line_of_credit", "bnpl"}
_LOAN = {"mortgage", "personal_loan", "auto_loan", "student_loan", "margin", "other_debt"}
CONVERT_TO_OWNED_ASSET = {
    "real_estate": "property",
    "vehicle": "vehicle",
    "collectible": "other",
    "other_asset": "other",
}
_NEW = {"cash", "fixed_deposit", "investment", "retirement", "receivable", "credit", "loan"}
_LIABILITY = {"credit", "loan"}
_REVERSE = {
    "cash": "checking",
    "fixed_deposit": "savings",
    "investment": "brokerage",
    "retirement": "retirement_acct",
    "receivable": "loan_receivable",
    "credit": "credit_card",
    "loan": "other_debt",
}
_DEAD_KEYS = ("asset_class", "locked", "on_budget", "group")
# services.owned_assets.CATEGORIES review cadences for the categories this migration mints.
_CADENCE = {"property": 6, "vehicle": 12, "other": 12}


def _locked(payload: dict) -> bool:
    """`locked` was a free-form payload key, so a CSV/JSON import could have written the STRING
    "false" — truthy in Python. A bare truth test would silently move that savings account into
    `fixed_deposit`, out of the runway baseline, with no way back."""
    value = payload.get("locked")
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def new_type(old: str, payload: dict) -> str:
    if old in _NEW:
        return old
    if old in _CASH:
        return "fixed_deposit" if _locked(payload) else "cash"
    if old in _INVESTMENT:
        return "investment"
    if old in _RETIREMENT:
        return "retirement"
    if old == "loan_receivable":
        return "receivable"
    if old in _CREDIT:
        return "credit"
    if old in _LOAN:
        return "loan"
    if old in CONVERT_TO_OWNED_ASSET:
        # Placeholder only: `type` is NOT NULL and must be one of the seven. The row is
        # archived and its value moves to an owned asset — this is never a real receivable.
        return "receivable"
    raise RuntimeError(f"seven_account_types: unknown account type {old!r} — refusing to migrate")


def _kind(t: str) -> str:
    return "liability" if t in _LIABILITY else "asset"


def _load(p):
    return json.loads(p) if isinstance(p, str) else (p or {})


def _f(x):
    return None if x is None else float(x)


def account_ladder_sql(conn, account_id: str, opening_txn_id):
    """The account's balance AS OF each distinct leg date, oldest-first, plus its opening facts.

    Mirror of services.ledger.account_balance: skip deleted; skip legs dated strictly before the
    opening row's date (the Starting Balance row itself counts); -from_amount when from,
    +to_amount when to. Returns ``([(date, cumulative_balance), ...], opening_date,
    opening_amount)``.

    Per-DATE rather than one final figure because the converted account leaves the net-worth
    fold entirely: an owned asset is valued by the newest mark ON OR BEFORE a date
    (``owned_assets_valuation.value_as_of``), so a single mark dated today would value the
    property at nothing in every historical month and drop the whole net-worth history by its
    value. One mark per leg date reproduces the old ladder exactly."""
    opening_date = None
    opening_amount = None
    if opening_txn_id:
        r = conn.execute(
            sa.text("SELECT date, from_amount, to_amount FROM transactions WHERE id = :i"),
            {"i": opening_txn_id},
        ).fetchone()
        if r is not None:
            opening_date = r[0]
            raw = _f(r[2]) if r[2] is not None else (-_f(r[1]) if r[1] is not None else None)
            opening_amount = None if raw is None else round(raw, 2)
    rows = conn.execute(
        sa.text(
            "SELECT date, from_account_id, to_account_id, from_amount, to_amount FROM transactions "
            "WHERE deleted = 0 AND (from_account_id = :i OR to_account_id = :i)"
        ),
        {"i": account_id},
    ).fetchall()
    per_date: dict[str, float] = {}
    for d, frm, to, fa, ta in rows:
        if opening_date and d < opening_date:
            continue
        delta = 0.0
        if frm == account_id and fa is not None:
            delta -= float(fa)
        if to == account_id and ta is not None:
            delta += float(ta)
        per_date[d] = per_date.get(d, 0.0) + delta
    ladder: list[tuple[str, float]] = []
    running = 0.0
    for d in sorted(per_date):
        running += per_date[d]
        ladder.append((d, round(running, 2)))
    return ladder, opening_date, opening_amount


def account_balance_sql(conn, account_id: str, opening_txn_id):
    """The account's balance today — the last rung of ``account_ladder_sql``."""
    ladder, opening_date, opening_amount = account_ladder_sql(conn, account_id, opening_txn_id)
    return (ladder[-1][1] if ladder else 0.0), opening_date, opening_amount


def _next_seq(conn, table: str, book_id: str) -> int:
    n = conn.execute(
        sa.text(f"SELECT COALESCE(MAX(seq), 0) FROM {table} WHERE book_id = :b"), {"b": book_id}
    ).scalar()
    return int(n or 0) + 1


def _marks(ladder: list[tuple[str, float]], now: datetime) -> list[dict]:
    """One mark per rung, NEWEST-FIRST (owned_assets_store._sort_marks' one ordering rule),
    with a strictly decreasing ``created_at`` per list position so the store's (date,
    created_at) tiebreak agrees with this order even if two rungs ever shared a date."""
    return [
        {
            "id": "mk_" + secrets.token_hex(6),
            "date": d,
            "value": value,
            "source": "Migrated from account ledger",
            "by": "you",
            "created_at": (now - timedelta(microseconds=i)).isoformat(),
        }
        for i, (d, value) in enumerate(reversed(ladder))
    ]


def _owned_asset_payload(
    acct: dict, category: str, ladder: list[tuple[str, float]], opening_date, opening_amount
) -> dict:
    """The db.owned_assets_store.create() row shape, built without importing the store —
    a migration must keep working when that module's defaults later move on."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    return {
        "id": "oa_" + secrets.token_hex(6),
        "name": acct.get("name") or "",
        "category": category,
        "description": "",
        "photo_ref": None,
        "facts": [],
        "notes": acct.get("note") or "",
        "cost": opening_amount,
        "cost_date": opening_date,
        "share_pct": 100.0,
        "share_note": "",
        "loan_account_id": None,
        "cadence_months": _CADENCE.get(category, 12),
        "automation_id": None,
        # str(): the payload is verbatim stored JSON, so `currency` is not guaranteed to be a
        # string. A bare `.upper()` on anything else aborts the boot migration with an
        # AttributeError naming nothing.
        "currency": str(acct.get("currency") or "").upper(),
        "marks": _marks(ladder, now_dt),
        "docs": [],
        # An archived account is already OUT of the net-worth fold. Minting its owned asset
        # live would resurrect its value as a brand-new addition to net worth.
        "archived": bool(acct.get("archived")),
        "disposed_on": acct.get("disposed_on") or None,
        "disposal_proceeds": None,
        "disposal_txn_id": None,
        "disposal_account_id": None,
        "created_at": now,
        "updated_at": now,
    }


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, book_id, name, type, currency, archived, opening_txn_id, payload "
            "FROM accounts"
        )
    ).fetchall()
    plan = []
    for acc_id, book_id, name, old_type, currency, archived, opening_txn_id, payload in rows:
        p = _load(payload)
        old = p.get("type") or old_type
        # A live payload can omit a key its promoted column carries (db/tables.py's own
        # verbatim-payload note) — the column is the fallback, never the other way round. In
        # particular a missing `archived` must not silently UN-archive the row on the way past.
        p.setdefault("archived", bool(archived))
        source = {
            **p,
            "name": p.get("name") or name or "",
            "currency": p.get("currency") or currency or "",
        }
        mapped = new_type(old, p)  # raises on an unknown type, before any write
        # ONLY a property-like row moves to the Assets tab. Any other `asset_class` value is
        # just a retired key: it is stripped and the row is remapped by its type.
        convert = old in CONVERT_TO_OWNED_ASSET or (
            p.get("asset_class") == "property" and mapped not in _LIABILITY
        )
        plan.append((acc_id, book_id, old, opening_txn_id, p, source, convert))
    for acc_id, book_id, old, opening_txn_id, p, source, convert in plan:
        if convert:
            category = CONVERT_TO_OWNED_ASSET.get(old, "property")
            ladder, opening_date, opening_amount = account_ladder_sql(conn, acc_id, opening_txn_id)
            oa = _owned_asset_payload(source, category, ladder, opening_date, opening_amount)
            conn.execute(
                sa.text(
                    "INSERT INTO owned_assets (book_id, seq, id, payload) VALUES (:b, :s, :i, :p)"
                ),
                {
                    "b": book_id,
                    "s": _next_seq(conn, "owned_assets", book_id),
                    "i": oa["id"],
                    "p": json.dumps(oa),
                },
            )
            p["archived"] = True
            t = "receivable"
        else:
            t = new_type(old, p)
        p["type"] = t
        p["kind"] = _kind(t)
        for dead in _DEAD_KEYS:
            p.pop(dead, None)
        conn.execute(
            sa.text(
                "UPDATE accounts SET type = :t, kind = :k, archived = :a, payload = :p "
                "WHERE id = :i"
            ),
            {
                "t": t,
                "k": p["kind"],
                "a": 1 if p.get("archived") else 0,
                "p": json.dumps(p),
                "i": acc_id,
            },
        )
    logger.info("seven_account_types: remapped %d accounts", len(plan))


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, type, payload FROM accounts")).fetchall()
    for acc_id, col_type, payload in rows:
        p = _load(payload)
        # `accounts.type` is NOT NULL: fall back to the promoted column rather than ever
        # writing the payload's missing key straight into it.
        current = p.get("type") or col_type
        t = _REVERSE.get(current, current)
        if not t:
            continue
        if current == "fixed_deposit":
            p["locked"] = True
        p["type"] = t
        conn.execute(
            sa.text("UPDATE accounts SET type = :t, payload = :p WHERE id = :i"),
            {"t": t, "p": json.dumps(p), "i": acc_id},
        )
