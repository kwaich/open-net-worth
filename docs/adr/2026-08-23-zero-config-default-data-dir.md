---
title: Default DATA_DIR/CACHE_DIR/LOGS_DIR move outside the git checkout; a legacy install is detected and refused, never silently emptied
status: Accepted
date: 2026-08-23
deciders: owner (one-command-install requirement, docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md Component 6) + implementing Claude agent (lane/launch-docs)
---

# Default DATA_DIR/CACHE_DIR/LOGS_DIR move outside the git checkout

## Context

`backend/core/config.py` defaulted `DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` to `backend/{data,cache,logs}`
— directories INSIDE the git checkout. Two problems follow directly from that:

1. **A fresh clone's financial database sits where git can destroy it.** `git clean -fd`, a hard
   reset, or even an ordinary `git pull` that touches `backend/data/` can wipe or corrupt live
   application data that happens to share a directory with tracked files. This is a self-hosted
   app; the data in that directory is the operator's own accounts, transactions, and secrets.
2. **`backend/data/` was simultaneously the live data dir AND git-tracked.** 23 files under it —
   the sanitized example dataset (`examples/`), shipped extension packs (`extensions/`), and
   tax-jurisdiction rule tables (`tax_jurisdictions/`) — are genuine repo content that must ship
   with every clone. Everything else that could land in the same directory (the SQLite database,
   auth secrets, connector credentials, sessions, checkpoints, caches) must never be tracked. One
   directory serving both roles is why `.gitignore` had grown ~20 individually-named secret-file
   exclusions: every new store needed its own line, and a missed one commits a credential.

`docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md` (Component 5/6) calls for a
fresh clone to run with **zero configuration** — no `.env` required — as part of one-command
install (`make setup` / `docker compose up`). `core/config.py`'s own docstring already referred to
`~/.open-net-worth/data` as "the user's production instance directory" in the `_REAL_DATA_DIRS`
QA-safety constant, and this repo's actual owner install already points `.env`'s `DATA_DIR` there
— the documented "real" location and the zero-config default were two different paths purely by
historical accident.

Constraints that shaped the fix:

- **Env vars must keep winning over defaults** (`CLAUDE.md`: "Balances and net worth are
  computed... reporting amounts are recomputed", and more directly, the owner's own live install
  depends on `DATA_DIR`/`CACHE_DIR`/`LOGS_DIR` set explicitly in `.env` continuing to work
  unchanged).
- **The QA fail-closed guards** (`assert_qa_data_dir_safe` et al., `ONW_QA_MODE=1`) must still
  correctly refuse a QA boot that resolves into the live tree — and, since the live tree is now
  ALSO the unconfigured default, a QA boot that forgets to override `DATA_DIR` becomes MORE
  dangerous than before (it used to fall through to the harmless in-repo seed dir), not less, so
  this guard needed re-verification, not just a pass-through.
- **The repo's own tracked seed/reference content must stay in the repo** and must not become
  entangled with — or accidentally excluded by — the runtime data dir's new location.
- **"No backward-compat shims"** (`CLAUDE.md`) argues against quietly reading from either location
  depending on what's present; **"be careful with live data"** argues against blindly moving a
  live financial database as an unreviewed side effect of an ordinary boot.
- **Native Windows is out of scope for this lane** (`backend/core/io.py` does a bare
  `import fcntl`, so `import backend.main` already fails on Windows outside WSL) — this ADR's
  `make setup`/native-boot path is macOS/Linux only; Docker is this repo's only Windows story and
  is unaffected by anything below (Windows is irrelevant to a Linux container).

## Decision

We will:

1. **Change the defaults** in `backend/core/config.py` — `_DEFAULT_DATA_DIR` / `_DEFAULT_CACHE_DIR`
   / `_DEFAULT_LOG_DIR` become `~/.open-net-worth/{data,cache,logs}` (via a new
   `_HOME_INSTANCE_ROOT = Path.home() / ".open-net-worth"` constant, reused by the pre-existing
   `_real_instance_root()` too). `_env_path()` is untouched, so `DATA_DIR`/`CACHE_DIR`/`LOGS_DIR`
   set in the environment or `.env` continue to win over the default exactly as before — verified
   by `tests/test_config_defaults.py::test_env_var_still_wins_over_the_default` and
   `test_settings_singleton_honors_env_override`.

2. **Leave the repo's tracked seed/reference content exactly where it is.** `EXAMPLES_DIR`
   (`scripts/seed_examples.py`), `SHIPPED_EXTENSIONS_DIR`, and `TAX_JURISDICTIONS_DIR`
   (`core/paths.py`) were ALREADY hardcoded to `backend/data/...` via the fixed `BACKEND` constant
   (`Path(__file__).resolve().parent.parent`), never via the configurable `DATA_DIR` — so this
   move required zero code change on that side. It only *looked* entangled because, before this
   change, the configurable default happened to equal the same physical directory. `.gitignore` is
   restructured to match: a single `backend/data/*` directory-level ignore, with `!backend/data/examples/`
   / `!backend/data/extensions/` / `!backend/data/tax_jurisdictions/` un-ignoring exactly those
   three fixed, tracked subtrees. Verified with `git check-ignore -v` against every currently
   tracked file (all 23 stay un-ignored) and against synthetic secret/runtime paths, including one
   with a name that has never appeared in any `.gitignore` line before (all correctly ignored) —
   see the implementing commit's verification output.

3. **Detect an unmigrated legacy install and refuse to boot, rather than silently starting empty
   or silently moving data.** `core/config.py::_assert_not_orphaning_legacy_data_dir`, called from
   `get_settings()` on first resolution of the singleton, refuses (prints a fix-it message to
   stderr, exits 1) exactly when: `DATA_DIR` was not set in the environment, `ONW_DATABASE_URL`
   was not set, the OLD default (`backend/data/onw.db`) exists and is non-empty, and the NEW
   default's `onw.db` does not exist yet. The message names both paths and gives two exact,
   copy-pasteable fixes: keep the old location (`DATA_DIR=<old path>` in `.env`) or migrate (an
   `rsync -a --exclude=examples --exclude=extensions --exclude=tax_jurisdictions` one-liner that
   copies, never moves, so the operator can verify the app works before cleaning up the old copy
   themselves).

4. **`make setup` detects native Windows and refuses with an actionable message** pointing at
   `docker compose up` instead of attempting to run a backend that cannot import on that platform
   (`fcntl`). This is a detection/messaging change only — no Windows support is added, per the
   explicit scope boundary above.

## Consequences

### Positive
- A fresh clone truly needs zero configuration: `make setup && make start` or `docker compose up`
  both work with no `.env`, and the resulting data lives somewhere `git` operations can never
  touch.
- `backend/data/` stops being a dual-purpose directory. The `.gitignore` shape can no longer leak
  a new secret file by omission — the directory-level ignore covers anything that lands there,
  known or not (verified empirically against a synthetic never-listed-before filename).
- The documented "real" instance directory and the unconfigured default are now the same path —
  one fewer thing for `CLAUDE.md`/`docs/DATA_MODEL.md` to explain as two different concepts.
- An install that predates this change gets a clear, one-time, actionable refusal instead of a
  silently empty book — the single worst outcome ("where did my data go") is structurally
  prevented, matching this codebase's existing fail-closed conventions (`DataIntegrityError`,
  `ensure_migrated()`'s verified-backup-or-refuse, the QA dir guards already in this same file).

### Negative
- **One extra boot-time filesystem check** (`Path.exists()` + `Path.stat()` on the legacy
  `onw.db`) on every process that resolves `Settings` for the first time — negligible cost, paid
  once per process via the existing singleton cache.
- **The legacy-install guard is scoped to `DATA_DIR`/`onw.db` only**, not `CACHE_DIR`/`LOGS_DIR`.
  An old install's caches/logs are left behind at the old location and simply regenerate fresh at
  the new one; this is a deliberate scope narrowing (those are regenerable/rotating, never "the
  data" an operator would say they lost), not an oversight, but it does mean a legacy `backend/cache/`
  or `backend/logs/` directory is never cleaned up automatically.
- **A hypothetical operator who deliberately wants a fresh, empty book while an old
  `backend/data/onw.db` from a past experiment still happens to sit in the checkout** now hits the
  refusal too, even though they don't care about that old file. The fix vectors in the printed
  message (set `DATA_DIR` explicitly, or move/delete the old file) cover this case, but it is one
  extra step such an operator wouldn't have needed before.
- This repo's OWN current owner install already sets `DATA_DIR` explicitly in `.env`
  (`~/.open-net-worth/data`), so this decision changes nothing about its behavior — the guard,
  like the default itself, only matters for other/future installs.

### Follow-ups
- `backend/core/paths.py`'s module docstring ("Their defaults are the historical `backend/data`
  and `backend/cache` directories, so an unconfigured instance is byte-for-byte unchanged") is now
  stale and should be updated by whichever change next touches that file — flagged in
  `docs/superpowers/notes/install-requests.md` rather than edited here, per this lane's
  file-ownership rules (`paths.py` belongs to a different lane file-owner).
- `tests/test_cache_dir_hermeticity.py` / `tests/test_logs_dir_hermeticity.py` reference
  `config._DEFAULT_CACHE_DIR` / `config._DEFAULT_LOG_DIR` symbolically (so they still pass
  unchanged), but a couple of their docstrings/test names still say "historical repo .../back-compat"
  — cosmetically stale now, not incorrect. Left untouched; not in this lane's file list.
- `docs/adr/README.md`'s index table is not updated by this ADR file itself, to avoid a concurrent
  multi-agent write race on a single shared file in this worktree; whoever integrates this lane
  should append the index row.

## Alternatives considered

- **Keep `backend/data` as the default, only fix `.gitignore`.** Rejected — it leaves the core
  problem (`git clean`/`git pull` can destroy a live database) completely unaddressed, which is
  the actual data-safety bug this change exists to fix, not merely a secret-leak risk.
- **Silently auto-migrate the legacy directory to the new default inside `get_settings()`.**
  Rejected — moving a live, potentially multi-hundred-MB SQLite database (plus WAL/SHM siblings)
  as an unreviewed side effect of "resolve my settings", the first thing almost every process does
  at import time, is exactly the kind of unattended, unreviewable operation on live financial data
  this codebase's conventions warn against. A partial move (crash mid-copy, permission error,
  another process holding the WAL open) would leave the operator in a worse, harder-to-diagnose
  state than a clear refusal with an exact command to run themselves.
- **Warn and continue instead of refusing.** Rejected — a warning is easy to miss in a log stream,
  and the failure mode it's protecting against ("my book looks empty") is exactly the kind of
  silent-degradation-on-financial-data this codebase treats as fail-closed everywhere else
  (`DataIntegrityError`, the migration boot gate, the QA dir guards in this same file).
- **Gate the legacy guard on `ONW_QA_MODE`-style opt-in flag instead of firing by default.**
  Rejected — the whole point is to protect an operator who does nothing differently (no flag, no
  new env var, just an old install on a new version of the app); an opt-in flag would need to be
  discovered and set by exactly the operator who doesn't know they need it.
- **Also guard `CACHE_DIR`/`LOGS_DIR` legacy locations the same way as `DATA_DIR`.** Rejected —
  scope narrowed deliberately to what an operator would actually call "my data"; caches and logs
  regenerate for free at the new location, so refusing to boot over them would be a false-positive
  refusal with no real data-loss risk behind it.
- **Ship a standalone `backend/scripts/migrate_legacy_data_dir.py` that performs the move.**
  Considered, deferred — `backend/scripts/` is not in this lane's owned file list, and the printed
  `rsync --exclude=...` one-liner in the refusal message already gives an exact, reviewable,
  copy-paste fix without adding a new script surface; a follow-up lane can promote it to a real
  script if that proves too manual in practice.

## Agent provenance

- **Decided by:** implementing Claude agent, `lane/launch-docs` (senior/staff engineer role)
- **Lane / branch:** `lane/launch-docs`, worktree `/Users/owner/code/worktrees/onw-launch-docs`
- **Alternatives weighed:** keep old default + gitignore-only fix; silent auto-migration inside
  `get_settings()`; warn-and-continue; opt-in-flag-gated guard; also guard CACHE_DIR/LOGS_DIR;
  ship a standalone migration script (see Alternatives above for the reasoning behind each)
- **Driving artifacts:** `docs/superpowers/specs/2026-08-23-product-docs-and-launch-design.md`
  (Component 5 ".env becomes optional" + Component 6 "one-command install"); the coordinator's
  explicit task brief for this lane ("Change the defaults... Detect the old location and either
  migrate or refuse... Decide which, and justify it in the ADR")
- **Why this way (one line):** a self-hosted app's financial database gets fail-closed treatment
  everywhere else in this codebase, and a boot-time refusal with an exact fix-it command is the
  only option here that can never itself become the cause of data loss.
