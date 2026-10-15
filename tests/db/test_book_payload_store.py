def test_book_store_writes_default_book_id(db_engine):
    from sqlalchemy import select

    from backend.db.payload_store import BookPayloadTableStore
    from backend.db.tables import (  # a real, already-registered book table
        DEFAULT_BOOK_ID,
        envelope_groups,
    )

    store = BookPayloadTableStore(envelope_groups, id_field="id", engine=db_engine)
    store.insert({"id": "grp_x", "name": "E"})
    with db_engine.connect() as conn:
        row = conn.execute(select(envelope_groups)).first()
    assert row.book_id == DEFAULT_BOOK_ID  # column present + written
    assert store.find_by_id("grp_x")["name"] == "E"  # surface unchanged


def test_generic_payload_store_unaffected_by_refactor():
    from backend.db.tables import instruments  # Tier-2, payload_table()

    assert "book_id" not in instruments.c  # the _insert_row refactor is default-identical
    assert {c.name for c in instruments.c} == {"seq", "id", "payload"}
