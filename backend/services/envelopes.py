"""
envelopes_service — the budget envelope entity, decoupled from category (P0 foundation).

Owns the envelope reverse-index (``env_of``/``budgeted_category_ids``) and the idempotent
first-boot seed that mints one 1:1 envelope per currently-budgeted category so a fresh
install's budget is immediately usable.

Money (assigned amounts, targets, notes) is ENVELOPE-ID NATIVE — see
docs/adr/2026-07-05-envelope-id-native.md. There is no category-keyed storage representation
anywhere in this layer; a category is a pure transaction label, an envelope is what holds
money.

Deliberately does NOT import ``services.planning`` — that module is the CALLER of this one
(activity/available/RTA are expressed over envelopes), so importing it here would create a
cycle. Where this module needs a value ``planning`` also derives (the budgeted-category
universe), it reads the same underlying sources directly (``services.budgets`` + the
assignments/targets stores), mirroring ``planning._spending_categories()`` without depending
on it.

See ``.claude/rfcs/envelope-not-category.md`` and its successor
``.claude/rfcs/envelope-category-full-split.md`` (envelope and category creation fully
decoupled — ``create_envelope`` mints NO category; ``create_category`` mints NO envelope).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.ids import new_id
from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.budget_engine_state_store import BudgetEngineStateStore
from backend.db.category_store import CategoryStore
from backend.db.engine import locked_unit_of_work
from backend.db.envelope_groups_store import EnvelopeGroupsStore
from backend.db.envelope_store import EnvelopeStore
from backend.db.plan_state_store import PlanStateStore
from backend.db.tables import budget_assignments as budget_assignments_table
from backend.db.tables import budget_engine_state as budget_engine_state_table
from backend.db.tables import envelopes as envelopes_table
from backend.db.tables import plan_state as plan_state_table
from backend.db.tables import profile as profile_table
from backend.models.category_defs import STARTER_GROUPS
from backend.services import budgets as budgets_service

logger = logging.getLogger(__name__)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# The 50/30/20 engine's own row id (services.budget_engines.proportional.PROPORTIONAL_ENGINE) —
# a bare string, not an import: services.budget_engines.* imports FROM this module (envelopes
# is its own entity's home), so importing back would cycle. BudgetEngineStateStore is a plain
# db repository, exactly like the other stores this module already reaches into directly.
_PROPORTIONAL_ENGINE_ID = "proportional"


def _validate_color(color: str) -> str:
    """Hex-validate an envelope colour (6-digit, mirrors ``services.categories.set_category_color``)."""
    color = (color or "").strip().lower()
    if not _HEX_COLOR_RE.match(color):
        raise ValidationError("color must be a 6-digit hex string like #3b86dd")
    return color


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


envelope_store = EnvelopeStore()
category_store = CategoryStore()
groups_store = EnvelopeGroupsStore()
assignments_store = BudgetAssignmentsStore()
engine_state_store = BudgetEngineStateStore()
plan_state_store = PlanStateStore()

# Not real budgetable categories: "" is the unclassified/blank sentinel, "split" is the
# structural split-transaction anchor (which, by a pre-existing quirk, carries kind="expense"
# and so would otherwise leak into the expense-category set). RFC §4 step 1.
_EXCLUDED_UNIVERSE_KEYS = frozenset({"", "split"})


# ── Reads ────────────────────────────────────────────────────────────────────────


def all_envelopes() -> list[dict]:
    """Every envelope row, display-ordered (order, name). Archived included — callers doing
    budget-axis math should filter ``archived`` themselves (mirrors ``account.archived``)."""
    return envelope_store.list_ordered()


def env_of() -> dict[str, str]:
    """Reverse index: category key -> envelope id, for every NON-ARCHIVED envelope.

    A category belongs to AT MOST one envelope (a partition) — this builds the read side;
    uniqueness is enforced at the write choke point (P2), not here."""
    result: dict[str, str] = {}
    for envelope in envelope_store.list_ordered():
        if envelope.get("archived"):
            continue
        for cat in envelope.get("category_ids") or []:
            result[cat] = envelope["id"]
    return result


def budgeted_category_ids() -> set[str]:
    """Every category key currently mapped to a (non-archived) envelope."""
    return set(env_of().keys())


# ── Create (envelope ⊥ category — RFC envelope-category-full-split.md §Backend 1) ──
# Envelope and category creation are now fully independent: an envelope can be created with
# ZERO linked categories (a named pot with a target), and creating one never mints a category
# (nor the reverse — see the removed hook in ``services.categories.create_category``).


def _assert_unique_name(name: str, *, exclude_id: str | None = None) -> None:
    """Refuse an envelope whose name (case-insensitively, trimmed) matches another
    NON-ARCHIVED envelope's. Archived envelopes are tombstones and legitimately share a name
    with a live one (delete 'Utilities', later make a fresh 'Utilities'), so they never block.
    ``exclude_id`` skips the row being renamed/restored so a self-rename is a no-op."""
    wanted = name.strip().lower()
    for env in envelope_store.all():
        if env.get("id") == exclude_id or env.get("archived"):
            continue
        if (env.get("name") or "").strip().lower() == wanted:
            raise ConflictError(f"An envelope named '{name}' already exists")


def create_envelope(
    name: str,
    group_id: str | None = None,
    color: str | None = None,
    *,
    enforce_unique: bool = True,
) -> dict:
    """Create a standalone envelope with no linked categories. Placed LAST in ``group_id``'s
    display order — falls into "Others" when ``group_id`` is omitted or doesn't resolve to a
    real group. Raises ValidationError on a blank name or an invalid ``color`` (handoff delta
    #5 — envelope owns its display colour; omit for the default ""), or ConflictError when a
    non-archived envelope already carries this name.

    ``enforce_unique=False`` skips the name-uniqueness guard for the bot-managed commitment
    sync (``planning.sync_commitment_envelopes`` runs on every page load and must never throw —
    a commitment envelope's identity is its ``commitment.envelope_id`` FK, not its name)."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("name is required")
    if enforce_unique:
        _assert_unique_name(name)
    target_group_id = (group_id or "").strip() or None
    if target_group_id is None or groups_store.find_by_id(target_group_id) is None:
        target_group_id = groups_store.ensure_others()["id"]
    color_value = _validate_color(color) if (color or "").strip() else ""
    return envelope_store.create(
        {
            "name": name,
            "category_ids": [],
            "group_id": target_group_id,
            "order": len(envelope_store.all()),
            "archived": False,
            "color": color_value,
        }
    )


# ── First-boot seed ────────────────────────────────────────────────────────────


def _read_category_targets() -> dict:
    """The stored ``profile.category_targets`` map: {envelope_id: {type, amount, date?, ...}}.
    Mirrors ``planning.read_category_targets`` without importing ``services.planning``."""
    raw = budgets_service._read_profile_raw().get("category_targets") or {}
    return raw if isinstance(raw, dict) else {}


def _read_category_notes() -> dict:
    """The stored ``profile.category_notes`` map: {envelope_id: note_text}. Mirrors
    ``planning.read_category_notes`` without importing ``services.planning``."""
    raw = budgets_service._read_profile_raw().get("category_notes") or {}
    return raw if isinstance(raw, dict) else {}


def _budgeted_category_universe() -> set[str]:
    """The categories an envelope can be minted for at migration time — mirrors
    ``planning._spending_categories()`` (expense set ∪ assignment keys ∪ target keys),
    excluding the blank/split structural keys (RFC §4 step 1)."""
    cats = set(budgets_service.expense_categories(budgets_service.read_budgets()))
    for month in assignments_store.months():
        cats.update(assignments_store.month_map(month).keys())
    cats.update(_read_category_targets().keys())
    return {c for c in cats if c and c not in _EXCLUDED_UNIVERSE_KEYS}


def _category_label(cat: str) -> str:
    """Human label for a category key — CategoryStore-only (the single source of truth since
    the categories-as-data re-arch). Deliberately STOPS there — no title-casing an unrecognized
    key, unlike ``planning``'s last-resort display fallback: this function's result is
    PERSISTED as an auto-minted envelope's name, so a genuinely unknown custom key is left
    verbatim rather than inventing a cosmetic label."""
    row = category_store.get(cat)
    if row is not None and (row.get("label") or "").strip():
        return str(row["label"])
    return cat


def ensure_envelopes_migrated() -> dict:
    """Idempotent first-boot seed: mint one 1:1 envelope per currently-budgeted category
    (the starter template's group + order) so a fresh install's budget is immediately usable —
    a brand-new category created via ``create_category`` after this never gets an auto-minted
    envelope (envelope⊥category), but a book that already has expense-category activity/
    targets before any envelope exists needs SOME envelope to render.

    A no-op once ``envelope_store`` is non-empty — this never re-enters on a live/migrated
    book (see docs/adr/2026-07-05-envelope-id-native.md: money is envelope-id-native from the
    write path up, so there is no category-keyed data to re-key here — only a first boot's
    complete absence of envelopes to seed)."""
    if envelope_store.all():
        return {"migrated": False, "reason": "already migrated"}

    universe = _budgeted_category_universe()
    if not universe:
        return {"migrated": False, "reason": "nothing to migrate"}

    group_id_by_name = {g["name"]: g["id"] for g in groups_store.list_ordered()}
    others = groups_store.ensure_others()

    envelopes: list[dict] = []
    minted: set[str] = set()
    order = 0

    def _mint(cat: str, group_id: str) -> None:
        nonlocal order
        envelopes.append(
            {
                "id": new_id("env"),
                "name": _category_label(cat),
                "category_ids": [cat],
                "group_id": group_id,
                "order": order,
                "archived": False,
            }
        )
        order += 1
        minted.add(cat)

    # The starter template's own placement (group order, then category order within it) ...
    for group_name, category_ids in STARTER_GROUPS:
        for cat in category_ids:
            if cat in universe and cat not in minted:
                _mint(cat, group_id_by_name.get(group_name, others["id"]))

    # ... anything else budgeted falls into Others, deterministic order.
    for cat in sorted(universe - minted):
        _mint(cat, others["id"])

    envelope_store.bulk_insert(envelopes)
    logger.info("envelope seed: minted %d envelope(s) for a fresh book", len(envelopes))
    return {"migrated": True, "envelope_count": len(envelopes)}


# ── Category-lifecycle maintenance ─────────────────────────────────────────────────
# services.categories calls these so the envelope mapping stays TRUE through a category
# rename/merge/delete.


def _move_assigned(from_key: str, to_key: str, *, conn: Connection | None = None) -> int:
    """Fold every month's assigned amount under ``from_key`` into ``to_key``. Returns the
    number of months touched. Money-conserving (sum per month unchanged).

    ``conn`` composes both writes into a caller's own open transaction (choke composition,
    plan 4a task 5) — omitting it (``None``, the default) is byte-identical to before: each
    ``set_assigned`` call opens its own lock-held transaction."""
    months = 0
    for month in assignments_store.months():
        amt = assignments_store.assigned(month, from_key)
        if not amt:
            continue
        dst = assignments_store.assigned(month, to_key)
        assignments_store.set_assigned(month, from_key, 0.0, conn=conn)
        assignments_store.set_assigned(month, to_key, round(dst + amt, 2), conn=conn)
        months += 1
    return months


def _rekey_row_note(
    old_row_key: str, new_row_key: str | None, *, conn: Connection | None = None
) -> None:
    """Move the note at ``old_row_key`` (an envelope id) to ``new_row_key`` — or DROP it when
    ``new_row_key`` is None (the row is dying: a merge source, a delete). Notes are keyed by
    envelope id, which never changes across an envelope's lifetime — so every SURVIVING-row
    call site is already a no-op (``old_row_key == new_row_key``); the only real callers are
    the DEATH paths (merge source dies, delete, last-category-deleted dies). ``conn`` composes
    into a caller's open transaction — omitting it opens its own, lock-free read + own-locked
    write (byte-identical to before)."""
    if old_row_key == new_row_key:
        return
    notes = _read_category_notes()
    if old_row_key not in notes:
        return
    note = notes.pop(old_row_key)
    if new_row_key:
        notes[new_row_key] = note
    budgets_service._write_profile_key("category_notes", notes, conn=conn)


def _pop_proportional_cap(envelope_id: str, *, conn: Connection) -> list | None:
    """Remove and return ``envelope_id``'s 50/30/20 cap history (``[[month, amount], ...]``),
    if any, from the proportional engine's own state row — composed into the caller's OPEN
    transaction. Returns None when the engine has never been configured, or carries no cap for
    this envelope."""
    state = engine_state_store.get(_PROPORTIONAL_ENGINE_ID)
    caps = state.get("caps") if isinstance(state, dict) else None
    if not isinstance(caps, dict) or envelope_id not in caps:
        return None
    cap = caps[envelope_id]
    remaining = {k: v for k, v in caps.items() if k != envelope_id}
    engine_state_store.set(_PROPORTIONAL_ENGINE_ID, {**state, "caps": remaining}, conn=conn)
    return cap


def on_income_category_folded(category_id: str, into: str | None = None) -> bool:
    """Keep the 50/30/20 engine's counted income categories true through a category delete or
    merge: ``category_id`` is replaced by ``into`` (where its transactions went) or, with no
    target, dropped. Left alone it named a dead id, so income re-keyed onto the target stopped
    counting as income (2026-10-13 audit). Order is kept and duplicates folded; ``None`` (count
    every income category) is never touched, and an emptied list stays empty — what it meant.
    Returns whether the state changed."""
    state = engine_state_store.get(_PROPORTIONAL_ENGINE_ID)
    ids = state.get("income_category_ids") if isinstance(state, dict) else None
    if not isinstance(ids, list) or category_id not in ids:
        return False
    folded: list[str] = []
    for cid in ids:
        kept = into if cid == category_id else cid
        if kept and kept not in folded:
            folded.append(kept)
    with locked_unit_of_work(budget_engine_state_table) as conn:
        engine_state_store.set(
            _PROPORTIONAL_ENGINE_ID, {**state, "income_category_ids": folded}, conn=conn
        )
    return True


def _restore_proportional_cap(envelope_id: str, cap: list | None, *, conn: Connection) -> None:
    """The undo twin of ``_pop_proportional_cap`` — puts ``cap`` back under ``envelope_id``.
    A no-op when there was nothing to restore, or the engine row itself no longer exists (it
    cannot: engine rows are never deleted — but a defensive read beats a KeyError on replay)."""
    if cap is None:
        return
    state = engine_state_store.get(_PROPORTIONAL_ENGINE_ID)
    if not isinstance(state, dict):
        return
    caps = {**(state.get("caps") or {}), envelope_id: cap}
    engine_state_store.set(_PROPORTIONAL_ENGINE_ID, {**state, "caps": caps}, conn=conn)


def _hard_delete_envelope(envelope_id: str, *, conn: Connection) -> dict:
    """Hard-delete ``envelope_id``'s row and every trace of its OWN config: target, note, and
    50/30/20 cap (assignments are each call site's own job — the amounts + which months differ
    per caller, and some already hold them for a batched write elsewhere). Composed into the
    caller's OPEN transaction — never called standalone. Returns ``{target, note, cap}``, the
    popped values, for a caller building an undo snapshot (None for whichever this envelope
    never had)."""
    targets = _read_category_targets()
    target = targets.pop(envelope_id, None)
    if target is not None:
        budgets_service._write_profile_key("category_targets", targets, conn=conn)
    notes = _read_category_notes()
    note = notes.pop(envelope_id, None)
    if note is not None:
        budgets_service._write_profile_key("category_notes", notes, conn=conn)
    cap = _pop_proportional_cap(envelope_id, conn=conn)
    envelope_store.delete(envelope_id, conn=conn)
    return {"target": target, "note": note, "cap": cap}


def on_category_relabeled(key: str, old_label: str, new_label: str) -> bool:
    """Keep a same-named 1:1 envelope's name following its category's LABEL-ONLY rename
    (owner catch, 2026-07-04: renaming 'TV, phone and internet' → 'Phone' in the manager left
    the envelope behind, so the row suddenly grew a '+ Phone' chip). Fires only when the
    mapping is 1:1 AND the envelope name matched the old label exactly — an independently
    named envelope (the user renamed it on purpose) is never touched."""
    env_id = env_of().get(key)
    if not env_id:
        return False
    envelope = envelope_store.find_by_id(env_id)
    if envelope is None or envelope.get("archived"):
        return False
    if list(envelope.get("category_ids") or []) != [key]:
        return False
    if (envelope.get("name") or "") != (old_label or ""):
        return False
    envelope_store.update_envelope(env_id, {"name": new_label})
    logger.info("envelope name follows label-only category rename: %s → %s", old_label, new_label)
    return True


def on_category_renamed(old_key: str, new_key: str, merged: bool) -> dict:
    """Keep the envelope mapping true through ``rename_category``. Money is envelope-id native
    (docs/adr/2026-07-05-envelope-id-native.md) — a category rename never moves money by
    itself, it only ever swaps a member key or (on a true merge) folds one envelope's REAL
    assigned money into another.

    Three cases, discriminated by which side(s) already resolve to an envelope (``env_of``):
      * old unmapped (whether or not new is mapped) → nothing to do: old_key was never a
        budget row, so no envelope's membership needs to change.
      * plain rename, or a merge into a still-unmapped key → the envelope holding old_key
        swaps the member key in place (its own id, assigned money, and target are all
        untouched). A 1:1 envelope still carrying the category's auto-minted name gets the
        new label.
      * true merge of two mapped categories → env_old folds into env_new. If old_key is the
        SOLE member of env_old, the whole envelope's assigned money moves and it archives
        (its own target/note die with it, mirroring merge_envelope/delete_envelope — a later
        restore must come back empty, never resurrecting a phantom target/note). If env_old
        has OTHER members, only old_key detaches — split semantics, no money moves, the other
        members keep env_old and its balance."""
    idx = env_of()
    e_old_id, e_new_id = idx.get(old_key), idx.get(new_key)
    if e_old_id is None:
        return {"envelopes": 0, "months_moved": 0}

    if not merged or e_new_id is None or e_old_id == e_new_id:
        # Plain rename (or merge into an unmapped key): swap the key on the old envelope.
        # The envelope's own id — and therefore its assigned money/target/note — is
        # untouched; category_ids is the only thing that changes.
        env = envelope_store.find_by_id(e_old_id)
        old_cats = list(env.get("category_ids") or [])
        keys = [new_key if k == old_key else k for k in old_cats]
        patch = {"category_ids": keys}
        if len(keys) == 1:
            # 1:1 envelopes carry the auto-minted category label — follow the rename.
            patch["name"] = _category_label(new_key)
        envelope_store.update_envelope(e_old_id, patch)
        return {"envelopes": 1, "months_moved": 0}

    # True merge of two mapped categories: fold env_old into env_new.
    env_old = envelope_store.find_by_id(e_old_id)
    old_members = list(env_old.get("category_ids") or [])
    other_members = [k for k in old_members if k != old_key]

    if other_members:
        # old_key is one of SEVERAL members: split semantics — detach old_key only. env_old
        # keeps its own id (and note/target/assigned money), so nothing else moves.
        envelope_store.update_envelope(e_old_id, {"category_ids": other_members})
        return {"envelopes": 1, "months_moved": 0}

    # old_key is env_old's sole member: fold the whole envelope into the target, then it stops
    # existing. Its OWN target/note/cap die with it — the target's own govern — exactly like
    # merge_envelope/delete_envelope: leaving them would let a stale target/cap sit orphaned
    # under a dead id forever.
    #
    # Sweep finding (plan 4a task 5 — not in the brief's binding list): the sole-member-merge
    # branch moves money AND hard-deletes the now-empty envelope — composed the same way as
    # merge_envelope/delete_envelope.
    with locked_unit_of_work(
        budget_assignments_table, budget_engine_state_table, envelopes_table, profile_table
    ) as conn:
        months_moved = _move_assigned(e_old_id, e_new_id, conn=conn)
        _hard_delete_envelope(e_old_id, conn=conn)
    return {"envelopes": 2, "months_moved": months_moved}


def on_category_deleted(key: str) -> dict:
    """Keep the envelope mapping true through ``delete_category``: detach the key; a
    now-empty envelope is hard-deleted and its env-keyed assigned amounts are zeroed across
    months (deleting the last category of an envelope releases its money back to RTA,
    matching the category-keyed scrub semantics). Its OWN target/note/cap are cleared too
    (BE-PHANTOM-TARGET-ON-RESTORE, 2026-07-04) — exactly like ``delete_envelope``/
    ``merge_envelope`` — so nothing is left orphaned under a dead id.

    Returns ``{envelopes, months_zeroed, zeroed}``. ``zeroed`` is the ``{month: amount}`` this
    call actually released, reported rather than merely counted so a caller with somewhere
    better to put it does not have to re-derive what was there after the fact — the money is
    already gone by then. ``delete_category`` uses it to carry the CURRENT month's figure onto
    the reassign target's envelope instead of leaving it released to Ready to Assign. It is
    empty whenever nothing moved: an unlinked category, or an envelope that SURVIVES because
    other categories still reference it (that pot was never emptied, so there is nothing to
    carry anywhere)."""
    env_id = env_of().get(key)
    if env_id is None:
        return {"envelopes": 0, "months_zeroed": 0, "zeroed": {}}
    env = envelope_store.find_by_id(env_id)
    remaining = [k for k in (env.get("category_ids") or []) if k != key]
    zeroed: dict[str, float] = {}
    if remaining:
        envelope_store.update_envelope(env_id, {"category_ids": remaining})
    else:
        # Sweep finding (plan 4a task 5 — not in the brief's binding list): this branch zeroes
        # assigned money across every month AND hard-deletes the now-empty envelope — composed
        # the same way as delete_envelope/force_delete_*_envelope.
        with locked_unit_of_work(
            budget_assignments_table, budget_engine_state_table, envelopes_table, profile_table
        ) as conn:
            for month in assignments_store.months():
                amount = assignments_store.assigned(month, env_id)
                if amount:
                    zeroed[month] = amount
                    assignments_store.set_assigned(month, env_id, 0.0, conn=conn)
            _hard_delete_envelope(env_id, conn=conn)
    return {"envelopes": 1, "months_zeroed": len(zeroed), "zeroed": zeroed}


# ── Envelope-native lifecycle (envelope≠category P4 — merge/split/rename) ─────────
# These back the THREE new apply_budget_change ops (envelope_merge/envelope_split_category/
# envelope_rename, RFC P4 §4.2), reached only from an N>1 (or merge-creating) row — a 1:1
# row's Rename/Merge-into affordances still key on the category, unchanged.


def is_commitments_synced(envelope: dict) -> bool:
    """True when ``envelope`` is the live budget row for some commitment — i.e. some commitment
    record's ``envelope_id`` points at it (``planning.sync_commitment_envelopes`` is the one
    writer of that FK; category-opaque-ids migration — replaces the old ``commitment_*``
    category-key-prefix check now that a synced commitment mints NO category at all, just a
    zero-category envelope). Checked via the FK rather than the envelope's ``group_id`` so it
    holds regardless of how group placement evolves. A scan over commitments is fine (few
    commitments exist per book). Public (not a leading-underscore helper) —
    ``planning._op_move_category`` also needs this guard (EXTRA A, 2026-07-04 owner incident: a
    Lucius reorg moved commitment envelopes out of the pinned Commitments group because
    move_category was the one write path that didn't check)."""
    from backend.db.commitments_store import CommitmentsStore

    env_id = envelope.get("id")
    if not env_id:
        return False
    return any(c.get("envelope_id") == env_id for c in CommitmentsStore().all())


def force_delete_commitment_envelope(envelope_id: str) -> dict:
    """Retire a Commitments-synced envelope whose commitment dropped out (paid off / cancelled
    / no unpaid installments) — called ONLY by ``planning.sync_commitment_envelopes``, which
    already knows this envelope's owning commitment is gone and has cleared its FK. The one
    deliberate bypass of ``delete_envelope``'s ``is_commitments_synced`` guard: that guard
    exists to block a USER/Lucius manual delete of a bot-managed row; the sync job IS the bot.

    Self-contained (like ``force_delete_card_payment_envelope`` — the two used to differ here,
    when this one relied on its caller to batch-manage ``category_targets`` across many
    commitments in one sync pass; now that hard-delete needs the SAME target/note/cap sweep
    every other death path uses, there is no separate batched path worth keeping). Same
    zero-and-hard-delete body as ``delete_envelope`` minus the guard and the linked-category
    detach (a commitment envelope carries none). A no-op on an unknown/already-archived
    (grandfathered pre-hard-delete) envelope. Returns ``{ok, zeroed_assignments}``."""
    envelope = envelope_store.find_by_id(envelope_id)
    if envelope is None or envelope.get("archived"):
        return {"ok": True, "zeroed_assignments": {}}
    zeroed: dict[str, float] = {}
    with locked_unit_of_work(
        budget_assignments_table, budget_engine_state_table, envelopes_table, profile_table
    ) as conn:
        for month in assignments_store.months():
            amount = assignments_store.assigned(month, envelope_id)
            if amount:
                zeroed[month] = amount
                assignments_store.set_assigned(month, envelope_id, 0.0, conn=conn)
        _hard_delete_envelope(envelope_id, conn=conn)
    return {"ok": True, "zeroed_assignments": zeroed}


def bot_managed_envelope_ids() -> set[str]:
    """Envelope ids whose funding is driven by the app, not chosen by the user: a commitment's
    own envelope (``sync_commitment_envelopes``) and an on-budget credit card's payment
    envelope (``sync_card_payment_envelopes``).

    Neither is a candidate for a hand-set monthly target. A card payment envelope is funded by
    what you already charged; a commitment envelope by its own funding plan. Both were once
    offered for one — which is how a real book ended up proposing S$10k/month of them, DCA
    commitments and four credit cards included (owner, 2026-08-23).

    One scan each over commitments and accounts, both small per book — cheaper than asking
    ``is_commitments_synced``/``is_card_payment_synced`` per envelope, which re-read the store
    every call.
    """
    from backend.db.account_store import AccountStore
    from backend.db.commitments_store import CommitmentsStore

    managed = {c.get("envelope_id") for c in CommitmentsStore().all() if c.get("envelope_id")}
    managed |= {
        a.get("payment_envelope_id") for a in AccountStore().all() if a.get("payment_envelope_id")
    }
    return {m for m in managed if m}


def is_card_payment_synced(envelope: dict) -> bool:
    """True when ``envelope`` is the live payment envelope for some on-budget credit card —
    i.e. some account's ``payment_envelope_id`` FK points at it
    (``planning.sync_card_payment_envelopes`` is the one writer of that FK; ADR
    2026-07-09-ynab-engine-credit-card-payment-envelopes.md — mirrors ``is_commitments_synced``).
    Checked via the FK so it holds regardless of group placement. A scan over accounts is fine
    (few accounts exist per book)."""
    from backend.db.account_store import AccountStore

    env_id = envelope.get("id")
    if not env_id:
        return False
    return any(a.get("payment_envelope_id") == env_id for a in AccountStore().all())


def force_delete_card_payment_envelope(envelope_id: str) -> dict:
    """Retire a card-payment-synced envelope whose credit card dropped out (archived / off-
    budget / deleted) — called ONLY by ``planning.sync_card_payment_envelopes``, which already
    knows this envelope's owning card no longer qualifies. The one deliberate bypass of
    ``delete_envelope``'s ``is_card_payment_synced`` guard: that guard blocks a USER/Lucius
    manual delete of a bot-managed row; the sync job IS the bot.

    UNLIKE a Commitments envelope (which carries little beyond its own monthly target), a
    payment envelope can hold REAL budgeted money — the auto-move fold credits it the full
    amount of every budgeted card spend (v1 is uncapped) — so zeroing every month's assigned
    amount here is the actual release of that reserve back to Ready-to-Assign, not a formality.
    Self-contained (unlike ``force_delete_commitment_envelope``, which relies on its caller to
    batch-manage ``category_targets`` across many commitments in one sync pass): a payment
    envelope never carries an auto-target, so any target here can only be one the user set by
    hand — cleared here directly, mirroring ``delete_envelope``. A no-op on an
    unknown/already-archived envelope.

    Returns ``{ok, envelope_id, name, zeroed_assignments}``. ``zeroed_assignments``
    (``{month: amount}``) is the SAME disclosure ``delete_envelope`` returns for the manual
    path, and it exists for the same reason: this is destructive, unrecoverable in place, and
    invisible to the caller otherwise. ``sync_card_payment_envelopes`` forwards it up to the
    account PATCH choke — a retype away from ``credit`` now retires the card's payment envelope
    IMMEDIATELY (it used to wait for the next boot), so a user who mis-clicks a type change is
    owed the number rather than left to discover it."""
    envelope = envelope_store.find_by_id(envelope_id)
    if envelope is None or envelope.get("archived"):
        return {"ok": True, "envelope_id": envelope_id, "name": "", "zeroed_assignments": {}}
    zeroed: dict[str, float] = {}
    with locked_unit_of_work(
        budget_assignments_table, budget_engine_state_table, envelopes_table, profile_table
    ) as conn:
        for month in assignments_store.months():
            amount = assignments_store.assigned(month, envelope_id)
            if amount:
                zeroed[month] = amount
                assignments_store.set_assigned(month, envelope_id, 0.0, conn=conn)
        _hard_delete_envelope(envelope_id, conn=conn)
    return {
        "ok": True,
        "envelope_id": envelope_id,
        "name": envelope.get("name") or "",
        "zeroed_assignments": zeroed,
    }


def merge_envelope(source_id: str, target_id: str) -> dict:
    """Fold ``source_id`` into ``target_id`` (RFC P4 §4.2): source's categories join target,
    every month's assigned amount (past AND future) moves too via ``_move_assigned`` — the
    same money-conserving primitive the category-merge path already uses — and source is then
    HARD-DELETED, its own target/note/cap cleared with it (owner decision 2026-09-26: envelopes
    are deleted for real, never archived — ADR `2026-09-26-deleting-an-envelope-deletes-it`).
    Target's note/target/cap are untouched. Returns
    ``{ok, target_envelope_id, categories_moved, months_moved}``."""
    source_id = (source_id or "").strip()
    target_id = (target_id or "").strip()
    if not source_id or not target_id:
        raise ValidationError("source_envelope_id and target_envelope_id are required")
    if source_id == target_id:
        raise ValidationError("an envelope cannot be merged into itself")
    source = envelope_store.find_by_id(source_id)
    target = envelope_store.find_by_id(target_id)
    if source is None:
        raise NotFoundError(f"envelope {source_id} not found")
    if target is None:
        raise NotFoundError(f"envelope {target_id} not found")
    if source.get("archived") or target.get("archived"):
        raise ValidationError("cannot merge an archived envelope")
    if is_commitments_synced(source) or is_commitments_synced(target):
        raise ValidationError("Commitments envelopes cannot be merged")
    if is_card_payment_synced(source) or is_card_payment_synced(target):
        raise ValidationError("Credit Card Payment envelopes cannot be merged")

    source_cats = list(source.get("category_ids") or [])
    existing = list(target.get("category_ids") or [])
    merged_keys = existing + [c for c in source_cats if c not in existing]
    # Money move, target patch and source's hard-delete (+ its own target/note/cap sweep) all
    # commit in ONE transaction (plan 4a task 5 — `locked_unit_of_work` composes the money move
    # AND the profile-key writes `_hard_delete_envelope` makes into the SAME unit as the
    # envelope writes, so there is no window where the money has moved but the source row — or
    # its target/note/cap — hasn't caught up, or vice versa). If the process dies mid-
    # transaction, NOTHING committed — a retry re-validates (source still exists) and runs the
    # whole merge fresh, exactly like a first attempt; there is no partial state to reconcile.
    # The target's own note/target/cap are UNTOUCHED — the merged row keeps showing whatever
    # the target already had, per this function's docstring.
    with locked_unit_of_work(
        budget_assignments_table, budget_engine_state_table, envelopes_table, profile_table
    ) as conn:
        months_moved = _move_assigned(source_id, target_id, conn=conn)
        envelope_store.update_envelope(target_id, {"category_ids": merged_keys}, conn=conn)
        _hard_delete_envelope(source_id, conn=conn)
    logger.info(
        "envelope_merge %s → %s: %d categories, %d month(s) moved",
        source_id,
        target_id,
        len(source_cats),
        months_moved,
    )
    return {
        "ok": True,
        "target_envelope_id": target_id,
        "categories_moved": len(source_cats),
        "months_moved": months_moved,
    }


def split_category(envelope_id: str, category_id: str) -> dict:
    """Unlink ``category_id`` from ``envelope_id`` (budget-envelopes handoff port, delta #1 —
    supersedes the old "split into a new 1:1 envelope" behavior). The category becomes
    ENVELOPE-LESS — it surfaces in the derived Unbudgeted group until relinked via
    ``add_category`` — and NO envelope is minted. Legal even when ``category_id`` is the
    envelope's SOLE member (1:1 rows get "Remove from envelope" too): the envelope keeps its
    own id (and therefore its note/target/assigned money) — its (possibly now zero-category)
    pool is completely untouched, NO money moves; the detached category's own activity simply
    stops folding into this row's Available. Returns ``{ok, category_id, envelope_id}``."""
    envelope_id = (envelope_id or "").strip()
    category_id = (category_id or "").strip()
    if not envelope_id or not category_id:
        raise ValidationError("envelope_id and category_id are required")
    envelope = envelope_store.find_by_id(envelope_id)
    if envelope is None:
        raise NotFoundError(f"envelope {envelope_id} not found")
    if envelope.get("archived"):
        raise ValidationError("cannot split an archived envelope")
    if is_commitments_synced(envelope):
        raise ValidationError("Commitments envelopes cannot be split")
    if is_card_payment_synced(envelope):
        raise ValidationError("Credit Card Payment envelopes cannot be split")
    members = list(envelope.get("category_ids") or [])
    if category_id not in members:
        raise ValidationError(f"'{category_id}' is not a member of envelope {envelope_id}")

    remaining = [c for c in members if c != category_id]
    # NOT composed via locked_unit_of_work (plan 4a task 5 binding is "genuinely write ≥2
    # wave tables"): this docstring's own "NO money moves" is load-bearing — split never
    # touches budget_assignments, so there is only ONE wave-table write here and nothing to
    # compose it with. Listed alongside merge/delete/force-delete in the task brief's binding
    # description, but the current code (and its own contract) is single-table; left
    # unwrapped rather than taking an unneeded budget_assignments lock for no atomicity gain.
    envelope_store.update_envelope(envelope_id, {"category_ids": remaining})
    logger.info(
        "envelope_split_category (unlink) %s: %s detached — envelope-less, no envelope minted",
        envelope_id,
        category_id,
    )
    return {"ok": True, "category_id": category_id, "envelope_id": envelope_id}


def rename_envelope(envelope_id: str, name: str | None = None, color: str | None = None) -> dict:
    """Rename an envelope and/or set its display colour (handoff delta #5 widens this from
    name-only to name?/color? — at least one is required). ``name=None``/``color=None`` means
    "leave unchanged"; an explicitly blank ``name`` still raises (a name can't be cleared).
    ``color`` is hex-validated (6-digit, like ``services.categories.set_category_color``).
    Returns ``{ok, envelope}``."""
    envelope_id = (envelope_id or "").strip()
    if not envelope_id:
        raise ValidationError("envelope_id is required")
    if envelope_store.find_by_id(envelope_id) is None:
        raise NotFoundError(f"envelope {envelope_id} not found")
    patch: dict = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationError("name is required")
        _assert_unique_name(name, exclude_id=envelope_id)
        patch["name"] = name
    if color is not None:
        patch["color"] = _validate_color(color)
    if not patch:
        raise ValidationError("name or color is required")
    updated = envelope_store.update_envelope(envelope_id, patch)
    return {"ok": True, "envelope": updated}


def add_category(envelope_id: str, category_id: str) -> dict:
    """Link an UNLINKED category (one currently mapped to NO envelope) to ``envelope_id``
    (contract addendum, RFC full-split — not composable from split+merge, since an unlinked
    category has no source envelope to split FROM). No money moves; a legacy category-keyed
    assignment remnant for ``category_id``, if any, folds into the envelope's own slot (same
    rule the legacy resync uses). Raises NotFoundError (unknown envelope/category),
    ValidationError (archived envelope, or a BOT-MANAGED one), or ConflictError (the category
    already belongs to SOME envelope — use merge/split instead). Returns
    ``{ok, envelope_id, category_id}``.

    A bot-managed envelope is refused for the same reason it cannot be merged, split or
    deleted: its funding is the app's to compute (a card's payment envelope from what you
    already charged, a commitment's from its funding plan), so it may not acquire a category
    whose spending — or whose money, on a ``categories.delete_category`` carry — would land
    inside that computed pot and corrupt the card's coverage math. Nothing enforced this
    before; ``_carry_assigned_to_target`` merely ASSUMED such a row could never have a
    category, and a security review walked a real carry straight through the gap."""
    envelope_id = (envelope_id or "").strip()
    category_id = (category_id or "").strip()
    if not envelope_id or not category_id:
        raise ValidationError("envelope_id and category_id are required")
    envelope = envelope_store.find_by_id(envelope_id)
    if envelope is None:
        raise NotFoundError(f"envelope {envelope_id} not found")
    if envelope.get("archived"):
        raise ValidationError("cannot add a category to an archived envelope")
    if is_commitments_synced(envelope):
        raise ValidationError("Commitments envelopes cannot be given a category")
    if is_card_payment_synced(envelope):
        raise ValidationError("Credit Card Payment envelopes cannot be given a category")
    if category_store.get(category_id) is None:
        raise NotFoundError(f"category '{category_id}' not found")
    existing_env = env_of().get(category_id)
    if existing_env is not None:
        raise ConflictError(
            f"'{category_id}' already belongs to envelope {existing_env} — "
            "use envelope_merge/envelope_split_category instead"
        )
    # Sweep finding (plan 4a task 5 — not in the brief's binding list): folding a legacy
    # category-keyed assignment remnant into the envelope's own slot, THEN patching
    # category_ids, are two separate envelopes+budget_assignments writes today — composed
    # the same way as merge/delete/force-delete.
    members = list(envelope.get("category_ids") or [])
    with locked_unit_of_work(budget_assignments_table, envelopes_table) as conn:
        months_moved = _move_assigned(category_id, envelope_id, conn=conn)
        updated = envelope_store.update_envelope(
            envelope_id, {"category_ids": members + [category_id]}, conn=conn
        )
    logger.info(
        "envelope_add_category %s += %s (%d month(s) of legacy remnant folded)",
        envelope_id,
        category_id,
        months_moved,
    )
    return {
        "ok": True,
        "envelope_id": envelope_id,
        "category_id": category_id,
        "envelope": updated,
    }


# ── Delete + its server-held Undo (ADR 2026-09-26-deleting-an-envelope-deletes-it) ────────
# "Delete envelope" is a THIRD removal verb, distinct from unlink (`split_category`, which
# leaves the envelope alive) and delete_category (which destroys the category, never the
# envelope): it detaches every member (→ Unbudgeted, never deleted) and HARD-DELETES the
# envelope itself — no more archive-and-restore (owner, 2026-09-26: "we should stop archiving
# envelope, just delete"). A caller who deletes by mistake gets ONE server-held Undo, the same
# pattern `planning.fresh_start`/`undo_fresh_start` use: `delete_envelope` snapshots the row
# (+ its target/note/cap/assignments) inside the SAME transaction that deletes it, stores the
# snapshot under a one-shot token (`PlanStateStore.set_envelope_delete_undo`), and hands back
# only the token — never the snapshot itself, for the identical reason fresh start's token is
# server-held: a client-supplied before-image would be an unauthenticated bulk write wearing an
# "undo" label. `undo_delete_envelope(token)` replays it exactly and consumes the token; a
# stale/unknown token is a `ConflictError` (409) that writes nothing. Past the undo window (or
# for a Lucius write), the recovery path is the generic one every other destructive write
# already has: `restore_checkpoint`. Refuses an already-archived (grandfathered pre-hard-delete)
# or Commitments/card-payment-synced envelope. Removing the row from `_budget_rows()` (ADR
# 2026-09-09's restored `_ready_to_assign`,
# which sums SIGNED `_available` over every non-archived envelope) releases a POSITIVE balance
# (unspent, held money) to Ready to Assign. A NEGATIVE balance (an unresolved overspend) is NOT
# forgiven and is NOT charged now either: the detached members become UNLINKED categories, and
# since ADR 2026-09-10 an unlinked category's CASH spend holds its own hole under the same
# clamped carry the envelope was using (`held_outside_envelopes`). The hold simply changes hands
# — it does not die with the envelope — so Ready to Assign does not drop, and the charge still
# waits for the month-end roll. Verified: assign 100/spend 300 (available -200,
# held_in_envelopes -200, RTA 900 with cash 700) → delete → held_in_envelopes 0,
# held_outside_envelopes -300, RTA 1000 (the zeroed 100 assignment comes back; the hole stays
# held), then July RTA 700 as the roll charges it exactly once. See
# `test_delete_envelope_releases_reserve_to_rta` for the mirror (positive-balance) case, and
# tests/test_delete_envelope_never_lowers_rta.py for the direction as a PROPERTY over random
# books rather than two worked examples.
#
# The move is `available(m, E) − Σ_c min(0, cash_activity(m, c))`, i.e.
# `max(0, prev_carry) + assigned + Σ_c max(0, cash_c) + Σ_c non_cash_c`. Corrections to what
# this comment used to claim:
#   * (review, 2026-09-10) it is NOT `max(0, cash_activity)` over the envelope's summed activity.
#     A MERGED envelope's members hold SEPARATELY once detached (deliberately —
#     `_unbudgeted_available_map` clamps per category so a refunded category cannot cancel
#     another's still-open hole), so spend 100 on one member + refund 100 on another moves RTA
#     by +100 where the summed form says 0.
#   * (fixed 2026-09-28, ADR 2026-09-28-iterative-available-walk-and-uniform-card-coverage)
#     `Σ_c non_cash_c` USED TO go negative when a `credit`-account leg drained a LINKED envelope
#     with nothing to offset it (an archived card's spend, a card-funded categorized transfer or
#     investment buy — all escaping the payment-envelope fold). Every such outflow is now a
#     card-spend candidate whether or not a live payment envelope resolves for it, so
#     `_CARD_UNCOVERED_KEY` cancels the drain the same way a live card's does — `non_cash_c` is 0
#     for it, unconditionally. "Can only ever RAISE Ready to Assign or leave it flat" is now an
#     absolute, with no excluded shape, and the property test asserts exactly that.


def assert_deletable(envelope: dict) -> None:
    """Raise unless ``envelope`` is one ``delete_envelope`` would actually delete — shared with
    ``planning.envelope_delete_preview``/``_op_envelope_delete`` so the confirm dialog and the
    realized figure are never computed for a row the write would refuse anyway."""
    if envelope.get("archived"):
        raise ValidationError("cannot delete an already-archived envelope")
    if is_commitments_synced(envelope):
        raise ValidationError("Commitments envelopes cannot be deleted")
    if is_card_payment_synced(envelope):
        raise ValidationError("Credit Card Payment envelopes cannot be deleted")


def delete_envelope(envelope_id: str) -> dict:
    """Delete ``envelope_id`` FOR REAL: detach every member (they surface in Unbudgeted, never
    deleted), zero its env-keyed assigned amount across EVERY month, clear its target/note/cap,
    and hard-delete the row. Because the row drops out of ``_ready_to_assign``'s sum (ADR
    2026-09-09), a POSITIVE balance releases to Ready to Assign; a NEGATIVE balance (an
    unresolved overspend) is neither forgiven NOR charged now — its detached categories are
    unlinked, and an unlinked category's cash spend holds the same hole in
    ``held_outside_envelopes`` until the month-end roll (ADR 2026-09-10). Ready to Assign never
    drops on a delete. Refuses an already-archived (grandfathered) or Commitments/card-payment
    -synced envelope.

    Before deleting anything, snapshots the row (id/name/color/group_id/order/category_ids) +
    its target/note/cap/assignments — the SAME data this function is about to erase — and
    stores it server-side under a fresh one-shot token (``PlanStateStore.
    set_envelope_delete_undo``), all inside the ONE transaction that does the deleting: a
    concurrent write landing between "read for the snapshot" and "delete" would otherwise be
    silently lost. See ``undo_delete_envelope`` for the replay half.

    Returns ``{ok, undo_token, former_category_ids, zeroed_assignments}`` — the latter two are
    ECHOED from the snapshot for a caller that wants them without a second read. The REALIZED
    Ready-to-Assign figure for a given month is a separate concern (``planning.
    envelope_delete_preview`` / ``_op_envelope_delete``'s own echo) — this function doesn't take
    a month at all, since the assignments it zeroes span every month, not one."""
    envelope_id = (envelope_id or "").strip()
    if not envelope_id:
        raise ValidationError("envelope_id is required")
    envelope = envelope_store.find_by_id(envelope_id)
    if envelope is None:
        raise NotFoundError(f"envelope {envelope_id} not found")
    assert_deletable(envelope)

    token = new_id("edu")
    with locked_unit_of_work(
        budget_assignments_table,
        budget_engine_state_table,
        envelopes_table,
        plan_state_table,
        profile_table,
    ) as conn:
        # Read INSIDE the lock — the snapshot must describe the row this transaction actually
        # deletes, not a possibly-stale pre-lock read (fresh_start's own rationale).
        envelope = envelope_store.find_by_id(envelope_id, conn=conn)
        if envelope is None:
            raise NotFoundError(f"envelope {envelope_id} not found")
        former_category_ids = list(envelope.get("category_ids") or [])

        zeroed_assignments: dict[str, float] = {}
        for month in assignments_store.months():
            amount = assignments_store.assigned(month, envelope_id)
            if amount:
                zeroed_assignments[month] = amount
                assignments_store.set_assigned(month, envelope_id, 0.0, conn=conn)

        extras = _hard_delete_envelope(envelope_id, conn=conn)

        snapshot = {
            "token": token,
            "envelope": {
                "id": envelope_id,
                "name": envelope.get("name") or "",
                "color": envelope.get("color") or "",
                "group_id": envelope.get("group_id"),
                "order": envelope.get("order", 0),
                "category_ids": former_category_ids,
            },
            "target": extras["target"],
            "note": extras["note"],
            "cap": extras["cap"],
            "assignments": zeroed_assignments,
            "created_at": _now_iso(),
        }
        plan_state_store.set_envelope_delete_undo(snapshot, conn=conn)

    logger.info(
        "delete_envelope %s: detached %d categor(y/ies), zeroed %d month(s) (undo token %s)",
        envelope_id,
        len(former_category_ids),
        len(zeroed_assignments),
        token,
    )
    return {
        "ok": True,
        "undo_token": token,
        "former_category_ids": former_category_ids,
        "zeroed_assignments": zeroed_assignments,
    }


def undo_delete_envelope(token: str) -> dict:
    """Undo the pending ``delete_envelope`` named by ``token`` — replays the SERVER-HELD
    before-image exactly (same id; target/note/cap/assignments restored) — from
    ``PlanStateStore.get_envelope_delete_undo``, then consumes the token in the same
    transaction. One-shot, mirroring ``planning.undo_fresh_start``: a second undo, or a stale
    token from another tab, raises ``ConflictError`` (409) rather than replaying stale data over
    whatever the user has done since.

    Re-links only categories STILL unlinked (one already re-linked elsewhere in the meantime —
    e.g. onto a brand-new envelope created during the undo window — is left alone), mirroring
    the retired ``restore_envelope``'s own re-link rule. The envelope reappears in its stored
    group if that group still exists, else 'Others'. Returns ``{ok, envelope, relinked}``."""
    if not isinstance(token, str) or not token.strip():
        raise ValidationError("token is required")
    with locked_unit_of_work(
        budget_assignments_table,
        budget_engine_state_table,
        envelopes_table,
        plan_state_table,
        profile_table,
    ) as conn:
        snapshot = plan_state_store.get_envelope_delete_undo()
        if not snapshot:
            raise ConflictError("There is nothing to undo — this delete was already undone")
        if snapshot.get("token") != token:
            raise ConflictError("This undo is no longer the most recent envelope delete")

        env_snapshot = snapshot.get("envelope") or {}
        envelope_id = env_snapshot.get("id")
        # One made since under the same name would leave two live ones — refused here as on
        # every create/rename path, before any write, so the token survives for a retry.
        _assert_unique_name(env_snapshot.get("name") or "", exclude_id=envelope_id)
        group_id = env_snapshot.get("group_id")
        if not group_id or groups_store.find_by_id(group_id) is None:
            group_id = groups_store.ensure_others()["id"]

        # Built out to the full row shape ``db.envelope_store._make_envelope`` guarantees a
        # fresh envelope (rather than calling that conn-less helper, which would try to
        # re-acquire the ``envelopes`` lock this transaction already holds and deadlock) — so a
        # restored row is indistinguishable from one that was never deleted.
        envelope_store.insert(
            {
                "id": envelope_id,
                "name": env_snapshot.get("name") or "",
                "color": env_snapshot.get("color") or "",
                "group_id": group_id,
                "order": env_snapshot.get("order", 0),
                "category_ids": [],
                "archived": False,
                "archive_reason": "",
                "merged_into": "",
                "former_category_ids": [],
                "archived_at": "",
            },
            conn=conn,
        )

        target = snapshot.get("target")
        if target is not None:
            targets = _read_category_targets()
            targets[envelope_id] = target
            budgets_service._write_profile_key("category_targets", targets, conn=conn)
        note = snapshot.get("note")
        if note is not None:
            notes = _read_category_notes()
            notes[envelope_id] = note
            budgets_service._write_profile_key("category_notes", notes, conn=conn)
        _restore_proportional_cap(envelope_id, snapshot.get("cap"), conn=conn)
        for month, amount in (snapshot.get("assignments") or {}).items():
            assignments_store.set_assigned(month, envelope_id, amount, conn=conn)

        former_categories = list(env_snapshot.get("category_ids") or [])
        plan_state_store.set_envelope_delete_undo(None, conn=conn)

    # Re-linking runs AFTER the transaction above commits, each through `add_category`'s OWN
    # locked_unit_of_work — that choke takes the SAME envelopes/budget_assignments locks this
    # function just released, so composing it INTO the block above would deadlock (a
    # `threading.Lock` is not reentrant). This mirrors the retired `restore_envelope`'s own
    # shape exactly: the row's re-creation is one atomic unit, and each re-link is its own.
    relinked: list[str] = []
    for cat in former_categories:
        if cat in env_of() or category_store.get(cat) is None:
            continue  # already linked elsewhere, or the category itself is gone
        add_category(envelope_id, cat)
        relinked.append(cat)

    updated = envelope_store.find_by_id(envelope_id)
    logger.info("undo_delete_envelope %s: relinked %s", envelope_id, relinked)
    return {"ok": True, "envelope": updated, "relinked": relinked}


def list_archived_envelopes() -> list[dict]:
    """Archived envelopes — a HISTORICAL, permanently-frozen list: every lifecycle path that
    used to archive (delete/merge/category-rekey/category-delete/commitment+card retirement)
    hard-deletes instead as of ADR 2026-09-26-deleting-an-envelope-deletes-it, so this can only
    ever show a row archived BEFORE that change shipped — ``restore_envelope`` that would have
    un-archived one is retired with it (Undo right after a delete is the replacement; an older
    write's recovery path is ``restore_checkpoint``). Newest-first by ``archived_at`` (rows
    archived before that metadata existed have no ``archived_at`` and sort last). Each row
    resolves its former members' live labels + its merge target's live name for display
    (handoff delta #3 — the Archived-envelopes footer)."""
    rows = [e for e in envelope_store.all() if e.get("archived")]
    rows.sort(key=lambda e: e.get("archived_at") or "", reverse=True)
    out: list[dict] = []
    for e in rows:
        merged_into = e.get("merged_into") or ""
        target = envelope_store.find_by_id(merged_into) if merged_into else None
        former_keys = list(e.get("former_category_ids") or [])
        out.append(
            {
                "id": e["id"],
                "name": e.get("name") or "",
                "color": e.get("color") or "",
                "archived_at": e.get("archived_at") or "",
                "archive_reason": e.get("archive_reason") or "",
                "merged_into": merged_into,
                "merged_into_name": target["name"] if target and not target.get("archived") else "",
                "former_category_ids": former_keys,
                "former_members": [{"key": k, "label": _category_label(k)} for k in former_keys],
            }
        )
    return out
