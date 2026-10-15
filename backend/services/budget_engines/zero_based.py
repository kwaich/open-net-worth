"""Engine #1 — zero-based budgeting, adapting ``services.planning.budget_month()`` into a
``ViewModel`` (RFC ``2026-08-22-pluggable-budget-engines.md``, Decision D).

**Naming correction (owner, 2026-08-23):** this engine was originally registered under the
id ``"envelope"``. That was a misnomer — its hero number is Ready to Assign, and "every
dollar gets a job before RTA reaches zero" *is* the zero-based rule. Renamed ``envelope.py``
→ ``zero_based.py``, id ``"envelope"`` → ``"zero_based"``. The container noun "envelope"
(the ``envelopes`` table/``envelope_id`` — the Plan budget ROW identity) is UNRELATED to
this rename and keeps its name everywhere, including this engine's own native verbs below
(``envelope_create``, ``move_money``, ...) — this engine operates on those containers, it's
only the PARADIGM id that changed.

``planning.py`` is the central risk this RFC manages and is NOT touched — not one line.
This module only reads ``budget_month()``'s output and reshapes it; the native payload
remains the Plan tab's and Lucius's zero-based surface. ``ZeroBasedEngine.fold()`` exists
so zero-based proves the SAME interface every other engine (envelope, 50/30/20) implements,
continuously checked by ``tests/test_budget_engine_equivalence.py`` — if the ``ViewModel``
ever drifted from ``budget_month()``'s numbers, that test is the trip wire.
"""

from __future__ import annotations

from backend.core.errors import ValidationError
from backend.services import envelopes as envelopes_service
from backend.services import planning
from backend.services.budget_engines.draft_signals import (
    build_history,
    current_period,
    envelope_months,
    spend_stability,
    suggest_amount,
)
from backend.services.budget_engines.shared_context import resolve_shared
from backend.services.budget_engines.types import (
    Descriptor,
    Group,
    Headline,
    Health,
    Measure,
    Row,
    Unmapped,
    Verb,
    ViewModel,
)

# rta_status (planning.budget_health) -> Health.verdict. Positive RTA needs a decision
# ("attention": go assign it), not itself bad; all-assigned is the paradigm's goal state;
# overassigned means committed more than the on-budget pool holds.
_RTA_VERDICT = {"to_assign": "attention", "all_assigned": "good", "overassigned": "bad"}

_MEASURE_LABELS: tuple[tuple[str, str], ...] = (
    ("assigned", "Assigned"),
    ("activity", "Activity"),
    ("available", "Available"),
)

# The 20 `_op_*` types `planning.apply_budget_change` dispatches (planning.py:2690) —
# declared here as this engine's native verbs (RFC Decision F: "the zero_based engine's
# native verbs" — still named `envelope_*`/`move_money`/etc. because those operate on the
# `envelopes` CONTAINER table, unrelated to which paradigm engine is home). `id` is exactly
# the `change["type"]` string, so Phase 3's `apply_verb`
# can route straight through with no translation table. Params are a short human-readable
# hint, not a JSON schema (Verb's own contract) — read off each `_op_*`'s `change.get(...)`
# calls, not invented.
_VERBS: list[Verb] = [
    Verb(
        "envelope_create",
        "Create envelope",
        {
            "name": "string, required",
            "group_id": "group id, optional",
            "color": "hex color, optional",
        },
    ),
    Verb(
        "envelope_add_category",
        "Link category to envelope",
        {"envelope_id": "envelope id, required", "category_id": "category id, required"},
    ),
    Verb(
        "envelope_merge",
        "Merge two envelopes",
        {
            "source_envelope_id": "envelope id, required",
            "target_envelope_id": "envelope id, required",
        },
    ),
    Verb(
        "envelope_split_category",
        "Unlink category from envelope",
        {"envelope_id": "envelope id, required", "category_id": "category id, required"},
    ),
    Verb(
        "envelope_rename",
        "Rename/recolor envelope",
        {
            "envelope_id": "envelope id, required",
            "name": "string, optional",
            "color": "hex color, optional",
        },
    ),
    Verb("envelope_delete", "Delete envelope", {"envelope_id": "envelope id, required"}),
    Verb(
        "envelope_restore", "Restore a deleted envelope", {"envelope_id": "envelope id, required"}
    ),
    Verb(
        "assign",
        "Assign money to an envelope",
        {
            "month": "YYYY-MM, required",
            "envelope_id": "envelope id, required",
            "amount": "number, required",
        },
    ),
    Verb(
        "move_money",
        "Move money between envelopes (or __rta)",
        {
            "month": "YYYY-MM, required",
            "from_envelope_id": "envelope id or __rta, required",
            "to_envelope_id": "envelope id or __rta, required",
            "amount": "number, required",
        },
    ),
    Verb(
        "set_target",
        "Set/clear/snooze an envelope target",
        {
            "envelope_id": "envelope id, required",
            "target_type": "monthly|weekly|yearly|by_date, optional (omit to clear)",
            "amount": "number, required with target_type",
            "date": "YYYY-MM-DD, required for by_date",
            "snooze_until": "YYYY-MM, optional",
        },
    ),
    Verb(
        "group_create",
        "Create an envelope group",
        {"name": "string, required"},
    ),
    Verb(
        "group_rename",
        "Rename/collapse a group",
        {
            "group_id": "group id, required",
            "name": "string, optional",
            "collapsed": "bool, optional",
        },
    ),
    Verb(
        "set_targets",
        "Set a monthly target on many envelopes at once",
        {"targets": "list of {envelope_id, amount}, required"},
    ),
    Verb("group_reorder", "Reorder groups", {"order": "list of group ids, required"}),
    Verb("group_delete", "Delete a group", {"group_id": "group id, required"}),
    Verb(
        "move_category",
        "Move a category to a different group",
        {
            "category": "category or envelope ref, required",
            "group_id": "destination group id, required",
            "index": "position within group, optional",
        },
    ),
    Verb(
        "reorder_categories",
        "Reorder categories within a group",
        {"group_id": "group id, required", "categories": "ordered list of category ids, required"},
    ),
    Verb(
        "category_create",
        "Create a category (a label — it joins no group and gets no envelope)",
        {
            "label": "string, required",
            "kind": "expense|income, optional (default expense)",
            "color": "hex color, optional",
        },
    ),
    Verb(
        "category_rename",
        "Rename or merge a category",
        {
            "key": "category id, required",
            "new_label": "string, optional",
            "merge_into": "category id, optional",
        },
    ),
    Verb(
        "category_delete",
        "Delete a category",
        {
            "key": "category id, required",
            "reassign": "bool, optional",
            "reassign_to": "category id, optional",
        },
    ),
    Verb(
        "auto_assign",
        "Auto-assign RTA across envelopes",
        {"month": "YYYY-MM, optional (default current)", "mode": "string, required"},
    ),
]


def _tone(key: str, amount: float) -> str:
    """Activity/available render negative in red; assigned is neutral regardless of sign
    (it is a plan, not a deviation)."""
    if key in ("activity", "available") and amount < -0.005:
        return "negative"
    return "neutral"


def _measures(row: dict) -> list[Measure]:
    return [
        Measure(
            key=key, label=label, amount_base=row.get(key, 0.0), tone=_tone(key, row.get(key, 0.0))
        )
        for key, label in _MEASURE_LABELS
    ]


def _subtotal_measures(subtotal: dict) -> list[Measure]:
    return [
        Measure(key=key, label=label, amount_base=subtotal.get(key, 0.0), tone="neutral")
        for key, label in _MEASURE_LABELS
    ]


def _group_status(rows: list[Row]) -> str:
    return "attention" if any(r.status == "underfunded" for r in rows) else "ok"


def _groups_and_unmapped(table: dict) -> tuple[list[Group], Unmapped | None]:
    """Split ``budget_month``'s ``groups[]`` into the real ``Group``s plus the synthetic
    ``__unsorted__`` group (category=None spend), which becomes ``unmapped`` instead — its
    row's key is literally ``None``, which can never be a ``Row.id`` (opaque ids are never
    bare, and ``None`` is not one). ``__unbudgeted__`` (labeled categories with no envelope)
    stays a normal ``Group``: real category-labeled activity, not unmapped spend."""
    groups: list[Group] = []
    unmapped: Unmapped | None = None
    for g in table["groups"]:
        if g["id"] == "__unsorted__":
            unmapped = Unmapped(
                label="Uncategorized", amount_base=g["subtotal"].get("activity", 0.0)
            )
            continue
        rows = [
            Row(
                id=c["key"],
                label=c["label"],
                color=c.get("color"),
                measures=_measures(c),
                status=c["status"],
                drilldown={
                    "category_ids": c.get("category_ids"),
                    "envelope_id": c.get("envelope_id"),
                },
            )
            for c in g["categories"]
        ]
        groups.append(
            Group(
                id=g["id"],
                label=g.get("name", ""),
                rows=rows,
                subtotal=_subtotal_measures(g["subtotal"]),
                status=_group_status(rows),
            )
        )
    return groups, unmapped


def _overspent(table: dict) -> list[tuple[str, float]]:
    """Every envelope row below zero this month as (label, available), the deepest first —
    the rows ``budget_health`` counts, named."""
    red = [
        (c["label"], float(c["available"]))
        for g in table.get("groups", [])
        for c in g.get("categories", [])
        if (c.get("available") or 0.0) < -0.005
    ]
    return sorted(red, key=lambda r: r[1])


def _health_reasons(h: dict, overspent: list[tuple[str, float]]) -> list[str]:
    reasons = []
    if h["overspent_count"]:
        reasons.append(f"{h['overspent_count']} envelope(s) overspent")
        reasons += [f"{label} is overspent by {-avail:.2f}." for label, avail in overspent]
    if h["underfunded_count"]:
        reasons.append(
            f"{h['underfunded_count']} envelope(s) underfunded "
            f"({h['underfunded_amount']:.2f} {h['base_currency']} needed)"
        )
    if not reasons:
        reasons.append("Every envelope is funded")
    return reasons


class ZeroBasedEngine:
    """Adapts ``planning.budget_month()``/``planning.budget_health()`` into the
    paradigm-neutral ``ViewModel`` — see module docstring for why this is an adapter, not a
    rewrite."""

    descriptor = Descriptor(
        id="zero_based",
        name="Zero-Based",
        description="Give every dollar a job until nothing's left unassigned.",
        model_note=(
            "YNAB-style 'envelope budgeting' IS this engine: Ready to Assign, and every "
            "envelope's leftover carries into next month. There is no separate cash-envelope "
            "method in this app — when a user asks for envelope budgeting, this is it. Its "
            "operations are named assign / envelope_create / move_money because they act on "
            "the 'envelopes' TABLE, which is this engine's storage. Never show this note to "
            "the user verbatim."
        ),
        question="Does every dollar have a job?",
        kind="configured",
        render="custom",
    )
    verbs = _VERBS

    def fold(
        self,
        period: str,
        shared: dict,
        own_state: dict,
        prior_state: dict | None,
    ) -> ViewModel:
        """``shared``/``own_state``/``prior_state`` are unused — zero-based's state lives in
        its own stores (``envelopes``/``budget_assignments``/``profile.category_targets``),
        read internally by ``budget_month``. Accepted anyway to satisfy the ``BudgetEngine``
        contract every other engine actually needs them for."""
        table = planning.budget_month(period)
        health_summary = planning.budget_health(period, table=table)
        groups, unmapped = _groups_and_unmapped(table)
        return ViewModel(
            engine_id="zero_based",
            period=table["month"],
            base_currency=table["base_currency"],
            headline=Headline(
                label="Ready to Assign",
                value=table["ready_to_assign"],
                unit="money",
                status=health_summary["rta_status"],
                help=(
                    "Ready to Assign is cash not yet assigned to an envelope. An envelope "
                    "holds its own overspend until month-end — Ready to Assign does not move "
                    "when a category goes red. Covering an overspend before then costs Ready "
                    "to Assign, up to the hole's size. CASH spending with no envelope holds "
                    "the same way until month-end; CARD spending with no envelope holds "
                    "nothing and never moves Ready to Assign."
                ),
            ),
            groups=groups,
            rows_shape="rows",
            health=Health(
                verdict=_RTA_VERDICT.get(health_summary["rta_status"], "unknown"),
                reasons=_health_reasons(health_summary, _overspent(table)),
            ),
            unmapped=unmapped,
        )

    def draft(self, shared: dict, history: dict) -> dict | None:
        """Propose a MONTHLY TARGET per envelope, from each envelope's own trailing spend.

        This used to return ``None`` — "zero-based already owns real state, there is nothing
        to propose". True of a book that already budgets, and exactly wrong for a new one: it
        left the most recognisable method as the only one with no assisted start, dropping a
        first-time user into an empty table with nothing to react to (owner, 2026-08-23).

        A target, not an assignment. Targets say what an envelope NEEDS each month, which is
        what makes "underfunded" mean anything; assigning the money is the user's own monthly
        ritual and must not be done on their behalf. Sized as the median of the envelope's
        trailing months (``draft_signals.suggest_amount``).

        Bot-managed envelopes are excluded (``envelopes_service.bot_managed_envelope_ids``):
        a commitment's envelope and a credit card's payment envelope already have their
        targets kept in sync by the app, so offering them here would invite the user to
        hand-set a number the next sync overwrites.
        """
        ctx = resolve_shared(shared)
        hist = (
            history
            if history
            else build_history(ctx["txns"], ctx["base"], ctx["rates"], current_period())
        )
        existing = planning.read_category_targets()
        rows = []
        targets: list[dict] = []
        managed = envelopes_service.bot_managed_envelope_ids()
        for env in sorted(
            (e for e in ctx["envelopes"] if not e.get("archived") and e["id"] not in managed),
            key=lambda e: (e.get("name") or "").lower(),
        ):
            env_id, label = env["id"], env.get("name", env["id"])
            months = envelope_months(env.get("category_ids") or [], hist)
            amount = suggest_amount(months)
            rows.append(
                {
                    "envelope_id": env_id,
                    "label": label,
                    # No keyword heuristic here: unlike a pot (which the user opts into), every
                    # envelope wants a target. The only question is whether we can size it.
                    "low_confidence": spend_stability(months) != "stable",
                    "suggested_amount": amount,
                    "current_amount": float((existing.get(env_id) or {}).get("amount") or 0.0),
                    "category_ids": list(env.get("category_ids") or []),
                }
            )
            if amount > 0:
                targets.append({"envelope_id": env_id, "amount": amount})
        return {"targets": targets, "rows": rows}

    def apply_verb(self, verb_id: str, params: dict, own_state: dict) -> dict:
        """Routes STRAIGHT through the existing ``planning.apply_budget_change`` choke
        point (RFC "Data flow, end to end": no new write choke is created) — every
        ``verb_id`` here is exactly the ``change["type"]`` string that choke already
        dispatches on (see ``_VERBS`` above), so no translation table is needed.
        ``own_state`` is unused, mirroring ``fold()``: zero-based's real state lives in
        its own stores, read internally by ``apply_budget_change``'s ``_op_*`` handlers —
        accepted only to satisfy the ``BudgetEngine`` contract. The caller
        (``services/budget_engines/service.py``) validates ``verb_id`` against
        ``self.verbs`` before ever reaching here, so an unrecognised ``type`` reaching
        ``apply_budget_change`` itself would only happen on a direct, bypassing call."""
        if verb_id == "set_targets":
            return self._apply_targets(params)
        return planning.apply_budget_change({"type": verb_id, **params})

    @staticmethod
    def _apply_targets(params: dict) -> dict:
        """Apply many monthly targets in one request — the only verb here with no 1:1
        ``change["type"]``; it fans out to N ``set_target`` changes, each still landing
        through the same ``apply_budget_change`` choke (no second write path).

        Reports what FAILED rather than raising on the first bad row: a setup screen that
        dies halfway leaves the book half-configured with no way to tell which half."""
        rows = params.get("targets")
        if not isinstance(rows, list):
            raise ValidationError("targets is required: a list of {envelope_id, amount} objects.")
        applied, failed = 0, []
        for entry in rows:
            if not isinstance(entry, dict) or not entry.get("envelope_id"):
                failed.append({"envelope_id": None, "error": f"malformed entry {entry!r}"})
                continue
            env_id = entry["envelope_id"]
            try:
                amount = round(float(entry.get("amount") or 0.0), 2)
            except (TypeError, ValueError):
                failed.append({"envelope_id": env_id, "error": "amount is not a number"})
                continue
            # A zero target means "no target", which set_target expresses by OMITTING
            # target_type rather than storing a 0 — see its own params.
            change = {"type": "set_target", "envelope_id": env_id}
            if amount > 0:
                change |= {"target_type": "monthly", "amount": amount}
            try:
                planning.apply_budget_change(change)
                applied += 1
            except Exception as exc:  # noqa: BLE001 — reported per row, never swallowed
                failed.append({"envelope_id": env_id, "error": str(exc)})
        return {"applied": applied, "failed": failed}


ZERO_BASED_ENGINE = ZeroBasedEngine()
