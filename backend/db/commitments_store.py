"""
CommitmentsStore — financial commitments (the "what I owe, when, and how I'll pay").

A commitment is a generic future obligation with a dated installment schedule and an
optional funding plan. Property purchases, car down-payments, renovations, school
fees, tax GIRO, and loan repayments are all the same shape — this store is the dumb
storage for that shape. All funding math (coverage verdict, earmark reconciliation,
projection events) lives in ``backend/commitments_service.py``, NOT here.

Commitment schema:
    id                 : str    slug (e.g. "riverside-heights") or generated
    name               : str    display name
    kind               : "purchase" | "loan_repayment" | "auto_pay" | "goal"
    currency           : str    native currency of the installments (e.g. "VND")
    status             : "active" | "completed" | "cancelled"
    linked_position_id : str|None  the property/asset position this funds (valuation)
    linked_account_id  : str|None  for loan_repayment: the liability account
    envelope_id        : str|None  the Planning envelope synced to this commitment's next
                                   installment (services.planning.sync_commitment_envelopes is
                                   the ONE writer of this FK — category-opaque-ids migration,
                                   replaces the old `commitment_{id}` bot-managed CATEGORY
                                   convention; a synced commitment mints NO category anymore,
                                   just a zero-category envelope)
    note               : str
    created_at         : ISO date
    installments       : [Installment]
    funding_plan       : [FundingSource]

Installment:
    id          : str
    label       : str    e.g. "Booking", "3rd", "Monthly Jan 2027"
    description : str
    due_date    : str    ISO date
    amount      : float  in commitment.currency (native)
    status      : "paid" | "due" | "upcoming"   (display; projection uses paid_txn_id)
    paid_txn_id : str|None  ledger txn that actually paid it (ledger-anchored "paid")

FundingSource (uniform shape; irrelevant fields are None per type):
    id            : str
    type          : "cash" | "income" | "asset_sale" | "loan" | "windfall"
    label         : str
    amount        : float|None   EVERY type — the frozen plan amount (asset_sale: the target
                                 the user intends to raise; owner decision 2026-07-26)
    currency      : str          currency of that amount
    ref_position_id : str|None   asset_sale: the position to sell
    sell_quantity   : float|None asset_sale: units to sell (informational, not the value)
    available_date  : str|None   when NEW-MONEY lands (asset_sale/loan/windfall)
    ref_account_id  : str|None   account the money comes from / the loan account
    repayment_commitment_id : str|None  loan: the sibling loan_repayment commitment
    note          : str
    disabled      : bool   set True when the action that would have unlocked this source is
                           marked "not happening" — excluded from projection/reconciliation
                           until re-enabled (services.commitments)
    cadence       : str|None   "monthly" — informational display hint for `type=income`
                           recurring set-asides; does not change projection semantics (income
                           is already counted in the baseline, see services/commitments.py)

Action (2026-07-22 commitments redesign): gained
    funding_id    : str|None   ties this action to the funding_plan[] entry it unlocks — the
                           "not happening" impact analysis reads this to know which source to
                           disable and to render "unlocks funding" on the row
    status        : "pending" | "done" | "missed"   ("missed" = "not happening", reversible)

Commitment: gained
    loan          : {principal, apr, term_months, payment} | None   set at creation by
                           services.commitments' loan-repayment helper; drives the detail
                           screen's loan-balance stat + early-payoff insight (pure client math)

See docs/rfc/commitments-and-funding-planning.md.

PLAN 4A TASK 3: DB-backed (Tier-1 planning, table ``commitments`` — backend/db/tables.py) via
``BookPayloadTableStore`` — the whole (deeply nested) commitment dict, installments/funding_plan/
actions/loan included, is stored as ONE verbatim ``payload`` column. Money lives in payload —
exact via JSON round-trip; no promoted money column, mirrors money_io's payload rule.
"""

from __future__ import annotations

import re
import uuid

from backend.core.errors import ValidationError
from backend.core.user_time import user_today
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import commitments

KINDS = {"purchase", "loan_repayment", "auto_pay", "goal"}
STATUSES = {"active", "completed", "cancelled"}
INSTALLMENT_STATUSES = {"paid", "due", "upcoming"}
FUNDING_TYPES = {"cash", "income", "asset_sale", "loan", "windfall"}
# A funding source's EXECUTION state — did this money actually arrive? Installments have
# carried `status` + `paid_txn_id` since day one; funding rows never could, so a plan could
# not be falsified by anything the owner did or failed to do. "missed" is deliberately NOT
# here: it is DERIVED from `available_date` at read time (services.commitments), because a
# stored "missed" is only ever true if something sweeps the table daily.
FUNDING_STATUSES = {"planned", "done"}
ACTION_STATUSES = {"pending", "done", "missed"}


def _today() -> str:
    return user_today().isoformat()


def _slugify(text: str) -> str:
    """Lower-case kebab slug; '' if nothing usable (caller falls back to a uuid)."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s


def _to_float(value, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _make_installment(data: dict) -> dict:
    status = data.get("status")
    if status not in INSTALLMENT_STATUSES:
        status = "upcoming"
    return {
        "id": data.get("id") or f"inst-{uuid.uuid4().hex[:8]}",
        "label": data.get("label", ""),
        "description": data.get("description", ""),
        "due_date": data.get("due_date", ""),
        "amount": _to_float(data.get("amount"), 0.0),
        "status": status,
        "paid_txn_id": data.get("paid_txn_id") or None,
    }


def _make_funding_source(data: dict) -> dict:
    ftype = data.get("type")
    if ftype not in FUNDING_TYPES:
        ftype = "cash"
    return {
        "id": data.get("id") or f"fs-{uuid.uuid4().hex[:8]}",
        "type": ftype,
        "label": data.get("label", ""),
        # EVERY type stores its own frozen amount — asset_sale included (owner decision
        # 2026-07-26: the plan must not drift with the market; see live_source_value).
        "amount": _to_float(data.get("amount"), None),
        "currency": (data.get("currency") or "").upper(),
        # asset_sale context: which position, how many units — informational, not the value.
        "ref_position_id": data.get("ref_position_id") or None,
        "sell_quantity": _to_float(data.get("sell_quantity"), None)
        if ftype == "asset_sale"
        else None,
        # NEW-MONEY sources land on a date; baseline-backed (cash/income) don't.
        "available_date": data.get("available_date") or None,
        "ref_account_id": data.get("ref_account_id") or None,
        "repayment_commitment_id": data.get("repayment_commitment_id") or None,
        "note": data.get("note", ""),
        "disabled": bool(data.get("disabled", False)),
        "cadence": data.get("cadence") or None,
        "status": data.get("status") if data.get("status") in FUNDING_STATUSES else "planned",
        "realized_txn_id": data.get("realized_txn_id") or None,
    }


def _make_action(data: dict) -> dict:
    """A dated task/event on a commitment that is NOT a payment — apply for a loan,
    sign a contract, confirm a transfer, review the plan, verify an auto-debit, a
    stock-sale window, handover. The 'events/actions' checklist layer."""
    status = data.get("status")
    if status not in ACTION_STATUSES:
        status = "pending"
    return {
        "id": data.get("id") or f"act-{uuid.uuid4().hex[:8]}",
        "label": data.get("label", ""),
        "category": data.get("category", ""),
        "due_date": data.get("due_date", ""),
        "start_by": data.get("start_by") or None,
        "amount": _to_float(data.get("amount"), None),
        "currency": (data.get("currency") or "").upper(),
        "status": status,
        "counterparty": data.get("counterparty", ""),
        "note": data.get("note", ""),
        "funding_id": data.get("funding_id") or None,
        # FK to a real Automation row. Set/cleared by the Actions tab's "remind me" bell;
        # this store never creates/deletes the Automation itself, only carries the link.
        "reminder_automation_id": data.get("reminder_automation_id") or None,
    }


def _make_loan(data: dict | None) -> dict | None:
    if not data:
        return None
    return {
        "principal": _to_float(data.get("principal"), 0.0),
        "apr": _to_float(data.get("apr"), 0.0),
        "term_months": int(data.get("term_months") or 0),
        "payment": _to_float(data.get("payment"), 0.0),
    }


def _hydrate_funding(commitment: dict | None) -> dict | None:
    """Fill execution state onto funding rows written before it existed.

    Read-side, not a migration: this store is a `BookPayloadTableStore`, so `funding_plan[]`
    is JSON inside the payload and a real book (the owner's holds 41 such rows) would
    otherwise hand every consumer a missing key to defend against. `query()`/`count()` route
    through `all()`, so overriding `all()` and `find_by_id()` covers every read path."""
    if not commitment:
        return commitment
    for source in commitment.get("funding_plan") or []:
        if source.get("status") not in FUNDING_STATUSES:
            source["status"] = "planned"
        source.setdefault("realized_txn_id", None)
    return commitment


class CommitmentsStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(commitments, id_field="id", engine=engine)

    # ── Reads (hydrated — see _hydrate_funding) ───────────────────────────────

    def all(self, *, conn=None) -> list[dict]:
        return [_hydrate_funding(c) for c in super().all(conn=conn)]

    def find_by_id(self, id_val: str, *, conn=None) -> dict | None:
        return _hydrate_funding(super().find_by_id(id_val, conn=conn))

    # ── Create ────────────────────────────────────────────────────────────────

    def create(self, data: dict) -> dict:
        """Build and persist a commitment with defaults, a unique id, and normalized
        nested installments / funding sources."""
        name = (data.get("name") or "").strip()
        cid = data.get("id") or _slugify(name) or uuid.uuid4().hex[:8]
        cid = self._unique_id(cid)

        kind = data.get("kind")
        if kind not in KINDS:
            kind = "purchase"
        status = data.get("status")
        if status not in STATUSES:
            status = "active"

        commitment = {
            "id": cid,
            "name": name,
            "kind": kind,
            "currency": (data.get("currency") or "").upper(),
            "status": status,
            "linked_position_id": data.get("linked_position_id") or None,
            "linked_account_id": data.get("linked_account_id") or None,
            "envelope_id": data.get("envelope_id") or None,
            "note": data.get("note", ""),
            "created_at": data.get("created_at") or _today(),
            "installments": [_make_installment(i) for i in (data.get("installments") or [])],
            "funding_plan": [_make_funding_source(f) for f in (data.get("funding_plan") or [])],
            "actions": [_make_action(a) for a in (data.get("actions") or [])],
            "loan": _make_loan(data.get("loan")),
        }
        return self.insert(commitment)

    def _unique_id(self, candidate: str) -> str:
        existing = {c.get("id") for c in self.all()}
        if candidate not in existing:
            return candidate
        n = 2
        while f"{candidate}-{n}" in existing:
            n += 1
        return f"{candidate}-{n}"

    # ── Top-level edit (whitelisted + validated) ───────────────────────────────

    def update_commitment(self, cid: str, changes: dict) -> dict | None:
        """Patch top-level fields only (never installments/funding_plan — those have
        dedicated methods). Unknown/immutable keys are dropped; kind/status validated."""
        if self.find_by_id(cid) is None:
            return None
        editable = {
            "name",
            "kind",
            "currency",
            "status",
            "linked_position_id",
            "linked_account_id",
            "envelope_id",
            "note",
        }
        clean = {k: v for k, v in changes.items() if k in editable}
        if "kind" in clean and clean["kind"] not in KINDS:
            clean.pop("kind")
        if "status" in clean and clean["status"] not in STATUSES:
            clean.pop("status")
        if "currency" in clean:
            clean["currency"] = (clean["currency"] or "").upper()
        return self.update_by_id(cid, clean) if clean else self.find_by_id(cid)

    # ── Nested writes (the base store only patches top-level items) ─────────────

    def set_installments(self, cid: str, installments: list[dict]) -> dict | None:
        """Replace the whole installments[] — used after a schedule is generated."""
        if self.find_by_id(cid) is None:
            return None
        rows = [_make_installment(i) for i in (installments or [])]
        return self.update_by_id(cid, {"installments": rows})

    def update_installment(self, cid: str, inst_id: str, changes: dict) -> dict | None:
        """Patch ONE installment row in place — fix row 7 without regenerating the rest.
        Returns the updated commitment, or None if commitment/installment not found."""
        commitment = self.find_by_id(cid)
        if commitment is None:
            return None
        installments = commitment.get("installments", [])
        patched = False
        for inst in installments:
            if inst.get("id") == inst_id:
                clean = dict(changes)
                if "amount" in clean:
                    clean["amount"] = _to_float(clean["amount"], inst.get("amount", 0.0))
                if "status" in clean and clean["status"] not in INSTALLMENT_STATUSES:
                    clean.pop("status")
                if "id" in clean:  # id is immutable
                    clean.pop("id")
                inst.update(clean)
                patched = True
                break
        if not patched:
            return None
        return self.update_by_id(cid, {"installments": installments})

    def set_funding_plan(self, cid: str, sources: list[dict]) -> dict | None:
        """Replace the whole funding_plan[]."""
        if self.find_by_id(cid) is None:
            return None
        rows = [_make_funding_source(s) for s in (sources or [])]
        return self.update_by_id(cid, {"funding_plan": rows})

    def update_funding_source(self, cid: str, fid: str, changes: dict) -> dict | None:
        """Patch ONE funding source in place — e.g. flip `disabled` when the action that
        would have unlocked it is marked "not happening", without resending the rest of
        the plan. Returns the updated commitment, or None if commitment/source not found."""
        commitment = self.find_by_id(cid)
        if commitment is None:
            return None
        sources = commitment.get("funding_plan", [])
        patched = False
        for f in sources:
            if f.get("id") == fid:
                clean = dict(changes)
                if "amount" in clean:
                    clean["amount"] = _to_float(clean["amount"], f.get("amount"))
                if "disabled" in clean:
                    clean["disabled"] = bool(clean["disabled"])
                if "status" in clean and clean["status"] not in FUNDING_STATUSES:
                    raise ValidationError(
                        f"funding source status must be one of {sorted(FUNDING_STATUSES)}, "
                        f"got {clean['status']!r}"
                    )
                if "currency" in clean:
                    cur = str(clean["currency"] or "").strip().upper()
                    if len(cur) != 3 or not cur.isalpha():
                        raise ValidationError(
                            "funding source currency must be a 3-letter ISO 4217 code, "
                            f"got {clean['currency']!r}"
                        )
                    clean["currency"] = cur
                clean.pop("id", None)
                clean.pop("type", None)  # type is immutable post-create
                f.update(clean)
                patched = True
                break
        if not patched:
            return None
        return self.update_by_id(cid, {"funding_plan": sources})

    # ── Granular child-collection writes (append / all-or-nothing remove) ────────
    # Existing rows and their ids are never touched by an append — ids are load-bearing:
    # actions reference funding sources via funding_id.

    def _append_child_rows(self, cid: str, key: str, maker, rows: list[dict]) -> dict | None:
        commitment = self.find_by_id(cid)
        if commitment is None:
            return None
        combined = list(commitment.get(key, []))
        # Append ALWAYS mints fresh ids — a caller-supplied id could collide with an
        # existing row's, and duplicate ids corrupt every id-addressed path (a remove of
        # that id would drop both rows). create()/set_* legitimately honor supplied ids
        # (round-tripping); the append path never does.
        combined.extend(maker({k: v for k, v in r.items() if k != "id"}) for r in (rows or []))
        return self.update_by_id(cid, {key: combined})

    def _remove_child_rows(
        self, cid: str, key: str, ids: list[str], is_blocked
    ) -> tuple[dict | None, list[str], list[str]]:
        """All-or-nothing removal from a child collection. Returns (commitment, [], [])
        on success; (None, unknown_ids, blocked_ids) on refusal — nothing removed; and
        (None, [], []) when the commitment itself is not found. ``is_blocked(row)`` marks
        rows that refuse removal (e.g. a paid installment anchoring a ledger txn)."""
        commitment = self.find_by_id(cid)
        if commitment is None:
            return None, [], []
        rows = commitment.get(key, [])
        existing = {r.get("id") for r in rows}
        unknown = [i for i in ids if i not in existing]
        drop = set(ids)
        blocked = [r["id"] for r in rows if r.get("id") in drop and is_blocked(r)]
        if unknown or blocked:
            return None, unknown, blocked
        kept = [r for r in rows if r.get("id") not in drop]
        return self.update_by_id(cid, {key: kept}), [], []

    def add_funding_sources(self, cid: str, sources: list[dict]) -> dict | None:
        """Append sources to the existing funding_plan[]. None if commitment not found."""
        return self._append_child_rows(cid, "funding_plan", _make_funding_source, sources)

    def remove_funding_sources(self, cid: str, fids: list[str]) -> tuple[dict | None, list[str]]:
        """Remove sources by id, all-or-nothing. Returns (updated commitment, []) on
        success, (None, unknown_ids) when any id is missing — nothing removed — and
        (None, []) when the commitment itself is not found."""
        c, unknown, _blocked = self._remove_child_rows(cid, "funding_plan", fids, lambda f: False)
        return c, unknown

    def add_installments(self, cid: str, installments: list[dict]) -> dict | None:
        """Append installments to the existing schedule. None if commitment not found."""
        return self._append_child_rows(cid, "installments", _make_installment, installments)

    def remove_installments(
        self, cid: str, installment_ids: list[str]
    ) -> tuple[dict | None, list[str], list[str]]:
        """All-or-nothing removal. A PAID installment (paid_txn_id set — it anchors a real
        ledger txn) refuses removal; unlink it first via update_installment."""
        return self._remove_child_rows(
            cid, "installments", installment_ids, lambda i: bool(i.get("paid_txn_id"))
        )

    def add_actions(self, cid: str, actions: list[dict]) -> dict | None:
        """Append actions to the existing checklist. None if commitment not found."""
        return self._append_child_rows(cid, "actions", _make_action, actions)

    def remove_actions(
        self, cid: str, action_ids: list[str]
    ) -> tuple[dict | None, list[str], list[str]]:
        """All-or-nothing removal. An action with a LIVE reminder Automation
        (reminder_automation_id set) refuses removal — the reminder would keep firing
        about a row that no longer exists; remove the reminder first."""
        return self._remove_child_rows(
            cid, "actions", action_ids, lambda a: bool(a.get("reminder_automation_id"))
        )

    def set_loan(self, cid: str, loan: dict | None) -> dict | None:
        """Store the loan terms (principal/apr/term_months/payment) a loan_repayment
        commitment's amortization schedule was generated from."""
        if self.find_by_id(cid) is None:
            return None
        return self.update_by_id(cid, {"loan": _make_loan(loan)})

    def set_actions(self, cid: str, actions: list[dict]) -> dict | None:
        """Replace the whole actions[] (the events/checklist layer)."""
        if self.find_by_id(cid) is None:
            return None
        rows = [_make_action(a) for a in (actions or [])]
        return self.update_by_id(cid, {"actions": rows})

    def update_action(self, cid: str, action_id: str, changes: dict) -> dict | None:
        """Patch ONE action row (typically mark it done). Returns the commitment or None."""
        commitment = self.find_by_id(cid)
        if commitment is None:
            return None
        actions = commitment.get("actions", [])
        patched = False
        for a in actions:
            if a.get("id") == action_id:
                clean = dict(changes)
                if "amount" in clean:
                    clean["amount"] = _to_float(clean["amount"], a.get("amount"))
                if "status" in clean and clean["status"] not in ACTION_STATUSES:
                    clean.pop("status")
                clean.pop("id", None)
                a.update(clean)
                patched = True
                break
        if not patched:
            return None
        return self.update_by_id(cid, {"actions": actions})

    # ── Queries ─────────────────────────────────────────────────────────────────

    def query_commitments(self, kind: str | None = None, status: str | None = None) -> list[dict]:
        items = self.all()
        if kind is not None:
            items = [c for c in items if c.get("kind") == kind]
        if status is not None:
            items = [c for c in items if c.get("status") == status]
        return items

    def get_next_installment(self, cid: str) -> dict | None:
        """First not-yet-paid installment by due_date (for the 'next payment' display)."""
        commitment = self.find_by_id(cid)
        if commitment is None:
            return None
        unpaid = [i for i in commitment.get("installments", []) if i.get("status") != "paid"]
        if not unpaid:
            return None
        return sorted(unpaid, key=lambda i: i.get("due_date") or "")[0]
