"""One-time, generic, idempotent data-hygiene cleanup.

Runs on ANY book at boot, so every rule below is written defensively — a book that has
never touched a given feature (no proportional engine configured, no archived envelopes,
no stale undo) is a correct, silent no-op for that rule. Nine independent rules, each
re-reading its own current state (never assuming an earlier rule already ran, though in
practice they run once, in this order, within the same transaction):

  1. Envelope payload copies: a pre-``envelope-id-native`` row (ADR
     2026-07-05-envelope-id-native.md) could still carry its own ``note``/``target`` keys —
     backfilled into ``profile.category_notes``/``category_targets`` (only when the profile
     map doesn't already have an entry for that envelope id), then stripped from the
     envelope payload either way.
  2. Slug-keyed notes/targets: a profile-map key that is not an envelope id (an even older
     scheme keyed by a slugified CATEGORY label) is re-keyed onto the one live envelope
     whose name normalizes to that slug, unless that envelope already has an entry; every
     key that still doesn't resolve to a live envelope id afterward is dropped.
  3. Archived envelope rows (ADR 2026-09-26-deleting-an-envelope-deletes-it.md: archiving
     is retired — a live delete now hard-deletes) are hard-deleted, taking their
     budget_assignments rows, profile target/note, and 50/30/20 cap with them — mirrors
     ``services.envelopes._hard_delete_envelope`` exactly, run once against every row the
     OLD archive-and-keep-forever behavior left behind.
  4. profile: drop the retired ``credit_card_mode`` flag (removed 2026-07-10).
     category_groups payloads: drop the retired ``envelope_ids`` key; prune a group's
     ``categories`` list down to ids that still exist.
  5. budget_engine_state 'proportional': drop the legacy ``mapping`` (write-only since the
     2026-09-25 envelope-based redesign); drop ``buckets`` ONLY when ``excluded_groups`` is
     already a real list (``proportional_state.normalize_own_state`` still DERIVES
     ``excluded_groups`` from ``buckets`` when absent — dropping it first would blank that
     derivation for a book that never explicitly saved ``excluded_groups``); drop the
     legacy ``targets`` ONLY when ``group_targets`` exists (``is_configured`` reads either).
  6. plan_state: drop a stale ``budget.fresh_start_undo``/``envelope_delete_undo`` (each a
     seconds-long one-shot token) older than one day.
  7. Retired/orphaned logs: ``pending_analysis`` done/failed rows older than 7 days;
     ``agent_findings`` (dead table, the retired Agent product) wiped entirely;
     ``run_history`` rows of that same retired product (``kind="agent"`` and its
     ``__streak__:agent:*`` companions); ``automation_runs`` whose ``automation_id`` no
     longer exists, taking their OWN dedicated session with them (the FK
     ``session_messages.session_id ON DELETE CASCADE`` takes the messages — the one
     intended use of that cascade; never ``batch_alter_table("sessions")``); any session
     left with zero messages.
  8. payees: a ``default_categories`` entry naming a category that no longer exists is
     dropped; a label-form entry is converted to the id when it resolves to exactly one
     live category (mirrors ``services.categories.resolve_category_ref``'s own id-or-
     unique-case-insensitive-label matching, frozen here rather than imported — a
     migration must not depend on a service module's behavior changing under it later).
  9. import_batches: deleted only when NO transaction (live or soft-deleted) carries its
     id, no statement's own ``batch_ids`` names it, and no ``merge_journal`` before-image
     references it; a still-``active`` batch whose every referencing transaction is
     soft-deleted is marked ``rolled_back`` instead of deleted (it is still real history).

Revision ID: 202609281300_data_hygiene_cleanup
Revises: 202609281200_statement_note
Create Date: 2026-09-28
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "202609281300_data_hygiene_cleanup"
down_revision = "202609281200_statement_note"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_PROPORTIONAL_ENGINE_ID = "proportional"
_PENDING_ANALYSIS_RETENTION_DAYS = 7
_STALE_UNDO_AGE_DAYS = 1


def _dict(raw, row_id: str) -> dict:
    """A payload cell as a dict — fail-closed (boot migrations never silently skip
    corruption): a row whose JSON doesn't parse, or isn't an object, refuses the whole
    migration rather than guess."""
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"payload {row_id!r} is unreadable; migration refused") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"payload {row_id!r} must be a JSON object; migration refused")
    return payload


def _list_or_default(raw, default: list) -> list:
    """A tolerant read of a JSON list cell (``statements_meta.batch_ids``) — malformed is
    exactly as informative as absent here (nothing this rule does depends on WHY a
    reference list is unusable, only on whether it names a batch), so this one reads
    defensively rather than fail-closed like ``_dict``."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, list) else default


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def _older_than(iso_str, cutoff: datetime) -> bool:
    """True when ``iso_str`` parses to before ``cutoff`` — and also for anything that
    DOESN'T parse (missing/malformed). Both undo snapshots are always stamped with
    ``created_at`` by their one write path (``planning.fresh_start`` /
    ``envelopes.delete_envelope``), so an unparseable value here is corruption, not a
    legitimate fresh entry — better swept than left permanently stuck."""
    if not iso_str:
        return True
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < cutoff


def _write_profile(conn, book_id: str, profile: dict, exists: bool) -> None:
    payload = json.dumps(profile)
    if exists:
        conn.execute(
            sa.text("UPDATE profile SET payload = :p WHERE book_id = :b"),
            {"p": payload, "b": book_id},
        )
    else:
        conn.execute(
            sa.text("INSERT INTO profile (book_id, payload) VALUES (:b, :p)"),
            {"b": book_id, "p": payload},
        )


def _read_profile(conn, book_id: str) -> tuple[dict, bool]:
    row = conn.execute(
        sa.text("SELECT payload FROM profile WHERE book_id = :b"), {"b": book_id}
    ).first()
    return (_dict(row[0], f"profile:{book_id}"), True) if row is not None else ({}, False)


def _read_engine_state(conn, book_id: str) -> dict | None:
    row = conn.execute(
        sa.text("SELECT payload FROM budget_engine_state WHERE book_id = :b AND engine_id = :e"),
        {"b": book_id, "e": _PROPORTIONAL_ENGINE_ID},
    ).first()
    return _dict(row[0], f"budget_engine_state:{book_id}") if row is not None else None


def _write_engine_state(conn, book_id: str, state: dict) -> None:
    conn.execute(
        sa.text(
            "UPDATE budget_engine_state SET payload = :p WHERE book_id = :b AND engine_id = :e"
        ),
        {"p": json.dumps(state), "b": book_id, "e": _PROPORTIONAL_ENGINE_ID},
    )


# ── Rules 1+2: envelope payload copies, then slug-keyed notes/targets ───────────────────


def _rule_envelope_notes_and_targets(conn, book_id: str, counts: dict) -> None:
    envelopes = [
        (row.id, _dict(row.payload, row.id))
        for row in conn.execute(
            sa.text("SELECT id, payload FROM envelopes WHERE book_id = :b"), {"b": book_id}
        ).fetchall()
    ]
    if not envelopes:
        return
    profile, exists = _read_profile(conn, book_id)
    notes = dict(profile.get("category_notes") or {})
    targets = dict(profile.get("category_targets") or {})
    before_notes, before_targets = dict(notes), dict(targets)

    # Rule 1 — backfill from the envelope's own payload, then always strip it there.
    for env_id, payload in envelopes:
        note = payload.get("note")
        if note and env_id not in notes:
            notes[env_id] = note
            counts["rule1_notes_copied_from_envelope"] += 1
        target = payload.get("target")
        if target and env_id not in targets:
            targets[env_id] = target
            counts["rule1_targets_copied_from_envelope"] += 1
        if "note" in payload or "target" in payload:
            payload.pop("note", None)
            payload.pop("target", None)
            conn.execute(
                sa.text("UPDATE envelopes SET payload = :p WHERE id = :id"),
                {"p": json.dumps(payload), "id": env_id},
            )
            counts["rule1_envelope_payloads_stripped"] += 1

    # Rule 2 — slug-keyed notes/targets re-keyed onto the one matching live envelope.
    live_ids = {env_id for env_id, _ in envelopes}
    slug_to_ids: dict[str, list[str]] = {}
    for env_id, payload in envelopes:
        slug = _slugify(payload.get("name") or "")
        if slug:
            slug_to_ids.setdefault(slug, []).append(env_id)

    for mapping, label in ((notes, "notes"), (targets, "targets")):
        for key in list(mapping.keys()):
            if key in live_ids:
                continue
            candidates = slug_to_ids.get(key, [])
            if len(candidates) == 1 and candidates[0] not in mapping:
                mapping[candidates[0]] = mapping.pop(key)
                counts[f"rule2_{label}_rekeyed_by_slug"] += 1
        for key in list(mapping.keys()):
            if key not in live_ids:
                del mapping[key]
                counts[f"rule2_{label}_dropped_unmatched"] += 1

    if notes != before_notes or targets != before_targets:
        profile["category_notes"] = notes
        profile["category_targets"] = targets
        _write_profile(conn, book_id, profile, exists)


# ── Rule 3: archived envelope rows (archiving is retired) ───────────────────────────────


def _rule_archived_envelopes(conn, book_id: str, counts: dict) -> None:
    archived_ids = [
        row.id
        for row in conn.execute(
            sa.text("SELECT id, payload FROM envelopes WHERE book_id = :b"), {"b": book_id}
        ).fetchall()
        if _dict(row.payload, row.id).get("archived")
    ]
    if not archived_ids:
        return

    profile, profile_exists = _read_profile(conn, book_id)
    notes = dict(profile.get("category_notes") or {})
    targets = dict(profile.get("category_targets") or {})
    engine_state = _read_engine_state(conn, book_id)
    caps = dict((engine_state or {}).get("caps") or {})
    profile_changed = False
    engine_changed = False

    for env_id in archived_ids:
        if notes.pop(env_id, None) is not None:
            profile_changed = True
        if targets.pop(env_id, None) is not None:
            profile_changed = True
        if caps.pop(env_id, None) is not None:
            engine_changed = True
        assignment_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM budget_assignments WHERE book_id = :b AND category = :c"),
            {"b": book_id, "c": env_id},
        ).scalar_one()
        conn.execute(
            sa.text("DELETE FROM budget_assignments WHERE book_id = :b AND category = :c"),
            {"b": book_id, "c": env_id},
        )
        counts["rule3_assignments_deleted"] += assignment_count
        conn.execute(sa.text("DELETE FROM envelopes WHERE id = :id"), {"id": env_id})
        counts["rule3_envelopes_deleted"] += 1

    if profile_changed:
        profile["category_notes"] = notes
        profile["category_targets"] = targets
        _write_profile(conn, book_id, profile, profile_exists)
    if engine_changed and engine_state is not None:
        engine_state["caps"] = caps
        _write_engine_state(conn, book_id, engine_state)


# ── Rule 4: profile.credit_card_mode, category_groups.envelope_ids / categories ─────────


def _rule_profile_and_category_groups(conn, book_id: str, counts: dict) -> None:
    profile, exists = _read_profile(conn, book_id)
    if exists and "credit_card_mode" in profile:
        del profile["credit_card_mode"]
        _write_profile(conn, book_id, profile, True)
        counts["rule4_credit_card_mode_dropped"] += 1

    live_category_ids = {
        row.id
        for row in conn.execute(
            sa.text("SELECT id FROM categories WHERE book_id = :b"), {"b": book_id}
        ).fetchall()
    }
    for row in conn.execute(
        sa.text("SELECT id, payload FROM category_groups WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        group = _dict(row.payload, row.id)
        changed = False
        if "envelope_ids" in group:
            del group["envelope_ids"]
            changed = True
            counts["rule4_group_envelope_ids_dropped"] += 1
        categories = group.get("categories")
        if isinstance(categories, list):
            kept = [c for c in categories if c in live_category_ids]
            if len(kept) != len(categories):
                group["categories"] = kept
                changed = True
                counts["rule4_group_dead_categories_pruned"] += len(categories) - len(kept)
        if changed:
            conn.execute(
                sa.text("UPDATE category_groups SET payload = :p WHERE id = :id"),
                {"p": json.dumps(group), "id": row.id},
            )


# ── Rule 5: budget_engine_state 'proportional' legacy keys ──────────────────────────────


def _rule_engine_state_legacy_keys(conn, book_id: str, counts: dict) -> None:
    state = _read_engine_state(conn, book_id)
    if state is None:
        return
    changed = False
    if "mapping" in state:
        del state["mapping"]
        changed = True
        counts["rule5_mapping_dropped"] += 1
    # `buckets` is still the ONLY source `proportional_state.derive_excluded_groups` has to
    # seed `excluded_groups` from when that field is absent/None — drop it ONLY once the row
    # already carries a real `excluded_groups` list of its own (the derivation has already
    # run and been saved), never before.
    if isinstance(state.get("excluded_groups"), list) and "buckets" in state:
        del state["buckets"]
        changed = True
        counts["rule5_buckets_dropped"] += 1
    if "group_targets" in state and "targets" in state:
        del state["targets"]
        changed = True
        counts["rule5_legacy_targets_dropped"] += 1
    if changed:
        _write_engine_state(conn, book_id, state)


# ── Rule 6: stale one-shot undo snapshots on plan_state ──────────────────────────────────


def _rule_plan_state_stale_undo(conn, book_id: str, counts: dict, *, now: datetime) -> None:
    row = conn.execute(
        sa.text("SELECT payload FROM plan_state WHERE book_id = :b"), {"b": book_id}
    ).first()
    if row is None:
        return
    state = _dict(row.payload, f"plan_state:{book_id}")
    budget = state.get("budget")
    if not isinstance(budget, dict):
        return
    cutoff = now - timedelta(days=_STALE_UNDO_AGE_DAYS)
    changed = False
    for key in ("fresh_start_undo", "envelope_delete_undo"):
        snapshot = budget.get(key)
        if isinstance(snapshot, dict) and _older_than(snapshot.get("created_at"), cutoff):
            del budget[key]
            changed = True
            counts[f"rule6_{key}_dropped"] += 1
    if changed:
        state["budget"] = budget
        conn.execute(
            sa.text("UPDATE plan_state SET payload = :p WHERE book_id = :b"),
            {"p": json.dumps(state), "b": book_id},
        )


# ── Rule 7: retired-product / orphaned logs (global — none of these carry book_id) ──────


def _rule_pending_analysis_retention(conn, counts: dict, *, now: datetime) -> None:
    cutoff = (now - timedelta(days=_PENDING_ANALYSIS_RETENTION_DAYS)).isoformat()
    stale_ids = []
    for row in conn.execute(sa.text("SELECT id, payload FROM pending_analysis")).fetchall():
        payload = _dict(row.payload, row.id)
        status = payload.get("status")
        if status == "done" and (payload.get("done_at") or "") < cutoff:
            stale_ids.append(row.id)
        elif status == "failed" and (payload.get("failed_at") or "") < cutoff:
            stale_ids.append(row.id)
    for row_id in stale_ids:
        conn.execute(sa.text("DELETE FROM pending_analysis WHERE id = :id"), {"id": row_id})
    counts["rule7_pending_analysis_deleted"] = len(stale_ids)


def _rule_agent_findings_wipe(conn, counts: dict) -> None:
    total = conn.execute(sa.text("SELECT COUNT(*) FROM agent_findings")).scalar_one()
    conn.execute(sa.text("DELETE FROM agent_findings"))
    counts["rule7_agent_findings_deleted"] = total


def _rule_run_history_retired_agent_product(conn, counts: dict) -> None:
    stale_ids = []
    for row in conn.execute(sa.text("SELECT id, payload FROM run_history")).fetchall():
        payload = _dict(row.payload, row.id)
        if payload.get("kind") == "agent" or str(row.id).startswith("__streak__:agent:"):
            stale_ids.append(row.id)
    for row_id in stale_ids:
        conn.execute(sa.text("DELETE FROM run_history WHERE id = :id"), {"id": row_id})
    counts["rule7_run_history_agent_rows_deleted"] = len(stale_ids)


def _rule_automation_runs_orphaned(conn, counts: dict) -> None:
    live_automation_ids = {
        row.id for row in conn.execute(sa.text("SELECT id FROM automations")).fetchall()
    }
    orphans = []
    for row in conn.execute(sa.text("SELECT id, payload FROM automation_runs")).fetchall():
        payload = _dict(row.payload, row.id)
        automation_id = str(payload.get("automation_id") or "")
        if automation_id not in live_automation_ids:
            orphans.append((row.id, payload.get("session_id")))
    for run_id, session_id in orphans:
        conn.execute(sa.text("DELETE FROM automation_runs WHERE id = :id"), {"id": run_id})
        if session_id:
            # ON DELETE CASCADE (session_messages.session_id) takes the messages with it —
            # the one intended use of that cascade; never batch_alter_table("sessions").
            conn.execute(sa.text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})
    counts["rule7_orphaned_automation_runs_deleted"] = len(orphans)


def _rule_sessions_with_zero_messages(conn, counts: dict) -> None:
    empty_ids = [
        row.id
        for row in conn.execute(
            sa.text(
                "SELECT s.id FROM sessions s LEFT JOIN session_messages m "
                "ON m.session_id = s.id WHERE m.session_id IS NULL"
            )
        ).fetchall()
    ]
    for session_id in empty_ids:
        conn.execute(sa.text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})
    counts["rule7_empty_sessions_deleted"] = len(empty_ids)


# ── Rule 8: payees.default_categories ────────────────────────────────────────────────────


def _rule_payees_default_categories(conn, book_id: str, counts: dict) -> None:
    live_ids: set[str] = set()
    label_to_ids: dict[str, list[str]] = {}
    for row in conn.execute(
        sa.text("SELECT id, payload FROM categories WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        live_ids.add(row.id)
        payload = _dict(row.payload, row.id)
        label = (payload.get("label") or "").strip().lower()
        if label:
            label_to_ids.setdefault(label, []).append(row.id)

    for row in conn.execute(
        sa.text("SELECT id, payload FROM payees WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        payee = _dict(row.payload, row.id)
        defaults = payee.get("default_categories")
        if not isinstance(defaults, dict) or not defaults:
            continue
        new_defaults: dict = {}
        changed = False
        for kind, ref in defaults.items():
            ref_str = str(ref or "").strip()
            if ref_str in live_ids:
                new_defaults[kind] = ref_str
                continue
            matches = label_to_ids.get(ref_str.lower(), [])
            if len(matches) == 1:
                new_defaults[kind] = matches[0]
                changed = True
                counts["rule8_label_resolved_to_id"] += 1
            else:
                changed = True
                counts["rule8_dead_default_dropped"] += 1
        if changed:
            payee["default_categories"] = new_defaults
            conn.execute(
                sa.text("UPDATE payees SET payload = :p WHERE id = :id"),
                {"p": json.dumps(payee), "id": row.id},
            )


# ── Rule 9: import_batches ───────────────────────────────────────────────────────────────


def _rule_import_batches_cleanup(conn, book_id: str, counts: dict) -> None:
    referenced_by_txn = {
        row.batch_id
        for row in conn.execute(
            sa.text(
                "SELECT DISTINCT batch_id FROM transactions "
                "WHERE book_id = :b AND batch_id IS NOT NULL"
            ),
            {"b": book_id},
        ).fetchall()
    }
    referenced_by_statement: set[str] = set()
    for row in conn.execute(
        sa.text("SELECT id, batch_ids FROM statements_meta WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        referenced_by_statement.update(str(i) for i in _list_or_default(row.batch_ids, []))
    referenced_by_merge_journal: set[str] = set()
    for row in conn.execute(
        sa.text("SELECT absorbed_id, payload FROM merge_journal WHERE book_id = :b"),
        {"b": book_id},
    ).fetchall():
        entry = _dict(row.payload, row.absorbed_id)
        for key in ("canonical_before", "absorbed_before", "post_canonical_snapshot"):
            snapshot = entry.get(key)
            if isinstance(snapshot, dict) and snapshot.get("batch_id"):
                referenced_by_merge_journal.add(str(snapshot["batch_id"]))

    for row in conn.execute(
        sa.text("SELECT id, status FROM import_batches WHERE book_id = :b"), {"b": book_id}
    ).fetchall():
        batch_id = row.id
        referenced = (
            batch_id in referenced_by_txn
            or batch_id in referenced_by_statement
            or batch_id in referenced_by_merge_journal
        )
        if not referenced:
            conn.execute(sa.text("DELETE FROM import_batches WHERE id = :id"), {"id": batch_id})
            counts["rule9_orphan_batches_deleted"] += 1
            continue
        if row.status == "active" and batch_id in referenced_by_txn:
            live_txn_count = conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM transactions "
                    "WHERE book_id = :b AND batch_id = :bid AND deleted = 0"
                ),
                {"b": book_id, "bid": batch_id},
            ).scalar_one()
            if live_txn_count == 0:
                brow = conn.execute(
                    sa.text("SELECT payload FROM import_batches WHERE id = :id"),
                    {"id": batch_id},
                ).first()
                payload = _dict(brow.payload, batch_id)
                payload["status"] = "rolled_back"
                conn.execute(
                    sa.text(
                        "UPDATE import_batches SET status = 'rolled_back', payload = :p "
                        "WHERE id = :id"
                    ),
                    {"p": json.dumps(payload), "id": batch_id},
                )
                counts["rule9_active_batches_marked_rolled_back"] += 1


# ── Entry points ──────────────────────────────────────────────────────────────────────────


def _run_all_rules(conn, *, now: datetime) -> dict:
    """The whole sweep against an already-open connection — split out from ``upgrade()``
    (which just resolves ``op.get_bind()`` and calls this) so a test can re-run it directly
    against its own connection, outside an alembic migration context, to prove idempotency:
    a second call against already-cleaned state returns an empty ``counts`` (nothing left to
    do, no further writes)."""
    counts: dict = defaultdict(int)

    book_ids = [row.id for row in conn.execute(sa.text("SELECT id FROM books")).fetchall()]
    for book_id in book_ids:
        _rule_envelope_notes_and_targets(conn, book_id, counts)
        _rule_archived_envelopes(conn, book_id, counts)
        _rule_profile_and_category_groups(conn, book_id, counts)
        _rule_engine_state_legacy_keys(conn, book_id, counts)
        _rule_plan_state_stale_undo(conn, book_id, counts, now=now)
        _rule_payees_default_categories(conn, book_id, counts)
        _rule_import_batches_cleanup(conn, book_id, counts)

    _rule_pending_analysis_retention(conn, counts, now=now)
    _rule_agent_findings_wipe(conn, counts)
    _rule_run_history_retired_agent_product(conn, counts)
    _rule_automation_runs_orphaned(conn, counts)
    _rule_sessions_with_zero_messages(conn, counts)

    return dict(counts)


def upgrade() -> None:
    conn = op.get_bind()
    counts = _run_all_rules(conn, now=datetime.now(timezone.utc))
    logger.info("data hygiene cleanup: %s", counts)


def downgrade() -> None:
    # A data-only sweep with no schema change: nothing to undo structurally, and the swept rows
    # have no before-image (the pre-migration backup is the recovery path). A no-op keeps the
    # chain steppable — the migration wrapper's own tests step back from head — and a re-upgrade
    # just re-runs the idempotent sweep, which reports zero changes.
    logger.info("data hygiene cleanup: downgrade is a no-op (swept rows are not restored)")
