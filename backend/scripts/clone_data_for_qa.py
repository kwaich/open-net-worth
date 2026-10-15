"""Clone the live DATA_DIR into a throwaway copy for agent/QA testing.

Why: agents that test only on the fabricated demo book (`backend/scripts/demo/`) miss bugs
that only appear on PRODUCTION-SHAPED data — real holdings, real history, real
edge-cases. This snapshots the user's REAL data dir into a fresh temp dir so a QA /
vision agent can `export DATA_DIR=<clone>` and exercise the app against
production-like data WITHOUT ever touching the real files (the app writes to the
clone, never the source).

Safety:
  * READ-ONLY on the source — we only copy OUT of it.
  * SECRETS ARE STRIPPED from the clone: secret-bearing files are dropped
    (`auth.json`) and write-only secret fields across every JSON are blanked
    (passwords / tokens / api keys / signing keys). The shared SQLite database is
    copied through SQLite's snapshot API and every centrally-declared secret or
    non-restorable table is emptied. The clone is safe to hand to an agent. (It
    still contains the user's financial figures — it stays LOCAL, in a temp dir,
    never committed; `/tmp` + the `.qa-data/` path are gitignored.)
  * Idempotent: prints ONLY the destination path on stdout, so callers can do
    `DATA_DIR=$(python3 -m backend.scripts.clone_data_for_qa)`.

Usage:
    python3 -m backend.scripts.clone_data_for_qa            # → prints a fresh /tmp clone path
    python3 -m backend.scripts.clone_data_for_qa --dest DIR # → clone into DIR (cleared first)
    DATA_DIR=/some/real python3 -m backend.scripts.clone_data_for_qa
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# Files dropped entirely from the clone (pure secret stores — never needed for QA;
# an unconfigured-auth clone just runs in the loopback-trust dev path).
_DROP_FILES = {
    "auth.json",
    # Runtime-only files that should never seed a QA run:
    "cost_log.json",  # LLM cost tracking — resets naturally on each boot
    "connector_sessions.json",  # Connector auth session tokens — runtime state
    "import_batches.json",  # Transient upload-import cache — not financial data
    # LEGACY auto-routing config, dropped so an older clone source cannot reintroduce it.
    # It used to OVERRIDE the provider/model pin below: its pins were "openrouter:*" entries
    # whose credentials this same pass scrubs, so every routed turn 401s in openai_provider
    # while model_config.json still read provider=anthropic. (Defect 7 of 7 between this
    # harness and one passing e2e spec — every one of which surfaced as a green exit code,
    # because the runner exits 0 on "passed OR skipped".) The engine is gone entirely
    # (ADR 2026-08-06-auto-model-switching-removed); its successor preferred_models.json is
    # deliberately NOT dropped — a shortlist only orders pickers, it can select nothing.
    "routing_config.json",
}

# Directories dropped entirely from the clone — runtime/session state that must
# never carry over into a QA run (e.g. stale chat conversations cause CH-04 spec
# failures when the QA agent opens an old error-bearing session instead of fresh).
_DROP_DIRS = {
    "sessions",  # Chat conversation sessions — QA must start with a clean slate
    "checkpoints",  # Turn checkpoints — runtime-only, tied to live session history
    "backups",  # Manual backup snapshots — not needed for QA
    # 2026-08-22 (consolidate-derived-data-storage, revision 0046): the live parsed-rows
    # sidecar moved to files/statements/<id>.parsed.json (see that migration's own
    # _DROP_FILES_SUBDIRS/_STATEMENTS_KEEP_SUFFIX handling below, which already keeps it).
    # Only *.migrated-<ts>/*.removed-<ts> trail copies are left in this now-vestigial
    # top-level directory — the same financial content is already kept once, correctly,
    # under files/statements/; copying this stale duplicate into every QA clone too is
    # dead weight with no upside.
    "statements_parsed",
}

# Nested-under-FILES_DIR equivalent of _DROP_DIRS (2026-08-21 — artifacts/attachments/
# receipts/statements moved from flat DATA_DIR siblings to DATA_DIR/files/<name>, see
# docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md). "files" itself is a
# top-level dir and so is NOT in _DROP_DIRS (its attachments/receipts/artifacts subtrees
# ARE wanted in a QA clone) — only its "statements" child's RAW files (transient uploaded
# statement bytes) are dropped, so "files" needs its own copy loop instead of the plain
# shutil.copytree/skip-whole-dir logic below.
#
# 2026-08-22 (consolidate-derived-data-storage RFC): "statements" moved from a fully
# skipped subdirectory to a PARTIALLY filtered one. Before this revision, the extracted
# parsed-rows sidecar lived in the separate, NOT-dropped `statements_parsed/` top-level
# dir, so a clone kept the parsed transaction facts (useful for QA decision-review
# scenarios) while dropping the bulky/sensitive raw PDFs. Revision 0046 relocates that
# sidecar INSIDE `files/statements/` as `<id>.parsed.json` — an unconditional whole-subdir
# skip would now silently drop the parsed rows too, a real regression in what a QA clone
# preserves. `_STATEMENTS_KEEP_SUFFIX` names the one thing worth keeping from that
# directory; everything else (the raw `<id><ext>` originals, and any `.migrated-`/
# `.removed-` migration trail) is still dropped, unchanged from before.
_DROP_FILES_SUBDIRS: set[str] = set()
_STATEMENTS_KEEP_SUFFIX = ".parsed.json"

QA_APP_PROVIDER = (os.getenv("QA_APP_PROVIDER") or "").strip()
QA_APP_MODEL = (os.getenv("QA_APP_MODEL") or "").strip()
if bool(QA_APP_PROVIDER) != bool(QA_APP_MODEL):
    raise RuntimeError("QA_APP_PROVIDER and QA_APP_MODEL must be set together")

# Substrings; any top-level (or nested) JSON key containing one of these is blanked.
_SECRET_KEY_HINTS = ("password", "token", "api_key", "secret", "signing_key", "bot_token")
_SQLITE_DB_NAME = "onw.db"
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _resolve_source() -> Path:
    """The live data dir: $DATA_DIR, else ~/.open-net-worth/data, else backend/data."""
    env = os.getenv("DATA_DIR")
    if env:
        return Path(env).expanduser()
    home = Path.home() / ".open-net-worth" / "data"
    if home.exists():
        return home
    return Path(__file__).resolve().parent.parent / "data"


def _scrub(obj):
    """Recursively blank any value whose key looks secret. Returns the scrubbed obj."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and any(h in k.lower() for h in _SECRET_KEY_HINTS):
                out[k] = "" if isinstance(v, str) else v if v is None else ""
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


def _assert_dest_is_temp(dest: Path) -> None:
    """Refuse to clone into a real / protected data directory.

    This is a belt-and-suspenders guard: even if the caller accidentally passes
    the live instance dir as --dest, the script will refuse and exit non-zero
    rather than silently overwriting real data.
    """
    # Import lazily to avoid polluting the module's top-level import footprint
    # (this script runs as __main__ and must import cleanly without a venv too).
    _home_onw_data = Path.home() / ".open-net-worth" / "data"
    _repo_backend = Path(__file__).resolve().parent.parent
    _repo_data = _repo_backend / "data"

    _protected: tuple[Path, ...] = (_home_onw_data, _repo_data)

    try:
        dest_resolved = dest.resolve()
    except OSError:
        dest_resolved = dest

    for real in _protected:
        try:
            real_resolved = real.resolve()
        except OSError:
            real_resolved = real
        if dest_resolved == real_resolved:
            raise SystemExit(
                f"ERROR: clone destination {dest} resolves to a REAL/PROTECTED data dir "
                f"({real}). Refusing to overwrite live data. "
                "Pass an explicit --dest pointing to a safe temp directory, "
                "or omit --dest to let the script create a fresh /tmp dir."
            )

    # Also refuse if dest is inside ~/.open-net-worth/ (catches subdirs).
    home_onw = Path.home() / ".open-net-worth"
    try:
        home_onw_resolved = home_onw.resolve()
    except OSError:
        home_onw_resolved = home_onw
    try:
        dest_resolved.relative_to(home_onw_resolved)
        raise SystemExit(
            f"ERROR: clone destination {dest} is inside {home_onw}. "
            "Refusing to clone into a live instance directory."
        )
    except ValueError:
        pass  # not a subdirectory — safe


def _copy_sanitised_sqlite(source: Path, dest: Path) -> None:
    """Snapshot the live SQLite DB, then remove every non-restorable table's rows.

    A byte copy is neither safe under WAL nor secret-free: credentials moved into
    ``secret_config``/``connector_secrets`` and approvals/runtime queues share ``onw.db`` with
    financial data. The online backup API preserves a consistent financial snapshot; the
    central sensitive-table policy strips the rows that must never leave the instance.
    ``VACUUM`` is required so deleted credential bytes do not remain in free pages.
    """
    from backend.core.sensitive import NON_RESTORABLE_TABLES, RETIRED_SECRET_TABLES

    source_conn = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            source_conn.backup(dest_conn)
            existing = {
                str(row[0])
                for row in dest_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            dest_conn.execute("PRAGMA foreign_keys = OFF")
            dest_conn.execute("PRAGMA secure_delete = ON")
            for table in sorted((NON_RESTORABLE_TABLES | RETIRED_SECRET_TABLES) & existing):
                quoted = table.replace('"', '""')
                dest_conn.execute(f'DELETE FROM "{quoted}"')
            dest_conn.commit()
            dest_conn.execute("VACUUM")
            check = dest_conn.execute("PRAGMA quick_check").fetchone()
            if check != ("ok",):
                raise RuntimeError(f"QA SQLite clone failed verification: {check}")
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def clone(source: Path, dest: Path) -> Path:
    """Copy `source` → `dest` (cleared first), dropping secret files + scrubbing
    secret fields from every JSON and sensitive SQLite rows. Returns `dest`."""
    if not source.exists():
        raise SystemExit(f"source data dir not found: {source}")

    # Resolve before any destination cleanup. Without this guard, source=dest (or a
    # destination above source) lets the rmtree below erase the book we were asked to
    # clone; a destination inside source can recursively copy its own build artifacts.
    source_resolved = source.resolve()
    dest_resolved = dest.resolve()
    if (
        source_resolved == dest_resolved
        or source_resolved in dest_resolved.parents
        or dest_resolved in source_resolved.parents
    ):
        raise SystemExit(
            f"ERROR: clone destination {dest} overlaps source {source}. "
            "Choose a separate temporary destination."
        )

    # Guard: never clone into the real data dir or the in-repo seed.
    _assert_dest_is_temp(dest)

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=f".{dest.name}-building-", dir=dest.parent))
    building.chmod(0o700)

    from backend.core.paths import DATA_DIR
    from backend.core.sensitive import is_secret

    try:
        for item in source.iterdir():
            if item.name in _DROP_FILES:
                continue
            # ``is_secret`` is deliberately rooted at core.paths.DATA_DIR. ``clone`` also
            # accepts an explicit source for tests/evals, so project the source-relative name
            # back onto that canonical root before asking the central policy. This keeps
            # migrated-suffix handling in one place without depending on the caller's path.
            if item.is_file() and (is_secret(item) or is_secret(DATA_DIR / item.name)):
                continue
            if item.name == _SQLITE_DB_NAME:
                _copy_sanitised_sqlite(item, building / item.name)
                continue
            if any(
                item.name == f"{_SQLITE_DB_NAME}{suffix}" for suffix in _SQLITE_SIDECAR_SUFFIXES
            ):
                continue
            if item.is_dir() and item.name in _DROP_DIRS:
                continue
            target = building / item.name
            if item.is_dir() and item.name == "files":
                target.mkdir()
                for sub in item.iterdir():
                    if sub.name in _DROP_FILES_SUBDIRS:
                        continue
                    subtarget = target / sub.name
                    if sub.is_dir() and sub.name == "statements":
                        # Partial copy: keep only each row's `<id>.parsed.json` (the
                        # extracted transaction facts), drop the raw original + any
                        # migration trail — see _STATEMENTS_KEEP_SUFFIX's own comment.
                        subtarget.mkdir()
                        for stmt_file in sub.iterdir():
                            if stmt_file.is_file() and stmt_file.name.endswith(
                                _STATEMENTS_KEEP_SUFFIX
                            ):
                                shutil.copy2(stmt_file, subtarget / stmt_file.name)
                    elif sub.is_dir():
                        shutil.copytree(sub, subtarget)
                    else:
                        shutil.copy2(sub, subtarget)
                continue
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        # Scrub secret fields from every JSON file in the clone (defensive — covers
        # email_ingest/channels/model_config/connector files without naming each).
        for jf in building.rglob("*.json"):
            try:
                data = json.loads(jf.read_text())
            except (ValueError, OSError):
                continue  # not parseable JSON / unreadable — leave as-is
            scrubbed = _scrub(data)
            # A QA-specific route is opt-in and must name both provider and model.
            # With neither set, preserve the clone's explicit source selection.
            if (
                jf.name == "model_config.json"
                and isinstance(scrubbed, dict)
                and QA_APP_PROVIDER
                and QA_APP_MODEL
            ):
                scrubbed["provider"] = QA_APP_PROVIDER
                scrubbed["model"] = QA_APP_MODEL
                # Clear per-task overrides too. Pinning only the top-level provider is HALF a
                # pin: task_models entries point at their own endpoints (e.g.
                # "openrouter:z-ai/glm-5.2"), whose credentials this same pass scrubs — so a
                # turn routed through any task model hits an unauthenticated provider and 401s.
                # That is what made every LLM-dependent browser-use spec return SKIP
                # ("AI not configured") while the top-level pin looked correct.
                scrubbed["task_models"] = {}
                # Keep ONLY the pinned provider. Chasing overrides one at a time did not
                # converge: the top-level pin was overridden by task_models, then by the
                # (since-removed) auto-routing config, then by a per-request model (grok-4.3)
                # arriving from the client. Leaving other providers configured means SOMETHING can always
                # select one, and their credentials are scrubbed by this same pass, so every
                # such selection 401s. A clone with one provider cannot route away from it.
                provs = scrubbed.get("providers")
                if isinstance(provs, dict) and QA_APP_PROVIDER in provs:
                    scrubbed["providers"] = {QA_APP_PROVIDER: provs[QA_APP_PROVIDER]}
            jf.write_text(json.dumps(scrubbed, indent=2))

        for path in building.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        os.replace(building, dest)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise

    return dest


def seed_fx_cache(dest: Path) -> Path | None:
    """Copy the live FX rates into the clone's CACHE_DIR, returning the file written.

    `make qa-e2e` / `make qa-manual` boot the clone with `CACHE_DIR=<dest>-cache`, a fresh empty
    directory. With no `fx_cache.json` in it, `to_base` has no rates and passes every non-base
    amount through 1:1, so every cash figure a QA run reports is inflated — a harness artefact
    indistinguishable from a real money bug. The file is `{currency: rate}` only; no secrets."""
    from backend.core.paths import CACHE_DIR

    src = CACHE_DIR / "fx_cache.json"
    if not src.is_file():
        return None
    cache_dir = dest.parent / f"{dest.name}-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return shutil.copy2(src, cache_dir / "fx_cache.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dest", help="clone into this dir (cleared first); default = a fresh /tmp dir"
    )
    args = ap.parse_args()

    source = _resolve_source()
    dest = (
        Path(args.dest).expanduser() if args.dest else Path(tempfile.mkdtemp(prefix="onw-qa-data-"))
    )
    clone(source, dest)
    seeded_fx = seed_fx_cache(dest)
    # Progress to stderr; ONLY the path to stdout (so `DATA_DIR=$(...)` works).
    print(f"cloned {source} → {dest} (secrets stripped)", file=sys.stderr)
    if seeded_fx is None:
        print(
            "no live fx_cache.json — QA figures will be 1:1 for non-base currencies",
            file=sys.stderr,
        )
    print(str(dest))


if __name__ == "__main__":
    main()
