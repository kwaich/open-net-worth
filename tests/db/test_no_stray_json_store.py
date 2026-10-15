"""Forever-guard: no runtime `.json` store file appears outside
``core.paths.INTENTIONALLY_FILE_BACKED`` — the JSON->DB migration's own exit criterion
(Wave 5F, ``docs/adr/2026-08-10-intentionally-file-backed-stores.md``). Mirrors
``tests/db/test_no_json_store.py``'s own "guards the invariant forever" role, for this
wave's exit bar instead of the ``JsonStore`` retirement.

Two independent checks:

  (a) ``is_intentionally_file_backed()`` itself is correct — a documented cache/blob/
      backup path IS recognized (an exact-file cache, any path nested under a blob or
      backup directory however deep/dynamically named, and a SYMLINK placed inside one —
      the real bug this wave's own final audit found, see below), an arbitrary stray path
      is NOT. Pure function, no filesystem I/O required for most cases, deterministic —
      the fast, always-precise half of this guard.
  (b) a representative slice of application writes through REAL store classes (an
      ordinary DB store unrelated to this wave, Wave 5F's own new
      ``sessions.extraction_cursor`` column, and all three Wave 5F ``runtime_state``
      watermarks) produces ZERO new ``.json`` files anywhere under ``DATA_DIR``/
      ``CACHE_DIR`` — proving these writes really do stay in the database — and a write
      made DIRECTLY into a documented exception directory is correctly tolerated rather
      than flagged, proving the checker recognizes a real exception rather than the
      assertion merely passing because the diff happened to be empty.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.core.ids import new_id
from backend.core.io import atomic_write_text
from backend.core.paths import (
    ATTACHMENTS_DIR,
    BACKUPS_DIR,
    CACHE_DIR,
    CODEX_HOME_DIR,
    DATA_DIR,
    FX_CACHE_JSON,
    SKILL_BUNDLES_DIR,
    is_intentionally_file_backed,
)


def test_documented_exceptions_are_recognized():
    assert is_intentionally_file_backed(FX_CACHE_JSON)
    assert is_intentionally_file_backed(ATTACHMENTS_DIR / "whatever.json")
    # Nested/dynamically-named content under a blob directory is exempt too — a blob
    # pool's whole point is per-item files this checker was never meant to enumerate.
    assert is_intentionally_file_backed(ATTACHMENTS_DIR / "nested" / "deep" / "whatever.json")
    # The third category (frozen historical/backup record), found only once this wave
    # re-ran the whole-tree audit against real accumulated data.
    assert is_intentionally_file_backed(BACKUPS_DIR / "20260810T203000Z.zip")
    assert is_intentionally_file_backed(SKILL_BUNDLES_DIR / "some-skill" / "v1" / "manifest.json")


def test_a_symlink_inside_an_exempt_directory_is_recognized_by_its_own_location(tmp_path):
    """Regression test for the exact bug this wave's own final audit found:
    ``CODEX_HOME_DIR/auth.json`` is a DELIBERATE symlink to the operator's real
    ``~/.codex/auth.json`` (``ai/models/codex_home.py``'s own docstring) — a naive
    ``path.resolve()`` follows it to a target entirely OUTSIDE ``CACHE_DIR`` and wrongly
    reports it as a stray. Reproduces the same shape with a throwaway symlink rather than
    depending on a real `$CODEX_HOME` existing in this test environment."""
    CODEX_HOME_DIR.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside_target.json"
    target.write_text("{}")
    link = CODEX_HOME_DIR / f"{new_id('lnk')}.json"
    try:
        link.symlink_to(target)
        assert is_intentionally_file_backed(link), (
            "a symlink placed INSIDE a documented exception directory must be recognized "
            "by its own location, not by where it points"
        )
    finally:
        link.unlink(missing_ok=True)


def test_an_arbitrary_stray_path_is_not_exempt():
    assert not is_intentionally_file_backed(DATA_DIR / "some_stray_store.json")
    assert not is_intentionally_file_backed(CACHE_DIR / "another_stray_cache.json")
    # SESSIONS_DIR itself is NOT a blob-pool exception post-Wave-5F: every file left in
    # it going forward is either an inert `.migrated-`/`.removed-` relic or a genuine
    # regression, never a fresh, legitimately-written `.extraction_cursor.json`.
    assert not is_intentionally_file_backed(
        DATA_DIR / "sessions" / f"{new_id('sess')}.extraction_cursor.json"
    )


def _snapshot_json_files(*roots: Path) -> set[Path]:
    found: set[Path] = set()
    for root in roots:
        if root.exists():
            found.update(p.resolve() for p in root.rglob("*.json"))
    return found


def test_representative_writes_produce_no_stray_json(db_isolated):
    """``db_isolated`` boots a fresh migrated database before this test body runs — the
    snapshot below is taken AFTER that fixture completes, so any of ITS OWN setup-time
    filesystem activity (unrelated to this test) is already reflected in the baseline and
    never counted as something this test's own writes produced.
    """
    from backend.db.account_store import AccountStore
    from backend.db.memory_consolidate_state_store import MemoryConsolidateStateStore
    from backend.db.news_screen_state_store import NewsScreenStateStore
    from backend.db.session_store import SessionStore

    before = _snapshot_json_files(DATA_DIR, CACHE_DIR)

    # An ordinary Tier-1 store write, unrelated to Wave 5F — the baseline "money data
    # writes stay in the database" every prior wave already established.
    AccountStore().create(
        {"name": "Guard Test", "kind": "asset", "type": "cash", "currency": "USD"}
    )

    # Wave 5F task 1: a session transcript save, plus the new extraction_cursor column.
    session_id = new_id("sess")
    SessionStore().set_transcript(session_id, [{"role": "user", "content": "hi"}])
    SessionStore().set_extraction_cursor(session_id, {"last_extracted_len": 1})

    # Wave 5F task 2: all three CACHE_DIR watermarks that were judged to be STATE, not
    # cache, and moved onto runtime_state namespace rows.
    NewsScreenStateStore().save({"day": "2026-08-10", "screened": 1})
    MemoryConsolidateStateStore().save({"last_count": 1, "ts": "2026-08-10T00:00:00+00:00"})

    # A write made DIRECTLY into a documented exception directory — proves the checker
    # tolerates a REAL exception rather than the assertion below passing only because
    # nothing new happened to appear.
    exempt_write = ATTACHMENTS_DIR / f"{new_id('upl')}.json"
    try:
        atomic_write_text(exempt_write, json.dumps({"probe": True}))

        after = _snapshot_json_files(DATA_DIR, CACHE_DIR)
        new_files = after - before

        assert exempt_write.resolve() in new_files, (
            "the deliberate exempt write should have shown up in the diff — "
            "the snapshot/diff mechanics themselves are broken if not"
        )
        stray = sorted(p for p in new_files if not is_intentionally_file_backed(p))
        assert not stray, (
            "representative writes produced stray .json file(s) outside "
            f"INTENTIONALLY_FILE_BACKED: {stray}"
        )
    finally:
        exempt_write.unlink(missing_ok=True)
