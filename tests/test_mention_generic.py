"""Unit tests for the generic mention engine (generic.py, _collect.py, mention_guard.py).

P0 scaffolding tests per RFC 2026-06-25-lucius-generic-entity-registry §10 Phase 0.

Covers:
- make_search: q-match, synonym match, cap, surface, search_override bypass
- make_resolve: find_one path, _find_by_id fallback, project_fn bypass, not_found
- _project: projection onto d.fields, sensitive-field omit-at-source, unknown key drop
- _collect.register: duplicate type raises MentionError, secret field key raises ValueError,
  forbidden-store callable raises ValueError
- mention_guard.check_descriptor_sources: AuthStore-backed descriptor is refused,
  including module-level lambda patterns (global name references via co_names)
- format_mention_context ellipsis: exactly-MAX_FACT_LEN values must NOT get ellipsis;
  only values LONGER than MAX_FACT_LEN get it (off-by-one regression guard)
"""

from __future__ import annotations

import pytest

from backend.core.errors import MentionError
from backend.core.mention_guard import (
    MENTION_FORBIDDEN_STORES,
    check_descriptor_sources,
    check_fields_keys,
)
from backend.services.mentions._collect import build_registry, register
from backend.services.mentions._common import _ROW_CAP
from backend.services.mentions.descriptor import EntityDescriptor, FieldSpec
from backend.services.mentions.generic import _find_by_id, _project, make_resolve, make_search
from backend.services.mentions.types import SearchQuery

# ── Fixture store (simple list — no db dependency) ────────────────────────────

_FIXTURE_ROWS = [
    {"id": "w1", "name": "Widget Alpha", "amount": 100, "secret_key": "SHOULD_NOT_LEAK"},
    {"id": "w2", "name": "Widget Beta", "amount": 200},
    {"id": "w3", "name": "Zeta Thing", "amount": 300},
]


def _make_rows_callable(rows=None):
    _rows = rows if rows is not None else _FIXTURE_ROWS
    return lambda q: list(_rows)


def _query(q: str = "", limit: int = 10, surfaces=frozenset({"entity"})) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=surfaces, limit=limit, cursor=None)


def _simple_descriptor(**kwargs) -> EntityDescriptor:
    defaults = dict(
        type="widget",
        label="Widget",
        icon="◆",
        order=99,
        rows=_make_rows_callable(),
        label_field="name",
        search_fields=("name",),
        fields={
            "name": FieldSpec("Name", in_sublabel=True),
            "amount": FieldSpec("Amount", "money"),
        },
    )
    defaults.update(kwargs)
    return EntityDescriptor(**defaults)


# ── _project ──────────────────────────────────────────────────────────────────


def test_project_includes_only_declared_fields():
    d = _simple_descriptor(
        fields={
            "name": FieldSpec("Name"),
            "amount": FieldSpec("Amount"),
        }
    )
    row = {"id": "w1", "name": "Widget Alpha", "amount": 100, "undeclared": "nope"}
    result = _project(d, row)
    assert set(result.keys()) == {"name", "amount"}
    assert result["name"] == "Widget Alpha"
    assert result["amount"] == 100


def test_project_omits_sensitive_field_at_source():
    d = _simple_descriptor(
        fields={
            "name": FieldSpec("Name"),
            "secret_key": FieldSpec("Secret", sensitive=True),
        }
    )
    row = {"name": "Alice", "secret_key": "super-secret-value"}
    result = _project(d, row)
    assert "secret_key" not in result, "sensitive field must be omitted at source"
    assert result == {"name": "Alice"}


def test_project_skips_none_values():
    d = _simple_descriptor(
        fields={
            "name": FieldSpec("Name"),
            "amount": FieldSpec("Amount"),
        }
    )
    row = {"name": "Widget", "amount": None}
    result = _project(d, row)
    assert "amount" not in result
    assert result == {"name": "Widget"}


def test_project_empty_fields_returns_empty_dict():
    d = _simple_descriptor(fields={})
    row = {"id": "x", "name": "X", "secret": "leak?"}
    result = _project(d, row)
    assert result == {}


# ── make_search ───────────────────────────────────────────────────────────────


def test_make_search_returns_all_on_empty_q():
    d = _simple_descriptor()
    fn = make_search(d)
    rows = fn(_query(q=""))
    assert len(rows) == 3
    assert all("type" in r and r["type"] == "widget" for r in rows)


def test_make_search_q_match_case_insensitive():
    d = _simple_descriptor()
    fn = make_search(d)
    rows = fn(_query(q="widget"))
    labels = [r["label"] for r in rows]
    assert "Widget Alpha" in labels
    assert "Widget Beta" in labels
    assert not any("Zeta" in lbl for lbl in labels)


def test_make_search_no_match_returns_empty():
    d = _simple_descriptor()
    fn = make_search(d)
    rows = fn(_query(q="xyz_no_match_999"))
    assert rows == []


def test_make_search_row_shape():
    d = _simple_descriptor()
    fn = make_search(d)
    rows = fn(_query(q=""))
    for row in rows:
        assert {"type", "id", "label", "sublabel", "value", "icon", "surface"} <= set(row)
        assert row["type"] == "widget"
        assert row["surface"] == "entity"
        assert row["value"] == row["id"]  # value == id for store-backed types


def test_make_search_respects_row_cap():
    big_rows = [{"id": str(i), "name": f"Item {i}"} for i in range(100)]
    d = _simple_descriptor(rows=_make_rows_callable(big_rows))
    fn = make_search(d)
    rows = fn(_query(q="", limit=100))
    assert len(rows) <= _ROW_CAP


def test_make_search_synonym_match_returns_all():
    # "automations" triggers the automation synonym → returns all rows
    d = _simple_descriptor(type="automation", label="Automation", icon="✦")
    fn = make_search(d)
    rows = fn(_query(q="automations"))
    assert len(rows) == 3  # all rows returned on synonym match


def test_make_search_uses_override_when_set():
    override_called = []

    def _override(q):
        override_called.append(True)
        return [
            {
                "type": "widget",
                "id": "ov",
                "label": "Override",
                "sublabel": "",
                "value": "ov",
                "icon": "◆",
                "surface": "entity",
            }
        ]

    d = _simple_descriptor(search_override=_override)
    fn = make_search(d)
    rows = fn(_query(q=""))
    assert override_called, "search_override must be used when set"
    assert rows[0]["id"] == "ov"


# ── make_resolve ──────────────────────────────────────────────────────────────


def test_make_resolve_find_one_path():
    find_one_called = []

    def _find_one(ref):
        find_one_called.append(ref)
        return {"id": ref, "name": "Found", "amount": 42}

    d = _simple_descriptor(find_one=_find_one)
    fn = make_resolve(d)
    result = fn("w1")
    assert find_one_called == ["w1"]
    assert result == {"name": "Found", "amount": 42}


def test_make_resolve_fallback_scan_when_no_find_one():
    d = _simple_descriptor()
    fn = make_resolve(d)
    result = fn("w2")
    assert result is not None
    assert result.get("name") == "Widget Beta"


def test_make_resolve_not_found_returns_none():
    d = _simple_descriptor()
    fn = make_resolve(d)
    result = fn("non-existent-id")
    assert result is None


def test_make_resolve_project_fn_bypasses_fields():
    project_fn_called = []

    def _proj(row):
        project_fn_called.append(row)
        return {"custom_field": "custom_value"}

    d = _simple_descriptor(project_fn=_proj)
    fn = make_resolve(d)
    result = fn("w1")
    assert project_fn_called, "project_fn must be called when set"
    assert result == {"custom_field": "custom_value"}


def test_make_resolve_uses_override_when_set():
    override_called = []

    def _override(ref):
        override_called.append(ref)
        return {"resolved": True}

    d = _simple_descriptor(resolve_override=_override)
    fn = make_resolve(d)
    result = fn("any-ref")
    assert override_called, "resolve_override must be used when set"
    assert result == {"resolved": True}


# ── _collect.register ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_collect():
    """Reset the _collect module state before each test so register() calls don't accumulate."""
    import backend.services.mentions._collect as col

    original_descriptors = list(col._DESCRIPTORS)
    original_types = set(col._REGISTERED_TYPES)
    yield
    col._DESCRIPTORS[:] = original_descriptors
    col._REGISTERED_TYPES.clear()
    col._REGISTERED_TYPES.update(original_types)


def test_register_and_build_registry():
    d = _simple_descriptor(type="unique_test_type_a", order=1000)
    register(d)
    registry = build_registry()
    assert "unique_test_type_a" in registry


def test_register_duplicate_raises_mention_error():
    d1 = _simple_descriptor(type="dup_type", order=1001)
    d2 = _simple_descriptor(type="dup_type", order=1002)
    register(d1)
    with pytest.raises(MentionError, match="Duplicate descriptor registration"):
        register(d2)


def test_register_multi_surface_without_override_raises():
    """R12: a multi-surface descriptor must supply search_override (generic search is single-surface)."""
    d = _simple_descriptor(
        type="multi_no_override",
        order=1006,
        surfaces=frozenset({"entity", "agent"}),
    )
    with pytest.raises(MentionError, match="must set search_override"):
        register(d)


def test_register_multi_surface_with_override_ok():
    """A multi-surface descriptor WITH a search_override registers fine."""
    d = _simple_descriptor(
        type="multi_with_override",
        order=1007,
        surfaces=frozenset({"entity", "agent"}),
        search_override=lambda q: [],
    )
    register(d)
    assert "multi_with_override" in build_registry()


def test_register_secret_field_key_raises():
    d = _simple_descriptor(
        type="secret_type",
        order=1003,
        fields={"password": FieldSpec("Pass")},
    )
    with pytest.raises(ValueError, match="secret key denylist"):
        register(d)


def test_register_secret_key_variants():
    """Multiple secret key name patterns must all be caught."""
    for bad_key in ("api_key", "token", "credential", "private_key", "session"):
        import backend.services.mentions._collect as col

        col._REGISTERED_TYPES.discard("sk_test")
        d = _simple_descriptor(
            type="sk_test",
            order=1004,
            fields={bad_key: FieldSpec("Bad")},
        )
        with pytest.raises(ValueError, match="secret key denylist"):
            register(d)
        col._REGISTERED_TYPES.discard("sk_test")


def test_register_sensitive_field_does_not_raise():
    """A sensitive field with a non-secret name is allowed — sensitivity is runtime."""
    d = _simple_descriptor(
        type="sens_type",
        order=1005,
        fields={"internal_note": FieldSpec("Note", sensitive=True)},
    )
    register(d)  # must not raise


# ── mention_guard: forbidden-store checks ─────────────────────────────────────


def test_check_descriptor_sources_authstore_raises():
    """A descriptor whose rows callable references AuthStore must be refused."""

    class AuthStore:
        pass

    store = AuthStore()

    def rows_fn(q):
        return store.all() if hasattr(store, "all") else []

    with pytest.raises(ValueError, match="forbidden store"):
        check_descriptor_sources(
            "auth_type",
            rows=rows_fn,
            find_one=None,
            project_fn=None,
            forbidden_sources=frozenset({"AuthStore"}),
        )


def test_check_descriptor_sources_module_level_lambda_raises():
    """Regression: a module-level lambda ``rows=lambda q: AuthStore().all()``
    references AuthStore as a GLOBAL name (``__closure__`` is None; the class
    name lives in ``fn.__code__.co_names``).  The old guard missed this because
    it only walked ``__closure__`` cells.
    """

    class FakeSecretStore:
        pass

    # Build a lambda that references FakeSecretStore as a global name, not a closure.
    fn = eval(
        compile("lambda q: FakeSecretStore().all()", "<module-level>", "eval"),
        {"FakeSecretStore": FakeSecretStore},
    )
    assert fn.__closure__ is None, "precondition: no closure cell"
    assert "FakeSecretStore" in fn.__code__.co_names, "precondition: name in co_names"

    with pytest.raises(ValueError, match="forbidden store"):
        check_descriptor_sources(
            "secret_via_global",
            rows=fn,
            find_one=None,
            project_fn=None,
            forbidden_sources=frozenset({"FakeSecretStore"}),
        )


def test_check_descriptor_sources_clean_callable_passes():
    """A callable not referencing any forbidden store must not raise."""

    def _rows(q):
        return []

    check_descriptor_sources(
        "clean_type",
        rows=_rows,
        find_one=None,
        project_fn=None,
        forbidden_sources=MENTION_FORBIDDEN_STORES,
    )


def test_check_fields_keys_secret_name_raises():
    with pytest.raises(ValueError, match="secret key denylist"):
        check_fields_keys("test_type", {"api_key": FieldSpec("API key")})


def test_check_fields_keys_clean_names_pass():
    check_fields_keys("test_type", {"name": FieldSpec("Name"), "amount": FieldSpec("Amount")})


# ── _find_by_id ───────────────────────────────────────────────────────────────


def test_find_by_id_finds_matching_row():
    d = _simple_descriptor()
    row = _find_by_id(d, "w2")
    assert row is not None
    assert row["id"] == "w2"


def test_find_by_id_returns_none_for_missing():
    d = _simple_descriptor()
    row = _find_by_id(d, "does-not-exist")
    assert row is None


def test_find_by_id_returns_none_when_no_rows():
    d = _simple_descriptor(rows=None)
    row = _find_by_id(d, "w1")
    assert row is None


# ── format_mention_context: ellipsis off-by-one regression ────────────────────


def test_format_mention_context_exactly_max_fact_len_no_ellipsis():
    """A value exactly 140 chars must NOT get an ellipsis (nothing was truncated)."""
    from backend.services.mentions.dispatch import format_mention_context

    exactly_140 = "x" * 140
    resolved = [
        {
            "type": "widget",
            "ref": "w1",
            "label": "Widget",
            "data": {"name": exactly_140},
        }
    ]
    out = format_mention_context(resolved)
    assert "x" * 140 in out
    assert ("x" * 140 + "…") not in out, "no ellipsis when value is exactly MAX_FACT_LEN"


def test_format_mention_context_over_max_fact_len_gets_ellipsis():
    """A value longer than 140 chars must be truncated and end with '…'."""
    from backend.services.mentions.dispatch import format_mention_context

    over_140 = "y" * 141
    resolved = [
        {
            "type": "widget",
            "ref": "w1",
            "label": "Widget",
            "data": {"name": over_140},
        }
    ]
    out = format_mention_context(resolved)
    assert ("y" * 140 + "…") in out, "truncated value must end with ellipsis"
    assert "y" * 141 not in out, "original over-length value must not appear verbatim"


def test_every_store_over_the_connectors_tables_is_forbidden_to_mention_descriptors():
    """A facade over ``connectors`` / ``connector_secrets`` can hand back a credential; a
    descriptor must never reach one. Price-source HEALTH stores hold quota and circuit state,
    no credential, so they are the only exemption."""
    import importlib
    import inspect
    import pkgutil

    import backend.db as db_package
    from backend.db.aggregator_config_store import AggregatorConfigStore

    health_only = {"CoinGeckoQuotaStore", "MarketProviderCircuitStore"}
    found: set[str] = set()
    for module_info in pkgutil.iter_modules(db_package.__path__):
        module = importlib.import_module(f"backend.db.{module_info.name}")
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__ or name in health_only:
                continue
            if issubclass(cls, AggregatorConfigStore) or "ConnectorStore" in inspect.getsource(cls):
                found.add(name)

    assert {"ConnectorStore", "McpClientStore", "TelegramConfigStore"} <= found
    assert found <= MENTION_FORBIDDEN_STORES, sorted(found - MENTION_FORBIDDEN_STORES)
