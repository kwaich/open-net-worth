"""The labelled envelope and category reads Lucius works from — ``list_envelopes``,
``list_categories`` and the rows of ``get_budget_month``: every opaque id beside its name
(ADR 2026-07-20), and every per-envelope setting read from the store the budget itself reads.

Why this exists (owner, 2026-09-26): ``list_envelopes`` returned raw envelope rows —
``group_id``/``category_ids`` with no names, plus ``target``/``note`` copies left on the rows
from before targets and notes moved to ``profile.category_targets``/``category_notes`` (ADR
2026-07-05-envelope-id-native). On one real book 14 of 46 envelopes' row copies disagreed with
the real target, and notes were readable only through the per-field ``get_envelope_note``.
Reads only; the rows never carry month money (that is ``get_budget_month``'s job).
"""

from __future__ import annotations

from typing import Iterable, Optional

from backend.core.errors import ValidationError, recovery_hint
from backend.core.user_time import user_today
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service

CATEGORY_KINDS = ("expense", "income", "transfer", "adjustment", "investment")


def _category_labels() -> dict[str, str]:
    return {
        c["id"]: c.get("label", "")
        for c in categories_service.get_all_categories(include_hidden=True)
    }


def _linked(ids: Iterable[str], labels: dict[str, str]) -> list[dict]:
    return [{"id": c, "label": labels.get(c, "")} for c in ids or []]


def _fifty_thirty_twenty(month: str) -> Optional[dict[str, dict]]:
    """``{envelope_id: {cap}}`` exactly as the 50/30/20 screen resolves it this month
    — ``None`` when that method was never set up, so there are no shares to report."""
    from backend.services.budget_engines import service as engines_service

    computed = engines_service.proportional_month(month)
    if not computed.get("configured"):
        return None
    return {r["id"]: {"cap": r.get("cap")} for r in computed["envelopes"]}


def _resolve_group(ref: str, groups: dict[str, str]) -> str:
    want = ref.strip()
    for gid, name in groups.items():
        if gid == want or name.strip().lower() == want.lower():
            return gid
    raise ValidationError(
        f"No envelope group '{ref}'."
        + recovery_hint(ref, groups.values(), tool_hint="list_envelopes")
    )


def _resolve_category(ref: str, labels: dict[str, str]) -> str:
    cid = categories_service.resolve_category_ref(ref)
    if cid is None:
        raise ValidationError(
            f"Unknown category '{ref}'."
            + recovery_hint(ref, labels.values(), tool_hint="list_categories")
        )
    return cid


def describe_envelopes(
    *,
    envelope_ids: Optional[Iterable[str]] = None,
    group: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    has_target: Optional[bool] = None,
    include_archived: bool = False,
) -> list[dict]:
    """Every envelope matching the filters (all of them combine), display-ordered. ``group`` is
    a group id or name, ``category`` a category id or label, ``search`` a case-insensitive
    substring of the envelope's name. An unknown id/group/category raises, naming the closest."""
    rows = envelopes_service.all_envelopes()
    if not include_archived:
        rows = [e for e in rows if not e.get("archived")]
    groups = {g["id"]: g.get("name", "") for g in planning_service.list_groups()}
    labels = _category_labels()
    targets = planning_service.read_category_targets()
    notes = planning_service.read_category_notes()

    if envelope_ids is not None:
        wanted = [str(i) for i in envelope_ids]
        known = {e["id"] for e in rows}
        unknown = [i for i in wanted if i not in known]
        if unknown:
            raise ValidationError(
                f"Unknown envelope id '{unknown[0]}'."
                + recovery_hint(unknown[0], [e["id"] for e in rows], tool_hint="list_envelopes")
            )
        rows = [e for e in rows if e["id"] in set(wanted)]
    if group:
        gid = _resolve_group(group, groups)
        rows = [e for e in rows if e.get("group_id") == gid]
    if category:
        cid = _resolve_category(category, labels)
        rows = [e for e in rows if cid in (e.get("category_ids") or [])]
    if search and search.strip():
        needle = search.strip().lower()
        rows = [e for e in rows if needle in (e.get("name") or "").lower()]
    if has_target is not None:
        rows = [e for e in rows if bool(targets.get(e["id"])) == has_target]

    managed = envelopes_service.bot_managed_envelope_ids()
    shares = _fifty_thirty_twenty(user_today().strftime("%Y-%m")) if rows else None
    out = []
    for e in rows:
        gid = e.get("group_id")
        row = {
            "id": e["id"],
            "name": e.get("name", ""),
            "group": {"id": gid, "name": groups.get(gid, "")} if gid else None,
            "linked_categories": _linked(e.get("category_ids"), labels),
            "zero_based_target": targets.get(e["id"]) or None,
            "note": notes.get(e["id"], ""),
            "color": e.get("color") or "",
            "managed_by_app": e["id"] in managed,
        }
        if shares is not None and e["id"] in shares:
            row["fifty_thirty_twenty"] = shares[e["id"]]
        if include_archived:
            row["archived"] = bool(e.get("archived"))
        out.append(row)
    return out


def describe_categories(
    *,
    include_hidden: bool = False,
    kind: Optional[str] = None,
    linked: Optional[bool] = None,
    envelope: Optional[str] = None,
    search: Optional[str] = None,
) -> list[dict]:
    """Every category matching the filters, each with the envelope it is linked to (or None —
    then its spending is in no envelope). ``envelope`` is an envelope id or name."""
    if kind and kind not in CATEGORY_KINDS:
        raise ValidationError(f"kind must be one of {', '.join(CATEGORY_KINDS)} (got '{kind}').")
    rows = categories_service.get_all_categories(include_hidden=include_hidden)
    hidden = categories_service.hidden_categories() if include_hidden else set()
    live = [e for e in envelopes_service.all_envelopes() if not e.get("archived")]
    env_of = {
        c: {"id": e["id"], "name": e.get("name", "")}
        for e in live
        for c in e.get("category_ids") or []
    }
    if kind:
        rows = [c for c in rows if c.get("kind") == kind]
    if linked is not None:
        rows = [c for c in rows if (c["id"] in env_of) == linked]
    if envelope:
        ref = envelope.strip()
        match = next(
            (e for e in live if e["id"] == ref or (e.get("name") or "").lower() == ref.lower()),
            None,
        )
        if match is None:
            raise ValidationError(
                f"No envelope '{envelope}'."
                + recovery_hint(
                    envelope, [e.get("name", "") for e in live], tool_hint="list_envelopes"
                )
            )
        rows = [c for c in rows if env_of.get(c["id"], {}).get("id") == match["id"]]
    if search and search.strip():
        needle = search.strip().lower()
        rows = [c for c in rows if needle in (c.get("label") or "").lower()]
    return [
        {**c, "envelope": env_of.get(c["id"]), **({"hidden": True} if c["id"] in hidden else {})}
        for c in rows
    ]


def label_budget_rows(table: dict) -> dict:
    """``planning.budget_month``'s table with each envelope row's ``linked_categories`` (id +
    label) and ``note`` added beside the existing ``category_ids`` — additive, nothing removed."""
    labels = _category_labels()
    notes = planning_service.read_category_notes()
    for group in table.get("groups") or []:
        for row in group.get("categories") or []:
            row["linked_categories"] = _linked(row.get("category_ids"), labels)
            if row.get("envelope_id"):
                row["note"] = notes.get(row["envelope_id"], "")
    return table


def _named_ids(params) -> list[str]:
    """The envelope ids an operation's params name, in order: ``envelope_id``,
    ``envelope_ids``, and the keys of ``caps``."""
    if not isinstance(params, dict):
        return []
    ids: list[str] = []
    if isinstance(params.get("envelope_id"), str):
        ids.append(params["envelope_id"])
    if isinstance(params.get("envelope_ids"), list):
        ids += [i for i in params["envelope_ids"] if isinstance(i, str)]
    if isinstance(params.get("caps"), dict):
        ids += [i for i in params["caps"] if isinstance(i, str)]
    return list(dict.fromkeys(ids))


def label_engine_result(result, params) -> dict:
    """A budget-method operation's saved delta with every envelope named (ADR 2026-07-20).

    An operation returns its WHOLE stored map (``buckets``, ``caps``) keyed by opaque ids — a
    one-envelope move came back as seven ``env_…: "wants"`` lines the owner could not read
    (2026-09-26). This reports only the envelopes the call named (every one when it named
    none, as ``copy_targets_to_caps`` does), each with its name; group targets and Excluded
    groups carry the group's name, Excluded envelopes the envelope's.
    Any other key passes through unchanged."""
    if not isinstance(result, dict):
        return result
    names = {e["id"]: e.get("name", "") for e in envelopes_service.all_envelopes()}
    named = _named_ids(params)

    group_names = {g["id"]: g.get("name", "") for g in planning_service.list_groups()}

    def envelope(env_id: str) -> dict:
        return {"id": env_id, "name": names.get(env_id, "")}

    def group(group_id: str) -> dict:
        return {"id": group_id, "name": group_names.get(group_id, "")}

    out: dict = {}
    for key, value in result.items():
        if key in ("buckets", "caps") and isinstance(value, dict):
            ids = [i for i in (named or list(value)) if i in value]
            field = "bucket" if key == "buckets" else "cap_history"
            out[key] = [{"envelope": envelope(i), field: value[i]} for i in ids]
        elif key == "group_targets" and isinstance(value, dict):
            out[key] = [{"group": group(g), "percent": pct} for g, pct in value.items()]
        elif key == "excluded_groups" and isinstance(value, list):
            out[key] = [group(g) for g in value if isinstance(g, str)]
        elif key == "excluded_envelopes" and isinstance(value, list):
            out[key] = [envelope(e) for e in value if isinstance(e, str)]
        else:
            out[key] = value
    return out
