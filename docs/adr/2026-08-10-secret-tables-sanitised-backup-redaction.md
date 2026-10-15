---
title: SECRET_TABLES — table-level credential redaction for the sanitised backup download
status: Accepted
date: 2026-08-10
deciders: Claude (implementer agent, storage plan Wave 5D1) — the mechanism ADR
  `docs/adr/2026-08-10-connector-sessions-deferred-to-secrets-wave.md` flagged as a
  prerequisite, resolved here
---

# 2026-08-10. `SECRET_TABLES` — table-level credential redaction for the sanitised backup download

## Context

`core.sensitive` answers two questions about every sensitive dataset in this app: (1) may it be
handed to a caller, and (2) may a restore roll it back. Before this wave, question 1 had only a
FILE-level answer — `SECRET_FILES`, tested by `is_secret(path)` — because every credential-bearing
dataset (`auth.json`, `model_config.json`, and the ~16 connector-credential configs) still lived in
its own JSON file. `services.backup.write_sanitised_copy` (the handler behind the "download a
disaster-recovery backup" endpoint) walks a backup `.zip`'s members and strips any whose path
resolves into `SECRET_FILES` — the control that stops an authenticated GET from handing over the
session-signing key, the API bearer token, or a bank/broker credential. `onw.db` itself has never
been in `SECRET_FILES` (nothing secret has ever lived inside it), so it has always shipped WHOLE.

Wave 5D1's job is to move 15 connector-credential configs + `connector_sessions` off their JSON
files and into `onw.db` tables. Once ANY credential lives inside `onw.db`, shipping it whole in a
sanitised download would silently defeat the exact control described above — the file-path-based
skip has nothing to skip, since the credential no longer has its own path. This gap was found and
explicitly NOT worked around during Wave 5C (`docs/adr/2026-08-10-connector-sessions-deferred-to-
secrets-wave.md`): that task found `connector_sessions.json` was ALSO a `SECRET_FILES` member,
recognized that migrating it would create this exact hole with no replacement mechanism, and
deferred the whole ~16-store secrets slice to a dedicated, security-reviewed wave rather than
build an unreviewed fix under time pressure. This ADR is that follow-up decision.

`core.sensitive.NON_RESTORABLE_TABLES` already answers the PARALLEL question (2) at table
granularity — `services.backup.restore_backup` captures a fixed set of tables' current rows before
the `onw.db` file swap and re-applies them right after, because a whole-file skip cannot express
"revert this file except these tables." Question 1 had no equivalent: nothing let
`write_sanitised_copy` express "ship this file except these tables."

## Decision

We will add **`SECRET_TABLES: frozenset[str]`** to `backend/core/sensitive.py` — the table-level
counterpart of `SECRET_FILES` — plus an `is_secret_table(name)` helper, documented the same way as
`NON_RESTORABLE_TABLES`. Every `SECRET_TABLES` entry is unioned into `NON_RESTORABLE_TABLES` too
(secret ⊆ non-restorable, mirroring `SECRET_FILES ⊆ NON_RESTORABLE_FILES`).

`write_sanitised_copy` is rewired so that, for the `onw.db` zip member specifically (identified the
same way `restore_backup` already identifies it — resolved against `_live_db_path()`), it no longer
copies the member's bytes verbatim. Instead a new `_sanitise_db_bytes` helper: writes the member's
bytes to a DISPOSABLE temp copy (never the live database, never the source zip's own bytes — the
original archive on disk is untouched); opens it with `sqlite3` directly (not the app's shared
SQLAlchemy engine, which only ever points at the LIVE database — this function operates on an
arbitrary PAST backup's snapshot, a standalone file with no relationship to the live connection
pool, mirroring `_sqlite_consistent_copy`'s own precedent in the same file rather than
`_capture_non_restorable_rows`'s engine-bound one); runs `PRAGMA secure_delete = ON` then `DELETE
FROM <table>` for every `SECRET_TABLES` table that actually exists in that snapshot (a table absent
from an older backup — or a future secret table added after some backups were already taken — is a
correct no-op, not a failure); commits; then `VACUUM`s. Each stripped table is reported in the same
`stripped` list an ordinary secret file lands in, as `"<db filename>:<table>"`.

`secure_delete` + `VACUUM`, not a bare `DELETE`, is the load-bearing detail: SQLite's ordinary
`DELETE` marks a row's pages as free but does not overwrite their bytes, so a "sanitised" file
produced by a naive `DELETE` alone could still yield a credential to anyone who opened it with a hex
editor or ran `strings` against it — invisible to a `SELECT`, but not actually gone from the file.
`secure_delete` overwrites freed content with zeros as part of the delete; the closing `VACUUM`
rebuilds the file from only its live pages, which cannot include stale freed content either way. The
combination was verified empirically (see below), not assumed.

Ordinary (non-`onw.db`) secret files keep being stripped by path exactly as before — no change to
that branch.

## Consequences

### Positive
- The credential-exfiltration control `write_sanitised_copy` already provided for JSON-file secrets
  now extends to table-resident secrets with the same guarantee, closing the gap Wave 5C found and
  correctly refused to paper over.
- `SECRET_TABLES ⊆ NON_RESTORABLE_TABLES` is enforced by construction (a `|` union, not two lists a
  future edit could let drift apart) — a table added to `SECRET_TABLES` automatically gains restore
  protection too, matching the file-level relationship exactly.
- `secure_delete` + `VACUUM` means the sanitised artifact is safe against a more sophisticated
  attacker than "runs a normal SQL client" — verified by a raw-byte forensic scan of the sanitised
  file finding zero recoverable copies of seeded test secrets, not just zero rows in a `SELECT`.

### Negative
- `write_sanitised_copy` now does real work (write a temp file, run SQL, VACUUM) for every sanitised
  download instead of a pure zip-copy loop — a cost proportional to `onw.db`'s size, not the amount
  of secret data in it. Acceptable: this endpoint is an occasional operator action, not a hot path.
- `VACUUM` briefly needs up to ~2x the database's disk space in the temp directory (SQLite rebuilds
  the file rather than editing in place). Mitigated by staging in `BACKUPS_DIR` (already the working
  directory for backup-sized temp files, per `_zip_live_db`'s own precedent) rather than a
  potentially smaller system temp partition.
- Adding a table to `SECRET_TABLES` requires that table to independently make sense as "the whole
  row is secret, in a namespace/list shape a blanket `DELETE FROM <table>` can safely wipe" — this
  mechanism does not support a table with a MIX of secret and non-secret rows or columns. Wave 5D1's
  two tenants (`secret_config`, `mcp_clients`) were designed with this constraint in mind
  (`secret_config` was kept separate from the non-secret `runtime_state` for exactly this reason);
  a future secret dataset with mixed sensitivity would need a different, column-level mechanism this
  ADR does not build.

### Follow-ups
- `tests/test_sensitive_paths.py`'s `test_non_restorable_tables_matches_db_resident_non_restorable_
  files` asserts `NON_RESTORABLE_TABLES == {four specific table names}` by exact equality — this now
  fails by construction (`secret_config`/`mcp_clients` joined the set). The test's `migrated` dict
  assumes a 1:1 file→table mapping that doesn't fit `secret_config` (it aggregates 15 JSON files
  into one table) — the test catch-up needs more than two more dict entries; it needs a shape that
  can express "these N files were absorbed into this ONE table." A new assertion pinning
  `SECRET_TABLES` membership (mirroring `test_credential_files_are_secret`) should join it in the
  same pass.
- The next secret dataset to migrate (`model_config`, Wave 5D2 — also a `SECRET_FILES` member today)
  should land as one more `secret_config` namespace row, joining `SECRET_TABLES` automatically via
  the table it already shares with the 15 stores this wave migrated — no new mechanism needed.
- `auth.json` (Wave 5D2) is a materially different shape (a single well-known file holding the
  password hash + session-signing key, not a namespace-per-connector table) — whether it fits
  `secret_config` or needs its own table is a decision for that wave, not pre-judged here.

## Alternatives considered

- **Keep shipping `onw.db` whole, exclude it from sanitised downloads entirely** — rejected: the
  database holds the vast majority of the user's actual disaster-recovery data (the whole money
  core, planning, investments, …); refusing to ship it defeats the DR-download feature's own
  purpose far more broadly than the ~2 tables actually needing protection.
- **Row-level `DELETE`, no `secure_delete`/`VACUUM`** — rejected after verifying empirically that a
  bare `DELETE` leaves the deleted bytes physically present in the file (SQLite's freelist), passing
  a naive "the SELECT returns nothing" test while still failing the actual security property this
  control exists for.
- **A `NON_RESTORABLE_TABLES`-style "capture and reapply around the swap" pattern reused verbatim**
  — rejected: that pattern answers "what should the LIVE database look like after a restore," a
  question with one live database and one swap. This question is "what should a DOWNLOADED COPY
  look like," which needs an actual redacted copy to exist as bytes on the wire — a different shape
  of problem, even though both start from the same `core.sensitive` frozenset pattern.
- **One shared `runtime_state` namespace row per connector secret, no new `secret_config` table** —
  rejected (see `db/tables.py`'s own docstring on `secret_config`): `SECRET_TABLES`/
  `NON_RESTORABLE_TABLES` operate at table granularity, the only granularity this codebase's
  backup/restore policy sets support; sharing `runtime_state` would force every entry in it
  (including `harness_state`, a mere timestamp) to be stripped from every sanitised download and
  protected from every restore as collateral, with no way to express "checkpoint-excluded but not
  secret" and "checkpoint-excluded and secret" as two different things in the same table.

## Agent provenance

- **Decided by:** Claude (implementer agent, Wave 5D1 of the JSON→DB storage migration). Lane:
  worktree `storage-plan1`.
- **Alternatives weighed:** exclude `onw.db` from downloads entirely (rejected — defeats the
  feature); bare `DELETE` with no forensic cleanup (rejected — empirically leaves bytes recoverable);
  reuse the `NON_RESTORABLE_TABLES` capture/reapply pattern (rejected — wrong shape of problem);
  share `runtime_state` instead of a new `secret_config` table (rejected — no way to express
  secret-vs-non-secret within one table at the granularity these policy sets support).
- **Driving artifacts:** `docs/adr/2026-08-10-connector-sessions-deferred-to-secrets-wave.md` (the
  prerequisite that named this exact gap and deferred it here); `docs/superpowers/plans/2026-08-10-
  json-to-db-wave5.md`'s "5D design (secrets)" section (specified the shape of this mechanism
  in advance); `backend/core/sensitive.py`, `backend/services/backup.py::write_sanitised_copy` /
  `_sanitise_db_bytes` (the implementation); a scratch-DB proof seeding real secret rows into both
  new tables, taking a real backup, sanitising it, and asserting both tables are empty (0 rows) in
  the sanitised copy while a raw-byte forensic scan finds zero recoverable copies of the seeded
  secrets and a control table (`accounts`) is untouched.
- **Why this way (one line):** the file-level credential-exfiltration control this app already had
  needed an exact table-level twin before any credential could safely move into `onw.db`, and a bare
  `DELETE` alone would have shipped a control that looked correct under a `SELECT` while still
  leaking the bytes.
