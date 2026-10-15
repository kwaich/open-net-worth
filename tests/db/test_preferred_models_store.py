"""PreferredModelsStore on NamespacedSettingsStore (plan 4d, task 2) — round trip plus
clean_model_refs parity (dedup, order-preserving, bounded).
"""

from __future__ import annotations

from backend.db.preferred_models_store import PreferredModelsStore, clean_model_refs


def test_resolved_defaults_to_empty_list(db_engine):
    store = PreferredModelsStore(engine=db_engine)
    assert store.resolved() == {"models": []}


def test_replace_round_trip(db_engine):
    store = PreferredModelsStore(engine=db_engine)
    result = store.replace(["anthropic:claude-sonnet-5", "openai:gpt-5"])
    assert result == {"models": ["anthropic:claude-sonnet-5", "openai:gpt-5"]}
    assert store.resolved() == result


def test_replace_is_a_full_replace_not_a_patch(db_engine):
    store = PreferredModelsStore(engine=db_engine)
    store.replace(["a:1", "b:2"])
    store.replace(["c:3"])
    assert store.resolved() == {"models": ["c:3"]}


# ── Domain parity: clean_model_refs dedup / bound / order-preserving ───────────────────────


def test_clean_model_refs_dedupes_preserves_order():
    assert clean_model_refs(["a:1", "b:2", "a:1", "c:3"]) == ["a:1", "b:2", "c:3"]


def test_clean_model_refs_drops_non_strings_and_blanks():
    assert clean_model_refs(["a:1", "", 5, None, "  "]) == ["a:1"]


def test_clean_model_refs_caps_count_and_length():
    from backend.db.preferred_models_store import _MAX_MODELS, _MAX_REF_LEN

    many = [f"m:{i}" for i in range(_MAX_MODELS + 10)]
    assert len(clean_model_refs(many)) == _MAX_MODELS

    long_ref = "x" * (_MAX_REF_LEN + 50)
    assert len(clean_model_refs([long_ref])[0]) == _MAX_REF_LEN


def test_replace_applies_clean_model_refs(db_engine):
    store = PreferredModelsStore(engine=db_engine)
    result = store.replace(["a:1", "a:1", "", None])
    assert result == {"models": ["a:1"]}
