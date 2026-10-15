def _mk(id, subjects, expires, domain="x.com", seen=1):
    return {
        "id": id,
        "url": f"https://{domain}/{id}",
        "domain": domain,
        "title": id,
        "snippet": "",
        "subjects": subjects,
        "queries": [],
        "first_seen": "2026-08-01T00:00:00+00:00",
        "last_seen": "2026-08-01T00:00:00+00:00",
        "expires_at": expires,
        "seen_count": seen,
        "kind": "news",
        "provider": "p",
        "source": "s",
        "source_url": "",
        "via": "",
        "guid": "",
        "author": "",
        "image": None,
        "published_at": "",
    }


def test_merge_hits_inserts_then_merges(db_engine):
    from backend.db.evidence_store import EvidenceStore

    store = EvidenceStore(engine=db_engine)
    store.merge_hits([_mk("e1", ["AAPL"], "2026-08-10T00:00:00+00:00")])
    out = store.merge_hits([_mk("e1", ["MSFT"], "2026-08-12T00:00:00+00:00")])
    assert out[0]["subjects"] == ["AAPL", "MSFT"]  # union, sorted
    assert out[0]["seen_count"] == 2  # incremented
    assert out[0]["expires_at"] == "2026-08-12T00:00:00+00:00"  # max
    assert store.count() == 1


def test_for_subjects_any_match_freshness_and_order(db_engine):
    from backend.db.evidence_store import EvidenceStore

    store = EvidenceStore(engine=db_engine)
    store.merge_hits(
        [
            _mk("e1", ["AAPL"], "2026-08-10T00:00:00+00:00"),
            _mk("e2", ["TSLA"], "2026-08-02T00:00:00+00:00"),
            _mk("e3", ["AAPL", "TSLA"], "2026-08-10T00:00:00+00:00"),
        ]
    )
    now = "2026-08-05T00:00:00+00:00"
    assert [r["id"] for r in store.for_subjects(["AAPL", "TSLA"], now=now)] == ["e1", "e3"]
    assert [r["id"] for r in store.for_subjects(["TSLA"], fresh_only=False)] == ["e2", "e3"]
    assert store.for_subjects([]) == []


def test_prune_respects_keep_domains_and_cascades(db_engine):
    from sqlalchemy import select

    from backend.db import tables as t
    from backend.db.evidence_store import EvidenceStore

    store = EvidenceStore(engine=db_engine)
    store.merge_hits(
        [
            _mk("e1", ["AAPL"], "2026-08-02T00:00:00+00:00", domain="stale.com"),
            _mk("e2", ["MSFT"], "2026-08-02T00:00:00+00:00", domain="evergreen.com"),
            _mk("e3", ["TSLA"], "2026-08-10T00:00:00+00:00"),
        ]
    )
    removed = store.prune("2026-08-05T00:00:00+00:00", keep_domains=frozenset({"evergreen.com"}))
    assert removed == 1
    assert {r["id"] for r in store.all()} == {"e2", "e3"}
    with db_engine.connect() as c:
        orphans = c.execute(
            select(t.evidence_subjects).where(t.evidence_subjects.c.evidence_id == "e1")
        ).fetchall()
    assert orphans == []


def test_merge_hits_concurrent_writers_no_seq_collision(db_engine):
    """Regression: intel_agent.run_intel_scan fans out two gather_rss calls that share ONE
    EvidenceStore via asyncio.gather, each hopping to a worker thread via asyncio.to_thread
    (monitoring/collect.py's ``_record_and_read``) — so two threads can call merge_hits on
    the SAME store concurrently. next_seq is a SELECT-max-then-INSERT: without serialization,
    two overlapping transactions can read the same max(seq) before either commits and then
    both insert it, raising a UNIQUE-constraint IntegrityError (reproduced by this test
    against a pre-fix build). The old JsonStore backing never had this failure mode — every
    mutating call serialized under one process-wide, path-keyed threading.Lock
    (json_store._lock_for) — so this is a parity break the store must close, not a test to
    relax."""
    import concurrent.futures

    from backend.db.evidence_store import EvidenceStore

    store = EvidenceStore(engine=db_engine)
    n = 24

    def _insert_one(i: int) -> None:
        store.merge_hits([_mk(f"c{i}", ["AAPL"], "2026-08-10T00:00:00+00:00")])

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(_insert_one, range(n)))

    assert store.count() == n
    with db_engine.connect() as c:
        from sqlalchemy import select

        from backend.db import tables as t

        seqs = sorted(r.seq for r in c.execute(select(t.evidence.c.seq)))
    assert seqs == list(range(n))  # every seq unique, none skipped


def test_parity_with_merge_row_semantics(db_engine):
    """The DB store and the pure _merge_row agree — the characterization guard."""
    from backend.db.evidence_store import EvidenceStore, _merge_row

    a = _mk("e1", ["B", "A"], "2026-08-10T00:00:00+00:00")
    b = _mk("e1", ["C"], "2026-08-09T00:00:00+00:00", seen=1)
    store = EvidenceStore(engine=db_engine)
    store.merge_hits([a])
    db_result = store.merge_hits([b])[0]
    assert db_result == _merge_row(a, b)


# ── _eng() must not cache a lazily-resolved engine (fix round 1, plan 3 task 4) ────
#
# The identical bug found and fixed in all six money stores and PayloadTableStore.
# EvidenceStore() (engine=None, the lazy default) is constructed fresh inside several
# real functions (backend/monitoring/collect.py, backend/monitoring/intel_agent.py,
# backend/services/evidence.py, backend/services/news_latency.py) rather than held as a
# module-level singleton, so the practical blast radius is smaller than the money
# stores' — but the SAME hazard exists for any instance that outlives a later redirect
# (a long-running background/monitoring task, or a future caller that DOES cache one),
# so it gets the same fix and the same direct proof, not just an assumption.


def test_eng_default_is_never_cached_across_a_later_engine_redirect(
    db_isolated, tmp_path, monkeypatch
):
    """engine=None (the default) must be re-resolved via get_engine() on EVERY call —
    never cached after the first."""
    from backend.db import engine as eng
    from backend.db.evidence_store import EvidenceStore

    store = EvidenceStore()
    first = store._eng()
    assert first is eng.get_engine()

    _redirect_engine(tmp_path / "second.db", monkeypatch)

    second = store._eng()
    assert second is eng.get_engine()
    assert second is not first, (
        "engine=None must re-resolve per call — a cached default silently ignores a later redirect"
    )


def test_eng_injected_engine_stays_honored_across_a_later_global_redirect(
    db_isolated, tmp_path, monkeypatch
):
    """The other half of the contract: an EXPLICITLY injected engine= stays pinned even
    if the process-wide engine singleton is later redirected."""
    from backend.db import engine as eng
    from backend.db.evidence_store import EvidenceStore

    injected = eng.get_engine()
    store = EvidenceStore(engine=injected)

    _redirect_engine(tmp_path / "second.db", monkeypatch)

    assert store._eng() is injected
    assert store._eng() is not eng.get_engine()


def _redirect_engine(db_path, monkeypatch) -> None:
    """Simulates a LATER db_isolated-style redirect happening mid-test (env var +
    dispose_engine() — see tests/db/conftest.py's own db_isolated fixture)."""
    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    eng.dispose_engine()
    command.upgrade(make_config(url), "head")
