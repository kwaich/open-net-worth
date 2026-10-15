import json

from sqlalchemy import select

ROWS = [
    {
        "id": "ev_a",
        "url": "https://x.com/1",
        "domain": "x.com",
        "title": "A",
        "snippet": "",
        "subjects": ["AAPL", "MSFT"],
        "queries": ["q1"],
        "first_seen": "2026-08-01T00:00:00+00:00",
        "last_seen": "2026-08-02T00:00:00+00:00",
        "expires_at": "2026-08-09T00:00:00+00:00",
        "seen_count": 2,
        "kind": "news",
        "provider": "rss",
        "source": "s",
        "source_url": "",
        "via": "",
        "guid": "",
        "author": "",
        "image": None,
        "published_at": "",
    },
    {
        "id": "ev_b",
        "url": "https://y.com/2",
        "domain": "y.com",
        "title": "B",
        "snippet": "",
        "subjects": ["TSLA"],
        "queries": [],
        "first_seen": "2026-08-03T00:00:00+00:00",
        "last_seen": "2026-08-03T00:00:00+00:00",
        "expires_at": "2026-08-04T00:00:00+00:00",
        "seen_count": 1,
        "kind": "news",
        "provider": "rss",
        "source": "s",
        "source_url": "",
        "via": "",
        "guid": "",
        "author": "",
        "image": None,
        "published_at": "",
    },
]


def test_import_then_export_round_trips_exactly(db_engine):
    from backend.db.evidence_io import export_evidence, import_evidence_json

    with db_engine.begin() as conn:
        n = import_evidence_json(conn, ROWS)
    assert n == 2
    with db_engine.connect() as conn:
        assert export_evidence(conn) == ROWS  # exact, ordered equality


def test_import_populates_derived_index_columns(db_engine):
    from backend.db import tables as t
    from backend.db.evidence_io import import_evidence_json

    with db_engine.begin() as conn:
        import_evidence_json(conn, ROWS)
    with db_engine.connect() as conn:
        row = conn.execute(select(t.evidence).where(t.evidence.c.id == "ev_a")).one()
        assert (row.expires_at, row.domain, row.seq) == ("2026-08-09T00:00:00+00:00", "x.com", 0)
        subs = {
            r.subject
            for r in conn.execute(
                select(t.evidence_subjects).where(t.evidence_subjects.c.evidence_id == "ev_a")
            )
        }
        assert subs == {"AAPL", "MSFT"}


def test_full_migration_imports_evidence_json_and_records_cutover(tmp_path, monkeypatch):
    src = tmp_path / "evidence.json"
    src.write_text(json.dumps(ROWS))
    monkeypatch.setenv("ONW_DATABASE_URL", f"sqlite:///{tmp_path}/onw.db")
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.SOURCE", src)
    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()
    from backend.db.evidence_io import export_evidence

    with eng.get_engine().connect() as conn:
        assert export_evidence(conn) == ROWS
    assert not src.exists() and len(list(tmp_path.glob("evidence.json.migrated-*"))) == 1
    eng.dispose_engine()


def test_missing_source_file_is_a_clean_empty_import(db_engine):
    from backend.db.evidence_io import export_evidence

    with db_engine.connect() as conn:
        assert export_evidence(conn) == []


def test_evidence_subjects_cascade_on_delete(db_engine):
    from sqlalchemy import insert, select

    from backend.db import tables as t

    with db_engine.begin() as c:
        c.execute(
            insert(t.evidence).values(
                id="ev1", seq=1, expires_at="", domain="x.com", created_at="", payload={"id": "ev1"}
            )
        )
        c.execute(insert(t.evidence_subjects).values(evidence_id="ev1", subject="AAPL"))
        c.execute(t.evidence.delete().where(t.evidence.c.id == "ev1"))
        assert c.execute(select(t.evidence_subjects)).fetchall() == []
