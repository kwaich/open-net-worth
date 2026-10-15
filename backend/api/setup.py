from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


def is_fresh_setup() -> bool:
    """Delegates to the ONE implementation in ``services.setup``.

    Kept as a name here because several modules already import it from this router. There used
    to be a second, independently-written copy in this file; the two had already drifted (this
    one read positions through ``api.portfolio``, the service through ``positions_service``),
    which is the kind of split that makes a freshness bug reproduce on only one surface.
    """
    from backend.services.setup import is_fresh_setup as _is_fresh

    return _is_fresh()


@router.get("/api/setup-status")
def setup_status():
    from backend.db.onboarding_state_store import OnboardingStateStore

    return {
        "is_fresh": is_fresh_setup(),
        "onboarding_completed_at": OnboardingStateStore().completed_at(),
        "restore_pending": OnboardingStateStore().restore_pending(),
        "book_id": OnboardingStateStore().book_id(),
    }


def _location_source() -> str:
    """Which rung of the resolution ladder answered: "env", "pointer" or "default"."""
    import os

    from backend.core.config import read_pointer

    if any(os.environ.get(v, "").strip() for v in ("ONW_ROOT", "DATA_DIR")):
        return "env"
    return "pointer" if read_pointer() else "default"


@router.get("/api/setup-status/data-location")
def data_location():
    """Where this instance actually keeps its data.

    First run SHOWS this path, so it has to be the resolved one — a hardcoded
    "~/.open-net-worth" in the UI is a claim the backend may not honour (DATA_DIR can point
    anywhere).
    """
    from backend.core.config import get_settings, instance_root

    resolved = get_settings().data_dir
    home = str(Path.home())

    def _shown(p) -> str:
        s = str(p)
        return "~" + s[len(home) :] if s.startswith(home) else s

    return {
        "data_dir": _shown(resolved),
        # What first run should SHOW: an instance is a working folder, and naming only its
        # data/ half invites someone to move that half on its own.
        "instance_root": _shown(instance_root()),
        # WHERE that answer came from, so the screen can say "set by ONW_ROOT" instead of
        # telling someone to set a variable they have plainly already set.
        "source": _location_source(),
    }


@router.get("/api/setup-status/inspect-path")
def inspect_path(path: str):
    """What is already at a candidate data folder, so first run can say so before adopting it.

    Metadata only — row counts and a timestamp, never contents and never a directory listing:
    an auth-gated endpoint that reports "is there a book here, and how big" must not double as
    a filesystem browser.
    """
    from backend.services.data_location import inspect_dict

    return inspect_dict(path)


@router.post("/api/setup-status/complete")
def complete_setup():
    """Mark first run finished. Idempotent — re-posting never moves the stamp."""
    from backend.db.onboarding_state_store import OnboardingStateStore

    return {"onboarding_completed_at": OnboardingStateStore().mark_complete()}


_RESET_PROFILE = {
    "name": "",
    "nationalities": [],
    "countries_of_residence": [],
    "tax_residencies": [],
    "employment_types": [],
    "marital_status": "",
    "base_currency": "USD",
    "risk_tolerance": "moderate",
    "retirement_target_age": None,
    "date_of_birth": None,
    "monthly_net_income": None,
    "monthly_expenses": None,
    "liabilities": [],
    "notes": "",
    "fire_expected_return_pct": 7.0,
    "fire_withdrawal_rate_pct": 4.0,
    "fire_monthly_retirement_spending": None,
}


@router.post("/api/reset")
def reset_all_data():
    """Wipe all user data and return to blank-slate state.

    The file set comes from the single canonical ``core.portable`` manifest
    (shared with export/import) — never a second hand-listed enumeration. A
    manifest entry backed by the DB (evidence/price_history/instruments — plan 2
    task 7) has no file to reset; ``reset_bridged_stores()`` wipes its table
    instead, in the same pass.

    CONFIRM ASYMMETRY (FR-M6 — DEFERRED for owner sign-off, plan 3 task 9): unlike
    ``/api/data/import`` (which requires an explicit ``replace=true`` intent flag), this endpoint
    takes no confirm argument, yet it is now MORE destructive than before — it also wipes
    ``txn_audit`` / ``merge_journal`` (the transaction audit trail) via ``reset_bridged_
    stores`` -> ``portable_bridge.wipe_all``. Adding a symmetric ``confirm`` gate was DEFERRED
    (not done in the security fix wave) because it ripples to THREE frontend callers
    (``DemoBanner`` "start fresh", ``DataSection``; ``DataSettings`` has since been deleted), past the "endpoint +
    the single caller" bar the fix wave set for "do it now", and the audit-trail wipe is
    itself an already-flagged deliberate change awaiting owner sign-off. Flagged here so the
    asymmetry is not lost.
    """
    from backend.ai.assistant import SESSIONS
    from backend.core import portable
    from backend.db import portable_bridge
    from backend.paths import DATA_DIR
    from backend.services.portability import (
        export_bytes_for,
        reset_bridged_stores,
        reset_sessions,
        session_bundle_entries,
    )

    SESSIONS.clear()

    # Snapshot current data into a timestamped backup before wiping. export_bytes_for
    # sources a bridged (DB-backed) store from the DB, so this backup covers
    # evidence/price_history/instruments too — not just what shutil.copy2 could reach
    # on disk (those files no longer exist post-cutover).
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    backup_dir = DATA_DIR / "backups" / stamp
    backup_dir.mkdir(parents=True)
    for src in portable.PORTABLE_FILES:
        data = export_bytes_for(src)
        if data is not None:
            (backup_dir / src.name).write_bytes(data)
    # Chat sessions (Wave 5E: DB rows, not a directory to shutil.copytree any more) —
    # same pre-wipe backup role, reconstructed via the shared helper
    # services.portability.session_bundle_entries also uses for the .zip export.
    sessions_backup_dir = backup_dir / "sessions"
    sessions_backup_dir.mkdir(parents=True)
    for filename, data in session_bundle_entries().items():
        (sessions_backup_dir / filename).write_bytes(data)

    # Reset each NON-bridged portable store to its empty state (`[]`/`{}`). A bridged filename
    # has no file to write — reset_bridged_stores() below wipes its table instead (profile is
    # bridged too as of Wave 5; it is re-seeded with _RESET_PROFILE right after that wipe).
    # watchlist.json is one such bridged filename (plan 4b task 6): WatchlistStore is
    # DB-backed, so it goes through the `continue` above like every other bridged store —
    # its own _ensure_shape() reseeds the my_holdings default on the next read, same as
    # every fresh-DB read already does (WatchlistStore._load).
    for path in portable.PORTABLE_FILES:
        if path.name in portable_bridge.BRIDGED:
            continue
        path.write_text(portable.empty_contents_for(path.name))

    # Every DB-backed store portable_bridge.BRIDGED/_WIPE_FNS names (reference-data, money,
    # planning, investments — see that module's docstring for the full roster) — one
    # transaction. write_log/json_cutovers (migration/checkpoint machinery) and the
    # identity tables are deliberately untouched.
    reset_bridged_stores()

    # skills_state was wiped but the skill folders were not: without this every starter reads
    # as a disabled user skill and the onboarding handoff 422s ("Structured skill ... disabled").
    from backend.db.skill_store import SkillStore

    SkillStore().restore_starters()

    # profile is now a bridged (DB) store — reset_bridged_stores wiped its table; re-seed the
    # blank _RESET_PROFILE shape (base USD, empty strings) onboarding expects, exactly as the old
    # file-write branch did (Wave 5, JSON->DB).
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw(dict(_RESET_PROFILE))

    # The completion stamp outlives the wipe on its own (runtime_state is non-portable), and
    # is_fresh_setup() trusts it over the now-empty book — without this a reset landed in a
    # blank app with no way back into the wizard.
    from backend.db.onboarding_state_store import OnboardingStateStore

    OnboardingStateStore().clear()

    # Instance-local runtime mapping (excluded from the portable set): every connector's
    # conversation map goes; the connectors' settings and credentials stay.
    from backend.connectors.session_map import clear_all as clear_connector_sessions

    clear_connector_sessions()

    # Chat sessions (Wave 5E, JSON->DB) — was shutil.rmtree(SESSIONS_DIR) +
    # SESSIONS_DIR.mkdir(parents=True); now a table wipe, one transaction.
    reset_sessions()

    return {"ok": True}
