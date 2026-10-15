"""Outcome-based scoring: assert against the resulting DATA, never the transcript.

The scoring model is a BEFORE/AFTER snapshot diff of the whole book. A task declares
what it expects to change and CONSUMES those rows; anything left in the diff that no
expectation claimed is by definition an UNINTENDED side effect (a duplicate row, a
minted account, a wrong-envelope assignment) and fails the run.

That inversion is the important part. A checker that only asserts "the right row is
present" silently passes a run that also booked the row twice. Here, silence is not
enough — every change must be accounted for.

Three outcome axes are recorded per run, deliberately kept separate because they fail
for different reasons and a single blended "score" would hide that:

* ``intended``  — did the correct effect land (right amount, sign, currency, account,
  category, date, kind)?
* ``unintended`` — did anything else land?
* ``value_ok``  — for reads, does the number the model reported match the value computed
  by the service directly? (``None`` for tasks that are not reads.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.evals.tool_surface import bootstrap  # noqa: F401

# Collections tracked in a snapshot. Each is {id: row}.
COLLECTIONS = (
    "transactions",
    "accounts",
    "envelopes",
    "lots",
    "realized",
    "positions",
    "categories",
    "payees",
)

#: Collections whose rows are a DOCUMENTED consequence of another consumed change
#: rather than an independent side effect. ``ledger.create_transaction`` mints/updates a
#: payee row for essentially every write (resolve_payee_id / note_usage / learn_category),
#: so flagging that as "unintended" would fail every ledger task for all arms equally —
#: noise, not signal. They are still SNAPSHOTTED (so a payee change with no accompanying
#: transaction change is still caught) and still reported, just not counted as unintended
#: when a transaction change was legitimately consumed.
DERIVED_COLLECTIONS = {"payees": "transactions"}


@dataclass
class Snapshot:
    data: dict[str, dict[str, dict]]
    assignments: dict[str, float]
    computed: dict[str, Any]

    def rows(self, collection: str) -> dict[str, dict]:
        return self.data.get(collection, {})


@dataclass
class Delta:
    """What changed between two snapshots."""

    added: dict[str, dict[str, dict]] = field(default_factory=dict)
    changed: dict[str, dict[str, tuple[dict, dict]]] = field(default_factory=dict)
    removed: dict[str, dict[str, dict]] = field(default_factory=dict)
    assignment_changes: dict[str, tuple[float, float]] = field(default_factory=dict)
    before: Snapshot | None = None
    after: Snapshot | None = None

    def added_rows(self, collection: str) -> dict[str, dict]:
        return self.added.get(collection, {})

    def changed_rows(self, collection: str) -> dict[str, tuple[dict, dict]]:
        return self.changed.get(collection, {})

    def total_changes(self) -> int:
        n = sum(len(v) for v in self.added.values())
        n += sum(len(v) for v in self.changed.values())
        n += sum(len(v) for v in self.removed.values())
        return n + len(self.assignment_changes)


def take_snapshot(month: str) -> Snapshot:
    """Read the whole book into a comparable structure."""
    from backend.db.account_store import AccountStore
    from backend.db.envelope_store import EnvelopeStore
    from backend.db.lot_store import LotStore
    from backend.db.position_store import PositionStore
    from backend.db.realized_event_store import RealizedEventStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger, planning
    from backend.services.categories import all_category_rows

    def by_id(rows: list[dict]) -> dict[str, dict]:
        return {r["id"]: dict(r) for r in rows if r.get("id")}

    txns = {
        r["id"]: dict(r) for r in TransactionStore().all() if r.get("id") and not r.get("deleted")
    }

    from backend.db.payee_store import PayeeStore

    data = {
        "transactions": txns,
        "payees": by_id(PayeeStore().all()),
        "accounts": by_id(AccountStore().all()),
        "envelopes": by_id(EnvelopeStore().all()),
        "lots": by_id(LotStore().all()),
        "realized": by_id(RealizedEventStore().all()),
        "positions": by_id(PositionStore().all()),
        "categories": by_id(all_category_rows()),
    }

    month_data = planning.budget_month(month)
    assignments: dict[str, float] = {}
    for group in month_data.get("groups", []):
        for row in group.get("categories", []):
            key = row.get("envelope_id") or row.get("key")
            if key:
                assignments[key] = float(row.get("assigned") or 0.0)

    computed = {
        "net_worth": ledger.net_worth(),
        "budget_month": month_data,
        "ready_to_assign": month_data.get("ready_to_assign"),
    }
    return Snapshot(data=data, assignments=assignments, computed=computed)


def diff(before: Snapshot, after: Snapshot) -> Delta:
    delta = Delta(before=before, after=after)
    for collection in COLLECTIONS:
        old, new = before.rows(collection), after.rows(collection)
        added = {k: v for k, v in new.items() if k not in old}
        removed = {k: v for k, v in old.items() if k not in new}
        changed = {
            k: (old[k], new[k]) for k in new if k in old and _material(old[k]) != _material(new[k])
        }
        if added:
            delta.added[collection] = added
        if removed:
            delta.removed[collection] = removed
        if changed:
            delta.changed[collection] = changed

    for key, new_value in after.assignments.items():
        old_value = before.assignments.get(key, 0.0)
        if abs(new_value - old_value) > 0.005:
            delta.assignment_changes[key] = (old_value, new_value)
    for key, old_value in before.assignments.items():
        if key not in after.assignments and abs(old_value) > 0.005:
            delta.assignment_changes[key] = (old_value, 0.0)
    return delta


_VOLATILE = {"updated_at", "created_at", "_rev"}


def _material(row: dict) -> dict:
    """Row minus fields that churn on every write and carry no meaning for scoring."""
    return {k: v for k, v in row.items() if k not in _VOLATILE}


# ── Check result ─────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """A task's verdict. ``consumed`` is how a check accounts for the rows it expected."""

    intended: bool
    reasons: list[str] = field(default_factory=list)
    consumed: set[tuple[str, str]] = field(default_factory=set)
    consumed_assignments: set[str] = field(default_factory=set)
    value_ok: bool | None = None
    #: True when ``value_ok`` GATES success. Read tasks set this: for a read, the stated
    #: figure IS the deliverable, so a confidently wrong number is a failed run, not a
    #: footnote. Near-miss tasks leave it False, because there ``value_ok`` only records
    #: the softer "did it also ask a sensible question" signal — a silent, clean refusal
    #: is already a correct data outcome.
    value_gates_success: bool = False
    allow_any_change: bool = False  # opt-out for checks that manage their own accounting

    def fail(self, reason: str) -> "CheckResult":
        self.intended = False
        self.reasons.append(reason)
        return self


def unintended_changes(delta: Delta, result: CheckResult) -> list[str]:
    """Everything in the diff that the task's check did not claim."""
    if result.allow_any_change:
        return []

    # A derived collection's churn is only excused when its PARENT change was actually
    # accounted for; a payee mutation with no consumed transaction is still a real
    # unexplained side effect and must surface.
    excused = {
        collection
        for collection, parent in DERIVED_COLLECTIONS.items()
        if any(kind == parent for kind, _ in result.consumed)
    }

    out: list[str] = []
    for collection in COLLECTIONS:
        if collection in excused:
            continue
        for row_id, row in delta.added_rows(collection).items():
            if (collection, row_id) not in result.consumed:
                out.append(f"unexpected new {collection[:-1]}: {_describe(collection, row)}")
        for row_id, (_old, new) in delta.changed_rows(collection).items():
            if (collection, row_id) not in result.consumed:
                out.append(f"unexpected modified {collection[:-1]}: {_describe(collection, new)}")
        for row_id, row in delta.removed.get(collection, {}).items():
            if (collection, row_id) not in result.consumed:
                out.append(f"unexpected deleted {collection[:-1]}: {_describe(collection, row)}")
    for envelope_id, (old, new) in delta.assignment_changes.items():
        if envelope_id not in result.consumed_assignments:
            out.append(f"unexpected assignment change on {envelope_id}: {old} -> {new}")
    return out


def _describe(collection: str, row: dict) -> str:
    if collection == "transactions":
        return (
            f"{row.get('date')} {row.get('kind')} {row.get('description')!r} "
            f"from={row.get('from_amount')} to={row.get('to_amount')}"
        )
    if collection == "accounts":
        return f"{row.get('name')!r} ({row.get('currency')})"
    return str(row.get("id"))


# ── Matchers used by task checks ─────────────────────────────────────────────


def find_txn(delta: Delta, **criteria: Any) -> tuple[str, dict] | None:
    """Find ONE newly added transaction matching every criterion.

    Supported criteria: date, kind, category (label or id), from_account_id,
    to_account_id, from_amount, to_amount, from_currency, to_currency,
    original_amount, original_currency, description_contains. Amounts compare with a
    1-cent tolerance.
    """
    for row_id, row in delta.added_rows("transactions").items():
        if _matches(row, criteria):
            return row_id, row
    return None


def find_all_txns(delta: Delta, **criteria: Any) -> list[tuple[str, dict]]:
    return [
        (row_id, row)
        for row_id, row in delta.added_rows("transactions").items()
        if _matches(row, criteria)
    ]


def _matches(row: dict, criteria: dict) -> bool:
    for key, expected in criteria.items():
        if expected is None:
            continue
        if key == "description_contains":
            if str(expected).lower() not in str(row.get("description") or "").lower():
                return False
            continue
        if key == "category":
            if not _category_matches(row.get("category"), expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            if abs(float(actual) - float(expected)) > 0.011:
                return False
            continue
        if isinstance(expected, str) and isinstance(actual, str):
            if actual.upper() != expected.upper():
                return False
            continue
        if actual != expected:
            return False
    return True


def _category_matches(actual: Any, expected: Any) -> bool:
    """Compare a stored category id against an expected id OR human label."""
    if expected is None:
        return actual in (None, "")
    if actual in (None, ""):
        return False
    if str(actual) == str(expected):
        return True
    from backend.services.categories import all_category_rows

    for row in all_category_rows():
        if row.get("id") == actual:
            return str(row.get("label", "")).lower() == str(expected).lower()
    return False


def category_id_for(label: str) -> str | None:
    from backend.services.categories import all_category_rows

    for row in all_category_rows():
        if str(row.get("label", "")).lower() == label.lower():
            return row["id"]
    return None


# ── Read-value checking ──────────────────────────────────────────────────────

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def text_states_value(text: str, expected: float, tolerance: float = 0.02) -> bool:
    """True if the model's final message states ``expected`` (within tolerance).

    Deliberately permissive about formatting (thousands separators, currency symbols,
    sign, a parenthesised negative) and deliberately strict about the VALUE. It is a
    weaker signal than a data assertion — a read task can only be checked through what
    the model said — so it is reported on its own axis rather than folded into
    ``intended``.
    """
    if not text:
        return False
    target = abs(round(float(expected), 2))
    for raw in _NUMBER.findall(text):
        try:
            value = abs(float(raw.replace(",", "")))
        except ValueError:
            continue
        if abs(value - target) <= tolerance:
            return True
        # Accept a value rounded to whole units (e.g. "about $102,278").
        if abs(round(value) - round(target)) < 0.5 and target >= 100:
            return True
    return False


_CLARIFY_HINTS = (
    "which account",
    "which one",
    "could you clarify",
    "can you clarify",
    "clarify",
    "did you mean",
    "i don't see",
    "i do not see",
    "couldn't find",
    "could not find",
    "no account",
    "not sure",
    "which of",
    "let me know",
    "confirm",
    "?",
)


def text_asks_for_clarification(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _CLARIFY_HINTS)


CheckFn = Callable[[Delta, dict, str], CheckResult]
