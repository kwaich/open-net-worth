"""Owned-assets write rules — every refusal the ONE write choke
(``services.owned_assets.apply_owned_asset_change``) can raise: length bounds, number and date
parsing, the purchase-first rule and the reference checks (the asset exists; a loan link points
at a real loan). Nothing here writes.

Each refusal is ONE plain sentence (the RFC assets-create-edit-redesign §3.11 catalogue) that
the UI shows beside ``field`` and Lucius receives verbatim; ``reason`` tells the 409s apart.
The bounds are enforced HERE as well as on the API models (security review, M3), so a raw dict
change — an AI tool, a test, a future caller — cannot bypass the wire's limits.
"""

from __future__ import annotations

import math
import re
from datetime import date

from sqlalchemy import Connection

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.user_time import user_today
from backend.db.account_store import AccountStore
from backend.db.owned_assets_store import OwnedAssetsStore

MAX_NAME = 120
MAX_DESCRIPTION = 300
MAX_NOTES = 4000
MAX_SHARE_NOTE = 200
MAX_FACT_FIELD = 120
MAX_FACTS = 40
MAX_CATEGORY_LABEL = 60
MAX_MARK_NOTE = 300
MAX_DOC_NAME = 200
MAX_DOC_NOTE = 1000  # the same cap as any note in Documents (services.vault.MAX_NOTE)

LOAN_TYPES = frozenset({"loan"})
RETIRED_FIELDS = frozenset(
    {"cadence_months", "automation_id", "months_old", "automation", "automate"}
)
# The identity fields `update` may change. Everything else has its own op — a value is
# recorded (add_mark) or corrected (correct_mark), a sale is dispose — or is never written.
EDITABLE_FIELDS = frozenset(
    {
        "name",
        "category",
        "description",
        "notes",
        "cost",
        "cost_date",
        "share_pct",
        "share_note",
        "loan_account_id",
        "insured",
        "maturity_date",
        "facts",
    }
)

NAME_REFUSAL = "Give it a name (up to 120 characters)."
FUTURE_PURCHASE = "The purchase date can't be in the future."
FUTURE_VALUE = "The date can't be in the future."
FUTURE_SALE = "The sale date can't be in the future."

_TOO_LONG = {
    "description": "The description can be up to 300 characters.",
    "notes": "Notes can be up to 4000 characters.",
    "share_note": "Who you share it with can be up to 200 characters.",
    "note": "The note can be up to 300 characters.",
    "label": "A category name can be up to 60 characters.",
    "name": "A document name can be up to 200 characters.",
    "doc_note": "A note can be up to 1000 characters.",
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_label(iso_day: str) -> str:
    """The date form every message uses: 2019-03-15 reads "15 Mar 2019". A fixed English
    table rather than strftime, whose month names follow the server's locale."""
    day = date.fromisoformat(iso_day)
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def refuse_retired(payload: dict) -> None:
    invalid = RETIRED_FIELDS.intersection(payload)
    if invalid:
        raise ValidationError(
            f"These retired asset fields are no longer accepted: {', '.join(sorted(invalid))}."
        )


def refuse_uneditable(changes: dict) -> None:
    unknown = sorted(set(changes) - EDITABLE_FIELDS)
    if not unknown:
        return
    if "value" in unknown:
        raise ValidationError(
            "The value can't be edited here — record a new value instead.", field="value"
        )
    raise ValidationError(f"'{unknown[0]}' can't be changed on an asset.", field=unknown[0])


# ── Text ─────────────────────────────────────────────────────────────────────────────────


def bounded_text(value, limit: int, field: str) -> str:
    text = str(value or "")
    if len(text) > limit:
        detail = _TOO_LONG.get(field, f"'{field}' can be up to {limit} characters.")
        raise ValidationError(detail, field=field)
    return text


def asset_name(value) -> str:
    name = str(value or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ValidationError(NAME_REFUSAL, field="name")
    return name


def bounded_facts(facts) -> list[list[str]]:
    facts = list(facts or [])
    if len(facts) > MAX_FACTS:
        raise ValidationError(f"An asset can hold up to {MAX_FACTS} details.", field="facts")
    out: list[list[str]] = []
    for pair in facts:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValidationError("Each detail needs a label and a value.", field="facts")
        label, value = (str(part or "") for part in pair)
        if label.strip().lower() in {"insured", "matures", "maturity date"}:
            raise ValidationError(
                f"'{label}' has its own field — set it there, not as a detail.", field="facts"
            )
        if len(label) > MAX_FACT_FIELD or len(value) > MAX_FACT_FIELD:
            raise ValidationError(
                f"A detail's label and value can be up to {MAX_FACT_FIELD} characters each.",
                field="facts",
            )
        out.append([label, value])
    return out


def insured_flag(value) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("Insured must be true or false.", field="insured")
    return value


def identity_changes(changes: dict) -> dict:
    """An update's patch with every field parsed and bounded. `category` and
    `loan_account_id` are left to the caller, which checks them against the book."""
    clean = dict(changes)
    if "name" in clean:
        clean["name"] = asset_name(clean["name"])
    for key, limit in (
        ("description", MAX_DESCRIPTION),
        ("notes", MAX_NOTES),
        ("share_note", MAX_SHARE_NOTE),
    ):
        if key in clean:
            clean[key] = bounded_text(clean[key], limit, key)
    if "cost" in clean:
        clean["cost"] = cost(clean["cost"])
    if "share_pct" in clean:
        clean["share_pct"] = share_pct(clean["share_pct"])
    if "cost_date" in clean:
        clean["cost_date"] = parse_day(
            clean["cost_date"], field="cost_date", future=FUTURE_PURCHASE
        )
    if "maturity_date" in clean:
        clean["maturity_date"] = parse_day(clean["maturity_date"], field="maturity_date")
    if "insured" in clean:
        clean["insured"] = insured_flag(clean["insured"])
    if "facts" in clean:
        clean["facts"] = bounded_facts(clean["facts"])
    return clean


# ── Numbers ──────────────────────────────────────────────────────────────────────────────


def _number(value) -> float | None:
    """A finite float, or None for anything that isn't one — a bool, NaN/Infinity (H1: they
    poison every sum they touch), or text that doesn't parse. Never raises."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def positive_value(value) -> float:
    number = _number(value)
    if number is None or number <= 0:
        raise ValidationError("The value must be more than 0.", field="value")
    return number


def share_pct(value) -> float:
    number = _number(value)
    if number is None or not 0 <= number <= 100:
        raise ValidationError("Your share must be between 0 and 100%.", field="share_pct")
    return number


def cost(value) -> float | None:
    """The purchase price — blank (None/"") clears it; otherwise a number above 0."""
    if value is None or value == "":
        return None
    number = _number(value)
    if number is None or number <= 0:
        raise ValidationError(
            "The price must be more than 0 — leave it blank if you don't know it.", field="cost"
        )
    return number


def proceeds(value) -> float:
    number = _number(value)
    if number is None:
        raise ValidationError("Enter the proceeds — 0 for a write-off.", field="proceeds")
    if number < 0:
        raise ValidationError("Proceeds can't be negative.", field="proceeds")
    return number


# ── Dates ────────────────────────────────────────────────────────────────────────────────


def parse_day(value, *, field: str, future: str | None = None) -> str | None:
    """A full ISO date (YYYY-MM-DD) for `field`; None/"" is blank. `future` is the refusal for
    a date after today — values, purchases and sales are observations of something that
    already happened; omitted (a maturity date), a future date is fine."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    parsed = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            pass
    if parsed is None:
        raise ValidationError("Use a full date (YYYY-MM-DD).", field=field)
    if future is not None and parsed > user_today():
        raise ValidationError(future, field=field)
    return parsed.isoformat()


def refuse_value_before_purchase(day: str, asset: dict) -> None:
    """Purchase-first: no valuation may be dated before cost_date. Checked only for a NEW or
    CORRECTED value date, so a legacy row that already breaks the rule stays editable."""
    cost_date = asset.get("cost_date")
    if cost_date and day < cost_date:
        what = "the policy started" if asset.get("category") == "policy" else "the purchase"
        raise ConflictError(
            f"A value can't be dated before {what} ({day_label(cost_date)}).",
            reason="before_purchase",
            field="date",
        )


def refuse_purchase_after_first_value(cost_date: str | None, asset: dict) -> None:
    """Purchase-first, from the other side: cost_date can't move after the earliest value.
    Checked only for a CHANGED cost_date; clearing it is always fine."""
    days = [m["date"] for m in asset.get("marks") or [] if m.get("date")]
    if cost_date and days and cost_date > min(days):
        raise ConflictError(
            f"The purchase date can't be after {day_label(min(days))}, the first value you "
            "recorded. Record a value on or after the purchase date, then remove the older one.",
            reason="after_first_value",
            field="cost_date",
        )


def refuse_purchase_after_sale(cost_date: str | None, asset: dict) -> None:
    """Purchase-first, against the sale: a CHANGED cost_date can't move past Sold on."""
    sold_on = asset.get("disposed_on")
    if cost_date and sold_on and cost_date > sold_on:
        raise ConflictError(
            f"The purchase date can't be after the sale ({day_label(sold_on)}).",
            reason="after_sale",
            field="cost_date",
        )


def refuse_sale_before_purchase(day: str, asset: dict) -> None:
    cost_date = asset.get("cost_date")
    if cost_date and day < cost_date:
        raise ConflictError(
            f"The sale date can't be before the purchase ({day_label(cost_date)}).",
            reason="before_purchase",
            field="date",
        )


# ── References ───────────────────────────────────────────────────────────────────────────


def require_asset(
    store: OwnedAssetsStore, asset_id: str, *, conn: Connection | None = None
) -> dict:
    asset = store.find_by_id(asset_id, conn=conn)
    if asset is None:
        raise NotFoundError(f"That asset ('{asset_id}') no longer exists.")
    return asset


def require_valuation(asset: dict, mark_id: str | None) -> dict:
    """The stored valuation `mark_id` names. The purchase point (id "purchase") is derived
    from cost/cost_date, so it can be neither corrected nor removed — only moved or cleared
    by editing the purchase."""
    if mark_id == "purchase":
        raise ConflictError(
            "The purchase point comes from the purchase price and date — change or clear "
            "them in Purchase.",
            reason="purchase_point",
        )
    mark = next((m for m in asset.get("marks") or [] if m.get("id") == mark_id), None)
    if mark is None:
        raise NotFoundError("That value no longer exists.")
    return mark


def validate_loan_account_id(loan_account_id: str | None) -> str | None:
    """`loan_account_id` is an FK-by-convention (owned_assets_store's own docstring), never
    enforced by the DB — this choke is the ONE place that checks it actually points at a real
    loan. `None`/"" clears the link (the detail rail's "remove loan" case)."""
    if not loan_account_id:
        return None
    account = AccountStore().find_by_id(loan_account_id)
    if account is None or account.get("archived") or account.get("type") not in LOAN_TYPES:
        raise ValidationError(
            "That account isn't one of your active loans.", field="loan_account_id"
        )
    return loan_account_id


def loan_holders(rows: list[dict]) -> dict[str, dict]:
    """`{loan_id: {"id", "name"}}` — the OLDEST live asset each loan secures. Only legacy data
    can hold a double link (the write path refuses one); the oldest holder is the one named."""
    holders: dict[str, dict] = {}
    live = [a for a in rows if not a.get("archived") and a.get("loan_account_id")]
    for asset in sorted(live, key=lambda a: a.get("created_at") or ""):
        holders.setdefault(
            asset["loan_account_id"], {"id": asset["id"], "name": asset.get("name") or ""}
        )
    return holders


def assert_loan_free(loan_id: str, *, asset_id: str | None, rows: list[dict]) -> None:
    """A loan secures at most one live asset. The caller checks only when a link is made or
    CHANGED, so re-saving the loan an asset already has is never refused."""
    holder = loan_holders([a for a in rows if a.get("id") != asset_id]).get(loan_id)
    if holder is None:
        return
    loan = AccountStore().find_by_id(loan_id) or {}
    raise ConflictError(
        f"{loan.get('name') or 'That loan'} already secures “{holder['name']}”. A loan can "
        "secure one asset — unlink it there first.",
        reason="loan_in_use",
        field="loan_account_id",
        holder=holder,
    )


def changed_loan_link(
    store: OwnedAssetsStore, asset: dict, loan_id, *, conn: Connection
) -> str | None:
    """The loan link an update stores, checked only when it CHANGES: the Ownership editor
    re-sends the link with every save, so an asset whose loan has since been closed must
    still save its share (code review R4)."""
    loan_id = loan_id or None
    if loan_id == (asset.get("loan_account_id") or None):
        return loan_id
    loan_id = validate_loan_account_id(loan_id)
    if loan_id and asset.get("archived"):
        raise ValidationError("A sold asset can't secure a loan.", field="loan_account_id")
    if loan_id:
        assert_loan_free(loan_id, asset_id=asset.get("id"), rows=store.all(conn=conn))
    return loan_id
