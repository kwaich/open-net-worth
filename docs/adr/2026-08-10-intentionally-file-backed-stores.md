---
title: Intentionally file-backed stores — the formal "deserves a file" exception list
status: Accepted
date: 2026-08-10
deciders: Claude (implementer agent, storage plan Wave 5F) — owner directive stated verbatim in Context
---

# 2026-08-10. Intentionally file-backed stores — the formal "deserves a file" exception list

## Context

Wave 5 (started 2026-08-10) closed the JSON→DB migration's remaining runtime `.json`
gaps in five sub-waves (5A–5E), landing on the owner's own exit criterion, stated
verbatim: *"everything must be in database, there should be no `.json` file at all,
except the ones that really deserve it."* Wave 5F is that criterion's own finisher — a
whole-tree audit of a real, long-lived dev instance's `DATA_DIR`/`CACHE_DIR` after 5A–5E
found a bar for "deserves it" had never been written down anywhere, only implied
piecemeal across five waves' worth of ADRs and progress-ledger prose (`docs/superpowers/
plans/2026-08-10-json-to-db-wave5.md`'s own store inventory calls the surviving bucket
"C. Caches... D. Blob byte-pools (owner call — likely keep as files)" without a checkable
rule). Two concrete gaps that ambiguity produced, found by this wave's own audit:

- `backend/services/favicons.py` and `backend/ai/models/codex_home.py` each constructed
  their own `CACHE_DIR / "..."` path INLINE, bypassing `core/paths.py`'s "central
  registry — all backend modules must import from here" convention (CLAUDE.md) — neither
  had a name in `core/paths.py` for anyone to even list as an exception.
- The audit template used by every prior wave's own verification
  (`glob.glob(DATA_DIR/"*.json")`, non-recursive) would never have SEEN either gap: both
  live one level deeper (`CACHE_DIR/favicons/index.json`, `CACHE_DIR/codex-home/**/*.json`).

Separately, this wave investigated four CACHE_DIR files the plan's own inventory had
left as an open "cache or state?" question (`digest_state.json`/`news_screen_state.json`/
`memory_consolidate_state.json`/`routing_decisions.json`) and concluded NONE of the four
belongs in this exception list — three are load-bearing scheduler/consolidation
watermarks (migrated to `runtime_state` namespace rows, revisions 0036-0037) and one is
dead code from an already-removed feature (retired as `.removed-<ts>`, revision 0038).
Zero-for-four is itself evidence the bar needs to be a written TEST, not a vibe — an
agent guessing case by case with no checklist would very plausibly have kept one or more
of these "because CACHE_DIR" alone.

## Decision

`backend/core/paths.py` gains `INTENTIONALLY_FILE_BACKED` — a `frozenset[Path]` naming
every path allowed to still be a file (or a whole directory of them), plus
`is_intentionally_file_backed(path)` to test membership. The test for inclusion, applied
to every candidate this wave found:

1. **Is it a genuine, rebuildable, provider/response CACHE?** — disposable (safe to
   delete; the app re-fetches/re-derives it), never user-authored, never itself queried
   INTO (read/written whole, exactly like the JSON stores this migration retired) —
   moving it into the database would buy nothing (no query benefit) while adding
   write-hook/checkpoint overhead to a high-churn path. PASS → an exact-file member:
   `fx_cache.json`, `models_cache.json`, `sectors_cache.json`, `names_cache.json`,
   `monitoring_price_cache.json`, `briefing_cache.json`, `classification_cache.json`,
   plus `favicons/` (found by this wave, previously unregistered — a directory member
   since its content is per-domain, dynamically named, and partly binary).
2. **Is it a large BLOB byte-pool** — HTML/binary/gzip content, or a third-party tool's
   own home directory the database literally cannot serve (an external process reads it
   directly off disk)? PASS → a directory member: `ARTIFACTS_DIR` (chat-artifact HTML),
   `UPLOADS_DIR` (attachment payloads), `CHECKPOINTS_BLOBS` (turn-checkpoint gzips),
   `STATEMENTS_PARSED_DIR` (parsed-statement sidecars), `CODEX_HOME_DIR` (found by this
   wave — an isolated `$CODEX_HOME` for spawned Codex CLI child processes; not app-owned
   data, cannot move into `onw.db` regardless of size).
3. **Is it a frozen HISTORICAL/BACKUP RECORD** — restore-only, never rewritten, read (if
   ever) as one opaque snapshot rather than as individual per-store files? PASS → a THIRD
   directory-member category, found only once this wave actually re-ran the whole-tree
   audit against real accumulated data rather than trusting the design up front:
   `BACKUPS_DIR` (`services/backup.py`'s daily disaster-recovery snapshots — mostly
   `.zip` today, but the audit surfaced one surviving pre-zip DIRECTORY-format snapshot
   with loose `.json` files inside it, exactly the legacy shape this category exists to
   cover) and `SKILL_BUNDLES_DIR` (`core/paths.py`'s own PRE-EXISTING comment already
   called this "LEGACY... left as inert history after migration; never written again" —
   this frozenset is simply the first place that claim became machine-checked instead of
   only stated in a comment).
4. **Otherwise — FAIL.** A load-bearing runtime STATE watermark (a scheduler cooldown, a
   cadence counter — read-modify-write, never re-derived from an external source) does
   NOT pass test 1 just because it happens to sit in `CACHE_DIR`: losing it is not
   "slower next read", it is wrong scheduling behavior (a duplicate digest send, a
   silently-reset daily budget). This is exactly why `digest_state`/`news_screen_state`/
   `memory_consolidate_state` were migrated, not listed here, despite living in
   `CACHE_DIR` right alongside the seven genuine caches above. Dead code (zero live
   readers, a retired feature's orphan) also fails — it isn't a cache OR a blob, it's
   garbage, handled by Wave 5F's separate dead-file cleanup (revision 0038), never by
   adding it here.

TWO real, opposite-direction symlink bugs surfaced by running this wave's own final
whole-tree audit against real accumulated data (neither caught by design review, or by
the guard test's synthetic writes before that audit forced the issue) — `is_intentionally_
file_backed`'s implementation went through two drafts to close both:

1. **First draft** compared a plain `path.resolve()` against every member — correct for
   every member except `CODEX_HOME_DIR/auth.json`, which `ai/models/codex_home.py`'s own
   docstring documents as a DELIBERATE symlink to the operator's real
   `~/.codex/auth.json` ("the whole point of this transport is spending the user's
   existing codex login"). `.resolve()` follows a symlink to its TARGET, so the check
   silently compared the operator's home directory against `CACHE_DIR` and failed —
   flagging a file our own code legitimately places at that path, via its target, not
   its location.
2. **Second draft** swapped in a bare `os.path.abspath()` (normalizes, follows NO
   symlinks at all) — which then failed the guard test's own `UPLOADS_DIR` write on
   macOS: `/tmp`/`/var` are themselves symlinks to `/private/tmp`/`/private/var`, so an
   env-var-overridden `DATA_DIR` built from the un-resolved `/var/folders/...` spelling
   compared unequal against a freshly-globbed candidate path that had walked through the
   ALREADY-resolved `/private/var/folders/...` spelling — a real, legitimate file wrongly
   flagged as a stray.

The fix (`_resolve_ancestors_only`) resolves every ANCESTOR directory (closing gap 2 —
root-level infrastructure symlinks like `/var` normalize away) while leaving the FINAL
path component exactly as given, never following ITS OWN symlink-ness (closing gap 1 —
a symlink we deliberately placed is compared by where it lives, not what it points at).

`favicons.py`/`codex_home.py` were also rewired onto new `FAVICONS_DIR`/`CODEX_HOME_DIR`
constants in `core/paths.py`, closing the registry-bypass gap in the same change — the
exception list can only be authoritative if every file it's supposed to cover is
reachable from ONE place, not re-derived ad hoc at each call site.

`tests/db/test_no_stray_json_store.py` (new) is the forever-guard: boots a fresh migrated
DB, performs a representative slice of writes through real store classes (an account +
transaction, a session transcript + extraction cursor, all three Wave 5F `runtime_state`
watermarks) plus two writes directly into documented exception paths (one exact-file
cache, one blob directory), and asserts every `.json` file that appears as a result is
either `is_intentionally_file_backed()` or is one of THIS test's own two deliberate
exception writes — proving both halves: ordinary application writes stay clean, and the
checker function correctly recognises the documented exceptions instead of false-flagging
them. Mirrors `tests/db/test_no_json_store.py`'s own "guards the invariant forever" role
for the `JsonStore` retirement itself.

## Consequences

### Positive
- "Deserves a file" is now a written, two-question test any future agent can apply
  mechanically instead of re-litigating case by case — this wave's own zero-for-four
  result on the CACHE_DIR watermarks is the proof it isn't just "wherever a file already
  happens to sit."
- Two real registry-bypass gaps (`favicons.py`, `codex_home.py`) are closed, not just
  documented around — `core/paths.py` is genuinely authoritative now, not "authoritative
  except for two directories."
- A regression (a future PR introducing a new stray `.json` store) fails a fast, cheap,
  always-run unit test instead of surviving until the next manual whole-tree audit.

### Negative
- `INTENTIONALLY_FILE_BACKED` mixes two different membership tests (exact-file vs.
  directory-prefix) in one frozenset — slightly less uniform than a single flat set, but
  splitting it into two collections would separate two halves of the SAME question
  ("does this location deserve to stay a file") for no real benefit; `is_intentionally_
  file_backed()` hides the distinction from every caller anyway.
- The guard test cannot exercise EVERY store this migration ever touched (that would
  duplicate most of `tests/db/`) — it is deliberately a representative slice, so a
  regression in an unexercised store's own write path could still slip through until the
  next whole-tree audit. Accepted: the alternative (an exhaustive per-store sweep in one
  test file) would be an unmaintainable duplicate of the whole test suite.

### Follow-ups
- None identified. `INTENTIONALLY_FILE_BACKED` is designed to grow — a genuine future
  cache/blob-pool joins it with its own one-line reasoning, exactly like the twelve
  entries here; nothing about this decision is expected to need revisiting on its own.

## Alternatives considered

- **Leave the exception implicit (five waves' worth of ADR prose + the plan doc's own
  inventory), no single source of truth** — rejected: this is the status quo this ADR
  replaces, and it directly produced the two registry-bypass gaps this wave found.
- **A comment-only convention in `core/paths.py` ("these constants are caches"), no
  frozenset/checker/test** — rejected: unenforceable — nothing stops a new file from
  quietly joining the survivors list the same way `favicons.py`/`codex_home.py` did,
  since a comment cannot be asserted against in a test.
- **Fold the CACHE_DIR watermark files into this exception list instead of migrating
  them, since they already live in "the cache directory"** — rejected: directory
  location is not the test; test 1 above (rebuildable/disposable) is, and none of the
  three passes it. Keeping them as files would have been the status-quo-bias failure
  mode this ADR exists to prevent.
- **One frozenset of exact files only; blob directories each get their own
  `is_under_<x>_dir()` helper** — rejected: four near-identical prefix-check functions
  for four directories (soon six, with `favicons`/`codex-home`) is more code than one
  frozenset mixing both shapes plus one shared checker, for no behavioral difference.

## Agent provenance

- **Decided by:** Claude (implementer agent, storage plan Wave 5F). Lane: worktree
  `storage-plan1`.
- **Alternatives weighed:** implicit/ADR-only convention (rejected — unenforceable,
  already proven to leak); comment-only convention (rejected — same reason); folding the
  CACHE_DIR watermarks in by location (rejected — fails test 1, the whole point of
  writing a test down); per-directory helper functions (rejected — more code, no
  behavioral gain over one shared checker).
- **Driving artifacts:** the owner's own verbatim exit criterion (`docs/superpowers/
  plans/2026-08-10-json-to-db-wave5.md`, quoted above); this wave's own whole-tree audit,
  which found `favicons/index.json` and `codex-home/**` surviving a non-recursive audit
  template and traced both to inline `CACHE_DIR / "..."` construction; `docs/adr/
  2026-08-09-json-store-retired.md` (the `tests/db/test_no_json_store.py` precedent this
  ADR's own guard test mirrors).
- **Why this way (one line):** five waves of "keep it a file, it's obviously a cache"
  judgment calls had already let two real files slip past the registry unnoticed, so the
  criterion itself needed to become a frozenset and a test, not stay a shared
  understanding.
