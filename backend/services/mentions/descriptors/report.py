"""Descriptor for the ``report`` mentionable type — a TYPE CATALOG (generic provider).

Analogous to ``skill``: there is NO persistent report-instance store. A "report" is a
COMPOSITION of read services chosen from a fixed catalog of 5 report TYPES
(``services/reports.list_reports()``). The mentionable rows are those catalog entries,
keyed by the stable report-type slug — exactly like the connector descriptor lists the
synthesized connector views, only here the source is a static type catalog rather than a
sanitizing service over a store.

DATA SOURCE — SECURITY: ``rows`` reads ``services/reports.list_reports()``, which returns
copies of the static ``REPORT_TYPES`` catalog (``type`` / ``title`` / ``description`` /
``uses_period``). No store, no PII, no secrets — every key is a hardcoded catalog string /
boolean. The ``_SECRET_KEY_RE`` denylist would refuse a secret field key anyway; none is
even named here.

STABLE ID: ``id_field="type"`` — the report-type slug (``net_worth_statement``,
``realized_income``, …) is the stable, human-meaningful ref the resolver round-trips on, so
``value == id == type`` for every row (the generic-provider invariant for store-backed-style
rows). ``label_field="title"`` is the display title.

Order continues after connector.py's 23, so report takes 24 — appended at the tail of the
registry sort. (The brief proposed order=20, but that slot is already taken by ``lot``; every
descriptor keeps a UNIQUE order, so report appends at the next free tail slot like connector.)
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery


def _report_rows(query: SearchQuery) -> list[dict]:
    """The fixed report-type catalog — [{type, title, description, uses_period}, ...].

    Reads the static catalog service (``list_reports``) — there is no report store. Each
    row is a non-PII catalog entry; the slug (``type``) is the stable id.
    """
    from backend.services.reports import list_reports

    return list_reports()


def _report_sublabel(row: dict) -> str:
    """A short description / period line.

    Leads with the report's one-line description (clamped) and appends whether the report
    accepts a period selector, so the picker row reads as "<what it is> • <period | as-of>".
    """
    desc = (row.get("description") or "").strip()
    period = "period" if row.get("uses_period") else "as-of"
    short = desc if len(desc) <= 80 else desc[:79].rstrip() + "…"
    return f"{short} • {period}".strip(" •")


register(
    EntityDescriptor(
        type="report",
        label="Report",
        icon="▦",  # ▦ — distinct monochrome glyph, not used by any other type
        order=24,
        tone="neutral",
        surfaces=frozenset({"entity"}),
        rows=_report_rows,
        id_field="type",  # the stable report-type slug
        label_field="title",
        search_fields=("title", "type"),
        sublabel_fn=_report_sublabel,
        fields={
            "title": FieldSpec("Title"),
            "type": FieldSpec("Type", "enum"),
            "description": FieldSpec("Description"),
            "uses_period": FieldSpec("Accepts period"),
        },
    )
)
