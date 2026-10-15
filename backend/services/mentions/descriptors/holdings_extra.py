"""position, option_contract, asset_brief, news_source.

Four domain objects that had no mention type. `position` is the notable one: `holding` (the
hydrated portfolio view) and `lot` (a tax lot) were both mentionable, but the stored position
row sitting between them was not — so "this position" resolved to neither.

Every label here is hydrated through the instruments store, because these rows key on
`instrument_id` and a bare id is not something anyone can pick out of a list.
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery


def _symbols() -> dict[str, tuple[str, str]]:
    """instrument_id -> (symbol, name). One read, reused across rows."""
    from backend.db.instrument_store import InstrumentStore

    out: dict[str, tuple[str, str]] = {}
    for row in InstrumentStore().all():
        out[row.get("id", "")] = (row.get("symbol") or "", row.get("name") or "")
    return out


def _accounts() -> dict[str, str]:
    from backend.db.account_store import AccountStore

    return {a.get("id", ""): a.get("name", "") for a in AccountStore().all()}


# ── position (order=31) ───────────────────────────────────────────────────────


def _position_rows(query: SearchQuery) -> list[dict]:
    from backend.db.position_store import PositionStore

    syms, accts = _symbols(), _accounts()
    rows = []
    for row in PositionStore().all():
        symbol, name = syms.get(row.get("instrument_id", ""), ("", ""))
        rows.append(
            {
                **row,
                "symbol": symbol or row.get("instrument_id", ""),
                "name": name,
                "account": accts.get(row.get("account_id", ""), ""),
            }
        )
    return rows


def _position_sublabel(row: dict) -> str:
    parts = [row.get("account") or "", f"{row.get('quantity') or 0} units", row.get("bucket") or ""]
    return " • ".join(p for p in parts if p)


register(
    EntityDescriptor(
        type="position",
        label="Position",
        icon="▤",
        order=31,
        tone="neutral",
        rows=_position_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "name", "account", "note"),
        sublabel_fn=_position_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "name": FieldSpec("Name"),
            "account": FieldSpec("Account"),
            "quantity": FieldSpec("Quantity", "qty"),
            "cost_basis": FieldSpec("Cost basis", "money"),
            "cost_currency": FieldSpec("Cost currency"),
            "bucket": FieldSpec("Bucket", "enum"),
            "acquired_at": FieldSpec("Acquired", "date"),
            "note": FieldSpec("Note"),
        },
    )
)


# ── option_contract (order=32) ────────────────────────────────────────────────


def _option_contract_rows(query: SearchQuery) -> list[dict]:
    from backend.db.option_contract_store import OptionContractStore

    syms, accts = _symbols(), _accounts()
    rows = []
    for row in OptionContractStore().all():
        symbol, _ = syms.get(row.get("underlying_instrument_id", ""), ("", ""))
        symbol = symbol or row.get("underlying_instrument_id", "")
        rows.append(
            {
                **row,
                "underlying": symbol,
                "account": accts.get(row.get("account_id", ""), ""),
                "label": f"{symbol} {row.get('strike', '')}{str(row.get('option_type', ''))[:1].upper()} {row.get('expiry', '')}".strip(),
            }
        )
    return rows


def _option_contract_sublabel(row: dict) -> str:
    parts = [str(row.get("side") or ""), str(row.get("status") or ""), row.get("account") or ""]
    return " · ".join(p for p in parts if p)


register(
    EntityDescriptor(
        type="option_contract",
        label="Option contract",
        icon="⬢",
        order=32,
        tone="neutral",
        rows=_option_contract_rows,
        id_field="id",
        label_field="label",
        search_fields=("label", "underlying", "account"),
        sublabel_fn=_option_contract_sublabel,
        fields={
            "underlying": FieldSpec("Underlying"),
            "account": FieldSpec("Account"),
            "option_type": FieldSpec("Type", "enum"),
            "side": FieldSpec("Side", "enum"),
            "status": FieldSpec("Status", "enum"),
            "strike": FieldSpec("Strike", "money"),
            "expiry": FieldSpec("Expiry", "date"),
            "contracts": FieldSpec("Contracts", "qty"),
            "multiplier": FieldSpec("Multiplier", "qty"),
        },
    )
)


# ── asset_brief (order=33) ────────────────────────────────────────────────────
# Keyed by instrument_id — it has no id of its own. Only scalar fields are declared:
# facts/peers/key_issues are nested structures and would render as "[object Object]". `news`
# isn't a field on this store at all (removed, RFC intel-signals-data-model §5 — the Asset
# Detail news feed is a live query over `evidence`, not a stored asset_brief field).


def _asset_brief_rows(query: SearchQuery) -> list[dict]:
    from backend.db.asset_brief_store import AssetBriefStore

    syms = _symbols()
    rows = []
    for row in AssetBriefStore().all():
        iid = row.get("instrument_id", "")
        symbol, name = syms.get(iid, ("", ""))
        rows.append(
            {
                "id": iid,
                "instrument_id": iid,
                "symbol": symbol or iid,
                "name": name,
                "description": row.get("description", ""),
                "portfolio_impact": row.get("portfolio_impact", ""),
                "refresh_stage": row.get("refresh_stage", ""),
            }
        )
    return rows


def _asset_brief_sublabel(row: dict) -> str:
    return row.get("name") or "Research brief"


register(
    EntityDescriptor(
        type="asset_brief",
        label="Research brief",
        icon="❐",
        order=33,
        tone="neutral",
        rows=_asset_brief_rows,
        id_field="id",
        label_field="symbol",
        search_fields=("symbol", "name", "description"),
        sublabel_fn=_asset_brief_sublabel,
        fields={
            "symbol": FieldSpec("Symbol"),
            "name": FieldSpec("Name"),
            "description": FieldSpec("Description"),
            "portfolio_impact": FieldSpec("Portfolio impact"),
            "refresh_stage": FieldSpec("Refresh stage", "enum"),
        },
    )
)


# ── news_source (order=34) ────────────────────────────────────────────────────


def _news_source_rows(query: SearchQuery) -> list[dict]:
    from backend.db.news_source_store import NewsSourceStore

    return NewsSourceStore().all()


def _news_source_sublabel(row: dict) -> str:
    parts = [row.get("kind") or "", "on" if row.get("enabled") else "off"]
    return " · ".join(p for p in parts if p)


register(
    EntityDescriptor(
        type="news_source",
        label="News source",
        icon="◰",
        order=34,
        tone="neutral",
        rows=_news_source_rows,
        id_field="id",
        label_field="label",
        search_fields=("label", "kind", "notes"),
        sublabel_fn=_news_source_sublabel,
        fields={
            "label": FieldSpec("Label"),
            "kind": FieldSpec("Kind", "enum"),
            "enabled": FieldSpec("Enabled"),
            "cadence": FieldSpec("Cadence"),
            "notes": FieldSpec("Notes"),
        },
    )
)
