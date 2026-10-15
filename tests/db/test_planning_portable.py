"""Planning stores join portable export/import, ``/api/reset``, and boot seeding (plan 4a,
task 7).

Extends ``tests/db/test_portability_db_bridge.py`` (plan 2's reference-data bridge) and
``tests/db/test_money_portability.py`` (plan 3's money bridge) to the twelve Tier-1 planning
stores — ``envelope_groups``, ``categories``, ``commitments``, ``envelopes``,
``income_events``, ``recurring_streams``, ``targets``, ``corrections``, ``match_reviews``,
``match_decisions``, ``budget_assignments``, ``plan_state`` — sourced from
``backend.db.planning_io`` via ``backend.db.portable_bridge``. A THIRD combination, distinct
from both precedents (see ``portable_bridge.py``'s own module docstring):

1. **Undoable, like the money six** — the reference-data no-hook ruling never applied to
   planning. ``planning_io``'s ``import_*`` functions already fire ``fire_db_write_hook``
   unconditionally (built in task 6); every planning ``_wipe_*`` function (task 7) reuses
   ``_capture_then_delete``, the SAME helper the money six's wipes use. The one documented
   exception is ``corrections``: its wipe fires the hook too (uniform with its eleven
   siblings), but ``"corrections"`` sits in ``ai.checkpoint._EXCLUDED_TABLES`` (a behavioral
   signal log, not user-authored data), so the capture is a STRUCTURAL no-op there —
   ``/api/reset`` still wipes it regardless ("reset means reset").
2. **No cross-file FK, like the three reference-data stores** — planning tables FK only
   ``books`` (never each other), so each of the twelve rides the GENERIC per-file
   ``services.portability._apply_bridged_import`` loop, one file per call — NOT
   ``apply_money_bundle``'s dedicated group orchestration.

NINE of the twelve are LIVE (present in ``core.portable.PORTABLE_FILES``):
``envelope_groups``, ``categories``, ``commitments``, ``envelopes``, ``recurring_streams``,
``targets``, ``budget_assignments``, ``plan_state`` were already live pre-dating this task
(tasks 3/4's own reports deferred wiring them here); ``income_events`` joined
``PORTABLE_FILES`` in this task's OWN fix round 1 (see below) — it is checkpoint-INCLUDED
real user data (``income_event_store.py``: "NOT a cache" — dividend/interest/distribution
income history), so ``wipe_all`` wiping it on every ``/api/reset`` with NO corresponding
``PORTABLE_FILES`` entry meant the pre-wipe backup loop (``api/setup.py::reset_all_data``,
which iterates ``PORTABLE_FILES``) silently OMITTED it — a self-hoster hitting reset would
lose their entire income history with no backup and no undo path (reset opens no checkpoint
turn). Fix round 1's own review caught this as a Blocker, reproduced end to end against the
live ``/api/reset`` route; the first implementation had wrongly classified this as a
"flag, don't fix" policy question rather than the same data-loss bug class
``docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md`` already fixed once for
price_history/instruments, and Plan 3 Task 6 fixed again for ``TXN_AUDIT_JSON``/
``MERGE_JOURNAL_JSON`` (commit ``89c1977d``) — the exact same "a bridged store `wipe_all` now
reaches MUST also be in ``PORTABLE_FILES`` unless its loss is genuinely inconsequential"
rule, missed for income_events on the first pass.

The remaining THREE stay DORMANT, mirroring ``evidence.json``'s own precedent exactly —
bridged (so a future flip is a manifest-only change) but not reachable through the
export/import bundle pipeline, each with an EXPLICIT reason ``wipe_all`` wiping them with no
backup is safe (see ``core/portable.py``'s own docstring for the full reasoning, verified
against ``ai.checkpoint._EXCLUDED_TABLES``): ``corrections.json`` is a behavioral signal log
(checkpoint-EXCLUDED, "the corrections log" in ``core.portable``'s pre-existing exclusion
list — predates this task entirely); ``match_reviews.json`` is the dedup engine's grouped
review queue ("the pending-review queue," also pre-existing); ``match_decisions.json`` is
its verdict-cache sibling. The latter two are checkpoint-INCLUDED (unlike every other
excluded file) yet still correctly non-portable: every row in either store is keyed on a
PAIR of transaction ids that ``/api/reset`` ALSO wipes in the same pass (``transactions.json``
is one of the LIVE-bridged money six), so neither is independently meaningful data once a
reset has run — losing them costs, at most, re-asking a dedup question the engine already
answered once, never a historical financial fact. ``wipe_all`` reaches all twelve regardless
of live/dormant status, same as it already does for ``evidence.json`` today —
reset-to-blank-slate and export-portability stay independent questions; being SAFELY
dormant (this paragraph) is a different claim from being CORRECTLY dormant, which is what
fix round 1 corrected for income_events.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from backend.ai import checkpoint as ckpt
from backend.core import portable
from backend.db import planning_io as pio
from backend.db import portable_bridge
from backend.db.engine import get_engine
from backend.db.tables import (
    budget_assignments,
    categories,
    commitments,
    corrections,
    envelope_groups,
    envelopes,
    income_events,
    match_decisions,
    match_reviews,
    plan_state,
    recurring_streams,
    targets,
)
from backend.services import portability as svc

ALL_PLANNING_FILES = (
    "category_groups.json",
    "categories.json",
    "commitments.json",
    "envelopes.json",
    "income_events.json",
    "recurring_streams.json",
    "targets.json",
    "corrections.json",
    "match_reviews.json",
    "match_decisions.json",
    "budget_assignments.json",
    "plan_state.json",
)

# LIVE in core.portable.PORTABLE_FILES: 8 pre-dating this task + income_events.json, added
# in fix round 1 (real user data — see module docstring). The other three stay dormant.
LIVE_PLANNING_FILES = (
    "category_groups.json",
    "categories.json",
    "commitments.json",
    "envelopes.json",
    "income_events.json",
    "recurring_streams.json",
    "targets.json",
    "budget_assignments.json",
    "plan_state.json",
)
DORMANT_PLANNING_FILES = tuple(set(ALL_PLANNING_FILES) - set(LIVE_PLANNING_FILES))

# ── Fixtures — small but non-trivial (mirrors test_planning_cutover.py's own shapes,
# trimmed to what each test below actually needs) ──────────────────────────────────

CATEGORY_GROUPS = [
    {
        "id": "grp_1",
        "name": "Essentials",
        "collapsed": False,
        "order": 0,
        "is_others": False,
    },
]
CATEGORIES = [
    {"id": "cat_1", "label": "Groceries", "group_id": "grp_1"},
]
COMMITMENTS = [{"id": "cmt_1", "name": "Car loan", "kind": "loan"}]
ENVELOPES = [{"id": "env_1", "name": "Car", "category_ids": ["cat_1"], "order": 0}]
INCOME_EVENTS = [{"id": "inc_1", "gross_amount": 100.0, "income_kind": "dividend"}]
RECURRING_STREAMS = [{"type": "declared", "id": "rst_1", "amount": 15.99, "currency": "USD"}]
TARGETS = [{"id": "tgt_1", "name": "Emergency fund", "target_amount": 10000.0}]
CORRECTIONS = [{"id": "corr_1", "to_category": "cat_1", "kind": "correction"}]
MATCH_REVIEWS = [{"id": "mr_1", "incoming_id": "txn_a", "status": "pending"}]
MATCH_DECISIONS = [{"pair_key": "pk_1", "verdict": "same", "source": "llm"}]
BUDGET_ASSIGNMENTS = {"2026-06": {"cat_1": 0.1 + 0.2, "cat_2": 800.0}, "2026-07": {"cat_1": 500.0}}
PLAN_STATE = {
    "scenarios": [{"id": "scen_1", "name": "Base case", "saving": 2000.0, "events": []}],
    "baseline": None,
}


def _seed_all_twelve(conn) -> None:
    pio.import_payload_list(conn, envelope_groups, CATEGORY_GROUPS)
    pio.import_payload_list(conn, categories, CATEGORIES)
    pio.import_payload_list(conn, commitments, COMMITMENTS)
    pio.import_payload_list(conn, envelopes, ENVELOPES)
    pio.import_payload_list(conn, income_events, INCOME_EVENTS)
    pio.import_payload_list(conn, recurring_streams, RECURRING_STREAMS)
    pio.import_payload_list(conn, targets, TARGETS)
    pio.import_payload_list(conn, corrections, CORRECTIONS)
    pio.import_payload_list(conn, match_reviews, MATCH_REVIEWS)
    pio.import_match_decisions(conn, MATCH_DECISIONS)
    pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)
    pio.import_plan_state(conn, PLAN_STATE)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle — mirrors test_portability_db_bridge.py's/
    test_money_portability.py's own identical helper."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entries = {}
        for name, data in files.items():
            zf.writestr(name, data)
            entries[name] = {"sha256": _sha(data)}
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": entries}),
        )
    return buf.getvalue()


# ── The registry itself: all twelve bridged + wipe-able, nine live / three dormant ───────


def test_all_twelve_planning_filenames_are_bridged_with_wipe_fns():
    for filename in ALL_PLANNING_FILES:
        assert filename in portable_bridge.BRIDGED, f"{filename} missing from BRIDGED"
        assert filename in portable_bridge._WIPE_FNS, f"{filename} missing from _WIPE_FNS"
        bridge = portable_bridge.BRIDGED[filename]
        assert callable(bridge.export_fn)
        assert callable(bridge.import_fn)


def test_nine_planning_files_are_live_three_stay_dormant():
    """Uses the REAL, unmonkeypatched manifest — this is the direct proof of the fix-round-1
    Blocker: ``income_events.json`` (real user financial data) must be genuinely, by default,
    a member of ``core.portable.PORTABLE_FILES`` — not merely asserted true by a test that
    injects it into a narrowed local copy. RED before the fix (``income_events.json`` absent
    from ``names``), GREEN after."""
    names = {p.name for p in portable.PORTABLE_FILES}
    for filename in LIVE_PLANNING_FILES:
        assert filename in names, f"{filename} expected LIVE in PORTABLE_FILES"
    for filename in DORMANT_PLANNING_FILES:
        assert filename not in names, (
            f"{filename} expected to stay OUT of PORTABLE_FILES (dormant, mirrors "
            "evidence.json's own precedent)"
        )


def test_planning_files_constant_matches_the_bridge_registry():
    """planning_io.PLANNING_FILES (task 6) and portable_bridge.BRIDGED (task 7) must name
    the identical twelve filenames — this task consumes, never redefines, task 6's list."""
    assert set(pio.PLANNING_FILES) == set(ALL_PLANNING_FILES)
    for filename in pio.PLANNING_FILES:
        assert filename in portable_bridge.BRIDGED


# ── Export: DB-sourced, under the original filenames ──────────────────────────────────


def test_export_bundle_contains_the_nine_live_planning_files_db_sourced(db_isolated):
    """Also the direct proof of "bundle round-trip includes income_events" (fix round 1,
    reviewer requirement): uses the REAL, unmonkeypatched build_export_bundle() — RED before
    the fix (income_events.json absent from the real bundle), GREEN after."""
    with get_engine().begin() as conn:
        _seed_all_twelve(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert set(LIVE_PLANNING_FILES) <= names
        for filename in DORMANT_PLANNING_FILES:
            assert filename not in names, f"{filename} must stay out of a real export bundle"
        assert json.loads(zf.read("category_groups.json")) == CATEGORY_GROUPS
        assert json.loads(zf.read("categories.json")) == CATEGORIES
        assert json.loads(zf.read("commitments.json")) == COMMITMENTS
        assert json.loads(zf.read("envelopes.json")) == ENVELOPES
        assert json.loads(zf.read("income_events.json")) == INCOME_EVENTS
        assert json.loads(zf.read("recurring_streams.json")) == RECURRING_STREAMS
        assert json.loads(zf.read("targets.json")) == TARGETS
        assert json.loads(zf.read("budget_assignments.json")) == BUDGET_ASSIGNMENTS
        assert json.loads(zf.read("plan_state.json")) == PLAN_STATE
        category_groups_bytes = zf.read("category_groups.json")

    # Byte-format check: matches JsonStore._save_raw's own convention exactly, like every
    # other bridged store's own equivalent assertion.
    with get_engine().connect() as conn:
        expected = json.dumps(
            pio.export_payload_list(conn, envelope_groups), ensure_ascii=False, indent=2
        ).encode("utf-8")
    assert category_groups_bytes == expected


def test_dormant_planning_bridges_round_trip_via_registry_directly(db_isolated):
    """corrections.json/match_reviews.json/match_decisions.json are the three still-dormant
    planning bridges (not in PORTABLE_FILES — see module docstring for why each is SAFELY,
    deliberately dormant), but their BRIDGED entries stay fully wired, exactly like
    evidence.json's own precedent: exercised directly against the registry (no bundle/
    manifest pipeline involved), still round-trip correctly. Proves flipping any one on later
    is a manifest-only change, not a rebuild. income_events.json is deliberately NOT covered
    here any more (fix round 1) — it is LIVE now, covered by the real-bundle tests instead."""
    for filename, fixture in (
        ("corrections.json", CORRECTIONS),
        ("match_reviews.json", MATCH_REVIEWS),
    ):
        bridge = portable_bridge.BRIDGED[filename]
        with get_engine().begin() as conn:
            count = bridge.import_fn(conn, fixture)
        assert count == len(fixture)
        with get_engine().connect() as conn:
            assert bridge.export_fn(conn) == fixture

    md_bridge = portable_bridge.BRIDGED["match_decisions.json"]
    with get_engine().begin() as conn:
        count = md_bridge.import_fn(conn, MATCH_DECISIONS)
    assert count == len(MATCH_DECISIONS)
    with get_engine().connect() as conn:
        assert md_bridge.export_fn(conn) == MATCH_DECISIONS


# ── Import: representative bundle (incl. the two dict stores) round-trips through the
# bridge, old-bundle restore, absent-resets-to-empty ──────────────────────────────────


def test_planning_bundle_round_trips_through_bridge(db_isolated):
    """The representative bundle: two generic list stores (incl. income_events, fix round
    1's LIVE addition) + the two bespoke dict stores."""
    with get_engine().begin() as conn:
        pio.import_payload_list(conn, envelope_groups, CATEGORY_GROUPS)
        pio.import_payload_list(conn, categories, CATEGORIES)
        pio.import_payload_list(conn, income_events, INCOME_EVENTS)
        pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)
        pio.import_plan_state(conn, PLAN_STATE)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert set(result["restored_files"]) >= {
        "category_groups.json",
        "categories.json",
        "income_events.json",
        "budget_assignments.json",
        "plan_state.json",
    }
    assert result["row_counts"]["category_groups.json"] == len(CATEGORY_GROUPS)
    assert result["row_counts"]["income_events.json"] == len(INCOME_EVENTS)
    assert result["row_counts"]["budget_assignments.json"] == 3
    assert result["row_counts"]["plan_state.json"] == 1

    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, envelope_groups) == CATEGORY_GROUPS
        assert pio.export_payload_list(conn, categories) == CATEGORIES
        assert pio.export_payload_list(conn, income_events) == INCOME_EVENTS
        assert pio.export_budget_assignments(conn) == BUDGET_ASSIGNMENTS
        assert pio.export_plan_state(conn) == PLAN_STATE


def test_an_old_bundle_group_row_loses_its_retired_membership_list_on_import(db_isolated):
    """A bundle exported before category->group membership was retired still carries each
    group's ``categories`` list (and, older still, ``envelope_ids``) — the import keeps the
    group (name/order/collapsed/is_others) and drops those keys."""
    old = [{**CATEGORY_GROUPS[0], "categories": ["food", "transport"], "envelope_ids": ["env_1"]}]
    svc.apply_import(_build_bundle({"category_groups.json": json.dumps(old).encode()}))

    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, envelope_groups) == CATEGORY_GROUPS


def test_import_synthetic_old_bundle_lands_rows_for_list_and_dict_stores(db_isolated):
    """A hand-built bundle stands in for one exported BEFORE these stores were migrated —
    old and new bundles are handled identically, by filename alone (mirrors
    test_portability_db_bridge.py's/test_money_portability.py's identical-shaped test)."""
    bundle = _build_bundle(
        {
            "category_groups.json": json.dumps(CATEGORY_GROUPS).encode(),
            "targets.json": json.dumps(TARGETS).encode(),
            "budget_assignments.json": json.dumps(BUDGET_ASSIGNMENTS).encode(),
            "plan_state.json": json.dumps(PLAN_STATE).encode(),
        }
    )

    result = svc.apply_import(bundle)

    assert result["row_counts"]["category_groups.json"] == len(CATEGORY_GROUPS)
    assert result["row_counts"]["targets.json"] == len(TARGETS)
    assert result["row_counts"]["budget_assignments.json"] == 3
    assert result["row_counts"]["plan_state.json"] == 1

    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, envelope_groups) == CATEGORY_GROUPS
        assert pio.export_payload_list(conn, targets) == TARGETS
        assert pio.export_budget_assignments(conn) == BUDGET_ASSIGNMENTS
        assert pio.export_plan_state(conn) == PLAN_STATE


def test_reimport_replaces_existing_planning_data_entirely(db_isolated):
    with get_engine().begin() as conn:
        pio.import_payload_list(conn, targets, TARGETS)

    new_targets = [{"id": "tgt_2", "name": "New target", "target_amount": 500.0}]
    bundle = _build_bundle({"targets.json": json.dumps(new_targets).encode()})
    result = svc.apply_import(bundle)

    assert result["row_counts"]["targets.json"] == len(new_targets)
    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, targets) == new_targets


def test_import_absent_planning_file_wipes_that_store_to_empty(db_isolated):
    with get_engine().begin() as conn:
        pio.import_payload_list(conn, targets, TARGETS)

    bundle = _build_bundle({"category_groups.json": json.dumps(CATEGORY_GROUPS).encode()})
    # targets.json intentionally absent.
    result = svc.apply_import(bundle)

    assert "targets.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, targets) == []


def test_bundle_containing_a_dormant_planning_file_is_rejected_as_unknown(db_isolated):
    """corrections.json (dormant — income_events.json is LIVE as of fix round 1, no longer a
    valid example here) in a hand-built bundle gets the SAME treatment any other non-portable
    filename already gets — mirrors test_bundle_containing_evidence_json_entry_is_rejected_
    as_unknown exactly."""
    bundle = _build_bundle({"corrections.json": json.dumps(CORRECTIONS).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("corrections.json" in e and "unknown" in e for e in report["errors"])

    with pytest.raises(Exception):
        svc.apply_import(bundle)


def test_corrupt_planning_bundle_import_rolls_back_atomically(db_isolated):
    """A budget_assignments entry with a non-numeric amount fails loudly and the whole
    store's transaction — wipe included — rolls back untouched, mirroring
    test_corrupt_bridged_import_rolls_back_atomically_nothing_partial."""
    with get_engine().begin() as conn:
        pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)

    corrupt = {"2026-08": {"cat_1": "not-a-number"}}
    bundle = _build_bundle({"budget_assignments.json": json.dumps(corrupt).encode()})

    with pytest.raises(Exception):
        svc.apply_import(bundle)

    with get_engine().connect() as conn:
        assert pio.export_budget_assignments(conn) == BUDGET_ASSIGNMENTS


# ── /api/reset — every one of the twelve planning tables, live or dormant ─────────────


def test_reset_wipes_all_planning_tables(db_isolated):
    with get_engine().begin() as conn:
        _seed_all_twelve(conn)

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, envelope_groups) == []
        assert pio.export_payload_list(conn, categories) == []
        assert pio.export_payload_list(conn, commitments) == []
        assert pio.export_payload_list(conn, envelopes) == []
        assert pio.export_payload_list(conn, income_events) == []
        assert pio.export_payload_list(conn, recurring_streams) == []
        assert pio.export_payload_list(conn, targets) == []
        assert pio.export_payload_list(conn, corrections) == []
        assert pio.export_payload_list(conn, match_reviews) == []
        assert pio.export_payload_list(conn, match_decisions) == []
        assert pio.export_budget_assignments(conn) == {}
        assert pio.export_plan_state(conn) == {"scenarios": [], "baseline": None}


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup — mirrors test_portability_db_bridge.py's/
    test_money_portability.py's own identical helper."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import paths as bpaths
    from backend.api import setup as setup_api

    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    profile = data / "profile.json"
    watchlist = data / "watchlist.json"
    profile.write_text(json.dumps({"name": "Alex"}))

    monkeypatch.setattr(portable, "PORTABLE_FILES", [profile])
    monkeypatch.setattr(bpaths, "DATA_DIR", data)
    monkeypatch.setattr(bpaths, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(bpaths, "CONNECTOR_SESSIONS_JSON", data / "connector_sessions.json")
    monkeypatch.setattr(bpaths, "PROFILE_JSON", profile)
    monkeypatch.setattr(bpaths, "WATCHLIST_JSON", watchlist)

    app = FastAPI()
    app.include_router(setup_api.router)
    return TestClient(app), data


def test_reset_endpoint_wipes_planning_tables_end_to_end(db_isolated, tmp_path, monkeypatch):
    with get_engine().begin() as conn:
        pio.import_payload_list(conn, targets, TARGETS)
        pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, targets) == []
        assert pio.export_budget_assignments(conn) == {}


def test_reset_backup_snapshot_contains_planning_rows(db_isolated, tmp_path, monkeypatch):
    """Regression test for the same bug class the reference-data bridge's own reset-backup
    fix closed (docs/adr/2026-08-07-portable-export-bridges-db-backed-stores.md):
    ``/api/reset``'s pre-wipe backup loop already called ``export_bytes_for(src)`` for every
    portable file (fixed in plan 2) — but BEFORE this task, none of the eight live planning
    filenames were in ``portable_bridge.BRIDGED`` yet, so ``export_bytes_for`` fell through
    to ``path.read_bytes()`` against a file the plan-4a cutover had already renamed away:
    ``path.exists()`` is False post-cutover, so ``export_bytes_for`` returned ``None`` and the
    backup loop silently OMITTED that store entirely — a self-hoster hitting reset would get
    a backup missing their category/budget/target data. Post-migration state (no
    targets.json/budget_assignments.json/income_events.json file on disk — the DB is the only
    source), seed real rows, hit ``POST /api/reset``, and assert the backup snapshot's
    targets.json/budget_assignments.json/income_events.json actually CONTAIN those pre-reset
    rows.

    EXTENDED (fix round 1, reviewer requirement 3): now also covers ``income_events.json`` —
    the Blocker this fix round closed. Unlike targets/budget_assignments (already correctly
    LIVE before this task), income_events.json was NOT in the real ``PORTABLE_FILES`` until
    fix round 1's own change to ``core/portable.py`` — the direct, unnarrowed-manifest proof
    of THAT fact lives in ``test_nine_planning_files_are_live_three_stay_dormant`` and
    ``test_export_bundle_contains_the_nine_live_planning_files_db_sourced`` above (both
    genuinely RED before this fix round, GREEN after); this test's own manifest-extension step
    below stays the same safe, narrowed-list technique its targets/budget_assignments
    coverage already used, now proving the BACKUP MECHANISM specifically also handles
    income_events correctly (right content, right filename) once it is live."""
    from backend.paths import BUDGET_ASSIGNMENTS_JSON, INCOME_EVENTS_JSON, TARGETS_JSON

    with get_engine().begin() as conn:
        pio.import_payload_list(conn, targets, TARGETS)
        pio.import_payload_list(conn, income_events, INCOME_EVENTS)
        pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)

    client, data = _reset_client(tmp_path, monkeypatch)
    # This test's whole point is the bridged-file backup path, so extend the narrowed
    # manifest _reset_client set up with three live bridged planning filenames (real Path
    # identities — only .name matters for a bridged lookup).
    monkeypatch.setattr(
        portable,
        "PORTABLE_FILES",
        [*portable.PORTABLE_FILES, TARGETS_JSON, BUDGET_ASSIGNMENTS_JSON, INCOME_EVENTS_JSON],
    )

    r = client.post("/api/reset")
    assert r.status_code == 200

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]

    restored_targets = json.loads((backup_dir / "targets.json").read_text())
    assert restored_targets == TARGETS

    restored_ba = json.loads((backup_dir / "budget_assignments.json").read_text())
    assert restored_ba == BUDGET_ASSIGNMENTS

    income_events_backup = backup_dir / "income_events.json"
    assert income_events_backup.exists(), (
        "income_events.json missing from the pre-reset backup — a self-hoster's dividend/"
        "income history would be destroyed with no recovery path (fix round 1 Blocker)"
    )
    assert json.loads(income_events_backup.read_text()) == INCOME_EVENTS

    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, income_events) == []


# ── corrections: the one documented exception — wiped, but a checkpoint no-op ─────────


def test_corrections_wipe_fires_hook_but_checkpoint_exclusion_makes_it_a_structural_no_op(
    db_isolated,
):
    """The brief's one documented exception: corrections' wipe fires fire_db_write_hook
    (uniform with its eleven siblings — _capture_then_delete, mirroring the money bridge),
    but ai.checkpoint._EXCLUDED_TABLES makes the capture a structural no-op — a /rewind must
    never resurrect stale correction history. Reset still wipes it regardless ("reset means
    reset"). Mirrors test_money_bundle_import_never_undoes_the_audit_trail's shape for
    txn_audit, at the reset layer instead of the import layer."""
    assert "corrections" in ckpt._EXCLUDED_TABLES

    with get_engine().begin() as conn:
        pio.import_payload_list(conn, corrections, CORRECTIONS)
        pio.import_payload_list(conn, targets, TARGETS)  # a NON-excluded sibling, for contrast

    turn_ctx, token = ckpt.begin_turn(
        "sess-plan-t7-corr", anchor_history_len=0, message_preview="r"
    )
    try:
        svc.reset_bridged_stores()
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    with get_engine().connect() as conn:
        assert pio.export_payload_list(conn, corrections) == []  # reset means reset
        assert pio.export_payload_list(conn, targets) == []

    assert entry is not None
    assert "targets" in entry["db_tables"], "a non-excluded planning wipe must be captured"
    assert "corrections" not in entry["db_tables"], (
        "corrections' capture must be a structural no-op, not merely untested"
    )


# ── Undoable planning imports (plan 4a task 7's binding requirement) ──────────────────


def test_planning_import_is_undoable_via_checkpoint_restore(db_isolated):
    """At least one planning store's import is undoable: the write_log before-images a
    checkpoint turn collects let restore() revert it. Direct ai.checkpoint begin_turn/
    end_turn/restore — mirrors tests/test_targets_checkpoint.py's minimal pattern (the file
    AND DB write hooks are already registered at backend.ai.checkpoint's own module-import
    time, so no per-test re-registration is needed)."""
    with get_engine().begin() as conn:
        pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)

    bundle = _build_bundle(
        {"budget_assignments.json": json.dumps({"2026-06": {"cat_1": 999.0}}).encode()}
    )

    turn_ctx, token = ckpt.begin_turn(
        "sess-plan-t7-undo", anchor_history_len=0, message_preview="i"
    )
    try:
        svc.apply_import(bundle)
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    assert entry is not None
    assert "budget_assignments" in entry["db_tables"]

    with get_engine().connect() as conn:
        assert pio.export_budget_assignments(conn) == {"2026-06": {"cat_1": 999.0}}

    ckpt.restore(entry["id"], mode="data")

    with get_engine().connect() as conn:
        assert pio.export_budget_assignments(conn) == BUDGET_ASSIGNMENTS


# ── Seeding: categories (main.py:427) + envelope_groups.ensure_others ─────────────────


def test_first_boot_seeds_categories_into_db(db_isolated):
    """db_isolated yields the redirected-singleton URL: simulate main.py:427's exact seed
    block against a fresh, migrated, category-less DB, then assert CategoryStore().all()
    (resolving the singleton) is non-empty and includes the 'split' structural sentinel —
    the CategoryStore seed already writes through the DB-backed store surface (verified,
    not modified, by this task)."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    store = CategoryStore()
    assert store.all() == []

    if not store.all():
        store.bulk_insert(list(BUILTIN_CATEGORIES))

    seeded = CategoryStore().all()
    assert len(seeded) == len(BUILTIN_CATEGORIES)
    ids = {c["id"] for c in seeded}
    assert "split" in ids


def test_category_groups_ensure_others_seeds_a_row_on_a_fresh_db(db_isolated):
    from backend.db.envelope_groups_store import EnvelopeGroupsStore

    store = EnvelopeGroupsStore()
    assert store.all() == []

    others = store.ensure_others()

    assert others["is_others"] is True
    assert EnvelopeGroupsStore().count() == 1


def test_budget_assignments_and_plan_state_id_pk_wipes_use_the_right_column(db_isolated):
    """Sanity check for the two hand-rolled tables' wipe fns: budget_assignments' PK column
    is `id`, plan_state's is `book_id` (module docstrings, backend/db/tables.py) — a
    _capture_then_delete call with the wrong pk_name would raise AttributeError on the very
    first seeded row rather than silently doing the wrong thing, so this just proves neither
    wipe fn raises and both actually empty their table."""
    with get_engine().begin() as conn:
        pio.import_budget_assignments(conn, BUDGET_ASSIGNMENTS)
        pio.import_plan_state(conn, PLAN_STATE)

    with get_engine().begin() as conn:
        portable_bridge.wipe(conn, "budget_assignments.json")
        portable_bridge.wipe(conn, "plan_state.json")

    with get_engine().connect() as conn:
        assert conn.execute(select(budget_assignments)).all() == []
        assert conn.execute(select(plan_state)).all() == []
