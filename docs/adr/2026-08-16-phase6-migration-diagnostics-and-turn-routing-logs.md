---
title: Phase 6 migration diagnostics — conservative scripts and one turn-routing log
status: Accepted
date: 2026-08-16
deciders: orchestrating Claude agent + repo owner (cross-channel-attachments RFC, Phase 6)
---

# Phase 6 migration diagnostics — conservative scripts and one turn-routing log

Implements Phase 6 (items 31-32) of
`.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`.

## Context

Item 31 asks for dry-run-first migration/diagnostic scripts over four categories of
legacy pre-RFC data (bare model refs, inline transcript images, legacy proactive
strings, mixed statement rows). Item 32 asks for one coherent structured
routing/context diagnostic record per turn, with no raw bytes, OCR bodies, or
credentials in it.

Two things made both items non-routine:

1. This repo's ~40 existing `backend/scripts/*.py` migrations run against the live
   `DATA_DIR`/`ONW_DATABASE_URL` by default and gate only the DESTRUCTIVE half behind
   `--apply` (see `migrate_checkpoint_blobs_external_id.py`, `dedup_cost_log.py`). That
   convention is a deliberate, accepted trade-off for an OPERATOR running a script by
   hand. It is the wrong default for a script an AGENT might invoke: even a read-only
   dry-run scan would, by that convention, silently connect to the operator's real
   `~/.open-net-worth` install the instant it runs with no arguments.
2. The RFC's diagnostic fields (canonical provider/model, attachment ids/kinds/counts,
   capability source, route, reader model + cache hit, token budget report, retry count)
   were mostly ALREADY computed somewhere, but surfaced through three separate ad-hoc
   `logger.info`/`logger.warning` calls with no shared schema —
   `context_budget.py`'s per-round `"context_preflight ..."` line (token budget only),
   `assistant.py`'s `"capability_retry ..."` line (retry only), and
   `"context_preflight_refused ..."` (refusal only).

## Decision

### 1. A stricter, script-family-specific live-data guard — `backend/scripts/_live_data_guard.py`

Every Phase 6 script refuses to touch ANY database until the caller passes either
`--db-url <url>` (an explicit target — a fixture/tmp_path database) or
`--i-accept-live-data-risk` (an unmissable, never-silently-inherited flag naming exactly
what it does). `--db-url` is ALSO refused if it happens to resolve inside the live
`~/.open-net-worth` tree or the repo's tracked seed data, even without the live-risk
flag — closing the loophole of passing the real path disguised as a "fixture" url. This
is a NEW, stricter posture than the rest of `backend/scripts/`, deliberately scoped to
this one family: these are the first scripts in the repo meant to be invocable by an
agent rather than only by the operator's own hand, and the existing convention's implicit
trust ("whoever runs this typed the command themselves and knows what DATA_DIR they're
pointed at") does not hold for that caller.

Mirrors `core.config`'s `ONW_QA_MODE` fail-closed posture (same real-instance-root
containment check, duplicated rather than imported so the guard stays a stdlib-only leaf
usable before any DATA_DIR resolution happens) without reusing its flag, since QA mode
answers a different question ("is this a test boot") than "did the caller explicitly mean
to target the real database."

### 2. Each of the four scripts stays SCAN-and-REPORT by default; two can also migrate

`migrate_bare_model_refs.py` and `migrate_inline_transcript_images.py` are true
migrations — dry-run (report) by default, `--apply` writes ONLY the rows the scan itself
classified as safe (never a guess, `--apply` or not). `scan_legacy_proactive_strings.py`
and `scan_mixed_statement_rows.py` are diagnostic-only — no `--apply` flag exists at all,
matching the RFC's explicit instruction that these two need visibility, not a rewrite
(proactive strings are already safe to read via Phase 1's `_normalize_proactive_event`;
statement-row reclassification is an owner decision, never an automated one).

"Unambiguous" for a bare model ref is defined narrowly: canonicalize ONLY when the
instance currently has AT MOST ONE configured provider — the one case where pinning it
to that provider is provably the only rational answer, not a guess among several
candidates. Two or more configured providers, or an unresolvable ref, is always reported
for owner review, never guessed. `model_config.default_model` is deliberately EXCLUDED
from this script's scope — it already self-heals unconditionally on every read
(`ModelConfigStore._migrate_default_model`, composed from an explicit stored `provider`
field, never a guess), so touching it here would be a second, redundant write path for
the same fact.

The inline-transcript-image migration is judged safe to actually rewrite (unlike the
RFC's general "never rewrite destructively" caution) because provenance IS fully
recoverable here: the media type and bytes ARE the block being extracted, and message/
session ownership is never ambiguous — the block is found inside one specific session's
one specific message. `source_channel` is honestly recorded as `"legacy:unknown"` (never
guessed as "telegram" or "web" — the real inbound channel was never captured
pre-Phase-2), and a migration receipt (`migrated_from`/`migrated_at`/
`source_message_index`/`source_block_index`) rides in the new `AttachmentStore` row's own
`metadata`, rather than a separate receipt file — one fewer artifact to keep in sync with
the row it describes. A block whose base64 fails to decode is left exactly as-is and
reported with an error, never dropped or guessed at.

### 3. One consolidated `log_turn_routing` call, not three scattered lines

`backend/ai/turn_diagnostics.py::log_turn_routing` is the one place every routing
decision's fields converge. It emits through `logging`'s `extra=` dict — this repo's one
prior structured-logging precedent (`services/skills.py`) — rather than inventing a new
JSON-logging convention: the payload rides both in the plain-text log line this repo
ships today and as `record.turn_routing` for a future JSON handler or a `caplog`-based
test.

Getting the token-budget half and the attachment/routing half into the SAME call meant
moving where each is computed relative to where it's known:

- `context_budget.run_preflight` no longer logs anything itself — it has exactly one
  caller (`ai/assistant.py`, both the initial and retry preflight), so its own
  `"context_preflight ..."` line was pure duplication once the caller could log the same
  report itself, joined with what only the caller knows (session/surface/attachments).
- `ai/attachments.py::resolve_attachment_refs_for_dispatch` gained an OPTIONAL out-param,
  `diagnostics: dict | None`, that it fills in with `route`/`capability_source`/
  `attachments`/`reader_model_ref`/`reader_cache_hit` — never logs anything itself. A
  RETURN-shape change (e.g. `-> (content, diagnostics)`) was rejected: this function has
  ~40 pre-existing test call sites (per the sibling ADR,
  `2026-08-16-attachment-store-replaces-upload-store`, which already weighed and rejected
  changing this exact function's shape once) and an out-param defaulting to `None` keeps
  every one of them byte-identical, while still giving `assistant.py::_stream_round` (the
  ONE place that also has the token-budget report in scope) everything it needs to build
  one call.
- `services.vision_capability.capability_source` and
  `services.external_context.peek_cache_hit`/`ExternalContextBlock.cache_hit` are two
  small, additive reads (never guesses — see their own docstrings for how each mirrors
  the EXACT resolution order of the function it reports on, so the label can never drift
  from the value that function actually returned) that fill in the two fields nothing
  previously exposed at all.

`ai/tools/uploads.py::read_attachments` (the `on-demand-tool` route — an OLDER attachment
the model explicitly asks to re-read, not this turn's own image) calls the same
`log_turn_routing` from its own vantage point, using the `core.chat_session`/
`core.audit_context` ContextVar seams every other write inside a tool dispatch already
reads — no new plumbing into the tool call signature.

### Security contract

`AttachmentDiagnostic` (id/media_type/byte COUNT) is the only shape a caller can pass as
"attachment" data — there is no field for text or bytes, so a caller cannot smuggle
either through by construction. `refusal_reason` is user-facing prose about the BUDGET
SHAPE (limits/counts — see `context_budget.py`'s own refusal message), never document
content, and is still defensively length-capped in case that ever changes.
`tests/test_turn_diagnostics.py` pins this with real integration tests (a full turn
through `assistant.run()`, `caplog`-asserting that neither the raw base64 image nor the
reader's extracted text body ever appears in a log record).

## Consequences

### Positive

- An agent (or a future contributor) cannot run one of these four scripts against the
  operator's real data by accident — the fail-closed default matches this task's own
  explicit scope boundary structurally, not just by discipline.
- One `caplog`/log-search query answers "what did this turn actually do", instead of
  joining three unrelated lines by timestamp.
- Two of the four migrations are real, tested, `--apply`-capable fixes an operator can
  run themselves later — not just reports.

### Negative

- `_live_data_guard.py`'s containment check duplicates ~15 lines of `core.config`'s own
  logic rather than importing it, so the two must be kept in sync by hand if the real
  instance root or seed-data path ever changes shape. Accepted: importing `core.config`
  here would pull in its `.env`/settings machinery at script-import time, defeating the
  "importing this module touches nothing" property the whole guard depends on.
- `log_turn_routing` fires once per provider round inside a turn (mirroring the preflight
  log's own prior per-round cadence), so a multi-round tool-loop turn emits several
  records, most with `route="none"`/`attachment_count=0` once the turn moves past its
  first attachment-bearing round. Left as-is rather than tracked "has this turn already
  logged" state — the per-round record is still individually accurate, and the
  alternative (turn-scoped state threaded through `_stream_round`'s several exit points)
  was judged more invasive than the verbosity it would save.

### Follow-ups (not built here — out of this task's scope)

- Migrating `model_config.default_model`, legacy proactive strings, or mixed statement
  rows into their fully "clean" shape is explicitly deferred to the owner (proactive
  strings: optional per the RFC; statement rows: an owner decision by design;
  default_model: already self-healing).
- Item 33 (full CI gate), item 34 (black-box web+Telegram QA), and item 35 (owner
  sign-off on the Meridian Bank card-statement numbers) are separately scoped — this task is
  diagnostics-and-dry-run only and never touched the operator's live
  `~/.open-net-worth` data.

## Alternatives considered

### Reuse `ONW_QA_MODE` directly instead of a new script-specific guard

Rejected: `ONW_QA_MODE` answers "is this a test/QA boot of the APP", a different
question from "did this SCRIPT's caller explicitly mean to target the real database" —
piggy-backing on it would mean a script silently behaves safely ONLY when an unrelated
env var happens to be set by something else (a test harness, a QA boot script), which is
exactly the "silent default" risk this guard exists to close.

### Log inside `context_budget.run_preflight` AND inside `resolve_attachment_refs_for_dispatch`, each with its own partial record

Rejected as the literal opposite of item 32's ask: two coherent-looking but INCOMPLETE
records (one missing attachment/route context, one missing the token budget) recreate the
"grep and hand-join" problem the RFC calls out, just with two lines instead of three.

### Change `resolve_attachment_refs_for_dispatch`'s return type to include diagnostics

Rejected: breaks ~40 pre-existing synchronous test call sites for a return-shape change
whose only purpose is carrying five extra fields one call away — an optional out-param
achieves the same result with zero blast radius on any existing caller.

## Agent provenance

- **Decided by:** orchestrating Claude agent (diagnostics-and-dry-run task, explicitly
  scoped to never touch the operator's real `~/.open-net-worth` data) + repo owner
  (Codex-authored RFC)
- **Lane / branch:** `phase6-migration-diagnostics`, based on
  `feature/cross-channel-attachments-epic`
- **Alternatives weighed:** reuse `ONW_QA_MODE` vs. a script-specific guard; two partial
  logs vs. one consolidated log; return-shape change vs. an optional out-param
- **Driving artifacts:**
  `.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md` Phase 6
  (items 31-32),
  `docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md`,
  `docs/adr/2026-08-16-attachment-store-replaces-upload-store.md`,
  `docs/adr/2026-08-16-context-budget-preflight-and-bounded-proactive-context.md`
- **Why this way (one line):** an agent-invocable script needs a stricter default than an
  operator-invoked one, and a diagnostic record is only as useful as its narrowest
  caller's context — joining the two halves at the one call site that has both, rather
  than logging twice, is what actually delivers "one coherent record."
