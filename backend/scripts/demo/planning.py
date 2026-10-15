"""Seed the Plan side of the demo book: groups, envelopes, targets, assignments, goals.

The budget is DERIVED from the ledger that was just written, not hand-typed beside it: each
envelope's target is that envelope's own median month of real activity, rounded up. Two
reasons. A hand-typed target drifts the moment a spending generator changes, and silently —
you only notice when the demo shows a Tax envelope with $34,000 sitting in it. And a budget
whose numbers came from the book is the budget a real owner would actually have arrived at.

Every month on the timeline gets assigned, not just recent ones. Two reasons. Card spending only
credits the credit-card payment envelope up to what its spending envelope was assigned, so an
unbudgeted stretch of history leaves that envelope permanently underfunded. And under the
restored YNAB identity (ADR 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category,
superseding 2026-09-08) an envelope's negative does NOT carry — it resets at the roll and the
hole is charged, once, to the NEXT month's Ready to Assign. An unassigned month therefore reads
as a one-month dent in Ready to Assign rather than a red row that never clears; assigning every
month is what keeps the walk's headline honest either way.
"""

from __future__ import annotations

import logging
import statistics

from backend.scripts.demo._timeline import Timeline, add_months, month_key

log = logging.getLogger(__name__)

# Envelope groups, in display order, and which envelope names belong to each. A name not
# listed here stays in the catch-all the envelope seed put it in.
GROUPS: list[tuple[str, list[str]]] = [
    (
        "Essentials",
        ["Home & Utilities", "Food & Dining", "Transport", "Health & Fitness", "Pet Care"],
    ),
    (
        "Lifestyle",
        [
            "Shopping",
            "Entertainment",
            "Subscriptions",
            "Hobby & Projects",
            "Travel",
            "Family & Gifts",
            "Giving",
            "Lifestyle",
        ],
    ),
    ("Obligations", ["Tax", "Other", "Stuff I Forgot to Budget For"]),
]

# Envelopes budgeted as a yearly pot rather than a monthly one — the lumpy ones, where a
# monthly target would be permanently either over- or under-funded.
YEARLY = {"Travel", "Family & Gifts"}

# Standalone sinking funds — envelopes with no category, which is what a savings goal IS in
# an envelope budget: a job for money, not a place spending gets classified.
SINKING_FUNDS = [
    ("Emergency Fund", 500.0, "#22c55e"),
    ("Travel Fund", 190.0, "#14b8a6"),
    ("Kitchen Renovation", 400.0, "#f59e0b"),
]


def _monthly_activity(tl: Timeline) -> dict[tuple[str, str], float]:
    """(category, month) → spend in base currency, over the COMPLETE months of the book.

    The current month is excluded everywhere it is used as a sample: it is partial, and
    including it would drag every median down by however far into the month the demo happens
    to be built.
    """
    from backend.core.fx import txn_base_amount
    from backend.db.transaction_store import TransactionStore

    cutoff = month_key(tl.today)
    by_cat_month: dict[tuple[str, str], float] = {}
    for txn in TransactionStore().all():
        if txn.get("deleted") or txn.get("kind") != "expense":
            continue
        month = (txn.get("date") or "")[:7]
        if not month or month >= cutoff:
            continue
        for line in txn.get("splits") or [{"category": txn.get("category"), "amount": None}]:
            category = line.get("category")
            if not category or category == "split":
                continue
            amount = (
                abs(float(line["amount"]))
                if line.get("amount") is not None
                else abs(txn_base_amount(txn))
            )
            by_cat_month[(category, month)] = by_cat_month.get((category, month), 0.0) + amount
    return by_cat_month


def _envelope_months(
    tl: Timeline, envelopes: list[dict]
) -> tuple[dict[str, dict[str, float]], dict[str, tuple[float, str]]]:
    """Per-envelope monthly spend, and the target derived from it.

    Returns ``(spend_by_envelope_and_month, targets)``. The target is the envelope's own
    median month, rounded up — except for the lumpy ones, which get a yearly pot sized off
    the real annual total, because a monthly target on a category you touch twice a year is
    permanently either over- or under-funded.
    """
    activity = _monthly_activity(tl)
    complete_months = max(len(tl.months) - 1, 1)
    spend: dict[str, dict[str, float]] = {}
    targets: dict[str, tuple[float, str]] = {}
    for envelope in envelopes:
        per_month: dict[str, float] = {}
        for (category, month), amount in activity.items():
            if category not in (envelope.get("category_ids") or []):
                continue
            per_month[month] = per_month.get(month, 0.0) + amount
        if not per_month:
            continue
        spend[envelope["id"]] = per_month
        total = sum(per_month.values())
        if envelope["name"] in YEARLY:
            targets[envelope["id"]] = (_round_up(total / complete_months * 12 * 1.05, 50), "yearly")
        else:
            samples = list(per_month.values())
            targets[envelope["id"]] = (
                _round_up(statistics.median(samples) * 1.06, 10),
                "monthly",
            )
    return spend, targets


def _round_up(value: float, step: int) -> float:
    return float(int((value + step - 0.01) // step) * step)


def seed_envelopes_and_budget(tl: Timeline) -> dict:
    """Mint the 1:1 envelopes, group them, target them off real activity, and budget every
    month of the book."""
    from backend.services import envelopes as envelopes_service
    from backend.services import planning as planning_service

    _group_envelopes()
    sinking = _create_sinking_funds()

    envelopes = [e for e in envelopes_service.all_envelopes() if not e.get("archived")]
    spend, targets = _envelope_months(tl, envelopes)
    for env_id, (amount, ttype) in targets.items():
        planning_service.apply_budget_change(
            {"type": "set_target", "envelope_id": env_id, "target_type": ttype, "amount": amount}
        )

    assigned = 0
    months = [month_key(m) for m in tl.months]
    current = months[-1]
    for i, month in enumerate(months):
        for env_id, (amount, ttype) in targets.items():
            planned = amount / 12.0 if ttype == "yearly" else amount
            # A CLOSED month is assigned near what it actually cost; the current month is
            # assigned to plan, because it has not happened yet. Assigning the plan to every
            # closed month instead would leave thirty months of small surpluses piled up in
            # the grocery envelope — arithmetically fine, and not what a real book looks like.
            actual = spend.get(env_id, {}).get(month, 0.0)
            base = planned if month == current else (actual * 0.6 + planned * 0.4)
            # A small per-month wobble: a real plan is revised, not stamped out.
            wobble = 1.0 + ((i * 37 + sum(map(ord, env_id[-4:]))) % 11 - 5) / 100.0
            planning_service.apply_budget_change(
                {
                    "type": "assign",
                    "month": month,
                    "envelope_id": env_id,
                    "amount": round(max(base, 0.0) * wobble, 2),
                }
            )
            assigned += 1
        for name, monthly, _color in SINKING_FUNDS:
            planning_service.apply_budget_change(
                {"type": "assign", "month": month, "envelope_id": sinking[name], "amount": monthly}
            )
            assigned += 1

    covered = _cover_overspending(months[-1])
    parked = _give_every_dollar_a_job(months, sinking["Emergency Fund"])
    return {
        "envelopes": len(envelopes),
        "targets": len(targets),
        "assignments": assigned,
        "covered": covered,
        "parked": parked,
    }


def _group_envelopes() -> None:
    """Put every envelope in a group a person would recognise, instead of one long list."""
    from backend.db.envelope_groups_store import EnvelopeGroupsStore
    from backend.services import envelopes as envelopes_service
    from backend.services import planning as planning_service

    groups = EnvelopeGroupsStore()
    by_name = {e["name"]: e for e in envelopes_service.all_envelopes()}
    for group_name, members in GROUPS:
        existing = next((g for g in groups.list_ordered() if g["name"] == group_name), None)
        group_id = (existing or groups.create({"name": group_name}))["id"]
        for name in members:
            envelope = by_name.get(name)
            if envelope is None or envelope.get("group_id") == group_id:
                continue
            try:
                planning_service.apply_budget_change(
                    {"type": "move_category", "category": envelope["id"], "group_id": group_id}
                )
            except Exception as exc:  # noqa: BLE001 — a synced envelope refuses the move by design
                log.info("demo: envelope %s stays where it is (%s)", name, exc)


def _create_sinking_funds() -> dict[str, str]:
    """The category-less envelopes that hold saved money. Returns name → envelope id."""
    from backend.db.envelope_groups_store import EnvelopeGroupsStore
    from backend.services import envelopes as envelopes_service

    groups = EnvelopeGroupsStore()
    existing_group = next((g for g in groups.list_ordered() if g["name"] == "Savings"), None)
    group_id = (existing_group or groups.create({"name": "Savings"}))["id"]

    # Envelope names are unique case-insensitively, and the starter budget already carries an
    # "Emergency fund" envelope — that one IS the pot, so the lookup must not be case-sensitive.
    by_name = {e["name"].strip().lower(): e["id"] for e in envelopes_service.all_envelopes()}
    out: dict[str, str] = {}
    for name, _monthly, color in SINKING_FUNDS:
        if name.lower() in by_name:
            out[name] = by_name[name.lower()]
            continue
        out[name] = envelopes_service.create_envelope(name, group_id=group_id, color=color)["id"]
    return out


def _cover_overspending(month: str) -> float:
    """Top up any envelope that finished the current month negative — carried-in holes included.

    Covering overspending is the tidying an envelope system invites every month, so a demo whose
    current month has four red rows and no coverage is showing the problem without the answer. A
    couple of rows are deliberately left overspent — see the tolerance below — because a budget
    with nothing to fix teaches nothing either.

    Under the restored YNAB identity (ADR 2026-09-09) the carry clamps at 0, so `available` here
    is THIS month's own overspend only — an earlier month's hole was already charged at its own
    roll and its row has since reset. Topping it up is what closes the row, and it genuinely
    COSTS Ready to Assign, dollar for dollar up to the hole (2026-09-08's "covering is free" is
    retired). The write is a plain `assign` through the money choke, deliberately NOT
    `_auto_cover_overspending`: the B4 cap belongs to the auto-assign TOOL, where the user must
    never be offered money that isn't there, while a seeder writes the book it means to show.
    Measured on the current generator, the cost is small next to the headline (39,274.53 →
    39,158.43 at the anchor month) and never approaches it; `_give_every_dollar_a_job` runs
    afterwards and parks the remainder into savings regardless, which is what lands the demo's
    Ready to Assign in its 300–900 band.
    """
    from backend.services import planning as planning_service

    covered = 0.0
    view = planning_service.budget_month(month)
    for group in view.get("groups") or []:
        for row in group.get("categories") or []:
            available = float(row.get("available") or 0)
            if available >= -25.0:  # a few dollars over is not worth a transfer
                continue
            env_id = row.get("envelope_id")
            if not env_id:
                continue
            planning_service.apply_budget_change(
                {
                    "type": "assign",
                    "month": month,
                    "envelope_id": env_id,
                    "amount": round(float(row.get("assigned") or 0) - available, 2),
                }
            )
            covered += -available
    return round(covered, 2)


def _give_every_dollar_a_job(months: list[str], catch_all: str) -> float:
    """Steer Ready-to-Assign into a small positive float, via the savings envelope.

    A book with two and a half years of saving behind it holds cash it accumulated BEFORE its
    first budgeted month. In an envelope budget that shows up as a large Ready-to-Assign,
    which reads as "you haven't started" rather than "you're running a plan". Assigning it to
    savings in the earliest budgeted months is exactly what somebody adopting an envelope
    budget does on day one: give the balance you already have a job.

    It corrects in BOTH directions — an over-assigned budget is a red banner, not a finished
    demo, and the earliest months may still leave too much unassigned. Pulling back walks forward
    through the months because one month's savings assignment is rarely headroom enough. The
    target is a band, not zero: a real budget is never exactly zeroed, and landing on 0.00
    looks staged.
    """
    from backend.services import planning as planning_service

    low, high, aim = 300.0, 900.0, 600.0
    moved = 0.0
    for _ in range(5):
        rta = float(planning_service.budget_month(months[-1])["ready_to_assign"])
        if low <= rta <= high:
            break
        remaining = rta - aim
        for month in months:
            if abs(remaining) < 0.01:
                break
            current = planning_service._assigned_amt(month, catch_all)
            # An assignment can never go negative, so a pull-back is capped per month.
            new = current + remaining if remaining > 0 else max(current + remaining, 0.0)
            if abs(new - current) < 0.01:
                continue
            planning_service.apply_budget_change(
                {
                    "type": "assign",
                    "month": month,
                    "envelope_id": catch_all,
                    "amount": round(new, 2),
                }
            )
            remaining -= new - current
            moved += new - current
        if abs(remaining) > 0.01:
            break  # no headroom left anywhere — stop rather than spin
    return round(moved, 2)


def seed_goals(tl: Timeline, accounts: dict) -> int:
    """Deadline savings targets — the "am I going to make it" surface."""
    from backend.db.targets_store import TargetsStore

    store = TargetsStore()
    goals = [
        {
            "name": "Six months of runway",
            "target_amount": 46000.0,
            "current_amount": 41700.0,
            "currency": "USD",
            "target_date": add_months(tl.today, 9).isoformat(),
            "linked_account_ids": [accounts["emergency"]],
            "priority": 1,
            "note": "Six months of core spending, held in the high-yield account.",
        },
        {
            "name": "Japan, next spring",
            "target_amount": 8500.0,
            "current_amount": 5900.0,
            "currency": "USD",
            "target_date": add_months(tl.today, 7).isoformat(),
            "linked_account_ids": [accounts["travelfund"]],
            "priority": 2,
            "note": "Two weeks, two people. Flights go on sale in November.",
        },
        {
            # Deliberately UNLINKED. `services.targets` treats a linked account's live balance
            # as authoritative and overrides the stored figure — so pointing this at the
            # emergency fund (which the commitment does draw on) would read its whole $41k
            # balance as renovation savings and mark a $26k goal "completed" on day one.
            # A second goal on the same account would double-count it besides.
            "name": "Kitchen renovation",
            "target_amount": 26000.0,
            "current_amount": 12000.0,
            "currency": "USD",
            "target_date": add_months(tl.today, 20).isoformat(),
            "priority": 3,
            "note": "Earmarked inside the emergency fund until the contractor is booked.",
        },
        {
            "name": "Replace the car",
            "target_amount": 19000.0,
            "current_amount": 19000.0,
            "currency": "USD",
            "target_date": add_months(tl.today, -2).isoformat(),
            "status": "completed",
            "priority": 4,
            "note": "Done — picked up the estate in July.",
        },
    ]
    for goal in goals:
        store.create(goal)
    return len(goals)


def seed_commitments(tl: Timeline, accounts: dict, positions: dict) -> int:
    """The dated obligations the Plan tab projects forward."""
    from backend.db.commitments_store import CommitmentsStore
    from backend.scripts.demo.commitments import COMMITMENTS

    store = CommitmentsStore()
    specs = COMMITMENTS(tl, accounts, positions)
    for spec in specs:
        store.create(spec)
    return len(specs)


def seed_watchlist() -> int:
    """A short shortlist, so the watchlist is not an empty screen."""
    from backend.core.ids import new_id
    from backend.db.watchlist_store import WatchlistStore

    store = WatchlistStore()
    # A named list of its own — "My holdings" is auto-managed and refuses hand-added items.
    watchlist = store.create_watchlist("Researching", "On the shortlist, not owned yet.")
    items = [
        {
            "type": "ticker",
            "value": "AVGO",
            "label": "Broadcom Inc.",
            "asset_class": "equity",
            "note": "Considering a starter position after the next earnings call.",
        },
        {"type": "ticker", "value": "COST", "label": "Costco Wholesale", "asset_class": "equity"},
        {"type": "ticker", "value": "SOL", "label": "Solana", "asset_class": "crypto"},
        {"type": "topic", "value": "US mortgage rates", "label": "US mortgage rates"},
    ]
    added = 0
    for item in items:
        try:
            # A topic is followed, never filed in a list (ADR 2026-09-27-topics-are-followed-…).
            lists = [] if item["type"] == "topic" else [watchlist["id"]]
            store.insert({"id": new_id("wli"), "watchlist_ids": lists, **item})
            added += 1
        except Exception as exc:  # noqa: BLE001 — decoration, never a build blocker
            log.warning("demo watchlist item %s skipped: %s", item["value"], exc)
    return added
