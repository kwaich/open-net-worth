"""Descriptors for the three dated things that hang off a commitment, plus the profile.

A commitment's detail screen is four sub-tabs — Overview / Schedule / Funding / Actions &
events — and the last three each render a list of rows the owner talks about by name ("that
installment", "the share sale funding it", "the contract signing"). Each has a globally unique
id minted by CommitmentsStore (``inst-``/``fs-``/``act-`` + 8 hex), so they resolve cleanly.

This does NOT contradict the commitment descriptor's "nested installments[]/funding_plan[]/
actions[] deliberately EXCLUDED" note in new_types.py. That note is about the commitment's own
FIELD PAYLOAD — a commitment row must not inline its children. Whether the children are
addressable in their own right is a separate question, and the answer here is yes.

``profile`` is the odd one out and is here because it is the same kind of omission: a real,
singular thing the owner refers to ("my base currency", "my retirement age") that simply never
got a descriptor. It has no id of its own, so it uses the same synthetic-ref shape the other
singletons use.
"""

from __future__ import annotations

from backend.services.mentions._collect import register
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.types import SearchQuery


def _commitments() -> list[dict]:
    from backend.db.commitments_store import CommitmentsStore

    return CommitmentsStore().all()


def _flatten(child_key: str) -> list[dict]:
    """Every child row across every commitment, stamped with its parent for context.

    The parent name is what makes these rows legible in the picker — an installment
    labelled "Tranche 2" is meaningless on its own.
    """
    out: list[dict] = []
    for commitment in _commitments():
        for child in commitment.get(child_key) or []:
            out.append(
                {
                    **child,
                    "commitment_id": commitment.get("id", ""),
                    "commitment_name": commitment.get("name", ""),
                    "currency": child.get("currency") or commitment.get("currency", ""),
                }
            )
    return out


# ── installment (order=27) ────────────────────────────────────────────────────


def _installment_rows(query: SearchQuery) -> list[dict]:
    rows = _flatten("installments")
    for row in rows:
        # label is frequently blank on generated schedules; fall back to something nameable.
        if not row.get("label"):
            row["label"] = f"{row.get('commitment_name', '')} instalment".strip()
    return rows


def _installment_sublabel(row: dict) -> str:
    parts = [row.get("commitment_name") or "", row.get("due_date") or "", row.get("status") or ""]
    return " • ".join(p for p in parts if p)


register(
    EntityDescriptor(
        type="installment",
        label="Installment",
        icon="◔",
        order=27,
        tone="neutral",
        rows=_installment_rows,
        id_field="id",
        label_field="label",
        search_fields=("label", "commitment_name", "description", "due_date"),
        sublabel_fn=_installment_sublabel,
        fields={
            "label": FieldSpec("Label"),
            "commitment_name": FieldSpec("Commitment"),
            "description": FieldSpec("Description"),
            "due_date": FieldSpec("Due", "date"),
            "amount": FieldSpec("Amount", "money"),
            "currency": FieldSpec("Currency"),
            "status": FieldSpec("Status", "enum"),
        },
    )
)


# ── funding_source (order=28) ─────────────────────────────────────────────────


def _funding_source_rows(query: SearchQuery) -> list[dict]:
    rows = _flatten("funding_plan")
    for row in rows:
        if not row.get("label"):
            row["label"] = (row.get("type") or "source").replace("_", " ")
    return rows


def _funding_source_sublabel(row: dict) -> str:
    parts = [
        (row.get("type") or "").replace("_", " "),
        row.get("commitment_name") or "",
        "disabled" if row.get("disabled") else "",
    ]
    return " · ".join(p for p in parts if p)


register(
    EntityDescriptor(
        type="funding_source",
        label="Funding source",
        icon="⊞",
        order=28,
        tone="neutral",
        rows=_funding_source_rows,
        id_field="id",
        label_field="label",
        search_fields=("label", "commitment_name", "type", "note"),
        sublabel_fn=_funding_source_sublabel,
        fields={
            "label": FieldSpec("Label"),
            "commitment_name": FieldSpec("Commitment"),
            "type": FieldSpec("Type", "enum"),
            "amount": FieldSpec("Amount", "money"),
            "currency": FieldSpec("Currency"),
            "available_date": FieldSpec("Available", "date"),
            "cadence": FieldSpec("Cadence"),
            "disabled": FieldSpec("Disabled"),
            "note": FieldSpec("Note"),
        },
    )
)


# ── commitment_action (order=29) ──────────────────────────────────────────────
# Named commitment_action, not action: `actions` is already a DIFFERENT store
# (backend/db, Wave 5C, natural integer ids) and a bare `action` type would read as that one.


def _commitment_action_rows(query: SearchQuery) -> list[dict]:
    return _flatten("actions")


def _commitment_action_sublabel(row: dict) -> str:
    parts = [row.get("commitment_name") or "", row.get("due_date") or "", row.get("status") or ""]
    return " · ".join(p for p in parts if p)


register(
    EntityDescriptor(
        type="commitment_action",
        label="Commitment event",
        icon="⌂",
        order=29,
        tone="neutral",
        rows=_commitment_action_rows,
        id_field="id",
        label_field="label",
        search_fields=("label", "commitment_name", "category", "counterparty", "note"),
        sublabel_fn=_commitment_action_sublabel,
        fields={
            "label": FieldSpec("Label"),
            "commitment_name": FieldSpec("Commitment"),
            "category": FieldSpec("Category"),
            "due_date": FieldSpec("Due", "date"),
            "start_by": FieldSpec("Start by", "date"),
            "amount": FieldSpec("Amount", "money"),
            "currency": FieldSpec("Currency"),
            "status": FieldSpec("Status", "enum"),
            "counterparty": FieldSpec("Counterparty"),
            "note": FieldSpec("Note"),
        },
    )
)


# ── profile (order=30) ────────────────────────────────────────────────────────
# A singleton: one row, synthetic id "profile". No secrets — the allowlist below is the
# planning/preference surface the owner actually talks about, not the whole model.


def _profile_rows(query: SearchQuery) -> list[dict]:
    from backend.services.countries import country_name
    from backend.services.profile import read_profile

    try:
        data = read_profile()
    except Exception:
        return []

    def _joined(key: str, *, names: bool = False) -> str:
        vals = data.get(key) or []
        if names:
            vals = [country_name(v) for v in vals if v]
        return ", ".join(v for v in vals if v)

    row = {
        **data,
        "id": "profile",
        "name": "Profile",
        "nationalities": _joined("nationalities", names=True),
        "countries_of_residence": _joined("countries_of_residence", names=True),
        "tax_residencies": _joined("tax_residencies", names=True),
        "employment_types": _joined("employment_types"),
    }
    return [row]


def _profile_sublabel(row: dict) -> str:
    parts = [row.get("base_currency") or "", row.get("countries_of_residence") or ""]
    return " · ".join(p for p in parts if p) or "Your settings and assumptions"


register(
    EntityDescriptor(
        type="profile",
        label="Profile",
        icon="◍",
        order=30,
        tone="neutral",
        rows=_profile_rows,
        id_field="id",
        label_field="name",
        search_fields=("name",),
        sublabel_fn=_profile_sublabel,
        fields={
            "base_currency": FieldSpec("Base currency"),
            "nationalities": FieldSpec("Nationalities"),
            "countries_of_residence": FieldSpec("Countries of residence"),
            "tax_residencies": FieldSpec("Tax residencies"),
            "employment_types": FieldSpec("Employment types"),
            "retirement_target_age": FieldSpec("Retirement target age", "qty"),
            "concentration_limit_pct": FieldSpec("Concentration limit", "pct"),
        },
    )
)
