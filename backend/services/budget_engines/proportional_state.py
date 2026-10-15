"""50/30/20's state-shape helpers — the envelope-based model (owner design, 2026-09-25; ADR
``docs/adr/2026-09-25-fifty-thirty-twenty-is-envelope-based-with-caps.md``). Split out of
``proportional_config.py``/``proportional_fold.py`` so validation (writes) and money math
(reads) share ONE reading of the state shape and ONE cap-history splice rule, instead of
each re-deriving it.

State (``budget_engine_state``, engine id ``proportional``). Since 2026-09-26 the method's
GROUPS are the book's Zero-Based groups — no second taxonomy to sort envelopes into (owner:
"I have many envelopes ... hard to know which should be needs or wants"):

    {
      "group_targets": {group_id: percent},       # a share of income per Zero-Based group
      "excluded_groups": [group_id, ...] | None,  # groups outside the split (None = never
                                                   # chosen: derived from legacy ``buckets``)
      "excluded_envelopes": [envelope_id, ...],    # single envelopes outside the split — in
                                                   # their group still, their spend uncounted
      "caps": {envelope_id: [["2026-09", 650.0], ...]},  # effective-from history, by month
      "income_category_ids": [category_id, ...] | None,  # None = every income category
      # Legacy, read-only: "targets" (Needs/Wants %), "mapping" (v1 category buckets),
      # "buckets" (per-envelope needs/wants/skip) — kept in the row, never written again;
      # only ``buckets`` is still read, to seed ``excluded_groups`` once.
    }

Every function here is a PURE function of its arguments — no store reads, no FX, no
``user_today()`` (the one function that needs "today", ``require_not_past_month``, takes it
as an explicit parameter rather than reading the clock itself, so it never needs a frozen
clock to test). ``services/budget_engines/service.py`` persists whatever
``ProportionalEngine.apply_verb`` returns via a SHALLOW top-level merge onto the engine's
current stored row (see that function's own docstring) — so a verb here only ever needs to
return the ONE top-level key(s) it changed, never the whole state; the merge is what
keeps ``caps``/``buckets``/etc. alive across a later call to a DIFFERENT verb (including the
legacy ``set_config``, whose own return shape is intentionally untouched by this file).
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

from backend.core.errors import ValidationError

_LEGACY_BUCKETS: tuple[str, ...] = ("needs", "wants", "skip")

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ── Reading the full state shape ─────────────────────────────────────────────────────


def normalize_own_state(own_state: dict | None) -> dict:
    """The full, defaulted shape — what every read (``fold()``, the REST endpoint) and every
    verb's "read current state, apply one delta" starts from. Tolerant of anything malformed
    (a stale/partial row from before a field existed reads as that field's default, never
    raises) — a read path must never crash on state that is merely incomplete."""
    own_state = own_state or {}
    raw_targets = own_state.get("group_targets")
    group_targets = (
        {
            str(gid): round(_safe_float(v, 0.0), 6)
            for gid, v in raw_targets.items()
            if isinstance(gid, str) and _safe_float(v, 0.0) > 0
        }
        if isinstance(raw_targets, dict)
        else {}
    )
    raw_excluded = own_state.get("excluded_groups")
    excluded_groups = (
        list(dict.fromkeys(str(g) for g in raw_excluded if isinstance(g, str) and g))
        if isinstance(raw_excluded, list)
        else None
    )
    raw_out = own_state.get("excluded_envelopes")
    excluded_envelopes = (
        list(dict.fromkeys(str(e) for e in raw_out if isinstance(e, str) and e))
        if isinstance(raw_out, list)
        else []
    )
    raw_buckets = own_state.get("buckets")
    buckets = (
        {k: v for k, v in raw_buckets.items() if v in _LEGACY_BUCKETS}
        if isinstance(raw_buckets, dict)
        else {}
    )
    raw_caps = own_state.get("caps")
    caps = (
        {env_id: normalize_cap_history(hist) for env_id, hist in raw_caps.items()}
        if isinstance(raw_caps, dict)
        else {}
    )
    raw_income_ids = own_state.get("income_category_ids")
    income_category_ids = (
        [str(c) for c in raw_income_ids if isinstance(c, str) and c.strip()]
        if isinstance(raw_income_ids, list)
        else None
    )
    return {
        "group_targets": group_targets,
        "excluded_groups": excluded_groups,
        "excluded_envelopes": excluded_envelopes,
        "buckets": buckets,
        "caps": caps,
        "income_category_ids": income_category_ids,
    }


def is_configured(own_state: dict | None) -> bool:
    """Set up at least once — under this model (``group_targets`` saved, even empty) or the
    one before it (Needs/Wants ``targets``), so a book configured then stays configured."""
    raw = own_state or {}
    return "group_targets" in raw or "targets" in raw


def _safe_float(raw, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# ── Row-envelope selection + ordering ───────────────────────────────────────────────


def row_envelopes(envelopes: list[dict], groups: list[dict], managed_ids: set[str]) -> list[dict]:
    """Every LIVE envelope except a card-payment/bot-managed one — the ROWS for the
    envelope-based 50/30/20 table, in the Zero-Based table's own order (group order, then
    envelope order within the group). Each row is the stored envelope dict plus
    ``group_name`` (its resolved Zero-Based group's display name; a ``group_id`` that
    resolves to nothing real — a deleted group, or a book that has never lazily created
    "Others" — reads as ``""`` rather than minting one here, self-healing the moment any
    other read path does, mirroring ``planning._envelope_group_buckets``'s own fallback).
    Envelopes with NO categories are included (a row with nothing to show activity for is
    still a real budget line — "no categories" is the UI's own empty-state, not a filter
    here)."""
    live = [e for e in envelopes if not e.get("archived") and e["id"] not in managed_ids]
    order_of = {g["id"]: i for i, g in enumerate(groups)}
    name_of = {g["id"]: g.get("name", "") for g in groups}
    others_id = next((g["id"] for g in groups if g.get("is_others")), None)

    def group_id_of(env: dict) -> str | None:
        gid = env.get("group_id")
        return gid if gid in order_of else others_id

    def sort_key(env: dict):
        gid = group_id_of(env)
        return (
            order_of.get(gid, len(groups)),
            int(env.get("order", 0) or 0),
            (env.get("name") or "").lower(),
        )

    return [
        {**env, "group_name": name_of.get(group_id_of(env), "")}
        for env in sorted(live, key=sort_key)
    ]


def row_group_id(env: dict, groups: list[dict]) -> str | None:
    """The Zero-Based group an envelope row sits in — its own ``group_id`` when that group
    exists, else "Others" (the same fallback ``row_envelopes`` orders by)."""
    ids = {g["id"] for g in groups}
    gid = env.get("group_id")
    if gid in ids:
        return gid
    return next((g["id"] for g in groups if g.get("is_others")), None)


def derive_excluded_groups(
    rows_by_group: dict[str, list[str]], buckets: dict[str, str]
) -> list[str]:
    """The groups to start outside the split when none were ever chosen: every group whose
    envelopes the owner had ALL put in the old Excluded bucket — their own earlier choice,
    carried over once (``excluded_groups`` is stored from the first toggle on)."""
    return [
        gid
        for gid, env_ids in rows_by_group.items()
        if env_ids and all(buckets.get(e) == "skip" for e in env_ids)
    ]


# Rows whose group resolves to nothing real (no group, and no "Others" to fall back to) still
# count — under one synthetic group — so no spend silently drops out of savings.
NO_GROUP = "__no_group__"


def groups_out(
    groups: list[dict], rows_by_group: dict[str, list[str]], rows: list[dict], state: dict
) -> list[dict]:
    """The method's groups — the book's Zero-Based groups, in their order — each with its
    target, whether it sits outside the split, and its spend (an envelope excluded on its own
    leaves it). A group holding envelopes is
    ALWAYS listed, whatever its flags, so no spend drops out of savings. An empty one is left
    out when the app keeps it (Credit Card Payments: its envelopes are the card's payments,
    not spending); an empty group of the owner's own stays as a drop
    target."""
    excluded = set(
        state["excluded_groups"]
        if state["excluded_groups"] is not None
        else derive_excluded_groups(rows_by_group, state["buckets"])
    )
    spent: dict[str, float] = {}
    for r in rows:
        if not r.get("excluded"):
            spent[r["group_id"]] = spent.get(r["group_id"], 0.0) + r["activity"]
    listed = list(groups)
    if NO_GROUP in rows_by_group:
        listed.append({"id": NO_GROUP, "name": "No group"})
    out = []
    for g in listed:
        if not rows_by_group.get(g["id"]) and g.get("synced"):
            continue
        out.append(
            {
                "id": g["id"],
                "name": g.get("name", ""),
                "target": state["group_targets"].get(g["id"]),
                "excluded": g["id"] in excluded,
                "synced": bool(g.get("synced")),
                "activity": round(spent.get(g["id"], 0.0), 2),
            }
        )
    return out


# ── Cap history ──────────────────────────────────────────────────────────────────────


def normalize_cap_history(raw) -> list[list]:
    """A tolerant read of one envelope's stored cap history — a list of ``[month, amount]``
    pairs, sorted by month. Anything malformed (not a list, a malformed entry, an
    unparseable month/amount) is DROPPED rather than raising, mirroring
    ``envelope_config.read_pots``'s "a read path never crashes on stale state" rule."""
    if not isinstance(raw, list):
        return []
    out: list[list] = []
    for entry in raw:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        month, amount = entry
        if not (isinstance(month, str) and _MONTH_RE.match(month)):
            continue
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            continue
        out.append([month, amount])
    out.sort(key=lambda e: e[0])
    return out


def cap_at(history: list, month: str) -> float | None:
    """The cap effective at ``month``: the amount of the LAST entry (by month) whose month
    is ``<= month`` — ``0``/absent means no cap (``None``), never a fabricated ceiling of
    zero spend."""
    eligible = [(m, a) for m, a in history if m <= month]
    if not eligible:
        return None
    _, amount = max(eligible, key=lambda e: e[0])
    return amount if amount else None


def splice_cap_history(history: list, month: str, amount: float) -> list:
    """Set the cap for ``month`` onward: every entry at or after ``month`` is replaced by
    ONE ``[month, amount]`` entry, so it applies from ``month`` onward; an entry BEFORE
    ``month`` is untouched — it is history. ``amount == 0`` is a normal, storable value
    (see ``cap_at``) — a caller never distinguishes "clear" from "set to zero"."""
    kept = [[m, a] for m, a in history if m < month]
    kept.append([month, round(float(amount), 2)])
    kept.sort(key=lambda e: e[0])
    return kept


# ── Month validation ─────────────────────────────────────────────────────────────────


def valid_month(raw) -> str:
    """``raw`` as a well-formed ``YYYY-MM`` string, or a typed ``ValidationError``."""
    month = (raw or "").strip() if isinstance(raw, str) else ""
    if not _MONTH_RE.match(month):
        raise ValidationError(f"month must be 'YYYY-MM' (got {raw!r}).")
    return month


def require_not_past_month(month: str, current_month: str) -> None:
    """Refuse a cap write for a month before the current CALENDAR month — past months are
    history, not a ceiling to be rewritten after the fact. ``current_month`` is passed in
    (``draft_signals.current_period()``, today's real month) rather than read from the
    clock here, so this stays a pure, freely-testable function."""
    if month < current_month:
        raise ValidationError(
            f"Can't set a cap for {month} — it's before the current month ({current_month}); "
            "past months are history."
        )


# ── Zero-Based target -> monthly-equivalent (for copy_targets_to_caps) ─────────────────

_TARGET_TYPES: tuple[str, ...] = ("monthly", "weekly", "yearly", "by_date")


def target_monthly_equivalent(target: dict | None, month: str) -> float:
    """The monthly-equivalent SIZE of a Zero-Based envelope target for ``month`` — what
    ``copy_targets_to_caps`` reads to seed a cap. Mirrors ``planning.needed_this_month``'s
    own per-type formula (monthly: the amount itself; weekly: amount x weeks touching the
    month; yearly: amount/12; by_date: amount spread over the months remaining) WITHOUT that
    function's progress subtraction — a cap is an independent monthly ceiling, not a
    remaining-funding gap, so what's already assigned/available never reduces it. ``0.0``
    for no target, an unknown type, or a non-positive amount."""
    if not isinstance(target, dict):
        return 0.0
    ttype = target.get("type")
    amount = _safe_float(target.get("amount"), 0.0)
    if ttype not in _TARGET_TYPES or amount <= 0:
        return 0.0
    if ttype == "monthly":
        return round(amount, 2)
    if ttype == "yearly":
        return round(amount / 12.0, 2)
    if ttype == "weekly":
        due_weekday = target.get("due_weekday")
        weekday = due_weekday if isinstance(due_weekday, int) and 0 <= due_weekday <= 6 else 0
        return round(amount * _weekday_occurrences(month, weekday), 2)
    months_left = _months_remaining(month, target.get("date"))
    return round(amount / months_left, 2)


def _weekday_occurrences(month: str, weekday: int) -> int:
    """Number of days matching ``weekday`` (0=Monday..6=Sunday) within ``month``."""
    year, mon = int(month[:4]), int(month[5:7])
    last = monthrange(year, mon)[1]
    return sum(1 for d in range(1, last + 1) if date(year, mon, d).weekday() == weekday)


def _months_remaining(month: str, target_date: str | None) -> int:
    """Whole months from the START of ``month`` to ``target_date`` (>=1, inclusive of the
    current month)."""
    if not target_date or len(target_date) < 7:
        return 1
    try:
        target_year, target_month = int(target_date[:4]), int(target_date[5:7])
    except (ValueError, IndexError):
        return 1
    year, mon = int(month[:4]), int(month[5:7])
    months = (target_year - year) * 12 + (target_month - mon) + 1
    return max(1, months)
