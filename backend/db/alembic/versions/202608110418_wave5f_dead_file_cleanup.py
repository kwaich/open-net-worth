"""wave5f dead-file cleanup: retire confirmed-dead runtime `.json` files — orphans from
deleted features, and stale one-off sidecars from past manual/scripted edits — as
`<name>.removed-<ts>` (non-destructive rename, never delete). Wave 5F (JSON->DB, the
zero-`.json` finisher).

This is NOT an absorb: none of these files' bytes end up anywhere in the database, so
they are not routed through `json_cutovers`/`_finish_cutovers` (that ledger's contract —
"these bytes now live, hash-verified, in a table" — does not hold for something that was
never migrated). Every candidate below was individually grep-confirmed to have ZERO
readers anywhere in `backend/` before being added to either list; this revision does not
discover new candidates dynamically — a hardcoded EXACT-name list and two narrow
substring checks (`.pre-`, `_migration_`) are the whole guard, so a future unrelated file
can never be swept up by accident, and this revision only ever runs ONCE (tracked by
Alembic like every other revision), so there is no ongoing sweep to worry about either.

Four categories:

1. **DATA_DIR orphans from already-deleted features** — `code_grants.json` (`run_code`'s
   per-conversation grant tier, deleted in `f581b86d`), `mcp_grant.json`/
   `mcp_proposals.json` (the inbound-MCP T0/T1/T2 authorization tier, deleted in
   `50626ce2`) — see `docs/adr/2026-08-10-mcp-grant-code-grants-mcp-proposals-already-
   deleted.md` (Wave 5D2b), which found the SAME three names already gone from the
   codebase months before this wave started.

2. **DATA_DIR stale one-off sidecar FILES** — matched by substring, not an exact list,
   since each carries a run-specific timestamp: `*.pre-*.json` (a one-off safety copy
   made before a risky in-place edit — `backend/scripts/retire_agents.py`'s own
   `_backup()` helper writes exactly this `<stem>.pre-retire-agents-<stamp><suffix>`
   shape; this worktree's own data also carries one
   `email_ingest.pre-cursor-reset-<stamp>.json` from an untracked manual edit, same
   convention) and `*_migration_*.json` (a one-shot migration script's own "receipt of
   having run" marker — `backend/scripts/migrate_automations_v3.py`'s `RECEIPT_NAME` and
   its three siblings below, same four CLI-only utilities as category 4).

3. **CACHE_DIR legacy/vestigial** — `pending_plans.json` (the pre-2026-07-30 CACHE_DIR
   location; the live DATA_DIR copy was already absorbed byte-identically by Wave 5D2b's
   revision 0031 — see `db/pending_plan_store.py`'s own history and the
   mcp-grant/code-grants ADR's "Separately" section) and `routing_decisions.json` (the
   cross-provider auto-routing engine that wrote it was removed in `5abdb183`, well
   before this wave — found while investigating this wave's own CACHE_DIR watermark
   files, see revision 0037's docstring).

4. **DATA_DIR stale one-off backup DIRECTORIES** — found by this wave's own final
   whole-tree audit re-run (categories 1-3 alone left 25 `.json` files surviving inside
   four dated backup trees). Matched by DIRECTORY-NAME PREFIX, each one the literal
   `BACKUP_PREFIX` module constant of one of category 2's own four one-shot scripts —
   `backend/scripts/migrate_automation_execution_to_lucius.py`
   (`"automation-lucius-execution-backup-"`),
   `backend/scripts/migrate_automation_lucius_cleanup_v2.py`
   (`"automation-lucius-cleanup-v2-backup-"`),
   `backend/scripts/migrate_automation_trigger_revisions.py`
   (`"automation-trigger-revision-backup-"`), and
   `backend/scripts/migrate_automations_v3.py` (`"automation-migration-v3-backup-"`) —
   confirmed by grep to be each script's OWN whole-directory pre-edit safety copy, the
   directory-shaped sibling of category 2's per-file `.pre-<action>-<stamp>` convention.
   The WHOLE directory is renamed as one unit (not walked file-by-file) — same
   non-destructive contract, coarser grain, matching how each script produced it as one
   atomic backup. Two OTHER dated directories this same audit found —
   `_repair_backup_2026-06-15T2155/` and
   `automation-migration-v3-poststart-recovery-20260801T202918Z/` — have NO comparably
   verifiable script provenance (no `BACKUP_PREFIX`-style constant anywhere in
   `backend/` names either) and are DELIBERATELY left untouched here: this revision's own
   safety bar is "grep-confirm zero readers AND a clear origin", not merely "old-looking
   and unread" — flagged in the wave's own report for an owner's manual look instead.

Idempotent by construction if this revision is retried after a partial failure (a crash
mid-loop, or a later revision in the same `upgrade head` batch failing and triggering the
fail-closed backup restore): an already-renamed file or directory no longer matches its
exact name/prefix/substring pattern on the next run, so it is silently skipped, never
double-renamed or errored on.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-10
"""

from datetime import datetime, timezone
from pathlib import Path

revision = "202608110418_wave5f_dead_file_cleanup"
down_revision = "202608110417_wave5f_cache_state_cutover"
branch_labels = None
depends_on = None

# Category 1 — exact orphan filenames, DATA_DIR only.
_DEAD_EXACT_DATA = ("code_grants.json", "mcp_grant.json", "mcp_proposals.json")

# Category 3 — exact legacy/vestigial filenames, CACHE_DIR only.
_DEAD_EXACT_CACHE = ("pending_plans.json", "routing_decisions.json")

# Category 4 — verified BACKUP_PREFIX constants from the same four one-shot scripts
# named in category 2 above (each grep-confirmed against that script's own source).
_DEAD_BACKUP_DIR_PREFIXES = (
    "automation-lucius-execution-backup-",
    "automation-lucius-cleanup-v2-backup-",
    "automation-trigger-revision-backup-",
    "automation-migration-v3-backup-",
)


def _is_stale_sidecar(name: str) -> bool:
    """Category 2: a one-off `.pre-<action>-<stamp>.json` safety copy, or a
    `*_migration_*.json` one-shot script receipt. Substring, not a full glob — matches
    this revision's own docstring (`*.pre-*.json`, `*_migration_*.json`)."""
    return ".pre-" in name or "_migration_" in name


def _rename_removed(path: Path, stamp: str) -> None:
    """Non-destructively retire a confirmed-dead file OR directory: rename, never
    delete — mirrors `backend/db/migrate.py`'s own `.migrated-<ts>` rename, under the
    honest label for "confirmed dead, kept for manual recovery" instead of "now lives in
    the database". `Path.rename` moves a directory as one atomic unit exactly like a
    file, so category 4 below needs no special-casing here.

    IDEMPOTENT (fixed 2026-08-25). The directory sweep below matches on
    ``name.startswith(_DEAD_BACKUP_DIR_PREFIXES)``, and a directory ALREADY renamed to
    ``X.removed-<ts>`` still starts with that prefix — so every re-run appended another
    stamp. Four entries in the owner's live DATA_DIR had accumulated seven, and the next
    rename failed with ``OSError: [Errno 63] File name too long``, which aborts the
    migration and therefore the boot. Anything already carrying the marker is left alone."""
    if ".removed-" in path.name:
        return
    path.rename(path.with_name(f"{path.name}.removed-{stamp}"))


def upgrade() -> None:
    from backend.core.paths import CACHE_DIR, DATA_DIR

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for name in _DEAD_EXACT_DATA:
        p = DATA_DIR / name
        if p.exists():
            _rename_removed(p, stamp)

    for name in _DEAD_EXACT_CACHE:
        p = CACHE_DIR / name
        if p.exists():
            _rename_removed(p, stamp)

    for p in sorted(DATA_DIR.glob("*.json")):
        if _is_stale_sidecar(p.name):
            _rename_removed(p, stamp)

    for p in sorted(DATA_DIR.iterdir()):
        if p.is_dir() and p.name.startswith(_DEAD_BACKUP_DIR_PREFIXES):
            _rename_removed(p, stamp)


def downgrade() -> None:
    """No-op: a rename is this revision's only effect, and — matching every other
    cutover's own downgrade in this migration family — a filesystem side effect is never
    automatically reversed by `downgrade()`. The `.removed-<ts>` files are the manual
    restore path if one is ever needed."""
