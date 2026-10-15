"""50/30/20's ``ViewModel`` assembly — reshapes ``proportional_fold.compute_month()``'s
output (the SAME computation the REST endpoint reads) into the paradigm-neutral
``ViewModel`` ``ProportionalEngine.fold()`` returns. Split out of ``proportional_fold.py``
(the money math itself) to keep both files under the ~400-line guidance; this file does NO
transaction/FX math of its own — ``to_view_model`` is a pure RESHAPE, never a second pass
over transactions, which is what structurally guarantees Lucius (``get_budget_view``) and
the screen (``GET /api/planning/proportional/month``) never disagree.
"""

from __future__ import annotations

from backend.services.budget_engines.types import (
    Group,
    Headline,
    Health,
    Measure,
    Row,
    Unmapped,
    ViewModel,
)


def pct(amount: float, income: float) -> float | None:
    """``None`` (never ``0.0``) when there's no income to divide by — an undefined percent
    must never render as a fabricated real one."""
    return round(amount / income * 100, 1) if income > 0 else None


def _row_measures(env_row: dict, income: float) -> list[Measure]:
    activity, cap = env_row["activity"], env_row["cap"]
    measures = [
        Measure(key="activity", label="Spent", amount_base=activity, tone="neutral"),
        Measure(
            key="pct_of_income", label="% of income", percent=pct(activity, income), tone="neutral"
        ),
    ]
    if cap is not None:
        left = round(cap - activity, 2)
        measures.append(Measure(key="cap", label="Cap", amount_base=cap, tone="neutral"))
        measures.append(
            Measure(
                key="left",
                label="Left",
                amount_base=left,
                tone="negative" if left < -0.005 else "neutral",
            )
        )
    return measures


def _row_status(env_row: dict, fallback: str) -> str:
    cap = env_row["cap"]
    if cap is None:
        return fallback
    return "over_cap" if round(cap - env_row["activity"], 2) < -0.005 else fallback


def _row(env_row: dict, income: float, *, fallback_status: str) -> Row:
    return Row(
        id=env_row["id"],
        label=env_row["label"],
        color=env_row["color"],
        measures=_row_measures(env_row, income),
        status=_row_status(env_row, fallback_status),
        drilldown={"category_ids": env_row["category_ids"]},
    )


def _sorted_rows(env_rows: list[dict]) -> list[dict]:
    return sorted(env_rows, key=lambda r: (-r["activity"], (r["label"] or "").lower()))


def group_view(g: dict, env_rows: list[dict], income: float) -> Group:
    """One Zero-Based group as a ``Group``: its envelopes, its spend as a share of income, and
    its target when it has one (``attention`` once over it)."""
    group_pct = pct(g["activity"], income)
    target = g["target"]
    over = target is not None and group_pct is not None and group_pct > target
    subtotal = [
        Measure(key="amount", label="Spent", amount_base=g["activity"], tone="neutral"),
        Measure(
            key="pct_of_income",
            label="% of income",
            percent=group_pct,
            tone="warning" if over else "neutral",
        ),
    ]
    if target is not None:
        subtotal.append(
            Measure(key="target", label="Target", percent=round(target, 1), tone="neutral")
        )
    return Group(
        id=g["id"],
        label=g["name"],
        rows=[_row(r, income, fallback_status="ok") for r in _sorted_rows(env_rows)],
        subtotal=subtotal,
        status="attention" if over else "ok",
    )


def savings_group(savings: float, income: float, target_pct: float | None) -> Group:
    savings_pct = pct(savings, income)
    tone = "negative" if savings < 0 else "positive"
    if income <= 0:
        status = "unknown"
    elif savings < 0:
        status = "bad"
    elif target_pct is not None and savings_pct is not None and savings_pct < target_pct:
        status = "attention"
    else:
        status = "ok"
    subtotal = [
        Measure(key="amount", label="Savings", amount_base=savings, tone=tone),
        Measure(
            key="pct_of_income",
            label="% of income",
            percent=savings_pct,
            tone="warning" if status == "attention" else tone,
        ),
    ]
    if target_pct is not None:
        subtotal.append(
            Measure(key="target", label="Target", percent=round(target_pct, 1), tone="neutral")
        )
    subtotal.append(Measure(key="income", label="Income", amount_base=income, tone="neutral"))
    return Group(
        id="savings",
        label="Savings",
        rows=[],  # Savings owns no rows — it's the residual, never a fake row set.
        subtotal=subtotal,
        status=status,
    )


_INCOME_SOURCE_NOTE = {
    "profile": "your profile's usual monthly net income",
    "actual": "what was actually received this month",
}


def headline(
    savings: float,
    savings_pct: float | None,
    income: float,
    target_pct: float | None,
    income_source: str,
) -> Headline:
    source_note = _INCOME_SOURCE_NOTE.get(income_source, income_source)
    if income <= 0:
        return Headline(
            label="Savings rate",
            value=0.0,
            unit="percent",
            status="no_income",
            help="No income recorded this month — percentages are undefined.",
        )
    if savings < 0:
        status = "negative"
    elif target_pct is None or savings_pct >= target_pct:
        status = "on_track"
    else:
        status = "below_target"
    target_note = (
        f"Target: {target_pct:g}% — what the group targets leave for savings."
        if target_pct is not None
        else "No group targets set — savings is whatever income is left."
    )
    return Headline(
        label="Savings rate",
        value=savings_pct,
        unit="percent",
        status=status,
        help=f"{target_note} Income this month is {source_note}.",
    )


def over_cap_envelopes(envelopes: list[dict]) -> list[dict]:
    """Every envelope spent past its monthly cap, biggest overrun first — in a counted group or
    outside the split alike, since its row reads ``over_cap`` on screen either way."""
    over = [
        {**e, "over_by": round(e["activity"] - e["cap"], 2)}
        for e in envelopes
        if e["cap"] is not None and round(e["activity"] - e["cap"], 2) > 0.005
    ]
    return sorted(over, key=lambda e: (-e["over_by"], (e["label"] or "").lower()))


def health(
    savings: float, income: float, groups: list[dict], over_cap: list[dict] | None = None
) -> Health:
    """The verdict and EVERY reason for it: spending past income, each group past its target and
    each envelope past its cap. The reasons are the complete overrun list, so an answer to "what
    is over budget" never depends on scanning rows (Rent, S$600 over its cap, was missed beside
    a smaller overrun that was named)."""
    over_cap = over_cap or []
    caps = [
        f"{e['label']} is over its cap by {e['over_by']:.2f} (spent {e['activity']:.2f} of "
        f"{e['cap']:.2f})."
        for e in over_cap
    ]
    if income <= 0:
        return Health(verdict="unknown", reasons=["No income recorded this month.", *caps])
    if savings < 0:
        return Health(
            verdict="bad",
            reasons=[f"Spending exceeds income by {abs(savings):.2f} this month.", *caps],
        )
    reasons = []
    for g in groups:
        g_pct = pct(g["activity"], income)
        if g["target"] is not None and g_pct is not None and g_pct > g["target"]:
            reasons.append(f"{g['name']} is {g_pct:g}% of income (target {g['target']:g}%).")
    reasons += caps
    if reasons:
        return Health(verdict="attention", reasons=reasons)
    if not any(g["target"] is not None for g in groups):
        return Health(verdict="good", reasons=["Within income; no group targets set."])
    return Health(verdict="good", reasons=["Every group is within its target."])


def to_view_model(computed: dict, base_currency: str) -> ViewModel:
    """Reshape ``compute_month()``'s output into the paradigm-neutral ``ViewModel`` —
    Lucius's/the generic renderer's picture, built from the SAME numbers the screen reads,
    never a second computation. One group per counted Zero-Based group (its envelopes as
    rows, cap/activity/left as measures), then Savings. Excluded groups' rows, envelopes
    excluded on their own, and the two unassigned figures fold into ``unmapped`` — outside the
    split — with each row attached
    (``Row.status`` carries its group's id)."""
    rows_by_group: dict[str, list[dict]] = {}
    left_out: list[dict] = []
    for env_row in computed["envelopes"]:
        if env_row.get("excluded"):
            left_out.append(env_row)
        else:
            rows_by_group.setdefault(env_row["group_id"], []).append(env_row)

    income_amount = computed["income"]["amount"]
    savings = computed["savings"]["amount"]
    savings_target = computed["savings"]["target"]
    counted = [g for g in computed["groups"] if not g["excluded"]]
    excluded = [g for g in computed["groups"] if g["excluded"]]

    groups = [group_view(g, rows_by_group.get(g["id"], []), income_amount) for g in counted]
    groups.append(savings_group(savings, income_amount, savings_target))

    no_envelope = computed["unassigned"]["no_envelope"]
    uncategorized = computed["unassigned"]["uncategorized"]
    outside_rows = [r for g in excluded for r in rows_by_group.get(g["id"], [])] + left_out
    outside_total = round(
        sum(g["activity"] for g in excluded) + sum(r["activity"] for r in left_out), 2
    )
    unmapped = None
    if outside_rows or no_envelope or uncategorized:
        unmapped = Unmapped(
            label="Outside the split",
            amount_base=round(outside_total + no_envelope + uncategorized, 2),
            skipped_amount_base=round(outside_total + no_envelope, 2),
            skipped_count=sum(1 for r in outside_rows if r["activity"] > 0.005)
            + (1 if no_envelope else 0),
            uncategorized_amount_base=uncategorized,
            uncategorized_count=computed["_uncategorized_count"],
            rows=[
                _row(r, income_amount, fallback_status=r["group_id"])
                for r in _sorted_rows(outside_rows)
            ],
        )

    return ViewModel(
        engine_id="proportional",
        period=computed["month"],
        base_currency=base_currency,
        headline=headline(
            savings,
            pct(savings, income_amount),
            income_amount,
            savings_target,
            computed["income"]["source"],
        ),
        groups=groups,
        rows_shape="proportion-bars",
        health=health(savings, income_amount, counted, over_cap_envelopes(computed["envelopes"])),
        unmapped=unmapped,
    )
