import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def eng(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    with e.connect() as c:
        c.execute(text("PRAGMA foreign_keys=ON"))
        c.commit()
    from backend.db.tables import metadata

    metadata.create_all(e)
    yield e
    e.dispose()


def test_create_all_and_seed_shapes(eng):
    from backend.db import tables as t

    with eng.begin() as c:
        c.execute(
            insert(t.users).values(
                id=t.DEFAULT_USER_ID,
                email="",
                display_name="Owner",
                created_at="2026-08-07T00:00:00+00:00",
            )
        )
        c.execute(
            insert(t.books).values(
                id=t.DEFAULT_BOOK_ID,
                name="My Book",
                base_currency="",
                created_at="2026-08-07T00:00:00+00:00",
            )
        )
        c.execute(
            insert(t.book_members).values(
                user_id=t.DEFAULT_USER_ID, book_id=t.DEFAULT_BOOK_ID, role="owner"
            )
        )
    with eng.connect() as c:
        assert c.execute(select(t.book_members.c.role)).scalar() == "owner"


def test_book_members_fk_enforced(eng):
    from backend.db import tables as t

    with pytest.raises(IntegrityError):
        with eng.begin() as c:
            c.execute(text("PRAGMA foreign_keys=ON"))
            c.execute(
                insert(t.book_members).values(user_id="usr_nope", book_id="book_nope", role="owner")
            )
