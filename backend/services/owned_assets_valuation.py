"""Owned-assets valuation — the value rule and the net-worth seam (ADR
2026-09-06-assets-tab-faithful-port, decision 6; RFC assets-create-edit-redesign §2.1).
Deliberately narrow imports: the store + ``core.fx`` + stdlib only, NO ``services.ledger`` —
this is what lets the ledger lane import THIS module (``ledger.py`` folding manual assets into
``net_worth()`` / ``net_worth_history``) without a cycle.

Stored marks are valuations only. The purchase point is DERIVED on read, never stored: it
exists only when the category is not purchaseless, cost is finite and > 0, and cost_date is
set. The value on a date is the newest point on or before it, across the valuations plus the
purchase point; on the same day a valuation outranks the purchase point, and a purchase point
dated after the newest valuation is left out. So today's value is always the newest valuation
— no cost, cost_date or category edit can move it — while history before the first valuation
reads the purchase price from the purchase date on.
"""

from __future__ import annotations

import math

from backend.core.fx import to_base
from backend.db.owned_assets_store import OwnedAssetsStore

# A policy's "cost" is premiums paid so far — placed at its start date it is not a value the
# policy ever had, so it never becomes a purchase point.
PURCHASELESS_CATEGORIES = frozenset({"policy"})

_RANK = {"valuation": 1, "purchase": 0}

# Reachability + net-worth banding (decision 6): every owned asset is illiquid — you cannot
# spend a house or a bar of gold today — but it still needs an asset-class band for
# allocation/reporting. `property` and `metal` get their own dedicated bands (they already
# have one in the wider net-worth model); everything else — vehicles, watches, art,
# policies, and any user-created category — folds into `other_asset`.
_BAND_BY_CATEGORY = {"property": "property", "metal": "commodity"}


def band_of(category: str | None) -> str:
    return _BAND_BY_CATEGORY.get(category or "", "other_asset")


def purchase_point(asset: dict) -> dict | None:
    cost, cost_date = asset.get("cost"), asset.get("cost_date")
    if asset.get("category") in PURCHASELESS_CATEGORIES or not cost_date:
        return None
    if cost is None or not math.isfinite(float(cost)) or float(cost) <= 0:
        return None
    return {
        "id": "purchase",
        "kind": "purchase",
        "date": cost_date,
        "value": float(cost),
        "note": "",
        "by": "you",
        "created_at": asset.get("created_at") or "",
        "edited_at": None,
    }


def series(asset: dict) -> list[dict]:
    """Every point that values the asset, newest first by (date, rank, created_at, id): the
    stored valuations, stamped ``kind: "valuation"``, plus the purchase point when it is dated
    on or before the newest valuation."""
    points = [
        {**m, "kind": "valuation", "edited_at": m.get("edited_at")}
        for m in asset.get("marks") or []
    ]
    purchase = purchase_point(asset)
    if purchase and points and purchase["date"] <= max(p.get("date") or "" for p in points):
        points.append(purchase)
    return sorted(
        points,
        key=lambda p: (
            p.get("date") or "",
            _RANK[p["kind"]],
            p.get("created_at") or "",
            p.get("id") or "",
        ),
        reverse=True,
    )


def current_mark(asset: dict) -> dict | None:
    """The point today's value comes from — always the newest valuation, or None when the
    asset has no valuations at all."""
    points = series(asset)
    return points[0] if points else None


def value_as_of(asset: dict, as_of: str | None) -> float | None:
    """The newest point on or before `as_of` (ISO date), or the current point when `as_of` is
    None. None if nothing values the asset on that date (e.g. `as_of` predates both the
    purchase and the first valuation) — never 0.0, which would be a real value."""
    for point in series(asset):
        if as_of is None or (point.get("date") or "") <= as_of:
            return point.get("value")
    return None


def share_adjusted_base(
    asset: dict, base: str, *, as_of: str | None = None, rates: dict | None = None
) -> float | None:
    """The asset's contribution to net worth on `as_of` (today when None): its value × its
    share, converted to `base`. The ONE place the band, category and composition folds take
    that figure from; None when nothing values the asset on that date."""
    value = value_as_of(asset, as_of)
    if value is None:
        return None
    share = (asset.get("share_pct") if asset.get("share_pct") is not None else 100) / 100.0
    return to_base(value * share, asset.get("currency") or base, base, rates)


def _owned_on(asset: dict, as_of: str | None) -> bool:
    """Today's view (`as_of` None) excludes every `archived` row; a historical `as_of` excludes
    only a row whose `disposed_on` is on or before it — it was still owned, and still worth
    something, on an earlier date even though it has since been sold."""
    if as_of is None:
        return not asset.get("archived")
    disposed_on = asset.get("disposed_on")
    return not (disposed_on and disposed_on <= as_of)


def values_by_band_from_rows(
    rows: list[dict], base: str, *, as_of: str | None = None, rates: dict | None = None
) -> dict[str, float]:
    """Pure fold over an already-loaded set of owned-asset rows — see `values_by_band`'s
    docstring for the exclusion/valuation rules. Split out so a caller charting many `as_of`
    points (``ledger.net_worth_history``) can load ``OwnedAssetsStore().all()`` ONCE and
    re-fold it per point, instead of re-reading the store on every charted month."""
    totals: dict[str, float] = {}
    for asset in rows:
        if not _owned_on(asset, as_of):
            continue
        value = share_adjusted_base(asset, base, as_of=as_of, rates=rates)
        if value is None:
            continue
        band = band_of(asset.get("category"))
        totals[band] = totals.get(band, 0.0) + value
    return {band: round(total, 2) for band, total in totals.items()}


def values_by_category_from_rows(
    rows: list[dict], base: str, *, rates: dict | None
) -> dict[str, float]:
    """Today's share-adjusted, base-converted value per category key — the same fold as
    `values_by_band_from_rows`, grouped finer, so the two always sum to the same total."""
    totals: dict[str, float] = {}
    for asset in rows:
        if not _owned_on(asset, None):
            continue
        value = share_adjusted_base(asset, base, rates=rates)
        if value is None:
            continue
        key = asset.get("category") or "other"
        totals[key] = totals.get(key, 0.0) + value
    return {key: round(total, 2) for key, total in totals.items()}


def values_by_band(
    base: str, *, as_of: str | None = None, rates: dict | None = None
) -> dict[str, float]:
    """One-shot wrapper: loads every owned asset then folds it via
    `values_by_band_from_rows`. Share-adjusted (`value × share_pct/100`), base-converted asset
    values, banded by `band_of(category)`. Excludes an asset already disposed as of the query
    date: `as_of is None` excludes every `archived` row (today's view); a historical `as_of`
    instead excludes only a row whose `disposed_on` is on or before that date — it was still
    owned, and still worth something, on an earlier as-of date even though it has since been
    sold. A caller that needs MANY as-of points (``ledger.net_worth_history``) should load
    the rows once and call `values_by_band_from_rows` directly instead of this wrapper."""
    return values_by_band_from_rows(OwnedAssetsStore().all(), base, as_of=as_of, rates=rates)
