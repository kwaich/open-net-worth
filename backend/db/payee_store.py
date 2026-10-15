"""PayeeStore — first-class payee entities (P4-PAYEE phase 1).

A payee is **config, not money movement**: a stable id + display name + an
auto-learned default category + the normalized aliases that resolve a raw bank
description to this entity. The entity LOGIC (resolve-or-create, default-category
learning, usage) lives in ``backend/services/payee_registry.py``; this store is the
dumb storage-format CRUD only.

Payee schema (see ``.claude/rfcs/phase4/payee-registry.md`` §1):
    id               : str    "pay_<uuid>" — the anchor stored on transactions
    name             : str    display name ("Anthropic")
    default_categories : {kind: category_id} auto-learned per-kind default (expense|income|
                         transfer|investment); a kind absent from the map has no default.
                         The one-time legacy `default_category` scalar (the un-migrated
                         expense-only field) was dropped 2026-07-19 —
                         a one-shot migration since removed
                         folded any surviving value into ``default_categories["expense"]``.
    autocategorize   : bool   apply default on new txns (Phase-1: always True)
    hidden           : bool   suppress from autocomplete (Phase-1: stored, unused)
    aliases          : [str]  normalized strings (from _normalize_payee) → this payee
    rules            : [{match_type, value}]  raw-description renaming rules (run BEFORE alias lookup):
                        match_type ∈ "contains"|"is"|"regex"; value = case-insensitive pattern
    last_used        : str|None ISO date of most-recent use (ranking signal; stored)
    created_at       : str    ISO 8601

Note: ``txn_count`` is NOT stored — it is computed live from the transaction store by
``payee_registry.list_registry()`` so it stays accurate after soft-deletes and reassignments.

PLAN 4D TASK 2: DB-backed (Tier-1, book-scoped, table ``payees``) via
``BookPayloadTableStore`` — spec:139-140 lists payees explicitly as Tier-1. MONEY-ADJACENT:
``payee_id`` and its learned ``default_categories`` are consumed by the ledger choke
(``services/ledger.py``/``services/payee_registry.py``) on every transaction create, so this
migration must not shift categorization behavior — every domain method below is an
unmodified port over ``all``/``find_by_id``/``insert``/``update_by_id``. See
``services/ledger.py`` for a related, separately-fixed self-deadlock this migration exposed
(``payee_registry.note_usage``/``learn_category`` used to run safely inside the ledger's own
DB transaction only because ``PayeeStore`` was file-backed; now that it opens its own
transaction, those two calls were moved to run AFTER the ledger write commits — see that
file's own comment for the full account).
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.ids import new_id
from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import payees


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PayeeStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(payees, id_field="id", engine=engine)

    # ── Create ────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        *,
        default_category: str | None = None,
        aliases: list[str] | None = None,
    ) -> dict:
        """Mint a payee with a ``pay_<uuid>`` id and the given name/aliases. The
        caller (``payee_registry``) is responsible for de-duping by alias first —
        this just persists. ``default_category``, if given, seeds the EXPENSE slot of
        ``default_categories`` (there is no separate legacy field to write). Returns
        the stored record."""
        payee = {
            "id": new_id("pay"),
            "name": (name or "").strip() or "Unknown payee",
            "default_categories": {"expense": default_category} if default_category else {},
            "autocategorize": True,
            "hidden": False,
            "aliases": _clean_aliases(aliases),
            "last_used": None,
            "created_at": _now(),
        }
        return self.insert(payee)

    # ── Queries ─────────────────────────────────────────────────────────────────

    def find_by_alias(self, norm: str) -> dict | None:
        """Return the payee whose ``aliases`` contains ``norm`` (the normalized
        description key), or None. Linear scan — the registry is small (same cost
        profile as ``transaction_store.by_external_ref``)."""
        if not norm:
            return None
        for p in self.all():
            if norm in (p.get("aliases") or []):
                return p
        return None

    def add_alias(self, payee_id: str, norm: str) -> dict | None:
        """Append a normalized alias if not already present. No-op if absent/dup."""
        p = self.find_by_id(payee_id)
        if p is None or not norm:
            return p
        aliases = list(p.get("aliases") or [])
        if norm in aliases:
            return p
        aliases.append(norm)
        return self.update_by_id(payee_id, {"aliases": aliases})

    # ── Mutations (best-effort usage + learning) ────────────────────────────────

    def set_default_category_for_kind(
        self, payee_id: str, kind: str, category: str | None
    ) -> dict | None:
        """Merge one kind's default category into ``default_categories`` (a blank/None
        value CLEARS that kind). Never touches the other kinds' slots."""
        p = self.find_by_id(payee_id)
        if p is None:
            return None
        current = dict(p.get("default_categories") or {})
        if category:
            current[kind] = category
        else:
            current.pop(kind, None)
        return self.update_by_id(payee_id, {"default_categories": current})

    def set_default_categories(self, payee_id: str, mapping: dict) -> dict | None:
        """Replace the whole per-kind default map (already validated by the caller)."""
        return self.update_by_id(payee_id, {"default_categories": dict(mapping or {})})

    def bump_usage(self, payee_id: str, *, when: str | None) -> dict | None:
        """Update last_used = max(last_used, when). Best-effort ranking signal;
        never raises (a failed stat write must not break the ledger write path).
        txn_count is NOT stored — it is computed live by list_registry()."""
        try:
            p = self.find_by_id(payee_id)
            if p is None:
                return None
            cur = p.get("last_used") or ""
            d = (when or "")[:10]
            return self.update_by_id(
                payee_id,
                {"last_used": d if d > cur else (cur or None)},
            )
        except Exception:
            return None


def _clean_aliases(aliases: list[str] | None) -> list[str]:
    """De-duped, order-preserving list of non-empty normalized strings."""
    out: list[str] = []
    for a in aliases or []:
        a = (a or "").strip()
        if a and a not in out:
            out.append(a)
    return out
