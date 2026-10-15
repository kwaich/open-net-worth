"""
Turn checkpoints — full-state rollback for AI edits.

Every AI turn that mutates data captures the *pre-write* content of each file it
touches, so the user can restore the system to the state before any message —
like Claude Code's /rewind. See .claude/rfcs/turn-checkpoints.md.

Mechanism
─────────
- A `CheckpointContext` is active (via a ContextVar) for the duration of an
  `assistant.run()` turn.
- The single write choke point `JsonStore._save_raw()` (and the data.csv writer)
  calls `ctx.snapshot_before_write(path)` *before* overwriting a file. The first
  write to each file per turn gzips + content-addresses the pre-write bytes into a
  shared blob pool; the checkpoint manifest just references blob hashes. Unchanged
  files are free across checkpoints (dedup) — this is what makes it incremental.
- Restore takes a `pre_restore` safety snapshot first (so any restore is itself
  undoable — its own DB half is FAIL-CLOSED, see below), refuses to run while a
  turn is active for the session, then STAGES every file's restored bytes to a
  temp path (durably) BEFORE the DB replay commits, and only RENAMES each staged
  temp into place — the one remaining split-world window — after that replay
  succeeds (Plan 3 task 1 restore hardening; see `_stage_restore_files` /
  `_commit_staged_restore_files`).

DB-backed stores (spec D7) use a parallel row-level mechanism instead of file blobs:
`core.io.fire_db_write_hook` fires unconditionally on every DB-store write;
`CheckpointContext.record_db_before` records the BEFORE image of each (table, pk) into
the `write_log` table, immediately and durably (its own short transaction — images
survive a crash even before `finalise()`). This is the **hybrid period**: one turn can
write both a JSON store and a DB store, and `restore()` reverts both — DB replay
first in one transaction, files staged-then-committed last — or neither (a DB replay
failure raises before any file is touched, and any already-staged temp is cleaned
up). `_EXCLUDED_TABLES` is the DB mirror of `_EXCLUDED_PATHS`.

The checkpoint MANIFEST itself (spec D7, plan 4e task 2) lives on `CheckpointIndexStore`
(`backend/db/checkpoint_index_store.py`, a `PayloadTableStore` over the `checkpoints`
table) — replacing the old `CHECKPOINTS_INDEX` JSON file. `CHECKPOINTS_BLOBS` (the Tier-4
gzip blob pool for file snapshots) is UNRELATED and stays exactly as it was — only the
manifest moved.

Storage is best-effort: snapshotting never raises into the chat path — with ONE
deliberate exception, `_capture_db_pre_restore`'s own write_log image capture
(`record_db_before(fail_closed=True)`), which RAISES instead: an undo snapshot that
silently failed to record a row would let `restore()` go on to mutate live data
behind a safety net that can't actually catch it, which is worse than refusing.

Package layout (plan 4e task 2 — split out of a single ~1370-line module, pure code
motion, no logic rewritten)
─────────────────────────────────────────────────────────────────────────────────────
- `_policy.py`   — MAX_CHECKPOINTS, the excluded-paths/prefixes/tables sets, _COMPANIONS.
- `_context.py`  — CheckpointContext, begin_turn/end_turn/get_active, the pre-write hook
                    registration, the pin/unpin refcounting.
- `_index.py`    — the CheckpointIndexStore-backed manifest: list/count/find/prune/orphan.
- `_restore.py`  — restore()/preview() and everything the restore path needs (staged file
                    commit, write_log replay, blob/write_log GC).

This file is a compatibility re-export shim: every name any of this package's ~45
dependent files touches — `from backend.ai import checkpoint as ckpt` + dotted access
(`ckpt.restore`, `ckpt.begin_turn`, `ckpt._EXCLUDED_PATHS`, …), or
`from backend.ai.checkpoint import <name>` directly — is re-exported here so NONE of
those call sites had to change. Cross-submodule calls inside the package go through
`from . import _sibling` (never through this file), both to avoid a circular import and
because a couple of module-level "constants" here (`DATA_DIR`, `CHECKPOINTS_BLOBS`,
`MAX_CHECKPOINTS`, `new_id`, `durable_replace`, `_replay_write_log`) are monkeypatched by
existing tests via THIS package's own attribute — a submodule that needs one of those
reads it back through `from backend.ai import checkpoint as ...` at call time so a patch
applied here is actually observed, instead of holding its own separate, never-patched copy.
"""

from __future__ import annotations

from backend.core.ids import new_id
from backend.core.io import (
    durable_replace,
    register_db_write_hook,
    register_write_hook,
)
from backend.paths import CHECKPOINTS_BLOBS, DATA_DIR

from ._context import (
    CheckpointContext,
    _active_ctx,
    _active_turns,
    _pin_checkpoint,
    _pinned_ids,
    _session_turn_active,
    _snapshot_active,
    _snapshot_db_active,
    _unpin_checkpoint,
    begin_turn,
    end_turn,
    get_active,
)
from ._index import (
    _append_index,
    _filtered_checkpoints,
    _index_lock,
    _mark_orphaned_after,
    _prune_old_checkpoints,
    _public,
    _read_index,
    _write_index,
    count_checkpoints,
    delete_checkpoint,
    find_checkpoint,
    list_checkpoints,
    turn_anchors,
    turn_receipts,
    turn_restores,
    turn_superseded,
)
from ._policy import (
    _COMPANIONS,
    _EXCLUDED_PATHS,
    _EXCLUDED_PREFIXES,
    _EXCLUDED_TABLES,
    CHECKPOINTS_INDEX,
    MAX_CHECKPOINTS,
)
from ._restore import (
    _blob_bytes,
    _blob_path,
    _capture_db_pre_restore,
    _capture_pre_restore_snapshot,
    _cleanup_staged_restore_files,
    _commit_staged_restore_files,
    _count_rows,
    _db_preview_rows,
    _gc_blobs,
    _gc_write_log,
    _replay_write_log,
    _restore_dest,
    _session_label,
    _single_pk_col,
    _stage_restore_files,
    _truncate_session_history,
    preview,
    restore,
)

__all__ = [
    # Public-ish surface — the names api/ai callers actually use.
    "CheckpointContext",
    "MAX_CHECKPOINTS",
    "begin_turn",
    "end_turn",
    "get_active",
    "list_checkpoints",
    "count_checkpoints",
    "find_checkpoint",
    "turn_anchors",
    "turn_receipts",
    "turn_restores",
    "turn_superseded",
    "delete_checkpoint",
    "preview",
    "restore",
    # Re-exported so `from backend.ai.checkpoint import _name` and dotted access
    # (`ckpt._name`) keep resolving for the ~45 dependent files/tests this split must
    # not disturb (module docstring above) — never truly "public", but a name every one
    # of those files was already free to touch through the old flat module.
    "new_id",
    "durable_replace",
    "register_db_write_hook",
    "register_write_hook",
    "CHECKPOINTS_BLOBS",
    "DATA_DIR",
    "CHECKPOINTS_INDEX",
    "_COMPANIONS",
    "_EXCLUDED_PATHS",
    "_EXCLUDED_PREFIXES",
    "_EXCLUDED_TABLES",
    "_active_ctx",
    "_active_turns",
    "_pin_checkpoint",
    "_pinned_ids",
    "_session_turn_active",
    "_snapshot_active",
    "_snapshot_db_active",
    "_unpin_checkpoint",
    "_append_index",
    "_filtered_checkpoints",
    "_index_lock",
    "_mark_orphaned_after",
    "_prune_old_checkpoints",
    "_public",
    "_read_index",
    "_write_index",
    "_blob_bytes",
    "_blob_path",
    "_capture_db_pre_restore",
    "_capture_pre_restore_snapshot",
    "_cleanup_staged_restore_files",
    "_commit_staged_restore_files",
    "_count_rows",
    "_db_preview_rows",
    "_gc_blobs",
    "_gc_write_log",
    "_replay_write_log",
    "_restore_dest",
    "_session_label",
    "_single_pk_col",
    "_stage_restore_files",
    "_truncate_session_history",
]
