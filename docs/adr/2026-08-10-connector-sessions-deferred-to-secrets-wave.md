---
title: connector_sessions.json stays a file — deferred to the secrets-reviewed wave
status: Accepted
date: 2026-08-10
deciders: Claude (implementer agent, storage plan Wave 5C) — flagged for repo owner / a future security-reviewed wave, not resolved unilaterally
---

# 2026-08-10. `connector_sessions.json` stays a file — deferred to the secrets-reviewed wave

## Context

Wave 5C's brief named `connector_sessions` as one of six runtime-state stores to migrate onto
the DB: "instance-local runtime mapping; `api/setup.py` resets it to `"{}"` and it is NOT in the
portable set. Find its store/readers." Investigation found its shape
(`backend/connectors/session_map.py` — a flat dict, `{"<connector>:<external_user_id>":
"<session-uuid>", "<connector>:<external_user_id>:effort": "high"|"medium"|"low"}`) and readers
(`get_or_create_session`/`reset_session`/`list_user_ids`/`get_effort`/`set_effort`, plus a
best-effort reverse lookup in `ai/checkpoint/_restore.py::_session_label` for a restore
preview's "Telegram"/"web" label). Both its checkpoint-EXCLUDED status
(`ai.checkpoint._policy._EXCLUDED_PATHS`) and its non-portable status
(`core.portable.PORTABLE_FILES` never lists it) confirmed the brief's own description.

The brief's description did NOT mention — and the investigation independently found — that
`CONNECTOR_SESSIONS_JSON` is ALSO a member of `core.sensitive.SECRET_FILES`
(`backend/core/sensitive.py`), the SAME frozenset holding `auth.json`, `model_config.json`, and
every one of the ~16 connector-credential config files (Telegram/Slack/Discord/WhatsApp bot
tokens, Plaid/GoCardless/SimpleFIN/IBKR/Alpaca/… broker and bank credentials). That
classification is load-bearing, not decorative: `services.backup.write_sanitised_copy` — the
handler behind the "download a disaster-recovery backup" endpoint — walks a backup `.zip`'s
members and strips any whose path resolves into `SECRET_FILES`, specifically so an
authenticated GET to that endpoint cannot exfiltrate the session-signing key, the API bearer
token, or a bank/broker credential. `connector_sessions.json` earning the same classification
means whoever wrote `core/sensitive.py` judged its contents — which external chat-platform user
IDs (a Telegram numeric chat ID, a WhatsApp phone number, …) are paired to this instance, and
their internal Lucius session ids — sensitive enough to withhold from that same download, even
though it holds no credential in the traditional bearer-token sense.

`write_sanitised_copy`'s stripping mechanism operates PURELY on the ZIP MEMBER'S FILE PATH,
tested against `core.sensitive.is_secret(path)`. `onw.db` (the SQLite database every already-
migrated store lives in) is itself ONE zip member — `is_secret(DATA_DIR / "onw.db")` is `False`
today (nothing secret has ever lived inside `onw.db`, since every `SECRET_FILES` member is
still, deliberately, a separate file — the whole "~16 connector configs... security-review gate"
slice of this migration is EXPLICITLY deferred to a later wave, per
`docs/superpowers/plans/2026-08-10-json-to-db-wave5.md`'s own store inventory). There is no
table-level equivalent of this redaction anywhere in this codebase — `core.sensitive.
NON_RESTORABLE_TABLES` exists, but it answers a DIFFERENT question ("must a `restore_backup`
roll this table's CURRENT rows backward" — grants/audit/mcp-write-log/automation-runtime), not
"must this table's rows be stripped from an outbound sanitised copy."

Migrating `connector_sessions` into `onw.db` as an ordinary table today would therefore silently
defeat an existing, deliberate security control: the sanitised-backup-download endpoint would
stop stripping this data with no replacement mechanism in place, and nothing in this migration's
test suite or review gate would catch it — `write_sanitised_copy` would simply keep working, one
security guarantee quieter.

## Decision

We will NOT migrate `connector_sessions` in this pass. `backend/connectors/session_map.py`
keeps its hand-rolled `json.loads`/`Path.write_text` I/O against `CONNECTOR_SESSIONS_JSON`,
unchanged. This is the ONE store of the six named in the Wave 5C brief this task does not
complete — flagged per this codebase's own stated guardrail for exactly this situation ("If ANY
store turns out to be secret-bearing, security-sensitive, or vestigial... STOP and report it
rather than guessing — several of these have permission/security siblings that belong in a
later security-reviewed wave").

`connector_sessions` belongs with Wave 5D ("secrets/config — checkpoint-EXCLUDED, chmod-parity
via the 0600 DB: auth, model_config, app_config, ~16 connector configs. security-review gate" —
the wave plan's own words), not Wave 5C, regardless of which wave's brief happened to name it
first. Migrating it correctly needs a real design decision this task is not positioned to make
unilaterally: either (a) a table-level redaction mechanism parallel to `NON_RESTORABLE_TABLES`
(a `SECRET_TABLES` frozenset plus teaching `write_sanitised_copy` how to produce a row-redacted
copy of a live SQLite file, not just skip a whole zip member), or (b) a considered judgment call
that this particular table's contents don't actually need that protection (chat-platform user
IDs are lower-sensitivity than a bearer credential) and the `SECRET_FILES` classification should
simply be dropped for it — either way, a security-reviewed decision, not a storage-migration
implementation detail.

## Consequences

### Positive
- No security regression: the sanitised-backup-download endpoint keeps stripping
  `connector_sessions.json` exactly as it does today.
- The real design question (redact-in-DB mechanism vs. reclassify-as-non-secret) gets decided
  once, deliberately, alongside the ~17 other connector stores it shares a security posture
  with — rather than being implicitly answered by which files happened to get migrated in which
  order.

### Negative
- Wave 5C's brief is not fully completed by this task — 5 of the 6 named stores are done; this
  one remains a file. The wave's "zero runtime `.json`" goal is not yet reached.
- `backend/connectors/session_map.py` is now the only Wave-5C-adjacent module still doing
  hand-rolled file I/O in an otherwise-migrated area of the codebase, until Wave 5D (or an
  earlier deliberate pass) picks it up.

### Follow-ups
- Wave 5D (or a dedicated follow-up) should design the `SECRET_TABLES`-equivalent redaction
  mechanism (or the reclassification) BEFORE migrating `connector_sessions` (and, by the same
  reasoning, before migrating any other `core.sensitive.SECRET_FILES` member) into `onw.db`.
- `ai/checkpoint/_restore.py::_session_label`'s direct file read of `CONNECTOR_SESSIONS_JSON`
  is untouched by this task and needs no change until `connector_sessions` itself migrates.

## Alternatives considered

- **Migrate it anyway, accepting the sanitised-backup gap** — rejected: this is exactly the
  silent security regression the mission's own safety guardrail exists to prevent; "storage
  migration" is not license to quietly weaken an existing credential-exfiltration control.
- **Migrate it AND build the `SECRET_TABLES` redaction mechanism in the same pass** — rejected:
  designing and implementing a new security control (teaching `write_sanitised_copy` to redact
  rows inside a live SQLite file, not just skip whole zip members) is real, security-critical
  work that deserves its own design/implementation/`security-reviewer` gate — bundling it into a
  six-store storage-migration task, unreviewed, is the "do NOT guess on anything security-
  adjacent" case the mission explicitly calls out.
- **Reclassify `CONNECTOR_SESSIONS_JSON` out of `SECRET_FILES` unilaterally, then migrate** —
  rejected: this task cannot judge, on its own authority, that a mapping of external chat
  platform user IDs is safe to expose in a disaster-recovery download — that is exactly the kind
  of security-sensitive judgment call the mission says to flag rather than resolve by guessing.

## Agent provenance

- **Decided by:** Claude (implementer agent, Wave 5C of the JSON→DB storage migration). Lane:
  worktree `storage-plan1`.
- **Alternatives weighed:** migrate-and-accept-the-gap (rejected — a real, concrete security
  regression, not hypothetical); migrate-and-build-the-fix-now (rejected — out of scope for an
  unreviewed storage task, needs its own security gate); unilateral reclassification (rejected —
  not this task's judgment call to make).
- **Driving artifacts:** `backend/core/sensitive.py` (`SECRET_FILES`, `is_secret`,
  `NON_RESTORABLE_TABLES` — the existing mechanism this finding is about);
  `backend/services/backup.py::write_sanitised_copy` (the actual consumer of `is_secret`, and
  where the gap would land); `docs/superpowers/plans/2026-08-10-json-to-db-wave5.md` (Wave 5D's
  own "secrets/config... security-review gate" scope line, which is where this store belongs).
- **Why this way (one line):** a storage-migration task found a real credential-adjacent
  exposure gap it would create, and stopped rather than papering over it or building an
  unreviewed fix under time pressure.
