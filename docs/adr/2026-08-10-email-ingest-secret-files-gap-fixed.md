---
title: email_ingest.json's missing SECRET_FILES membership — fixed alongside its DB migration
status: Accepted
date: 2026-08-10
deciders: Claude (implementer agent, storage plan Wave 5D2a)
---

# 2026-08-10. `email_ingest.json`'s missing `SECRET_FILES` membership — fixed alongside its DB migration

## Context

Wave 5D2a's brief asked this task to migrate `auth`, `model_config`, `app_config`, and
`email_ingest` off their JSON files and onto `onw.db`, and to explicitly investigate whether
`email_ingest` "holds a credential" — treat it as SECRET like the connectors if so, non-secret
excluded if not.

`backend/db/email_config_store.py`'s own docstring answers this unambiguously: the config holds
`imap_password` — a bare IMAP account password, stored on disk and "NEVER returned to the
frontend" (the store's `public()` view exposes only a `has_password` boolean; `credentials()`,
the internal domain method that actually opens an IMAP connection, is the one place the plaintext
is read back). This is a real login credential to the user's mail account, not a lesser API token
— exactly the class of secret `core.sensitive.SECRET_FILES` exists to protect.

Checking `backend/core/sensitive.py`'s `SECRET_FILES` frozenset (the file-level "may this be
handed to a caller?" answer that `services.backup.write_sanitised_copy` enforces) found
`EMAIL_INGEST_JSON` **absent** — every one of its 15 connector-credential siblings
(`TELEGRAM_CONFIG_JSON`, `PLAID_CONFIG_JSON`, `ALPACA_CONFIG_JSON`, …) is listed, `AUTH_JSON` and
`MODEL_CONFIG_JSON` are listed, but `EMAIL_INGEST_JSON` was never added. This is a pre-existing
gap, not something this migration introduced: on every instance with email ingestion configured,
a `GET` to the sanitised disaster-recovery backup download has been shipping the operator's raw
IMAP password in the clear, unredacted, since `email_ingest.json` was first written — while every
structurally-identical connector credential file next to it was correctly stripped.

`EMAIL_INGEST_JSON` **was** already correctly `ai.checkpoint._policy._EXCLUDED_PATHS`-excluded
(never turn-mutable), so a `/rewind` was never at risk — this gap was specific to the sanitised
backup download's `SECRET_FILES` allowlist.

Wave 5D2a's own migration compounds the urgency of catching this now rather than later:
`db/migrate.py::_finish_cutovers` renames `email_ingest.json` to `email_ingest.json.migrated-<ts>`
once absorbed, and `core.sensitive._norms()` teaches `is_secret()`/`is_non_restorable()` to
recognise that renamed sibling **only when the ORIGINAL, un-suffixed name is itself a
`SECRET_FILES`/`NON_RESTORABLE_FILES` member** (a fix from Wave 5D1's own security review). Left
unfixed, the migration would have permanently frozen the gap: the renamed file would sit in
`DATA_DIR` forever holding the same plaintext, with no path — short of a second migration —
back to correct classification.

## Decision

`EMAIL_INGEST_JSON` is added to `core.sensitive.SECRET_FILES` in the same change as the DB
migration, not deferred. It automatically joins `NON_RESTORABLE_FILES` via the existing
`SECRET_FILES | {...}` union — a `restore_backup` must not resurrect a rotated IMAP password any
more than a sanitised download may leak the current one. No new table entry was needed on the DB
side: `email_ingest`'s new home (a namespace row in the pre-existing `secret_config` table) was
already a whole-table `SECRET_TABLES`/`NON_RESTORABLE_TABLES`/checkpoint-excluded member from
Wave 5D1, so the table-level protection is correct from the row's first write.

This is scoped narrowly: the fix is one frozenset membership addition (plus its own inline
comment explaining why), landing exactly where every other Wave 5D1/5D2a secret classification
decision already lives. It does not touch `write_sanitised_copy`'s mechanism, `is_secret()`'s
logic, or any other file's classification — those are all unaffected, already-correct code this
finding has no bearing on.

## Consequences

### Positive
- Closes a real, live credential-exfiltration gap: a sanitised backup download taken AFTER this
  change no longer includes a configured IMAP password.
- The `.migrated-<ts>` file this cutover produces inherits the correct secrecy immediately, via
  the existing `_norms()` mechanism — no follow-up fix needed once the rename has happened.
- Zero new mechanism: the fix rides entirely on machinery Wave 5D1 already built and
  security-reviewed (`SECRET_FILES`/`NON_RESTORABLE_FILES`/`SECRET_TABLES` and their `_norms()`
  migrated-suffix handling).

### Negative
- Every sanitised backup taken BEFORE this change, while email ingestion was configured, still
  contains the plaintext IMAP password in its zip archive on whatever disk it was downloaded to
  — this fix cannot retroactively redact an archive that already left the server. Out of this
  task's reach (no record exists of which past downloads happened, or where they were saved);
  worth a release-notes callout if this instance's backups have ever been shared/exported.

### Follow-ups
- None identified for `email_ingest` itself. The broader lesson — a file holding an obvious
  credential can still be missing from `SECRET_FILES` if no one wired it in when the file was
  first created — is exactly what `tests/test_sensitive_paths.py::test_credential_files_are_secret`
  exists to catch prospectively; it does not currently include `EMAIL_INGEST_JSON` in its checked
  set (deferred to the Wave 5D2a test catch-up, alongside the rest of that file's known gaps —
  see `docs/superpowers/plans/2026-08-10-json-to-db-wave5.md`'s progress ledger).

## Alternatives considered

- **Leave `EMAIL_INGEST_JSON` unclassified, migrate anyway** — rejected: this would freeze the
  gap permanently (see Context) and directly contradicts the mission's own instruction to
  classify `email_ingest` correctly rather than default to "not secret."
- **Fix it as a separate, later change, unrelated to this migration** — rejected: this migration
  is the exact moment the file's contents become a renamed, permanent sibling on disk; separating
  the fix from the migration only widens the window where a sanitised download is wrong, for no
  benefit (the fix is a one-line, fully-scoped, non-risky addition to an already-reviewed
  mechanism, not new security design that needs its own gate).
- **Also reclassify `email_ingest`'s new table row into its own dedicated table** (mirroring
  `mcp_clients`'s "genuinely list-shaped, gets its own table" precedent) — rejected: `email_ingest`
  is namespace-singleton shaped exactly like `auth`/`model_config`/every connector config, so it
  fits `secret_config` precisely; a dedicated table would be shape-mismatched extra structure
  with no benefit, and `SECRET_TABLES` already protects `secret_config` as a whole.

## Agent provenance

- **Decided by:** Claude (implementer agent, Wave 5D2a of the JSON→DB storage migration). Lane:
  worktree `storage-plan1`.
- **Alternatives weighed:** leave unclassified (rejected — freezes a real gap permanently);
  fix separately/later (rejected — needlessly widens the exposure window for a fully-scoped,
  low-risk fix); a dedicated table (rejected — shape mismatch, no benefit over the existing
  `secret_config` namespace-row pattern).
- **Driving artifacts:** `backend/core/sensitive.py` (`SECRET_FILES`, `_norms`, the
  `.migrated-<ts>` inheritance mechanism this finding directly interacts with);
  `backend/db/email_config_store.py` (the store docstring that names `imap_password` as a
  disk-stored, never-returned-to-frontend secret); `backend/services/backup.py::write_sanitised_copy`
  (the actual consumer this gap affected); `tests/test_sensitive_paths.py` (the drift guard that
  should have caught this and — per the follow-up above — still needs `EMAIL_INGEST_JSON` added
  to its checked set).
- **Why this way (one line):** a routine storage migration investigation surfaced a real,
  live credential-exposure gap in an unrelated existing mechanism, and the fix was small, scoped,
  and low-risk enough to land in the same change rather than being flagged-and-deferred.
