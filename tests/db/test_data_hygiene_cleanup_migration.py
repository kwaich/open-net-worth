"""Real migration coverage for the one-time data-hygiene cleanup
(202609281300_data_hygiene_cleanup) — every rule, including its own "keep" side.

Convention mirrors tests/db/test_owned_asset_review_removal_migration.py: migrate a fresh
sqlite file up to the revision right before this one, seed rows with a PLAIN
``create_engine`` connection (no FK enforcement on this seeding connection — the migration's
OWN connection, opened through env.py's ``_make_engine``, is the one that enforces real FKs),
then run ``command.upgrade(..., "head")`` and assert on the resulting rows.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.db.alembic_config import make_config
from backend.db.tables import DEFAULT_BOOK_ID

_FROM = "202609281200_statement_note"
_TARGET = "202609281300_data_hygiene_cleanup"
_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "backend/db/alembic/versions" / f"{_TARGET}.py"
)
_B = DEFAULT_BOOK_ID


def _mod():
    spec = importlib.util.spec_from_file_location("_data_hygiene_rev", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "onw.db"
    url = f"sqlite:///{path}"
    command.upgrade(make_config(url), _FROM)
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def _upgrade(url):
    command.upgrade(make_config(url), "head")


def _payload(conn, table: str, row_id: str, id_col: str = "id"):
    raw = conn.execute(
        text(f"SELECT payload FROM {table} WHERE {id_col} = :id"), {"id": row_id}
    ).scalar()
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def _insert_envelope(conn, seq, env_id, *, name="Env", archived=False, **extra):
    payload = {"id": env_id, "name": name, "archived": archived, **extra}
    conn.execute(
        text("INSERT INTO envelopes (book_id, seq, id, payload) VALUES (:b, :seq, :id, :payload)"),
        {"b": _B, "seq": seq, "id": env_id, "payload": json.dumps(payload)},
    )


def _insert_category(conn, seq, cat_id, *, label, kind="expense"):
    payload = {"id": cat_id, "label": label, "kind": kind}
    conn.execute(
        text("INSERT INTO categories (book_id, seq, id, payload) VALUES (:b, :seq, :id, :payload)"),
        {"b": _B, "seq": seq, "id": cat_id, "payload": json.dumps(payload)},
    )


def _write_profile(conn, payload: dict, *, exists=False):
    if exists:
        conn.execute(
            text("UPDATE profile SET payload = :p WHERE book_id = :b"),
            {"p": json.dumps(payload), "b": _B},
        )
    else:
        conn.execute(
            text("INSERT INTO profile (book_id, payload) VALUES (:b, :p)"),
            {"b": _B, "p": json.dumps(payload)},
        )


def _write_engine_state(conn, payload: dict):
    conn.execute(
        text(
            "INSERT INTO budget_engine_state (id, book_id, engine_id, payload) "
            "VALUES (:id, :b, 'proportional', :p)"
        ),
        {"id": f"{_B}:proportional", "b": _B, "p": json.dumps(payload)},
    )


def _insert_assignment(conn, month, category, amount="10.0"):
    conn.execute(
        text(
            "INSERT INTO budget_assignments (id, book_id, month, category, amount) "
            "VALUES (:id, :b, :m, :c, :a)"
        ),
        {"id": f"{_B}:{month}:{category}", "b": _B, "m": month, "c": category, "a": amount},
    )


def _assignment_exists(conn, month, category) -> bool:
    return (
        conn.execute(
            text(
                "SELECT COUNT(*) FROM budget_assignments "
                "WHERE book_id = :b AND month = :m AND category = :c"
            ),
            {"b": _B, "m": month, "c": category},
        ).scalar_one()
        > 0
    )


def _insert_session(conn, session_id, *, now="2026-09-01T00:00:00+00:00"):
    conn.execute(
        text(
            "INSERT INTO sessions (id, title, created_at, updated_at, message_count, meta) "
            "VALUES (:id, 't', :now, :now, 0, '{}')"
        ),
        {"id": session_id, "now": now},
    )


def _insert_message(conn, session_id, role="user", content="hi"):
    conn.execute(
        text("INSERT INTO session_messages (session_id, role, content) VALUES (:sid, :r, :c)"),
        {"sid": session_id, "r": role, "c": json.dumps(content)},
    )


def _session_exists(conn, session_id) -> bool:
    return (
        conn.execute(
            text("SELECT COUNT(*) FROM sessions WHERE id = :id"), {"id": session_id}
        ).scalar_one()
        > 0
    )


def _insert_automation(conn, seq, auto_id, *, name="Auto"):
    payload = {"id": auto_id, "name": name}
    conn.execute(
        text("INSERT INTO automations (book_id, seq, id, payload) VALUES (:b, :seq, :id, :p)"),
        {"b": _B, "seq": seq, "id": auto_id, "p": json.dumps(payload)},
    )


def _insert_automation_run(conn, seq, run_id, automation_id, session_id=None, status="completed"):
    payload = {
        "id": run_id,
        "automation_id": automation_id,
        "session_id": session_id,
        "status": status,
    }
    conn.execute(
        text("INSERT INTO automation_runs (seq, id, payload) VALUES (:seq, :id, :p)"),
        {"seq": seq, "id": run_id, "p": json.dumps(payload)},
    )


def _insert_run_history(conn, seq, row_id, *, kind, entity_id="e1"):
    payload = {"id": row_id, "kind": kind, "entity_id": entity_id}
    conn.execute(
        text("INSERT INTO run_history (seq, id, payload) VALUES (:seq, :id, :p)"),
        {"seq": seq, "id": row_id, "p": json.dumps(payload)},
    )


def _insert_pending_analysis(conn, seq, row_id, *, status, done_at=None, failed_at=None):
    payload = {"id": row_id, "status": status, "subject": "asset:x"}
    if done_at:
        payload["done_at"] = done_at
    if failed_at:
        payload["failed_at"] = failed_at
    conn.execute(
        text("INSERT INTO pending_analysis (seq, id, payload) VALUES (:seq, :id, :p)"),
        {"seq": seq, "id": row_id, "p": json.dumps(payload)},
    )


def _insert_payee(conn, seq, payee_id, *, default_categories):
    payload = {"id": payee_id, "name": "Payee", "default_categories": default_categories}
    conn.execute(
        text("INSERT INTO payees (book_id, seq, id, payload) VALUES (:b, :seq, :id, :p)"),
        {"b": _B, "seq": seq, "id": payee_id, "p": json.dumps(payload)},
    )


def _insert_batch(conn, seq, batch_id, *, status="active"):
    payload = {"id": batch_id, "description": "d", "transaction_count": 1, "status": status}
    conn.execute(
        text(
            "INSERT INTO import_batches "
            "(id, book_id, seq, created_at, description, transaction_count, status, payload) "
            "VALUES (:id, :b, :seq, '2026-01-01T00:00:00Z', 'd', 1, :status, :p)"
        ),
        {"id": batch_id, "b": _B, "seq": seq, "status": status, "p": json.dumps(payload)},
    )


def _insert_txn(conn, seq, txn_id, *, batch_id=None, deleted=0, kind="expense"):
    payload = {"id": txn_id}
    conn.execute(
        text(
            "INSERT INTO transactions "
            "(id, book_id, seq, date, kind, source, description, batch_id, deleted, payload) "
            "VALUES (:id, :b, :seq, '2026-01-01', :kind, 'manual', 'd', :batch_id, :deleted, :p)"
        ),
        {
            "id": txn_id,
            "b": _B,
            "seq": seq,
            "kind": kind,
            "batch_id": batch_id,
            "deleted": deleted,
            "p": json.dumps(payload),
        },
    )


def _insert_statement(conn, seq, stmt_id, *, batch_ids):
    payload = {"id": stmt_id}
    conn.execute(
        text(
            "INSERT INTO statements_meta "
            "(id, book_id, seq, created_at, filename, mime, size_bytes, file_hash, file_path, "
            "kind, batch_ids, payload) "
            "VALUES (:id, :b, :seq, '2026-01-01T00:00:00Z', 'f.csv', 'text/csv', 1, 'h', 'p', "
            "'text', :batch_ids, :p)"
        ),
        {
            "id": stmt_id,
            "b": _B,
            "seq": seq,
            "batch_ids": json.dumps(batch_ids),
            "p": json.dumps(payload),
        },
    )


def _insert_merge_journal(conn, seq, absorbed_id, *, canonical_id, batch_id, key="absorbed_before"):
    entry = {
        "absorbed_id": absorbed_id,
        "canonical_id": canonical_id,
        "canonical_before": {"id": canonical_id},
        "absorbed_before": {"id": absorbed_id},
        key: {
            "id": absorbed_id if key == "absorbed_before" else canonical_id,
            "batch_id": batch_id,
        },
        "created_at": "2026-01-01T00:00:00Z",
    }
    conn.execute(
        text(
            "INSERT INTO merge_journal (absorbed_id, book_id, seq, canonical_id, created_at, payload) "
            "VALUES (:id, :b, :seq, :cid, '2026-01-01T00:00:00Z', :p)"
        ),
        {"id": absorbed_id, "b": _B, "seq": seq, "cid": canonical_id, "p": json.dumps(entry)},
    )


# ── Rules 1+2: envelope payload copies, then slug-keyed notes/targets ───────────────────


def test_rule1_copies_envelope_note_and_target_into_profile_and_strips_payload(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(
            conn,
            1,
            "env_a",
            name="Groceries",
            note="watch this",
            target={"type": "monthly", "amount": 50},
        )

    _upgrade(url)

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert profile["category_notes"] == {"env_a": "watch this"}
        assert profile["category_targets"] == {"env_a": {"type": "monthly", "amount": 50}}
        env = _payload(conn, "envelopes", "env_a")
        assert "note" not in env and "target" not in env
        assert env["name"] == "Groceries"


def test_rule1_never_overwrites_an_existing_profile_note_but_still_strips_the_payload(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_a", name="Groceries", note="stale payload note")
        _write_profile(conn, {"category_notes": {"env_a": "the real, current note"}})

    _upgrade(url)

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert profile["category_notes"] == {"env_a": "the real, current note"}
        assert "note" not in _payload(conn, "envelopes", "env_a")


# ── Rule 2 ────────────────────────────────────────────────────────────────────────────────


def test_rule2_rekeys_a_slug_matched_note_onto_the_one_live_envelope(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_a", name="Sister Support")
        _write_profile(conn, {"category_notes": {"sister_support": "quarterly gift"}})

    _upgrade(url)

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert profile["category_notes"] == {"env_a": "quarterly gift"}


def test_rule2_drops_a_slug_key_with_no_matching_envelope(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_a", name="Groceries")
        _write_profile(conn, {"category_notes": {"long_gone_envelope": "orphaned"}})

    _upgrade(url)

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert profile["category_notes"] == {}


def test_rule2_drops_an_ambiguous_slug_key_matching_two_envelopes(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_a", name="Sister Support")
        _insert_envelope(conn, 2, "env_b", name="sister-support")  # same slug
        _write_profile(conn, {"category_targets": {"sister_support": {"amount": 1}}})

    _upgrade(url)

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert profile["category_targets"] == {}


def test_rule2_does_not_overwrite_an_envelope_that_already_has_its_own_note(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_a", name="Sister Support")
        _write_profile(
            conn,
            {
                "category_notes": {
                    "env_a": "the real note",
                    "sister_support": "stale slug-keyed duplicate",
                }
            },
        )

    _upgrade(url)

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        # Not overwritten, and the orphan slug key was dropped by the final catch-all.
        assert profile["category_notes"] == {"env_a": "the real note"}


# ── Rule 3: archived envelopes hard-deleted with their assignments/note/target/cap ───────


def test_rule3_hard_deletes_an_archived_envelope_with_its_assignments_note_target_and_cap(
    legacy_db,
):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_dead", name="Old thing", archived=True)
        _write_profile(
            conn,
            {
                "category_notes": {"env_dead": "n"},
                "category_targets": {"env_dead": {"amount": 1}},
            },
        )
        _write_engine_state(conn, {"caps": {"env_dead": [["2026-08", 100.0]]}})
        _insert_assignment(conn, "2026-08", "env_dead")
        _insert_assignment(conn, "2026-09", "env_dead")

    _upgrade(url)

    with engine.begin() as conn:
        assert _payload(conn, "envelopes", "env_dead") is None
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert "env_dead" not in profile.get("category_notes", {})
        assert "env_dead" not in profile.get("category_targets", {})
        engine_state = _payload(conn, "budget_engine_state", f"{_B}:proportional")
        assert "env_dead" not in engine_state.get("caps", {})
        assert not _assignment_exists(conn, "2026-08", "env_dead")
        assert not _assignment_exists(conn, "2026-09", "env_dead")


def test_rule3_leaves_a_live_envelope_and_its_data_alone(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_live", name="Still here", archived=False)
        _insert_assignment(conn, "2026-09", "env_live")

    _upgrade(url)

    with engine.begin() as conn:
        assert _payload(conn, "envelopes", "env_live") is not None
        assert _assignment_exists(conn, "2026-09", "env_live")


# ── Rule 4: profile.credit_card_mode / category_groups ───────────────────────────────────


def test_rule4_drops_credit_card_mode_and_group_envelope_ids_and_prunes_dead_categories(
    legacy_db,
):
    engine, url = legacy_db
    with engine.begin() as conn:
        _write_profile(conn, {"credit_card_mode": True, "base_currency": "USD"})
        _insert_category(conn, 1, "cat_live", label="Live")
        conn.execute(
            text(
                "INSERT INTO category_groups (book_id, seq, id, payload) VALUES (:b, 1, 'grp_a', :p)"
            ),
            {
                "b": _B,
                "p": json.dumps(
                    {
                        "id": "grp_a",
                        "name": "Group",
                        "envelope_ids": ["env_x"],
                        "categories": ["cat_live", "cat_dead"],
                    }
                ),
            },
        )

    command.upgrade(make_config(url), _TARGET)  # this rule's own revision, before the rename

    with engine.begin() as conn:
        profile = _payload(conn, "profile", _B, id_col="book_id")
        assert "credit_card_mode" not in profile
        assert profile["base_currency"] == "USD"
        group = _payload(conn, "category_groups", "grp_a")
        assert "envelope_ids" not in group
        assert group["categories"] == ["cat_live"]


# ── Rule 5: budget_engine_state 'proportional' legacy keys ───────────────────────────────


def test_rule5_drops_mapping_always_and_buckets_only_when_excluded_groups_is_already_a_list(
    legacy_db,
):
    engine, url = legacy_db
    with engine.begin() as conn:
        _write_engine_state(
            conn,
            {
                "mapping": {"cat_a": "needs"},
                "buckets": {"env_a": "needs"},
                "excluded_groups": ["grp_x"],
            },
        )

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "budget_engine_state", f"{_B}:proportional")
        assert "mapping" not in state
        assert "buckets" not in state
        assert state["excluded_groups"] == ["grp_x"]


def test_rule5_keeps_buckets_when_excluded_groups_is_absent(legacy_db):
    """`proportional_state.derive_excluded_groups` still reads `buckets` to DERIVE
    `excluded_groups` when that field is absent/None — dropping `buckets` here would blank
    that derivation for a book that never explicitly saved `excluded_groups`."""
    engine, url = legacy_db
    with engine.begin() as conn:
        _write_engine_state(conn, {"mapping": {"cat_a": "needs"}, "buckets": {"env_a": "needs"}})

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "budget_engine_state", f"{_B}:proportional")
        assert "mapping" not in state
        assert state["buckets"] == {"env_a": "needs"}


def test_rule5_keeps_buckets_when_excluded_groups_is_explicitly_none(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _write_engine_state(conn, {"buckets": {"env_a": "wants"}, "excluded_groups": None})

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "budget_engine_state", f"{_B}:proportional")
        assert state["buckets"] == {"env_a": "wants"}


def test_rule5_drops_legacy_targets_only_when_group_targets_exists(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _write_engine_state(
            conn, {"targets": {"needs": 50, "wants": 30}, "group_targets": {"grp_a": 50}}
        )

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "budget_engine_state", f"{_B}:proportional")
        assert "targets" not in state
        assert state["group_targets"] == {"grp_a": 50}


def test_rule5_keeps_legacy_targets_when_group_targets_is_absent(legacy_db):
    """`proportional_state.is_configured` reads EITHER key — dropping the only one present
    (pre-redesign book, never migrated to group_targets) would un-configure the engine."""
    engine, url = legacy_db
    with engine.begin() as conn:
        _write_engine_state(conn, {"targets": {"needs": 50, "wants": 30}})

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "budget_engine_state", f"{_B}:proportional")
        assert state["targets"] == {"needs": 50, "wants": 30}


# ── Rule 6: stale one-shot undo snapshots on plan_state ──────────────────────────────────


def test_rule6_drops_stale_undo_snapshots_older_than_a_day(legacy_db):
    engine, url = legacy_db
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO plan_state (book_id, payload) VALUES (:b, :p)"),
            {
                "b": _B,
                "p": json.dumps(
                    {
                        "scenarios": [],
                        "baseline": None,
                        "budget": {
                            "fresh_start_undo": {"token": "t1", "created_at": old},
                            "envelope_delete_undo": {"token": "t2", "created_at": old},
                            "home_engine_id": "proportional",
                        },
                    }
                ),
            },
        )

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "plan_state", _B, id_col="book_id")
        assert "fresh_start_undo" not in state["budget"]
        assert "envelope_delete_undo" not in state["budget"]
        assert state["budget"]["home_engine_id"] == "proportional"  # untouched


def test_rule6_keeps_a_fresh_undo_snapshot(legacy_db):
    engine, url = legacy_db
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO plan_state (book_id, payload) VALUES (:b, :p)"),
            {
                "b": _B,
                "p": json.dumps(
                    {
                        "scenarios": [],
                        "baseline": None,
                        "budget": {"fresh_start_undo": {"token": "t1", "created_at": recent}},
                    }
                ),
            },
        )

    _upgrade(url)

    with engine.begin() as conn:
        state = _payload(conn, "plan_state", _B, id_col="book_id")
        assert state["budget"]["fresh_start_undo"]["token"] == "t1"


# ── Rule 7: retired-product / orphaned logs ──────────────────────────────────────────────


def test_rule7_pending_analysis_deletes_old_finished_keeps_recent_and_pending(legacy_db):
    engine, url = legacy_db
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=8)).isoformat()
    recent = (now - timedelta(hours=1)).isoformat()
    with engine.begin() as conn:
        _insert_pending_analysis(conn, 1, "pan_old_done", status="done", done_at=old)
        _insert_pending_analysis(conn, 2, "pan_old_failed", status="failed", failed_at=old)
        _insert_pending_analysis(conn, 3, "pan_recent_done", status="done", done_at=recent)
        _insert_pending_analysis(conn, 4, "pan_pending", status="pending")

    _upgrade(url)

    with engine.begin() as conn:
        remaining = {r[0] for r in conn.execute(text("SELECT id FROM pending_analysis")).fetchall()}
        assert remaining == {"pan_recent_done", "pan_pending"}


def test_rule7_agent_findings_wiped_entirely(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO agent_findings (seq, id, payload) VALUES (1, 'f1', '{}')"))

    _upgrade(url)

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM agent_findings")).scalar_one() == 0


def test_rule7_run_history_drops_agent_product_rows_keeps_task_and_system_job(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_run_history(conn, 1, "run_agent1", kind="agent")
        _insert_run_history(conn, 2, "__streak__:agent:e1", kind="__streak__")
        _insert_run_history(conn, 3, "run_task1", kind="task")
        _insert_run_history(conn, 4, "run_sysjob1", kind="system_job")
        _insert_run_history(conn, 5, "__streak__:task:e1", kind="__streak__")

    _upgrade(url)

    with engine.begin() as conn:
        remaining = {r[0] for r in conn.execute(text("SELECT id FROM run_history")).fetchall()}
        assert remaining == {"run_task1", "run_sysjob1", "__streak__:task:e1"}


def test_rule7_deletes_orphaned_automation_run_and_its_session_keeps_live_automations_run(
    legacy_db,
):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_automation(conn, 1, "auto_live")
        _insert_session(conn, "automation-run-run_orphan")
        _insert_message(conn, "automation-run-run_orphan")
        _insert_automation_run(conn, 1, "run_orphan", "auto_gone", "automation-run-run_orphan")
        _insert_session(conn, "automation-run-run_live")
        _insert_message(conn, "automation-run-run_live")
        _insert_automation_run(conn, 2, "run_live", "auto_live", "automation-run-run_live")

    _upgrade(url)

    with engine.begin() as conn:
        remaining_runs = {
            r[0] for r in conn.execute(text("SELECT id FROM automation_runs")).fetchall()
        }
        assert remaining_runs == {"run_live"}
        assert not _session_exists(conn, "automation-run-run_orphan")
        assert _session_exists(conn, "automation-run-run_live")
        # The cascade took the orphan's message with it.
        orphan_msgs = conn.execute(
            text("SELECT COUNT(*) FROM session_messages WHERE session_id = :s"),
            {"s": "automation-run-run_orphan"},
        ).scalar_one()
        assert orphan_msgs == 0


def test_rule7_deletes_a_session_with_zero_messages_keeps_one_with_messages(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_session(conn, "s_empty")
        _insert_session(conn, "s_has_messages")
        _insert_message(conn, "s_has_messages")

    _upgrade(url)

    with engine.begin() as conn:
        assert not _session_exists(conn, "s_empty")
        assert _session_exists(conn, "s_has_messages")


# ── Rule 8: payees.default_categories ────────────────────────────────────────────────────


def test_rule8_drops_dead_ref_resolves_label_to_id_keeps_valid_id(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_category(conn, 1, "cat_groceries", label="Groceries")
        _insert_payee(
            conn,
            1,
            "pay_a",
            default_categories={
                "expense": "cat_groceries",  # already a valid id — kept as-is
                "income": "Groceries",  # label form, resolves uniquely — converted to id
                "transfer": "cat_long_gone",  # dead id — dropped
            },
        )

    _upgrade(url)

    with engine.begin() as conn:
        payee = _payload(conn, "payees", "pay_a")
        assert payee["default_categories"] == {
            "expense": "cat_groceries",
            "income": "cat_groceries",
        }


def test_rule8_drops_an_ambiguous_label_match(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_category(conn, 1, "cat_1", label="Misc")
        _insert_category(conn, 2, "cat_2", label="misc")
        _insert_payee(conn, 1, "pay_a", default_categories={"expense": "Misc"})

    _upgrade(url)

    with engine.begin() as conn:
        payee = _payload(conn, "payees", "pay_a")
        assert payee["default_categories"] == {}


# ── Rule 9: import_batches ────────────────────────────────────────────────────────────────


def test_rule9_deletes_a_fully_unreferenced_batch(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_batch(conn, 1, "imp_orphan")

    _upgrade(url)

    with engine.begin() as conn:
        assert _payload(conn, "import_batches", "imp_orphan") is None


def test_rule9_keeps_a_batch_referenced_by_a_live_transaction(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_batch(conn, 1, "imp_live")
        _insert_txn(conn, 1, "txn_1", batch_id="imp_live", deleted=0)

    _upgrade(url)

    with engine.begin() as conn:
        assert _payload(conn, "import_batches", "imp_live") is not None


def test_rule9_keeps_a_batch_referenced_only_by_a_statement(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_batch(conn, 1, "imp_stmt")
        _insert_statement(conn, 1, "stmt_1", batch_ids=["imp_stmt"])

    _upgrade(url)

    with engine.begin() as conn:
        assert _payload(conn, "import_batches", "imp_stmt") is not None


def test_rule9_keeps_a_batch_referenced_only_by_a_merge_journal_before_image(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_batch(conn, 1, "imp_merged")
        _insert_merge_journal(
            conn, 1, "txn_absorbed", canonical_id="txn_canon", batch_id="imp_merged"
        )

    _upgrade(url)

    with engine.begin() as conn:
        assert _payload(conn, "import_batches", "imp_merged") is not None


def test_rule9_marks_rolled_back_when_every_referencing_transaction_is_soft_deleted(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_batch(conn, 1, "imp_all_deleted", status="active")
        _insert_txn(conn, 1, "txn_1", batch_id="imp_all_deleted", deleted=1)
        _insert_txn(conn, 2, "txn_2", batch_id="imp_all_deleted", deleted=1)

    _upgrade(url)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM import_batches WHERE id = 'imp_all_deleted'")
        ).first()
        assert row.status == "rolled_back"
        assert _payload(conn, "import_batches", "imp_all_deleted")["status"] == "rolled_back"


def test_rule9_already_rolled_back_batch_is_left_alone(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_batch(conn, 1, "imp_already", status="rolled_back")
        _insert_txn(conn, 1, "txn_1", batch_id="imp_already", deleted=1)

    _upgrade(url)

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status FROM import_batches WHERE id = 'imp_already'")
        ).first()
        assert row.status == "rolled_back"


# ── Fail-closed on malformed data ─────────────────────────────────────────────────────────


def test_upgrade_refuses_a_malformed_envelope_payload(legacy_db):
    engine, url = legacy_db
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO envelopes (book_id, seq, id, payload) VALUES (:b, 1, 'env_bad', :p)"),
            {"b": _B, "p": "not-json"},
        )

    with pytest.raises(RuntimeError, match="migration refused"):
        _upgrade(url)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == _FROM


# ── Idempotency: a second run against already-cleaned state changes nothing ─────────────


def test_migration_is_idempotent(legacy_db):
    """Every rule, seeded together, run once via the real `command.upgrade`, then the whole
    sweep is invoked a SECOND time directly (mirrors
    tests/db/test_owned_asset_purchase_point_migration.py's own idempotency pattern) — a
    second pass over already-cleaned state must report zero further changes."""
    engine, url = legacy_db
    with engine.begin() as conn:
        _insert_envelope(conn, 1, "env_a", name="Groceries", note="n", target={"amount": 1})
        _insert_envelope(conn, 2, "env_dead", name="Gone", archived=True)
        _write_profile(
            conn,
            {"category_notes": {"sister_support": "x"}, "credit_card_mode": True},
        )
        _write_engine_state(
            conn,
            {
                "mapping": {"a": "b"},
                "buckets": {"env_a": "needs"},
                "excluded_groups": ["g1"],
                "targets": {"needs": 1},
                "group_targets": {"g1": 1},
            },
        )
        _insert_assignment(conn, "2026-09", "env_dead")
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        conn.execute(
            text("INSERT INTO plan_state (book_id, payload) VALUES (:b, :p)"),
            {
                "b": _B,
                "p": json.dumps(
                    {
                        "scenarios": [],
                        "baseline": None,
                        "budget": {"fresh_start_undo": {"created_at": old}},
                    }
                ),
            },
        )
        _insert_pending_analysis(
            conn,
            1,
            "pan_old",
            status="done",
            done_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        )
        conn.execute(text("INSERT INTO agent_findings (seq, id, payload) VALUES (1, 'f1', '{}')"))
        _insert_run_history(conn, 1, "run_agent1", kind="agent")
        _insert_automation(conn, 1, "auto_live")
        _insert_session(conn, "automation-run-run_orphan")
        _insert_automation_run(conn, 1, "run_orphan", "auto_gone", "automation-run-run_orphan")
        _insert_session(conn, "s_empty")
        _insert_category(conn, 1, "cat_a", label="A")
        _insert_payee(conn, 1, "pay_a", default_categories={"expense": "A", "income": "cat_dead"})
        _insert_batch(conn, 1, "imp_orphan")
        _insert_batch(conn, 2, "imp_all_deleted")
        _insert_txn(conn, 1, "txn_1", batch_id="imp_all_deleted", deleted=1)

    command.upgrade(make_config(url), _TARGET)  # real run, through alembic, at its own revision

    mod = _mod()
    with engine.begin() as conn:
        counts_again = mod._run_all_rules(conn, now=datetime.now(timezone.utc))
    assert not any(counts_again.values()), counts_again
