"""Price-alert tools — standalone, ticker-keyed alerts (ADR 0014).

Alerts are independent of watchlist membership: setting one never adds the ticker to a
watchlist, and removing a ticker from a watchlist never deletes its alerts. All mutations
route through the services.alerts choke point (never db directly).

Batch-pluralized 2026-07-19 (owner directive: every AI tool that acts on ONE thing should
accept MANY): ``get_alerts``'s ``ticker`` filter widened to accept a LIST; the three write
tools each gained a batch shape and were renamed to their plural form —
``set_price_alert`` -> ``set_price_alerts``, ``update_price_alert`` -> ``update_price_alerts``,
``remove_price_alert`` -> ``remove_price_alerts`` — with the former singular name kept as a
registry alias (a tool name is persisted data). The single-target call stays exactly as
trivial as before: the pre-batch top-level fields are still accepted bare and wrapped into a
one-item batch internally. Batches are NOT atomic — each alert is an independent write, so
one bad id/value reports its own error and every other item still lands.

**Param-arity follow-up (2026-07-19d, ``backend/scripts/tool_inventory.py --lint``):** the
pass above widened ``ticker``/``alert_id`` to accept a list but kept their SINGULAR name —
the exact trap that made the model stringify ``delete_account(account_id='["a","b"]')``
instead of passing an array. Renamed: ``get_alerts.ticker`` -> ``tickers``,
``remove_price_alerts.alert_id`` -> ``alert_ids``, ``update_price_alerts.alert_id`` ->
``alert_ids`` (top-level broadcast key only — the per-item ``alert_id`` inside ``updates``
stays singular, an attribute of the one item in that slot). A param rename is not a tool
rename — no alias-table entry, just read-side tolerance (``_dual_ids``, mirroring
``payees.py``'s ``_payee_ids_param``/``ledger.py``'s ``_dual_filter``): the new plural key
is read first, falling back to the pre-rename singular key, so a stored/scripted caller or
an in-flight session on the old key keeps working. The old key is deliberately NOT
re-declared in the schema — that would re-trip this exact linter rule for a decoy the
model is never meant to pick.
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional, Union

from pydantic import Field

from backend.ai.tools import _bounded as _b
from backend.ai.tools._shared import no_op_write_result
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    require_nonempty,
    schema_of,
    schema_of_batch_only,
)
from backend.core.errors import NotFoundError, ValidationError
from backend.services import alerts as _alerts

_Op = Literal[">=", "<=", ">", "<"]
_Kind = Literal["price", "move"]

# get_alerts had no cap at all before this fix — mirrors payees.py's own file-local
# default (not every bounded read's default belongs in _bounded.py).
_DEFAULT_ALERT_LIMIT = 50

# ── Batch shape helpers (self-contained per-file, mirroring payees.py/watchlist.py) ───


def _as_list(value) -> list[str]:
    """Normalize a target that may arrive as a bare string OR a list into a plain list."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _dual_ids(input_data: dict, new_key: str, old_key: str) -> list[str]:
    """Read a multi-target id/value list under its current PLURAL name, falling back to
    the pre-2026-07-19d singular name — a param rename needs no alias-table entry the way
    a TOOL rename does, just this read-side tolerance (mirrors ``payees.py``'s
    ``_payee_ids_param``/``ledger.py``'s ``_dual_filter``), so a stored/scripted caller or
    an in-flight session still on the old key keeps working."""
    raw = input_data.get(new_key)
    if raw is None:
        raw = input_data.get(old_key)
    return _as_list(raw)


def _items_or_bare(input_data: dict, array_key: str, bare_keys: tuple[str, ...]) -> list[dict]:
    """The ``add_transactions`` shape-leniency convention: an explicit array is used
    verbatim; otherwise the top-level bare fields wrap into ONE item."""
    raw = input_data.get(array_key)
    if raw is None:
        bare = {k: input_data[k] for k in bare_keys if k in input_data}
        return [bare] if bare else []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _broadcast_or_updates(
    input_data: dict,
    updates_key: str,
    id_key: str,
    shared_fields: tuple[str, ...],
    *,
    ids_key: Optional[str] = None,
) -> list[dict]:
    """An explicit ``updates_key`` array of per-target dicts takes precedence; otherwise
    the top-level broadcast id(s) (scalar or list) apply the SAME shared field values to
    every id. ``id_key`` is also the PER-ITEM dict field name emitted below and read back
    inside ``updates_key`` entries — an attribute of the one item in that slot, correctly
    singular. ``ids_key``, when given, is the current PLURAL top-level broadcast key
    (falls back to ``id_key`` for a pre-2026-07-19d caller still on the singular name)."""
    raw = input_data.get(updates_key)
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    raw_ids = input_data.get(ids_key) if ids_key else None
    if raw_ids is None:
        raw_ids = input_data.get(id_key)
    ids = _as_list(raw_ids)
    if not ids:
        return []
    shared = {k: input_data[k] for k in shared_fields if k in input_data}
    return [{id_key: v, **shared} for v in ids]


def _batch_result(results: list[dict], *, count_key: str) -> dict:
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_count == len(results), count_key: ok_count, "results": results}


class GetAlertsInput(ToolInput):
    """Schema-of-record for ``get_alerts`` (see ``schema_from_model``)."""

    tickers: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Filter to one symbol, e.g. 'AAPL', or a LIST to match any of several.",
    )
    limit: Optional[int] = Field(
        default=None,
        description=f"Max rows to return (default {_DEFAULT_ALERT_LIMIT}). Page further with offset.",
    )
    offset: Optional[int] = Field(
        default=None, description="Rows to skip before applying limit. Default 0."
    )


@tool(
    "get_alerts",
    description=(
        "List price alerts (optionally for one ticker, or a LIST of tickers). Independent of "
        f"watchlists. An unfiltered call returns up to {_DEFAULT_ALERT_LIMIT} rows — every "
        "response reports `showing` and `total` so you can always tell whether you're looking "
        "at a subset."
    ),
    input_schema=schema_of(GetAlertsInput, tool_name="get_alerts"),
)
async def get_alerts(input_data: dict):
    tickers = _dual_ids(input_data, "tickers", "ticker")
    total = len(_alerts.list_alerts(None))
    if len(tickers) <= 1:
        rows = _alerts.list_alerts(tickers[0] if tickers else None)
    else:
        wanted = {t.upper() for t in tickers}
        rows = [a for a in _alerts.list_alerts(None) if (a.get("ticker") or "").upper() in wanted]
    matched = len(rows)
    offset = max(0, int(input_data.get("offset") or 0))
    limit_raw = input_data.get("limit")
    limit = int(limit_raw) if limit_raw not in (None, "") else _DEFAULT_ALERT_LIMIT
    page, _total_after_offset = _b.cap(rows[offset:], limit)
    showing = len(page)

    out: dict = {"alerts": page, "showing": showing, "total": total}
    notes: list[str] = []
    if showing == 0:
        if total == 0:
            notes.append("The user has no price alerts set.")
        elif matched == 0:
            notes.append(
                f"0 of {total} alerts match these filters — they DO exist, your filter "
                "excluded them. Drop or loosen the filter and retry. Do NOT report that the "
                "user has no alerts."
            )
        else:
            notes.append(
                f"offset={offset} is past the end of the {matched} matching alerts — "
                "retry with a smaller offset (e.g. offset=0). Do NOT report that the user has "
                "no alerts."
            )
    elif offset + showing < matched:
        notes.append(
            f"Showing {showing} of {matched} matching alerts (total {total}) — "
            f"page with offset={offset + showing}."
        )
    elif matched < total:
        notes.append(
            f"Showing {showing} of {matched} alerts — filtered, not truncated (total {total})."
        )
    if notes:
        out["note"] = " ".join(notes)
    return json.dumps(out, ensure_ascii=False), None


# ── set_price_alerts ─────────────────────────────────────────────────────────────────

_TICKER_DESC = "Symbol, e.g. 'AAPL'"
_ALERT_VALUE_DESC = "Price level in the asset's currency; a percent (0-100] if kind='move'"
_ALERT_KIND_DESC = (
    "'price' (default) or 'move': value is a % move vs. previous close ('drops 5% today' "
    "= '<=' 5); '>=' fires on a rise of value%, '<=' on a fall."
)
_ALERT_NOTE_DESC = "Instructions for Lucius when it fires."
_ALERT_LABEL_DESC = "Display name (defaults to ticker)"
_ALERT_NAME_DESC = "Short display name for the alert, e.g. 'Stop-loss zone'. Optional."
# Verified against price_monitor._run_alert_lane: firing is RISING-EDGE on `op`/`value`
# only — the lane reads currency/instrument from the portfolio row, never the alert's own
# asset_class. Shared with the top-level twins so both shapes carry the same text.
_ALERT_OP_DESC = (
    "Comparison direction: the alert fires when live price `op` `value` ('>=' 200 fires "
    "at or above 200; '<=' 90 is a stop). Rising-edge — it fires ONCE on the crossing, "
    "re-arms when the condition goes false again, and is not deleted by firing."
)
_ALERT_ASSET_CLASS_DESC = (
    "Classifies the alert for display/grouping in the Intel UI: equity | crypto | "
    "commodity | property | other. Defaults to 'equity'. Does NOT affect when the alert "
    "fires (that is op/value alone) and is unrelated to crash-watch thresholds, which are "
    "set per class in update_monitoring_config.crash_thresholds."
)


_ALERT_CHANNELS_DESC = (
    "Where this alert is ALSO sent when it fires, besides the in-app feed: channel ids "
    "('telegram', 'email', 'webhook'; get_alert_channels lists what's set up). [] = in-app "
    "only. Omit on create to use the user's price-alert default."
)


class SetAlertItem(NestedInput):
    """Schema-of-record for a ``set_price_alerts.alerts`` batch item (see
    ``schema_from_model``) — the SAME full field docs as the top-level singular fields
    below, since the literal reused one dict for both (unlike rules.py's bare-typed
    batch-item twins)."""

    ticker: str = Field(description=_TICKER_DESC)
    op: _Op = Field(description=_ALERT_OP_DESC)
    value: float = Field(description=_ALERT_VALUE_DESC)
    kind: Optional[_Kind] = Field(default=None, description=_ALERT_KIND_DESC)
    note: Optional[str] = Field(default=None, description=_ALERT_NOTE_DESC)
    label: Optional[str] = Field(default=None, description=_ALERT_LABEL_DESC)
    name: Optional[str] = Field(default=None, description=_ALERT_NAME_DESC)
    channels: Optional[List[str]] = Field(default=None, description=_ALERT_CHANNELS_DESC)
    asset_class: Optional[str] = Field(default=None, description=_ALERT_ASSET_CLASS_DESC)


class SetPriceAlertsInput(ToolInput):
    """Schema-of-record for ``set_price_alerts`` (see ``schema_from_model``)."""

    ticker: Optional[str] = Field(default=None, description=_TICKER_DESC)
    op: Optional[_Op] = Field(default=None, description=_ALERT_OP_DESC)
    value: Optional[float] = Field(default=None, description=_ALERT_VALUE_DESC)
    kind: Optional[_Kind] = Field(default=None, description=_ALERT_KIND_DESC)
    note: Optional[str] = Field(default=None, description=_ALERT_NOTE_DESC)
    label: Optional[str] = Field(default=None, description=_ALERT_LABEL_DESC)
    name: Optional[str] = Field(default=None, description=_ALERT_NAME_DESC)
    channels: Optional[List[str]] = Field(default=None, description=_ALERT_CHANNELS_DESC)
    asset_class: Optional[str] = Field(default=None, description=_ALERT_ASSET_CLASS_DESC)
    alerts: List[SetAlertItem] = Field(
        default_factory=list,
        description=(
            "Create MULTIPLE alerts in one call: [{ticker, op, value, kind?, note?, "
            "label?, name?, channels?, asset_class?}, ...]. Omit when creating a single alert via the "
            "top-level fields above."
        ),
    )


@tool(
    "set_price_alerts",
    description=(
        "Create one or many price alerts on a ticker ('alert me when X hits Y', 'set a stop "
        "at 90', 'X drops 5% today' via kind='move'). No watchlist needed; each alert is one "
        "condition on one ticker. The note is "
        "what Lucius does when it fires (e.g. 'take-profit: check if the move has legs "
        "before trimming').\n\n"
        "Single alert: ticker + op + value (+ optional kind/note/label/name/asset_class). "
        "Setting up monitoring on several tickers/levels in one call: `alerts` — [{ticker, "
        "op, value, kind?, note?, label?, name?, asset_class?}, ...]. Each alert is created "
        "independently."
    ),
    # Batch array only — the bare single-item fields are still ACCEPTED by the handler,
    # just not advertised (see schema_from_model.schema_of_batch_only).
    input_schema=schema_of_batch_only(
        SetPriceAlertsInput, array="alerts", tool_name="set_price_alerts"
    ),
    mutates=("alerts",),
)
async def set_price_alerts(input_data: dict):
    items = _items_or_bare(
        input_data,
        "alerts",
        ("ticker", "op", "value", "kind", "note", "label", "name", "channels", "asset_class"),
    )
    if not items:
        return json.dumps(
            {"ok": False, "error": "ticker, op and value (or alerts) is required"}
        ), None

    single = len(items) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    for i, item in enumerate(items):
        try:
            alert = _alerts.apply_alert_change(
                {
                    "type": "create",
                    "ticker": item.get("ticker", ""),
                    "op": item.get("op", ""),
                    "value": item.get("value"),
                    "kind": item.get("kind") or "price",
                    "note": item.get("note", ""),
                    "label": item.get("label", ""),
                    "name": item.get("name", ""),
                    "asset_class": item.get("asset_class", "equity"),
                    **({"channels": item["channels"]} if item.get("channels") is not None else {}),
                }
            )
            results.append({"index": i, "ok": True, "id": alert["id"]})
            if single:
                single_value = alert
        except ValidationError as e:
            results.append({"index": i, "error": str(e)})

    out = _batch_result(results, count_key="created")
    if single:
        if single_value is not None:
            out["alert"] = single_value
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── update_price_alerts ───────────────────────────────────────────────────────────────


# Shared between the `updates[]` batch item and its top-level broadcast twin below. The
# nested shape shipped blank while the top-level carried the real text — the same drift
# SetAlertItem.op had. One constant per field so they cannot diverge again.
_UPDATE_ALERT_OP_DESC = (
    "New comparison direction for the trigger — the alert fires when the live price "
    "is `op` `value` (e.g. '>=' 200 fires at or above 200). Omit to leave the "
    "existing direction unchanged."
)
_UPDATE_ALERT_VALUE_DESC = (
    "New trigger price level, in the asset's own quote currency (not the portfolio "
    "base); a percent for a 'move' alert. Omit to leave it unchanged."
)
_UPDATE_ALERT_KIND_DESC = "'price' level or 'move' % vs. previous close; send a new value too."
_UPDATE_ALERT_NOTE_DESC = (
    "Replacement instructions for Lucius to act on when the alert fires (e.g. "
    "'take-profit: check if the move has legs before trimming'). Replaces the whole "
    "note, never appends. Omit to leave it unchanged."
)
_UPDATE_ALERT_LABEL_DESC = (
    "Replacement display name for the alert (defaults to the ticker when the alert "
    "was created). Omit to leave it unchanged."
)
_UPDATE_ALERT_NAME_DESC = (
    "Replacement short display name for the alert, e.g. 'Stop-loss zone'. Omit to leave "
    "it unchanged."
)


class UpdateAlertItem(NestedInput):
    """Schema-of-record for an ``update_price_alerts.updates`` batch item (see
    ``schema_from_model``)."""

    alert_id: str = Field(description="UUID of the alert")
    op: Optional[_Op] = Field(default=None, description=_UPDATE_ALERT_OP_DESC)
    value: Optional[float] = Field(default=None, description=_UPDATE_ALERT_VALUE_DESC)
    kind: Optional[_Kind] = Field(default=None, description=_UPDATE_ALERT_KIND_DESC)
    note: Optional[str] = Field(default=None, description=_UPDATE_ALERT_NOTE_DESC)
    label: Optional[str] = Field(default=None, description=_UPDATE_ALERT_LABEL_DESC)
    name: Optional[str] = Field(default=None, description=_UPDATE_ALERT_NAME_DESC)
    channels: Optional[List[str]] = Field(default=None, description=_ALERT_CHANNELS_DESC)


class UpdatePriceAlertsInput(ToolInput):
    """Schema-of-record for ``update_price_alerts`` (see ``schema_from_model``)."""

    # Annotated as the bare (non-null) Union — the literal's `alert_ids` anyOf has NO null
    # branch (unlike get_alerts.tickers above), even though the field is optional (absent
    # from `required`). `default=None` makes it non-required without widening the type to
    # Optional[...], which would leak a spurious third `null` anyOf branch transform 2 can't
    # collapse (it only fires on the exact 2-branch X-or-null shape). The model is schema-only
    # — never instantiated against real input — so the type/default mismatch is inert.
    alert_ids: Union[str, List[str]] = Field(
        default=None,
        description="UUID of the alert, or a list to set the SAME change on several at once.",
    )
    op: Optional[_Op] = Field(default=None, description=_UPDATE_ALERT_OP_DESC)
    value: Optional[float] = Field(default=None, description=_UPDATE_ALERT_VALUE_DESC)
    kind: Optional[_Kind] = Field(default=None, description=_UPDATE_ALERT_KIND_DESC)
    note: Optional[str] = Field(default=None, description=_UPDATE_ALERT_NOTE_DESC)
    label: Optional[str] = Field(default=None, description=_UPDATE_ALERT_LABEL_DESC)
    name: Optional[str] = Field(default=None, description=_UPDATE_ALERT_NAME_DESC)
    channels: Optional[List[str]] = Field(default=None, description=_ALERT_CHANNELS_DESC)
    updates: List[UpdateAlertItem] = Field(
        default_factory=list,
        description=(
            "Per-alert overrides when different alerts need different changes in "
            "one call: [{alert_id, op?, value?, kind?, note?, label?, name?, channels?}, ...]. Takes "
            "precedence over the top-level fields above."
        ),
    )


@tool(
    "update_price_alerts",
    description=(
        "Change one or many alerts' op/value/kind, note, label, name, or channels by id (get_alerts "
        "to find it).\n\n"
        "Single alert: alert_ids (one id) + the field(s) to change. The SAME change on "
        "SEVERAL alerts: alert_ids as a LIST. Different changes per alert in one call: "
        "`updates` — [{alert_id, op?, value?, kind?, note?, label?, name?, channels?}, ...]."
    ),
    input_schema=schema_of(UpdatePriceAlertsInput, tool_name="update_price_alerts"),
    mutates=("alerts",),
)
async def update_price_alerts(input_data: dict):
    entries = _broadcast_or_updates(
        input_data,
        "updates",
        "alert_id",
        ("op", "value", "kind", "note", "label", "name", "channels"),
        ids_key="alert_ids",
    )
    if not entries:
        return json.dumps({"ok": False, "error": "alert_ids (or updates) is required"}), None

    single = len(entries) == 1
    results: list[dict] = []
    single_value: Optional[dict] = None
    single_no_op_echo: Optional[dict] = None
    updated_count = 0
    no_op_count = 0
    for i, entry in enumerate(entries):
        alert_id = entry.get("alert_id", "")
        changes = {
            k: entry[k]
            for k in ("op", "value", "kind", "note", "label", "name", "channels")
            if k in entry
        }
        if not changes:
            # Nothing to change on this row — guard BEFORE the store/service write.
            # AlertStore.update's own `if not patch: return find_by_id(...)` no-ops the
            # write silently, and apply_alert_change still records an "update" (write
            # receipt) on that no-op — so routing through it would report a write that
            # never happened. Look the row up directly instead (a read, never a mutation)
            # so a genuinely missing id still reports "not found" rather than a false
            # no_op.
            existing = _alerts.alert_store().find_by_id(alert_id)
            if existing is None:
                results.append({"index": i, "error": "alert not found"})
                continue
            results.append({"index": i, "ok": True, "no_op": True, "id": alert_id})
            no_op_count += 1
            if single:
                single_no_op_echo = existing
            continue
        try:
            alert = _alerts.apply_alert_change({"type": "update", "id": alert_id, **changes})
            results.append({"index": i, "ok": True, "id": alert_id})
            updated_count += 1
            if single:
                single_value = alert
        except NotFoundError:
            results.append({"index": i, "error": "alert not found"})
        except ValidationError as e:
            results.append({"index": i, "error": str(e)})

    if single and single_no_op_echo is not None:
        return (
            json.dumps(no_op_write_result({"alert": single_no_op_echo}), ensure_ascii=False),
            None,
        )

    out: dict = {
        "ok": (updated_count + no_op_count) == len(results),
        "updated": updated_count,
        "results": results,
    }
    if no_op_count:
        out["no_op_count"] = no_op_count
        out["note"] = (
            f"{no_op_count} of {len(entries)} alert(s) carried no field that could change "
            "anything and were left as-is — see each row's own `no_op` flag in results."
        )
    if single:
        if single_value is not None:
            out["alert"] = single_value
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None


# ── remove_price_alerts ───────────────────────────────────────────────────────────────


class RemovePriceAlertsInput(ToolInput):
    """Schema-of-record for ``remove_price_alerts`` (see ``schema_from_model``)."""

    alert_ids: Union[str, List[str]] = Field(
        min_length=1,
        json_schema_extra=require_nonempty,
        description="UUID of the alert, or a list to delete several at once.",
    )


@tool(
    "remove_price_alerts",
    description=(
        "Delete one or many price alerts by id (get_alerts to find it).\n\n"
        "Pass alert_ids — one id, or a LIST to delete several in one call — each is "
        "independent, so one not-found id doesn't block the rest."
    ),
    input_schema=schema_of(RemovePriceAlertsInput, tool_name="remove_price_alerts"),
    mutates=("alerts",),
)
async def remove_price_alerts(input_data: dict):
    ids = _dual_ids(input_data, "alert_ids", "alert_id")
    if not ids:
        return json.dumps({"ok": False, "error": "alert_ids is required"}), None

    single = len(ids) == 1
    results: list[dict] = []
    for i, alert_id in enumerate(ids):
        try:
            _alerts.apply_alert_change({"type": "delete", "id": alert_id})
            results.append({"index": i, "ok": True, "id": alert_id})
        except NotFoundError:
            results.append({"index": i, "error": "alert not found"})

    # count_key is "deleted_count" (not "deleted") so the single-item branch below can set
    # "deleted" as the original literal bool without a name collision.
    out = _batch_result(results, count_key="deleted_count")
    if single:
        if results[0].get("ok"):
            out["deleted"] = True
        else:
            out["error"] = results[0]["error"]
    return json.dumps(out, ensure_ascii=False), None
