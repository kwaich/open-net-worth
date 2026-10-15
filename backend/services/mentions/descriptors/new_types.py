"""Descriptors for new mentionable types using the GENERIC provider (Phase 2a).

RFC 2026-06-25-lucius-generic-entity-registry §10 Phase 2.

These are the FIRST real exercises of the generic provider (make_search / make_resolve /
_project) with production stores — no search_override or resolve_override; only
rows + id_field + search_fields + fields + label_field + sublabel_fn.

Types added:
  commitment   (order=16) — financial commitments from CommitmentsStore
  notification (order=17) — the app's notification log, from NotificationLogStore
  alert        (order=18) — price alerts from AlertStore

For all three: lazy in-lambda store imports per the dependency rule
(services must not import db at module scope in a way that couples layers).
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery

# ── commitment (order=16) ──────────────────────────────────────────────────────
# Backed by CommitmentsStore (commitments.json, flat list, id_field="id").
# Nested installments[]/funding_plan[]/actions[] deliberately EXCLUDED — only
# top-level scalar display fields are in the allowlist.  FK ids (linked_position_id,
# linked_account_id) omitted — no join in the generic path, not model-useful as raw ids.


def _commitment_rows(query: SearchQuery) -> list[dict]:
    from backend.db.commitments_store import CommitmentsStore

    return CommitmentsStore().all()


def _commitment_sublabel(row: dict) -> str:
    kind = row.get("kind") or ""
    status = row.get("status") or ""
    return f"{kind} • {status}".strip(" •")


register(
    EntityDescriptor(
        type="commitment",
        label="Commitment",
        icon="⊙",
        order=16,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_commitment_rows,
        id_field="id",
        label_field="name",
        search_fields=("name", "kind", "note"),
        sublabel_fn=_commitment_sublabel,
        fields={
            "name": FieldSpec("Name"),
            "kind": FieldSpec("Kind", "enum"),
            "currency": FieldSpec("Currency"),
            "status": FieldSpec("Status", "enum"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── notification (order=17) ─────────────────────────────────────────────────────
# Backed by NotificationLogStore (table notification_log, id_field="id").
# CHOICE: NotificationLogStore (the titled notification-log feed: id/title/summary/
# severity/type — renamed 2026-08-20 from SignalsStore/signals.json, revision 0044)
# backs this type — NOT IntelSignalsStore (intel_signals.json), which is per-ticker
# signal STATE keyed by ticker/label with no stable discrete-event label and is
# consumed as a per-holding annotation, a poor @-mention target. (This descriptor
# was itself misnamed "intel_signal" pre-rename despite never reading
# IntelSignalsStore — exactly the collision the rename fixes.)
#
# SECURITY NOTE: title/summary/portfolio_impact are EXTERNALLY-SOURCED
# (RSS/LLM-synthesised) untrusted text — they ride the Phase-0 hardened
# format_mention_context fence (sanitize + «mention» fence), so exposure is safe;
# still allowlisted explicitly.  sources/source_urls/affected_tickers arrays
# EXCLUDED (URLs + list shape not model-display fields).


def _notification_rows(query: SearchQuery) -> list[dict]:
    from backend.db.notification_log_store import NotificationLogStore

    return NotificationLogStore().query_signals(limit=50)


def _notification_sublabel(row: dict) -> str:
    severity = row.get("severity") or ""
    sig_type = row.get("type") or ""
    return f"{severity} • {sig_type}".strip(" •")


def _find_notification(ref: str) -> dict | None:
    from backend.db.notification_log_store import NotificationLogStore

    return NotificationLogStore().find_by_id(ref)


register(
    EntityDescriptor(
        type="notification",
        label="Notification",
        icon="◮",
        order=17,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_notification_rows,
        id_field="id",
        label_field="title",
        search_fields=("title", "summary", "type"),
        sublabel_fn=_notification_sublabel,
        find_one=_find_notification,
        fields={
            "title": FieldSpec("Title"),
            "summary": FieldSpec("Summary"),
            "type": FieldSpec("Type", "enum"),
            "severity": FieldSpec("Severity", "enum"),
            "portfolio_impact": FieldSpec("Portfolio impact"),
            "read": FieldSpec("Read"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── alert (order=18) ───────────────────────────────────────────────────────────
# Backed by AlertStore (alerts.json, id_field="id").
# label_field="ticker" — norm_ticker enforces non-empty, so it never falls back to id.
# The optional human `label` field is a search+display field.
#
# `value` is a raw threshold price in the ticker's quote currency, with NO currency
# field paired with it — so it is typed "text" (a bare display scalar), NOT "money":
# it is a user-entered threshold, not a portfolio amount, and typing it money without
# a currency anchor would mislead the renderer + model. _project emits it verbatim.
# instrument_id FK omitted — no join in generic path.


def _alert_rows(query: SearchQuery) -> list[dict]:
    from backend.db.alert_store import AlertStore

    return AlertStore().all()


def _alert_sublabel(row: dict) -> str:
    op = row.get("op") or ""
    value = row.get("value")
    if value is not None:
        return f"{op} {value}"
    return op


register(
    EntityDescriptor(
        type="alert",
        label="Alert",
        icon="⚑",
        order=18,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_alert_rows,
        id_field="id",
        label_field="ticker",
        search_fields=("ticker", "label", "note"),
        sublabel_fn=_alert_sublabel,
        fields={
            "ticker": FieldSpec("Ticker"),
            "op": FieldSpec("Op", "enum"),
            "value": FieldSpec(
                "Value", "text"
            ),  # threshold scalar, no paired currency — not "money"
            "label": FieldSpec("Label"),
            "asset_class": FieldSpec("Asset class", "enum"),
            "note": FieldSpec("Note"),
            "created_at": FieldSpec("Created", "date"),
        },
    )
)

# ── asset (order=35) ─────────────────────────────────────────────────────────────
# Backed by OwnedAssetsStore via services.owned_assets.list_assets() (ADR
# 2026-09-06-assets-tab-faithful-port) — the SERVICE projection, not the raw store: `value`/
# `valued_on` are derived read fields (the newest mark), never persisted on the stored row.
# Non-archived only, mirroring the Assets tab's own default listing. Nested `marks[]`/
# `docs[]`/`facts[]` are deliberately EXCLUDED — only top-level display scalars are
# allowlisted, same rule as commitment's installments/funding_plan/actions above.


def _asset_rows(query: SearchQuery) -> list[dict]:
    from backend.services import owned_assets as owned_assets_service

    return owned_assets_service.list_assets(include_archived=False)


def _asset_sublabel(row: dict) -> str:
    category = row.get("category") or ""
    valued_on = row.get("valued_on") or ""
    return f"{category} • {valued_on}".strip(" •")


register(
    EntityDescriptor(
        type="asset",
        label="Asset",
        icon="⬥",
        order=35,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_asset_rows,
        id_field="id",
        label_field="name",
        search_fields=("name", "category", "description"),
        sublabel_fn=_asset_sublabel,
        fields={
            "name": FieldSpec("Name"),
            "category": FieldSpec("Category", "enum"),
            "value": FieldSpec("Value", "money"),
            "valued_on": FieldSpec("Valued on", "date"),
            "share_pct": FieldSpec("Share", "pct"),
            "notes": FieldSpec("Notes"),
        },
    )
)
