from backend.db.institution_store import InstitutionStore


def test_resolve_or_create_reuses_normalized_name(db_isolated):
    store = InstitutionStore()

    first = store.resolve_or_create("  DBS  ")
    second = store.resolve_or_create("dbs")

    assert first["id"].startswith("inst_")
    assert first == second
    assert store.all() == [first]
