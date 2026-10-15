"""
AI tool-dispatch tests for backend/ai/tools/payees.py.

Covers the batch-pluralized surface (2026-07-19): every write tool now accepts one target
via bare fields (unchanged trivial single-target call) OR many via a batch array — either
the "same change on N" broadcast (payee_id as a list + top-level fields) or a divergent
per-item ``updates``/``renames``/... array. Also covers that every renamed tool's OLD name
still dispatches correctly via the registry alias table (a tool name is persisted data).

Isolation strategy:
  - Payee store: DB-backed (plan 4d task 2) — db_isolated redirects the module singleton
    ``payee_registry._store`` to a fresh, empty database.
  - Transaction store: monkeypatch ``TransactionStore.all`` / ``replace_all`` to
    avoid touching disk.

All tests are offline (no LLM, no network) — no ``integration`` marker needed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services import payee_registry

# ── helpers ────────────────────────────────────────────────────────────────────


def call(tool: str, **inp):
    """Run execute_tool synchronously; return (parsed_json, proposed_change)."""
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


@pytest.fixture
def reg(db_isolated):
    """Isolate the singleton PayeeStore to a fresh, empty database. CategoryStore is
    ALSO DB-backed and shares this same redirected engine, so it needs reseeding here too
    (tests/conftest.py's shared autouse seed ran BEFORE db_isolated activated and landed in
    the old, now-orphaned default engine) — several tests below resolve a default category
    by LABEL (e.g. "shopping"), which needs the real builtin rows present to resolve."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    return payee_registry


@pytest.fixture
def reg_with_txns(reg, monkeypatch):
    """Also patch TransactionStore so store-writes that touch transactions are safe."""
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)
    return reg


# ── tool registration ──────────────────────────────────────────────────────────


def test_all_payee_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "get_payees",
        "create_payees",
        "rename_payees",
        "set_payees_default_category",
        "set_payees_hidden",
        "set_payees_autocategorize",
        "add_payee_aliases",
        "remove_payee_aliases",
        "add_payee_rules",
        "remove_payee_rules",
        "set_payees_rules",
        "apply_payee_rules",
        "apply_payee_rules_to_payees",
        "apply_payee_categories",
        "merge_payees",
        "delete_payees",
    } <= names


def test_former_singular_names_are_not_offered_directly():
    """Aliases stay out of the offered schemas (zero prompt cost, one name per tool)."""
    names = {t["name"] for t in TOOL_SCHEMAS}
    formerly = {
        "create_payee",
        "rename_payee",
        "set_payee_default_category",
        "set_payee_hidden",
        "set_payee_autocategorize",
        "add_payee_alias",
        "remove_payee_alias",
        "add_payee_rule",
        "remove_payee_rule",
        "set_payee_rules",
        "apply_payee_rules_to_payee",
        "apply_payee_category",
        "delete_payee",
    }
    assert not (formerly & names)


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [
        ("create_payee", "create_payees"),
        ("rename_payee", "rename_payees"),
        ("set_payee_default_category", "set_payees_default_category"),
        ("set_payee_hidden", "set_payees_hidden"),
        ("set_payee_autocategorize", "set_payees_autocategorize"),
        ("add_payee_alias", "add_payee_aliases"),
        ("remove_payee_alias", "remove_payee_aliases"),
        ("add_payee_rule", "add_payee_rules"),
        ("remove_payee_rule", "remove_payee_rules"),
        ("set_payee_rules", "set_payees_rules"),
        ("apply_payee_rules_to_payee", "apply_payee_rules_to_payees"),
        ("apply_payee_category", "apply_payee_categories"),
        ("delete_payee", "delete_payees"),
    ],
)
def test_old_name_resolves_to_new_tool(old_name, new_name):
    from backend.ai.tools import registry

    assert new_name in registry.registered_tool_names()


def test_a_legacy_singular_name_is_refused_loudly(reg):
    """The 2026-07-18 outage was a stored name that silently resolved to NOTHING. The fix
    was an alias; the fix is now a MIGRATION (ADR 2026-08-25-tool-names-are-final), so a
    former name is an unknown tool that says so — loud, which is the property that mattered.
    The migration is what makes the stored playbook work again."""
    res, proposed = call("create_payee", name="LegacyDispatchCheck")
    assert proposed is None
    assert "unknown tool" in res.get("error", "")


def test_get_payees_schema_has_no_registry_param():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_payees")
    assert "registry" not in schema["input_schema"]["properties"]


def test_get_payees_schema_advertises_queries_as_string_array():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_payees")
    queries = schema["input_schema"]["properties"]["queries"]
    assert queries["type"] == "array"
    assert queries["items"] == {"type": "string"}


def test_get_payees_schema_omits_misleading_plural_only_cap():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_payees")
    queries = schema["input_schema"]["properties"]["queries"]
    assert queries["type"] == "array"
    assert "maxItems" not in queries


def test_set_payees_default_category_category_stays_explicit_null():
    """Non-negotiable: `category` must stay a `["string", "null"]` field on every path —
    a collapse to bare `"string"` would make clearing a default inexpressible."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "set_payees_default_category")
    props = schema["input_schema"]["properties"]
    assert props["category"]["type"] == ["string", "null"]
    assert props["updates"]["items"]["properties"]["category"]["type"] == ["string", "null"]


# ── get_payees — always returns registry entities ──────────────────────────────


def test_get_payees_lists_registry_entities(reg_with_txns):
    pid = reg_with_txns.create_payee("Netflix")["id"]
    res, proposed = call("get_payees")
    assert proposed is None
    assert res["count"] >= 1
    ids = [r["id"] for r in res["payees"]]
    assert pid in ids


def test_get_payees_rows_have_id_field(reg_with_txns):
    """Every row must carry an id (pay_…) so callers can always use it as payee_id."""
    reg_with_txns.create_payee("Vantage Brokerage")
    res, _ = call("get_payees")
    assert all("id" in r for r in res["payees"])


def test_get_payees_scalar_query_keeps_legacy_flat_response(reg_with_txns):
    reg_with_txns.create_payee("Spotify Music")
    reg_with_txns.create_payee("Netflix")
    res, _ = call("get_payees", query="spotify")
    assert "results" not in res
    assert res["count"] == 1
    assert res["payees"][0]["name"] == "Spotify Music"


def test_get_payees_query_filters_by_alias(reg_with_txns):
    pid = reg_with_txns.create_payee("Starbucks")["id"]
    reg_with_txns._store.add_alias(pid, "starbucks coffee sg")
    res, _ = call("get_payees", query="coffee sg")
    assert res["count"] == 1
    assert res["payees"][0]["id"] == pid


def test_get_payees_queries_return_ordered_attribution_including_unmatched(reg_with_txns):
    spotify = reg_with_txns.create_payee("Spotify Music")["id"]
    netflix = reg_with_txns.create_payee("Netflix")["id"]
    reg_with_txns.create_payee("Dropbox")

    res, _ = call("get_payees", queries=[" spotify ", "missing", "NETFLIX"])

    assert [result["query"] for result in res["results"]] == ["spotify", "missing", "NETFLIX"]
    assert [[row["id"] for row in result["payees"]] for result in res["results"]] == [
        [spotify],
        [],
        [netflix],
    ]
    assert [(result["showing"], result["total"]) for result in res["results"]] == [
        (1, 1),
        (0, 0),
        (1, 1),
    ]
    assert all("note" not in result for result in res["results"])


def test_get_payees_queries_attribute_overlap_to_each_relevant_result(reg_with_txns):
    starbucks = reg_with_txns.create_payee("Starbucks")["id"]
    reg_with_txns._store.add_alias(starbucks, "starbucks coffee sg")
    reg_with_txns.create_payee("Netflix")

    res, _ = call("get_payees", queries=["starbucks", "coffee sg"])

    assert [[row["id"] for row in result["payees"]] for result in res["results"]] == [
        [starbucks],
        [starbucks],
    ]


def test_get_payees_queries_do_not_deduplicate_repeated_terms(reg_with_txns):
    netflix = reg_with_txns.create_payee("Netflix")["id"]

    res, _ = call("get_payees", queries=["netflix", " netflix "])

    assert [result["query"] for result in res["results"]] == ["netflix", "netflix"]
    assert [[row["id"] for row in result["payees"]] for result in res["results"]] == [
        [netflix],
        [netflix],
    ]


def test_get_payees_scalar_query_precedes_plural_queries(reg_with_txns):
    amazon = reg_with_txns.create_payee("Amazon")["id"]
    etsy = reg_with_txns.create_payee("Etsy")["id"]
    netflix = reg_with_txns.create_payee("Netflix")["id"]

    res, _ = call("get_payees", query=" amazon ", queries=["etsy", "netflix"])

    assert [result["query"] for result in res["results"]] == ["amazon", "etsy", "netflix"]
    assert [[row["id"] for row in result["payees"]] for result in res["results"]] == [
        [amazon],
        [etsy],
        [netflix],
    ]


def test_get_payees_queries_apply_limit_independently_per_result(reg_with_txns):
    for name in ["Apple Music", "Apple Store", "Banana Boat", "Banana Republic"]:
        reg_with_txns.create_payee(name)

    res, _ = call("get_payees", queries=["apple", "banana"], limit=1)

    assert [(result["showing"], result["total"]) for result in res["results"]] == [
        (1, 2),
        (1, 2),
    ]
    assert all(len(result["payees"]) == 1 for result in res["results"])
    assert all("Showing 1 of 2 payees" in result["note"] for result in res["results"])


def test_get_payees_queries_clamp_limit_to_fifty(reg_with_txns):
    for index in range(51):
        reg_with_txns.create_payee(f"Bounded {index:02d}")

    res, _ = call("get_payees", queries=["bounded"], limit=100)

    result = res["results"][0]
    assert result["showing"] == 50
    assert result["total"] == 51
    assert len(result["payees"]) == 50


def test_get_payees_scalar_query_keeps_higher_limit_behavior(reg_with_txns):
    for index in range(51):
        reg_with_txns.create_payee(f"Scalar {index:02d}")

    res, _ = call("get_payees", query="scalar", limit=100)

    assert res["count"] == 51
    assert res["total"] == 51


def test_get_payees_queries_over_ten_return_whole_call_error(reg_with_txns):
    reg_with_txns.create_payee("Netflix")

    res, proposed = call("get_payees", queries=[f"term-{index}" for index in range(11)])

    assert proposed is None
    assert res == {
        "error": "11 queries exceeds the 10-query cap for one call — split into multiple calls."
    }


def test_get_payees_scalar_plus_ten_queries_exceeds_combined_cap(reg_with_txns):
    res, proposed = call(
        "get_payees",
        query="scalar",
        queries=[f"term-{index}" for index in range(10)],
    )

    assert proposed is None
    assert res == {
        "error": "11 queries exceeds the 10-query cap for one call — split into multiple calls."
    }


def test_get_payees_blank_queries_do_not_consume_combined_cap(reg_with_txns):
    terms = [f"term-{index}" for index in range(9)]

    res, _ = call("get_payees", query="scalar", queries=[*terms, " ", ""])

    assert [result["query"] for result in res["results"]] == ["scalar", *terms]


def test_get_payees_queries_read_registry_once(reg_with_txns, monkeypatch):
    reg_with_txns.create_payee("Spotify")
    real_list_registry = reg_with_txns.list_registry
    calls = 0

    def counted_list_registry(**kwargs):
        nonlocal calls
        calls += 1
        return real_list_registry(**kwargs)

    monkeypatch.setattr(payee_registry, "list_registry", counted_list_registry)

    call("get_payees", queries=["spotify", "missing"])

    assert calls == 1


def test_get_payees_blank_query_terms_preserve_unfiltered_behavior(reg_with_txns):
    alpha = reg_with_txns.create_payee("Alpha")["id"]
    beta = reg_with_txns.create_payee("Beta")["id"]

    res, _ = call("get_payees", query=" ", queries=["", "   "])

    assert [row["id"] for row in res["payees"]] == [alpha, beta]


def test_get_payees_payee_ids_ignore_queries_and_limit(reg_with_txns):
    alpha = reg_with_txns.create_payee("Alpha")["id"]
    beta = reg_with_txns.create_payee("Beta")["id"]
    reg_with_txns.create_payee("Netflix")

    res, _ = call("get_payees", payee_ids=[alpha, beta], queries=["netflix"], limit=1)

    assert [row["id"] for row in res["payees"]] == [alpha, beta]


def test_get_payees_limit(reg_with_txns):
    for name in ["Aardvark", "Banana", "Cherry", "Delta", "Echo"]:
        reg_with_txns.create_payee(name)
    res, _ = call("get_payees", limit=2)
    assert len(res["payees"]) == 2
    assert res["total"] == 5


def test_get_payees_by_id_falls_back_to_raw_when_list_registry_empty(reg_with_txns, monkeypatch):
    """Regression: if list_registry() silently returns [] (an internal exception) but the payee
    EXISTS, get_payees must return the payee via the require_payee fallback — NOT a false
    'not found'. (The old code raised NotFoundError unconditionally in that branch.)"""
    pid = reg_with_txns.create_payee("Netflix")["id"]
    monkeypatch.setattr(payee_registry, "list_registry", lambda *a, **k: [])
    res, proposed = call("get_payees", payee_id=pid)
    assert proposed is None
    assert "error" not in res
    assert res["payee"]["id"] == pid


# ── get_payees — payee_id exact lookup ────────────────────────────────────────


def test_get_payees_by_payee_id_returns_entity(reg_with_txns):
    pid = reg_with_txns.create_payee("Anthropic")["id"]
    res, proposed = call("get_payees", payee_id=pid)
    assert proposed is None
    assert res["payee"]["id"] == pid
    assert res["payee"]["name"] == "Anthropic"


def test_get_payees_by_payee_id_not_found_returns_error(reg_with_txns):
    res, proposed = call("get_payees", payee_id="pay_ghost")
    assert proposed is None
    assert "error" in res


# ── get_payees — payee_id as a LIST (multi-lookup) ────────────────────────────


def test_get_payees_by_id_list_returns_all_requested(reg_with_txns):
    a = reg_with_txns.create_payee("Alpha")["id"]
    b = reg_with_txns.create_payee("Beta")["id"]
    c = reg_with_txns.create_payee("Gamma")["id"]
    res, proposed = call("get_payees", payee_id=[a, b, c])
    assert proposed is None
    ids = {r["id"] for r in res["payees"]}
    assert ids == {a, b, c}
    assert res["showing"] == 3
    assert res["total"] == 3
    assert "not_found" not in res


def test_get_payees_by_id_list_reports_misses_by_id(reg_with_txns):
    a = reg_with_txns.create_payee("Alpha")["id"]
    res, proposed = call("get_payees", payee_id=[a, "pay_ghost1", "pay_ghost2"])
    assert proposed is None
    ids = {r["id"] for r in res["payees"]}
    assert ids == {a}
    assert set(res["not_found"]) == {"pay_ghost1", "pay_ghost2"}
    assert "pay_ghost1" in res["note"]


# ── create_payees ────────────────────────────────────────────────────────────


def test_create_payees_returns_entity(reg):
    res, proposed = call("create_payees", name="Lazada")
    assert proposed is None
    assert "payee" in res
    assert res["payee"]["name"] == "Lazada"
    assert res["payee"]["id"].startswith("pay_")


def test_create_payees_with_default_category(reg):
    # default_category is normalized like update_payee's — a label resolves to its id
    # (builtin "Shopping"), never stored raw.
    res, _ = call("create_payees", name="Shopee", default_category="shopping")
    assert res["payee"]["default_categories"] == {"expense": "cat_6c4bc0170000"}


def test_create_payees_conflict_returns_error(reg):
    call("create_payees", name="Netflix")
    # Same normalized name → ConflictError
    res, proposed = call("create_payees", name="NETFLIX")
    assert proposed is None
    assert "error" in res


def test_create_payees_empty_name_returns_error(reg):
    res, _ = call("create_payees", name="   ")
    assert "error" in res


def test_create_payees_structural_category_returns_error(reg):
    res, _ = call("create_payees", name="MyPayee", default_category="split")
    assert "error" in res


def test_create_payees_batch_creates_several_in_one_call(reg):
    res, proposed = call(
        "create_payees",
        payees=[
            {"name": "BatchA"},
            {"name": "BatchB", "default_category": "shopping"},
            {"name": "BatchC"},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["created"] == 3
    assert len(res["results"]) == 3
    names = {r["name"] for r in res["results"]}
    assert names == {"BatchA", "BatchB", "BatchC"}
    # Batch response shape (no single "payee" convenience key for N>1).
    assert "payee" not in res


def test_create_payees_batch_partial_failure_stays_legible(reg):
    """Does partial failure stay legible? 2 of 3 succeed; the caller can tell exactly
    which by index, and the batch is NOT atomic (the good ones still land)."""
    reg.create_payee("Existing")
    res, _ = call(
        "create_payees",
        payees=[
            {"name": "GoodOne"},
            {"name": "EXISTING"},  # conflicts (same normalized name)
            {"name": "GoodTwo"},
        ],
    )
    assert res["ok"] is False
    assert res["created"] == 2
    assert res["results"][0]["ok"] is True
    assert res["results"][1]["ok"] is False
    assert "error" in res["results"][1]
    assert res["results"][2]["ok"] is True
    # The successes are real, durable creates — not rolled back by the one failure.
    names = {p["name"] for p in reg.list_registry()}
    assert {"GoodOne", "GoodTwo"} <= names


# ── rename_payees ──────────────────────────────────────────────────────────────


def test_rename_payees_updates_name(reg):
    pid = reg.create_payee("OldName")["id"]
    res, proposed = call("rename_payees", payee_id=pid, new_name="NewName")
    assert proposed is None
    assert res["payee"]["name"] == "NewName"


def test_rename_payees_not_found_returns_error(reg):
    res, _ = call("rename_payees", payee_id="pay_ghost", new_name="Ghost")
    assert "error" in res


def test_rename_payees_empty_name_returns_error(reg):
    pid = reg.create_payee("Valid")["id"]
    res, _ = call("rename_payees", payee_id=pid, new_name="")
    assert "error" in res


def test_rename_payees_duplicate_name_returns_error(reg):
    """A rename onto another payee's name is a per-item error, not a raised
    ConflictError that would abort the whole batch."""
    reg.create_payee("Netflix")
    pid = reg.create_payee("Spotify")["id"]
    res, _ = call("rename_payees", payee_id=pid, new_name="NETFLIX")
    assert "error" in json.dumps(res)
    assert reg._store.find_by_id(pid)["name"] == "Spotify"


def test_rename_payees_batch_via_renames_array(reg):
    a = reg.create_payee("A1")["id"]
    b = reg.create_payee("B1")["id"]
    res, proposed = call(
        "rename_payees",
        renames=[{"payee_id": a, "new_name": "A2"}, {"payee_id": b, "new_name": "B2"}],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["renamed"] == 2
    assert reg.require_payee(a)["name"] == "A2"
    assert reg.require_payee(b)["name"] == "B2"


# ── set_payees_default_category ─────────────────────────────────────────────────


def test_set_payees_default_category_sets_value(reg):
    pid = reg.create_payee("Amazon")["id"]
    res, proposed = call("set_payees_default_category", payee_id=pid, category="shopping")
    assert proposed is None
    # "shopping" resolves (case-insensitive label match) to the built-in Shopping category id.
    assert res["payee"]["default_categories"]["expense"] == "cat_6c4bc0170000"


def test_set_payees_default_category_clears_with_none(reg):
    pid = reg.create_payee("Grab")["id"]
    reg._store.set_default_category_for_kind(pid, "expense", "cat_transport_test")
    res, _ = call("set_payees_default_category", payee_id=pid, category=None)
    assert "expense" not in res["payee"]["default_categories"]


def test_set_payees_default_category_structural_returns_error(reg):
    pid = reg.create_payee("X")["id"]
    # "split" is the only structural key — cannot be a payee default.
    res, _ = call("set_payees_default_category", payee_id=pid, category="split")
    assert "error" in res


def test_set_payees_default_category_not_found_returns_error(reg):
    res, _ = call("set_payees_default_category", payee_id="pay_ghost", category="food")
    assert "error" in res


def test_set_payees_default_category_income_kind_routes_per_kind(reg):
    # Per-kind (2026-07-10): kind=income lands in default_categories["income"], NOT the
    # legacy expense slot, and never resolves cross-kind.
    from backend.services import categories as cats

    inc = cats.create_category("Salary", "income")["id"]
    pid = reg.create_payee("Employer")["id"]
    res, _ = call("set_payees_default_category", payee_id=pid, category=inc, kind="income")
    assert res["payee"]["default_categories"]["income"] == inc
    assert res["payee"].get("default_category") in (None, "")  # expense slot untouched
    assert reg.default_category_for(pid, "income") == inc
    assert reg.default_category_for(pid, "expense") is None


def test_set_payees_default_category_broadcasts_to_a_list_of_ids(reg):
    """'The SAME category on several payees' — a bare payee_id list + shared fields, no
    per-item repetition required."""
    a = reg.create_payee("Amazon2")["id"]
    b = reg.create_payee("Ebay2")["id"]
    c = reg.create_payee("Etsy2")["id"]
    res, proposed = call("set_payees_default_category", payee_id=[a, b, c], category="shopping")
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 3
    for pid in (a, b, c):
        assert reg.require_payee(pid)["default_categories"]["expense"] == "cat_6c4bc0170000"


def test_set_payees_default_category_updates_array_allows_divergent_values(reg):
    a = reg.create_payee("Amazon3")["id"]
    b = reg.create_payee("Ebay3")["id"]
    res, proposed = call(
        "set_payees_default_category",
        updates=[
            {"payee_id": a, "category": "shopping"},
            {"payee_id": b, "category": "entertainment"},
        ],
    )
    assert proposed is None
    assert res["updated"] == 2
    assert reg.require_payee(a)["default_categories"]["expense"] == "cat_6c4bc0170000"
    assert reg.require_payee(b)["default_categories"]["expense"] == "cat_dfcf9f910000"


# ── set_payees_hidden ──────────────────────────────────────────────────────────


def test_set_payees_hidden_true(reg):
    pid = reg.create_payee("HideMe")["id"]
    res, proposed = call("set_payees_hidden", payee_id=pid, hidden=True)
    assert proposed is None
    assert res["payee"]["hidden"] is True


def test_set_payees_hidden_false(reg):
    pid = reg.create_payee("ShowMe")["id"]
    reg._store.update_by_id(pid, {"hidden": True})
    res, _ = call("set_payees_hidden", payee_id=pid, hidden=False)
    assert res["payee"]["hidden"] is False


def test_set_payees_hidden_not_found_returns_error(reg):
    res, _ = call("set_payees_hidden", payee_id="pay_ghost", hidden=True)
    assert "error" in res


def test_set_payees_hidden_broadcasts_to_several_ids_in_one_call(reg):
    """The owner's own example: 'hide these 5 payees' — trivial, no per-item payload."""
    ids = [reg.create_payee(f"Hide{i}")["id"] for i in range(3)]
    res, proposed = call("set_payees_hidden", payee_id=ids, hidden=True)
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 3
    for pid in ids:
        assert reg.require_payee(pid)["hidden"] is True


def test_set_payees_hidden_batch_partial_failure_reports_by_index(reg):
    a = reg.create_payee("HideA")["id"]
    res, _ = call(
        "set_payees_hidden",
        updates=[
            {"payee_id": a, "hidden": True},
            {"payee_id": "pay_ghost", "hidden": True},
        ],
    )
    assert res["ok"] is False
    assert res["updated"] == 1
    assert res["results"][0]["ok"] is True
    assert res["results"][1]["ok"] is False


# ── set_payees_autocategorize ──────────────────────────────────────────────────


def test_set_payees_autocategorize_false(reg):
    pid = reg.create_payee("Spotify")["id"]
    res, proposed = call("set_payees_autocategorize", payee_id=pid, autocategorize=False)
    assert proposed is None
    assert res["payee"]["autocategorize"] is False


def test_set_payees_autocategorize_true(reg):
    pid = reg.create_payee("Spotify2")["id"]
    reg._store.update_by_id(pid, {"autocategorize": False})
    res, _ = call("set_payees_autocategorize", payee_id=pid, autocategorize=True)
    assert res["payee"]["autocategorize"] is True


def test_set_payees_autocategorize_not_found_returns_error(reg):
    res, _ = call("set_payees_autocategorize", payee_id="pay_ghost", autocategorize=True)
    assert "error" in res


def test_set_payees_autocategorize_broadcasts_to_list(reg):
    ids = [reg.create_payee(f"AutoCat{i}")["id"] for i in range(2)]
    res, _ = call("set_payees_autocategorize", payee_id=ids, autocategorize=False)
    assert res["updated"] == 2
    for pid in ids:
        assert reg.require_payee(pid)["autocategorize"] is False


# ── add_payee_aliases ──────────────────────────────────────────────────────────


def test_add_payee_aliases_normalizes_and_adds(reg):
    pid = reg.create_payee("Grab")["id"]
    res, proposed = call("add_payee_aliases", payee_id=pid, text="Grab Taxi SG")
    assert proposed is None
    assert "grab taxi sg" in res["payee"].get("aliases", [])


def test_add_payee_aliases_conflict_returns_error(reg):
    a = reg.create_payee("PayeeA")["id"]
    reg.create_payee("PayeeB")
    # "payeeb" is already claimed by PayeeB
    res, _ = call("add_payee_aliases", payee_id=a, text="PayeeB")
    assert "error" in res


def test_add_payee_aliases_empty_text_returns_error(reg):
    pid = reg.create_payee("X")["id"]
    # Processor-prefix-only text normalizes to empty
    res, _ = call("add_payee_aliases", payee_id=pid, text="SQ *")
    assert "error" in res


def test_add_payee_aliases_not_found_returns_error(reg):
    res, _ = call("add_payee_aliases", payee_id="pay_ghost", text="anything")
    assert "error" in res


def test_add_payee_aliases_batch_adds_several_at_once(reg):
    a = reg.create_payee("Multi1")["id"]
    b = reg.create_payee("Multi2")["id"]
    res, proposed = call(
        "add_payee_aliases",
        aliases=[{"payee_id": a, "text": "multi one"}, {"payee_id": b, "text": "multi two"}],
    )
    assert proposed is None
    assert res["added"] == 2
    assert "multi one" in reg.require_payee(a)["aliases"]
    assert "multi two" in reg.require_payee(b)["aliases"]


# ── remove_payee_aliases ────────────────────────────────────────────────────────


def test_remove_payee_aliases_removes_entry(reg):
    pid = reg.create_payee("Netflix")["id"]
    reg._store.add_alias(pid, "netflix.com")
    res, proposed = call("remove_payee_aliases", payee_id=pid, alias="netflix.com")
    assert proposed is None
    assert "netflix.com" not in res["payee"].get("aliases", [])


def test_remove_payee_aliases_noop_when_missing(reg):
    pid = reg.create_payee("Netflix")["id"]
    # Removing a non-existent alias should not error
    res, _ = call("remove_payee_aliases", payee_id=pid, alias="does_not_exist")
    assert "error" not in res
    assert res["payee"]["id"] == pid


def test_remove_payee_aliases_not_found_returns_error(reg):
    res, _ = call("remove_payee_aliases", payee_id="pay_ghost", alias="alias")
    assert "error" in res


def test_remove_payee_aliases_batch(reg):
    a = reg.create_payee("RA")["id"]
    reg._store.add_alias(a, "ra alias one")
    b = reg.create_payee("RB")["id"]
    reg._store.add_alias(b, "rb alias one")
    res, _ = call(
        "remove_payee_aliases",
        aliases=[
            {"payee_id": a, "alias": "ra alias one"},
            {"payee_id": b, "alias": "rb alias one"},
        ],
    )
    assert res["removed"] == 2
    assert "ra alias one" not in reg.require_payee(a).get("aliases", [])
    assert "rb alias one" not in reg.require_payee(b).get("aliases", [])


# ── add_payee_rules ─────────────────────────────────────────────────────────────


def test_add_payee_rules_contains(reg):
    pid = reg.create_payee("Grab")["id"]
    res, proposed = call("add_payee_rules", payee_id=pid, match_type="contains", value="grab")
    assert proposed is None
    rules = res["payee"].get("rules", [])
    assert any(r["match_type"] == "contains" and r["value"] == "grab" for r in rules)


def test_add_payee_rules_is(reg):
    pid = reg.create_payee("Netflix")["id"]
    res, _ = call("add_payee_rules", payee_id=pid, match_type="is", value="NETFLIX.COM")
    rules = res["payee"].get("rules", [])
    assert any(r["match_type"] == "is" and r["value"] == "NETFLIX.COM" for r in rules)


def test_add_payee_rules_regex(reg):
    pid = reg.create_payee("KestrelEats")["id"]
    res, _ = call("add_payee_rules", payee_id=pid, match_type="regex", value=r"^fp\s*\*")
    rules = res["payee"].get("rules", [])
    assert any(r["match_type"] == "regex" for r in rules)


def test_add_payee_rules_invalid_match_type_returns_error(reg):
    pid = reg.create_payee("X")["id"]
    res, _ = call("add_payee_rules", payee_id=pid, match_type="startswith", value="foo")
    assert "error" in res


def test_add_payee_rules_conflict_returns_error(reg):
    a = reg.create_payee("PayeeA")["id"]
    b = reg.create_payee("PayeeB")["id"]
    call("add_payee_rules", payee_id=a, match_type="contains", value="shared_kw")
    # Same rule on a different payee → ConflictError
    res, _ = call("add_payee_rules", payee_id=b, match_type="contains", value="shared_kw")
    assert "error" in res


def test_add_payee_rules_not_found_returns_error(reg):
    res, _ = call("add_payee_rules", payee_id="pay_ghost", match_type="contains", value="foo")
    assert "error" in res


def test_add_payee_rules_invalid_regex_returns_error(reg):
    pid = reg.create_payee("Y")["id"]
    res, _ = call("add_payee_rules", payee_id=pid, match_type="regex", value="[bad(")
    assert "error" in res


def test_add_payee_rules_batch_different_payees(reg):
    a = reg.create_payee("BatchRuleA")["id"]
    b = reg.create_payee("BatchRuleB")["id"]
    res, proposed = call(
        "add_payee_rules",
        rules=[
            {"payee_id": a, "match_type": "contains", "value": "batchrulea kw"},
            {"payee_id": b, "match_type": "contains", "value": "batchruleb kw"},
        ],
    )
    assert proposed is None
    assert res["added"] == 2
    assert any(r["value"] == "batchrulea kw" for r in reg.require_payee(a)["rules"])
    assert any(r["value"] == "batchruleb kw" for r in reg.require_payee(b)["rules"])


# ── remove_payee_rules ──────────────────────────────────────────────────────────


def test_remove_payee_rules_by_index(reg):
    pid = reg.create_payee("Grab")["id"]
    call("add_payee_rules", payee_id=pid, match_type="contains", value="grab")
    call("add_payee_rules", payee_id=pid, match_type="contains", value="grabfood")
    res, proposed = call("remove_payee_rules", payee_id=pid, index=0)
    assert proposed is None
    rules = res["payee"].get("rules", [])
    assert len(rules) == 1
    assert rules[0]["value"] == "grabfood"


def test_remove_payee_rules_out_of_range_returns_error(reg):
    pid = reg.create_payee("Z")["id"]
    call("add_payee_rules", payee_id=pid, match_type="contains", value="only")
    res, _ = call("remove_payee_rules", payee_id=pid, index=5)
    assert "error" in res


def test_remove_payee_rules_not_found_returns_error(reg):
    res, _ = call("remove_payee_rules", payee_id="pay_ghost", index=0)
    assert "error" in res


def test_remove_payee_rules_broadcasts_same_index_to_a_list(reg):
    a = reg.create_payee("RRa")["id"]
    b = reg.create_payee("RRb")["id"]
    call("add_payee_rules", payee_id=a, match_type="contains", value="rra kw")
    call("add_payee_rules", payee_id=b, match_type="contains", value="rrb kw")
    res, proposed = call("remove_payee_rules", payee_id=[a, b], index=0)
    assert proposed is None
    assert res["removed"] == 2
    assert reg.require_payee(a)["rules"] == []
    assert reg.require_payee(b)["rules"] == []


def test_remove_payee_rules_same_payee_multiple_indices_highest_first(reg):
    """Two removals on the SAME payee in one call must apply highest-index-first, so
    each requested index still refers to the row the caller meant regardless of order."""
    pid = reg.create_payee("MultiRule")["id"]
    call("add_payee_rules", payee_id=pid, match_type="contains", value="rule0")
    call("add_payee_rules", payee_id=pid, match_type="contains", value="rule1")
    call("add_payee_rules", payee_id=pid, match_type="contains", value="rule2")
    res, proposed = call(
        "remove_payee_rules",
        removals=[{"payee_id": pid, "index": 0}, {"payee_id": pid, "index": 2}],
    )
    assert proposed is None
    assert res["removed"] == 2
    remaining = {r["value"] for r in reg.require_payee(pid)["rules"]}
    assert remaining == {"rule1"}


# ── set_payees_rules ─────────────────────────────────────────────────────────


def test_set_payees_rules_replaces_all(reg):
    pid = reg.create_payee("X")["id"]
    call("add_payee_rules", payee_id=pid, match_type="contains", value="old_rule")
    res, proposed = call(
        "set_payees_rules",
        payee_id=pid,
        rules=[
            {"match_type": "is", "value": "exact_match"},
            {"match_type": "contains", "value": "new_rule"},
        ],
    )
    assert proposed is None
    rules = res["payee"].get("rules", [])
    values = {r["value"] for r in rules}
    assert "old_rule" not in values
    assert "exact_match" in values
    assert "new_rule" in values


def test_set_payees_rules_empty_clears_all(reg):
    pid = reg.create_payee("Y")["id"]
    call("add_payee_rules", payee_id=pid, match_type="contains", value="something")
    res, _ = call("set_payees_rules", payee_id=pid, rules=[])
    assert res["payee"].get("rules", []) == []


def test_set_payees_rules_invalid_entry_returns_error(reg):
    pid = reg.create_payee("Z")["id"]
    res, _ = call("set_payees_rules", payee_id=pid, rules=[{"match_type": "bad", "value": "x"}])
    assert "error" in res


def test_set_payees_rules_not_found_returns_error(reg):
    res, _ = call("set_payees_rules", payee_id="pay_ghost", rules=[])
    assert "error" in res


def test_set_payees_rules_broadcasts_same_list_to_several_payees(reg):
    a = reg.create_payee("SRa")["id"]
    b = reg.create_payee("SRb")["id"]
    res, proposed = call(
        "set_payees_rules",
        payee_id=[a, b],
        rules=[{"match_type": "contains", "value": "shared broadcast rule"}],
    )
    assert proposed is None
    assert res["updated"] == 2
    for pid in (a, b):
        values = {r["value"] for r in reg.require_payee(pid)["rules"]}
        assert values == {"shared broadcast rule"}


def test_set_payees_rules_updates_array_allows_divergent_lists(reg):
    a = reg.create_payee("SRc")["id"]
    b = reg.create_payee("SRd")["id"]
    res, _ = call(
        "set_payees_rules",
        updates=[
            {"payee_id": a, "rules": [{"match_type": "contains", "value": "a only"}]},
            {"payee_id": b, "rules": [{"match_type": "contains", "value": "b only"}]},
        ],
    )
    assert res["updated"] == 2
    assert {r["value"] for r in reg.require_payee(a)["rules"]} == {"a only"}
    assert {r["value"] for r in reg.require_payee(b)["rules"]} == {"b only"}


# ── apply_payee_rules (GLOBAL, deliberately still singular) ────────────────────


def test_apply_payee_rules_with_confirm_runs(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    res, proposed = call("apply_payee_rules")
    assert proposed is None
    assert "updated" in res
    assert isinstance(res["updated"], int)


def test_apply_payee_rules_updates_matching_transactions(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Grab")["id"]
    call("add_payee_rules", payee_id=pid, match_type="contains", value="grab")

    txns = [
        {"id": "t1", "description": "Grab Ride", "payee_id": None, "deleted": False},
        {"id": "t2", "description": "Netflix", "payee_id": None, "deleted": False},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    res, _ = call("apply_payee_rules")
    assert res["updated"] == 1
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] == pid
    assert by_id["t2"]["payee_id"] is None


def test_apply_payee_rules_generic_exception_returns_error(reg, monkeypatch):
    """Covers the bare `except Exception` branch in apply_payee_rules."""
    monkeypatch.setattr(
        payee_registry,
        "apply_payee_rules_to_history",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    res, proposed = call("apply_payee_rules")
    assert proposed is None
    assert "error" in res
    assert "boom" in res["error"]


# ── apply_payee_rules_to_payees ──────────────────────────────────────────────


def test_apply_payee_rules_to_payees_not_found_returns_error(reg):
    res, _ = call("apply_payee_rules_to_payees", payee_id="pay_ghost")
    assert "error" in res


def test_apply_payee_rules_to_payees_only_touches_the_given_payee(reg, monkeypatch):
    """Regression: apply_payee_rules_to_payees must be scoped — a transaction that matches
    a DIFFERENT payee's rule must not move, even if that other payee also has a matching
    rule."""
    from backend.db.transaction_store import TransactionStore

    grab = reg.create_payee("Grab")["id"]
    call("add_payee_rules", payee_id=grab, match_type="contains", value="grab")
    other = reg.create_payee("Other")["id"]
    call("add_payee_rules", payee_id=other, match_type="contains", value="other kw")

    txns = [
        {"id": "t1", "description": "Grab Ride", "payee_id": None, "deleted": False},
        {"id": "t2", "description": "Other kw thing", "payee_id": None, "deleted": False},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    res, proposed = call("apply_payee_rules_to_payees", payee_id=grab)
    assert proposed is None
    assert res["updated"] == 1
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] == grab
    # t2 matches a DIFFERENT payee's rule — must stay untouched by this scoped call.
    assert by_id["t2"]["payee_id"] is None


def test_apply_payee_rules_to_payees_batch_reports_per_payee(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    grab = reg.create_payee("GrabBatch")["id"]
    call("add_payee_rules", payee_id=grab, match_type="contains", value="grabbatch")
    ghost_missing = "pay_definitely_not_real"

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    res, proposed = call("apply_payee_rules_to_payees", payee_id=[grab, ghost_missing])
    assert proposed is None
    assert res["updated"] == 0
    by_payee = {row["payee_id"]: row for row in res["by_payee"]}
    assert by_payee[grab]["updated"] == 0
    assert "error" in by_payee[ghost_missing]


# ── apply_payee_categories ───────────────────────────────────────────────────


def test_apply_payee_categories_not_found_returns_error(reg):
    res, _ = call("apply_payee_categories", payee_id="pay_ghost")
    assert "error" in res


def test_apply_payee_categories_no_default_category_returns_error(reg):
    pid = reg.create_payee("NoDefault")["id"]
    res, _ = call("apply_payee_categories", payee_id=pid)
    assert "error" in res


def test_apply_payee_categories_stamps_matching_transactions(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("Bar Example", default_category="shopping")["id"]  # → cat_6c4bc0170000
    other = reg.create_payee("Other")["id"]

    txns = [
        {
            "id": "t1",
            "payee_id": pid,
            "kind": "expense",
            "category": "uncategorized",
            "deleted": False,
        },
        {
            "id": "t2",
            "payee_id": pid,
            "kind": "expense",
            "category": "cat_6c4bc0170000",
            "deleted": False,
        },
        {
            "id": "t3",
            "payee_id": other,
            "kind": "expense",
            "category": "uncategorized",
            "deleted": False,
        },
        {"id": "t4", "payee_id": pid, "kind": "transfer", "category": None, "deleted": False},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    res, proposed = call("apply_payee_categories", payee_id=pid)
    assert proposed is None
    assert res["updated"] == 1
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["category"] == "cat_6c4bc0170000"
    assert by_id["t3"]["category"] == "uncategorized"  # a different payee — untouched
    assert by_id["t4"]["category"] is None  # transfer — skipped


def test_apply_payee_categories_batch_two_payees(reg, monkeypatch):
    """This tool re-reads the store ONCE PER payee in the batch (apply_default_category_to
    _transactions is called per id) — so the mock must behave like a real store (each
    call sees the PRIOR call's writes), not a frozen snapshot, or the second payee's pass
    would silently stomp the first payee's change when it re-saves the full row set."""
    from backend.db.transaction_store import TransactionStore

    # Labels resolve to builtin ids ("transport" → Transport, "shopping" → Shopping) —
    # default_category is normalized on create, never stored raw.
    a = reg.create_payee("CatBatchA", default_category="transport")["id"]
    b = reg.create_payee("CatBatchB", default_category="shopping")["id"]
    state = [
        {"id": "t1", "payee_id": a, "kind": "expense", "category": None, "deleted": False},
        {"id": "t2", "payee_id": b, "kind": "expense", "category": None, "deleted": False},
    ]
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(state))

    def _replace_all(self, rows):
        state[:] = rows

    monkeypatch.setattr(TransactionStore, "replace_all", _replace_all)

    res, proposed = call("apply_payee_categories", payee_id=[a, b])
    assert proposed is None
    assert res["updated"] == 2
    by_id = {r["id"]: r for r in state}
    assert by_id["t1"]["category"] == "cat_db5642ab0000"
    assert by_id["t2"]["category"] == "cat_6c4bc0170000"


# ── merge_payees ───────────────────────────────────────────────────────────────


def test_merge_payees_folds_source_into_target(reg_with_txns):
    src_id = reg_with_txns.create_payee("GrabFood")["id"]
    tgt_id = reg_with_txns.create_payee("GrabDelivery")["id"]
    # Add a unique alias to source so we can check it folds
    reg_with_txns._store.add_alias(src_id, "grabfood sg")
    res, proposed = call("merge_payees", source_id=src_id, target_id=tgt_id)
    assert proposed is None
    assert "merged_into" in res
    # Source should be deleted
    assert reg_with_txns._store.find_by_id(src_id) is None
    # Target should contain the source alias
    assert "grabfood sg" in res["merged_into"].get("aliases", [])


def test_merge_payees_folds_source_rules_into_target(reg_with_txns):
    src_id = reg_with_txns.create_payee("GrabFood")["id"]
    tgt_id = reg_with_txns.create_payee("GrabDelivery")["id"]
    # Target already has a rule; source has that same rule plus a distinct one — proves
    # the merge folds rules AND dedups by equality instead of duplicating.
    reg_with_txns._store.update_by_id(
        tgt_id, {"rules": [{"match_type": "is", "value": "GRAB*FOOD"}]}
    )
    reg_with_txns._store.update_by_id(
        src_id,
        {
            "rules": [
                {"match_type": "is", "value": "GRAB*FOOD"},
                {"match_type": "contains", "value": "grabfood promo"},
            ]
        },
    )
    res, proposed = call("merge_payees", source_id=src_id, target_id=tgt_id)
    assert proposed is None
    rules = res["merged_into"].get("rules", [])
    assert {"match_type": "contains", "value": "grabfood promo"} in rules
    assert rules.count({"match_type": "is", "value": "GRAB*FOOD"}) == 1


def test_merge_payees_same_id_returns_error(reg_with_txns):
    pid = reg_with_txns.create_payee("X")["id"]
    res, _ = call("merge_payees", source_id=pid, target_id=pid)
    assert "error" in res


def test_merge_payees_source_not_found_returns_error(reg_with_txns):
    tgt_id = reg_with_txns.create_payee("Target")["id"]
    res, _ = call("merge_payees", source_id="pay_ghost", target_id=tgt_id)
    assert "error" in res


def test_merge_payees_target_not_found_returns_error(reg_with_txns):
    src_id = reg_with_txns.create_payee("Source")["id"]
    res, _ = call("merge_payees", source_id=src_id, target_id="pay_ghost")
    assert "error" in res


def test_merge_payees_batch_chains_in_order(reg_with_txns):
    """A cluster of duplicates folding into one canonical payee — a later merge entry may
    reference a payee an earlier entry just merged INTO."""
    dup1 = reg_with_txns.create_payee("Dup One")["id"]
    dup2 = reg_with_txns.create_payee("Dup Two")["id"]
    canonical = reg_with_txns.create_payee("Canonical")["id"]

    res, proposed = call(
        "merge_payees",
        merges=[
            {"source_id": dup1, "target_id": canonical},
            {"source_id": dup2, "target_id": canonical},
        ],
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["merged"] == 2
    assert reg_with_txns._store.find_by_id(dup1) is None
    assert reg_with_txns._store.find_by_id(dup2) is None
    assert reg_with_txns._store.find_by_id(canonical) is not None


# ── delete_payees ────────────────────────────────────────────────────────────


def test_delete_payees_removes_entity(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    pid = reg.create_payee("ToDelete")["id"]
    res, proposed = call("delete_payees", payee_id=pid)
    assert proposed is None
    assert res["deleted"] == pid
    assert res["reassigned_to"] is None
    assert reg._store.find_by_id(pid) is None


def test_delete_payees_with_reassign(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    src_id = reg.create_payee("Source")["id"]
    tgt_id = reg.create_payee("Target")["id"]
    txns = [{"id": "t1", "payee_id": src_id, "description": "x"}]
    saved = []

    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    res, _ = call("delete_payees", payee_id=src_id, reassign_to=tgt_id)
    assert res["deleted"] == src_id
    assert res["reassigned_to"] == tgt_id
    # Source gone
    assert reg._store.find_by_id(src_id) is None
    # Transaction re-pointed to target
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] == tgt_id


def test_delete_payees_not_found_returns_error(reg):
    res, _ = call("delete_payees", payee_id="pay_ghost")
    assert "error" in res


def test_delete_payees_reassign_target_missing_returns_error(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    pid = reg.create_payee("X")["id"]
    res, _ = call("delete_payees", payee_id=pid, reassign_to="pay_nowhere")
    assert "error" in res


def test_delete_payees_clears_txn_payee_id(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    pid = reg.create_payee("ClearTxns")["id"]
    txns = [
        {"id": "t1", "payee_id": pid},
        {"id": "t2", "payee_id": "pay_other"},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    call("delete_payees", payee_id=pid)
    by_id = {r["id"]: r for r in saved}
    assert by_id["t1"]["payee_id"] is None
    assert by_id["t2"]["payee_id"] == "pay_other"


def test_delete_payees_broadcasts_reassign_to_several_ids(reg, monkeypatch):
    """Folding a cluster of duplicates into one survivor: delete several payees at once,
    all reassigning to the SAME target — no per-item repetition of reassign_to."""
    from backend.db.transaction_store import TransactionStore

    dup1 = reg.create_payee("DelDup1")["id"]
    dup2 = reg.create_payee("DelDup2")["id"]
    survivor = reg.create_payee("Survivor")["id"]
    txns = [
        {"id": "t1", "payee_id": dup1},
        {"id": "t2", "payee_id": dup2},
    ]
    saved = []
    monkeypatch.setattr(TransactionStore, "all", lambda self: list(txns))
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: saved.extend(rows))

    res, proposed = call("delete_payees", payee_id=[dup1, dup2], reassign_to=survivor)
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted"] == 2
    assert reg._store.find_by_id(dup1) is None
    assert reg._store.find_by_id(dup2) is None


def test_delete_payees_updates_array_allows_divergent_reassignment(reg, monkeypatch):
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    d1 = reg.create_payee("DivDel1")["id"]
    d2 = reg.create_payee("DivDel2")["id"]
    t1 = reg.create_payee("DivTarget1")["id"]
    t2 = reg.create_payee("DivTarget2")["id"]

    res, proposed = call(
        "delete_payees",
        updates=[
            {"payee_id": d1, "reassign_to": t1},
            {"payee_id": d2, "reassign_to": t2},
        ],
    )
    assert proposed is None
    assert res["deleted"] == 2
    assert reg._store.find_by_id(d1) is None
    assert reg._store.find_by_id(d2) is None


# ── proposed_change is always None for all payee tools ────────────────────────


def test_all_payee_tools_return_none_proposed_change(reg, monkeypatch):
    """None of the payee tools should return a proposed_change (they are direct writes,
    not proposals)."""
    from backend.db.transaction_store import TransactionStore

    monkeypatch.setattr(TransactionStore, "all", lambda self: [])
    monkeypatch.setattr(TransactionStore, "replace_all", lambda self, rows: None)

    pid = reg.create_payee("PropCheck", default_category="food")["id"]
    pid2 = reg.create_payee("PropCheck2")["id"]

    tool_calls = [
        ("get_payees", {}),
        ("get_payees", {"payee_id": pid}),
        ("create_payees", {"name": "UniqueTestPropX"}),
        ("rename_payees", {"payee_id": pid, "new_name": "PropCheckRenamed"}),
        ("set_payees_default_category", {"payee_id": pid, "category": "food"}),
        ("set_payees_hidden", {"payee_id": pid, "hidden": True}),
        ("set_payees_autocategorize", {"payee_id": pid, "autocategorize": False}),
        ("add_payee_aliases", {"payee_id": pid, "text": "propcheck alias unique"}),
        ("remove_payee_aliases", {"payee_id": pid, "alias": "propcheck alias unique"}),
        (
            "add_payee_rules",
            {"payee_id": pid, "match_type": "contains", "value": "propcheck_kw"},
        ),
        ("remove_payee_rules", {"payee_id": pid, "index": 0}),
        ("set_payees_rules", {"payee_id": pid, "rules": []}),
        ("apply_payee_rules", {"confirm": True}),
        ("apply_payee_rules_to_payees", {"payee_id": pid, "confirm": True}),
        ("apply_payee_categories", {"payee_id": pid, "confirm": True}),
        ("merge_payees", {"source_id": pid2, "target_id": pid}),
        ("delete_payees", {"payee_id": pid}),
    ]

    for tool_name, inp in tool_calls:
        _, proposed = asyncio.run(execute_tool(tool_name, inp))
        assert proposed is None, f"Tool {tool_name!r} returned non-None proposed_change: {proposed}"


# ── regression: get_payees always uses live txn_count ────────────────────────


def test_get_payees_registry_payee_id_returns_live_txn_count(reg_with_txns, monkeypatch):
    """get_payees(payee_id=...) must return the LIVE txn_count from list_registry(),
    never a stale stored value. Regression for the orphaned-payee drift bug."""
    from backend.db.transaction_store import TransactionStore

    pid = reg_with_txns.create_payee("Orphan")["id"]
    # The payee exists but no transaction references it
    monkeypatch.setattr(TransactionStore, "all", lambda self: [])

    res, _ = call("get_payees", payee_id=pid)
    assert "error" not in res
    assert res["payee"]["txn_count"] == 0  # live: no txns → 0, not a stale stored 1


def test_get_payees_registry_no_stored_txn_count_leaks(reg_with_txns, monkeypatch):
    """After bump_usage (which no longer writes txn_count), get_payees must not echo
    a stored txn_count field — only the live-computed value from list_registry()."""
    from backend.db.transaction_store import TransactionStore

    pid = reg_with_txns.create_payee("Bump Target")["id"]
    reg_with_txns.note_usage(
        pid, "2026-06-01"
    )  # used to write stored txn_count; now only last_used

    monkeypatch.setattr(TransactionStore, "payee_usage", lambda self: {pid: (1, "2026-06-01")})

    res, _ = call("get_payees", payee_id=pid)
    assert res["payee"]["txn_count"] == 1  # live count from the transaction
    # Stored row must have no txn_count
    raw = reg_with_txns._store.find_by_id(pid)
    assert "txn_count" not in raw


# ── payee_ids param rename (2026-07-19b) ──────────────────────────────────────
#
# Owner complaint: a scalar-or-list param named SINGULAR (payee_id) invites the model to
# stringify a list into it — the exact bug `delete_account(account_id='[...]')` hit. Every
# payees.py tool whose entity-target param widened to scalar-or-list is renamed
# payee_id -> payee_ids (verified against `backend.scripts.tool_inventory --lint`). A PARAM
# rename needs no alias-table entry (unlike a TOOL rename) — just read-side tolerance, so
# these tests prove BOTH: the new plural key works, and the old singular key still does.
#
# Every existing `payee_id=...` call above this section (get_payees, set_payees_hidden,
# etc.) already exercises the OLD key and keeps passing unmodified — that IS the
# backward-compat proof for those tools. The tests below add the NEW `payee_ids` key on
# the same tools, so both halves of the contract are pinned explicitly in one place.


def test_get_payees_by_payee_ids_plural_key_returns_all_requested(reg_with_txns):
    a = reg_with_txns.create_payee("PluralAlpha")["id"]
    b = reg_with_txns.create_payee("PluralBeta")["id"]
    res, proposed = call("get_payees", payee_ids=[a, b])
    assert proposed is None
    assert {r["id"] for r in res["payees"]} == {a, b}
    assert res["total"] == 2


def test_get_payees_schema_advertises_payee_ids_not_payee_id():
    """The model-facing schema shows the plural name; `payee_id` is a read-side-only
    fallback, never re-declared in the schema (that would itself trip the same naming
    linter rule for a decoy the model is never meant to pick)."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_payees")
    props = schema["input_schema"]["properties"]
    assert "payee_ids" in props
    assert "payee_id" not in props


def test_set_payees_hidden_broadcasts_via_payee_ids_plural_key(reg):
    a = reg.create_payee("HidePluralA")["id"]
    b = reg.create_payee("HidePluralB")["id"]
    res, proposed = call("set_payees_hidden", payee_ids=[a, b], hidden=True)
    assert proposed is None
    assert res["ok"] is True
    assert res["updated"] == 2
    assert reg.require_payee(a)["hidden"] is True
    assert reg.require_payee(b)["hidden"] is True


def test_set_payees_hidden_old_singular_key_still_works(reg):
    """Backward compat: a stored caller/in-flight session on the pre-rename key must
    keep working, scalar AND list alike."""
    pid = reg.create_payee("HideOldKey")["id"]
    res, _ = call("set_payees_hidden", payee_id=pid, hidden=True)
    assert res["ok"] is True
    assert reg.require_payee(pid)["hidden"] is True

    a = reg.create_payee("HideOldKeyListA")["id"]
    b = reg.create_payee("HideOldKeyListB")["id"]
    res, _ = call("set_payees_hidden", payee_id=[a, b], hidden=True)
    assert res["updated"] == 2


def test_set_payees_autocategorize_broadcasts_via_payee_ids_plural_key(reg):
    a = reg.create_payee("AutoPluralA")["id"]
    b = reg.create_payee("AutoPluralB")["id"]
    res, proposed = call("set_payees_autocategorize", payee_ids=[a, b], autocategorize=False)
    assert proposed is None
    assert res["updated"] == 2
    assert reg.require_payee(a)["autocategorize"] is False
    assert reg.require_payee(b)["autocategorize"] is False


def test_remove_payee_rules_broadcasts_via_payee_ids_plural_key(reg):
    a = reg.create_payee("RulePluralA")["id"]
    b = reg.create_payee("RulePluralB")["id"]
    reg.add_payee_rule(a, "contains", "foo")
    reg.add_payee_rule(b, "contains", "bar")
    res, proposed = call("remove_payee_rules", payee_ids=[a, b], index=0)
    assert proposed is None
    assert res["ok"] is True
    assert reg.require_payee(a)["rules"] == []
    assert reg.require_payee(b)["rules"] == []


def test_set_payees_rules_broadcasts_via_payee_ids_plural_key(reg):
    a = reg.create_payee("SetRulesPluralA")["id"]
    b = reg.create_payee("SetRulesPluralB")["id"]
    new_rules = [{"match_type": "contains", "value": "shared"}]
    res, proposed = call("set_payees_rules", payee_ids=[a, b], rules=new_rules)
    assert proposed is None
    assert res["updated"] == 2
    assert reg.require_payee(a)["rules"] == new_rules
    assert reg.require_payee(b)["rules"] == new_rules


def test_delete_payees_broadcasts_via_payee_ids_plural_key(reg_with_txns):
    a = reg_with_txns.create_payee("DeletePluralA")["id"]
    b = reg_with_txns.create_payee("DeletePluralB")["id"]
    res, proposed = call("delete_payees", payee_ids=[a, b])
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted"] == 2
    assert reg_with_txns._store.find_by_id(a) is None
    assert reg_with_txns._store.find_by_id(b) is None


def test_apply_payee_rules_to_payees_via_payee_ids_plural_key(reg_with_txns, monkeypatch):
    a = reg_with_txns.create_payee("ApplyRulesPluralA")["id"]
    b = reg_with_txns.create_payee("ApplyRulesPluralB")["id"]
    monkeypatch.setattr(
        payee_registry,
        "apply_payee_rules_to_existing",
        lambda pid: {"updated": 1, "payee_id": pid},
    )
    res, proposed = call("apply_payee_rules_to_payees", payee_ids=[a, b])
    assert proposed is None
    assert res["updated"] == 2
    assert {row["payee_id"] for row in res["by_payee"]} == {a, b}


def test_apply_payee_categories_via_payee_ids_plural_key(reg_with_txns, monkeypatch):
    a = reg_with_txns.create_payee("ApplyCatPluralA")["id"]
    b = reg_with_txns.create_payee("ApplyCatPluralB")["id"]
    monkeypatch.setattr(
        payee_registry,
        "apply_default_category_to_transactions",
        lambda pid: {"updated": 2, "payee_id": pid},
    )
    res, proposed = call("apply_payee_categories", payee_ids=[a, b])
    assert proposed is None
    assert res["updated"] == 4
    assert {row["payee_id"] for row in res["by_payee"]} == {a, b}


# ── set_payees_default_category: payee_ids + the incidentally-fixed dispatch bug ──
#
# Discovered while writing the tests above: EVERY call to this tool through the real
# registry.dispatch() (i.e. via `call()`/execute_tool, not the bare handler) crashed with
# `TypeError: unhashable type: 'list'` in `registry.coerce_stringified_containers` —
# reproduces identically on the harness/reliability-program base commit BEFORE this param
# rename (verified with `git stash`), so it predates and is unrelated to the payee_ids
# rename itself. Root cause: `category`'s schema type is `["string", "null"]` (an
# explicit-null field) — a LIST — and the function folded `spec.get("type")` straight into
# a set literal, which crashes for ANY string-valued key once ANY OTHER key on the same
# call has a list-shaped `type`. Fixed in `registry.coerce_stringified_containers`
# (normalizes `type` to a list before the set-union) since it silently broke every
# `set_payees_default_category`/`create_payees(default_category=...)` call on this branch
# — left unfixed, the tests below (and 7 pre-existing ones above) could not pass.


def test_set_payees_default_category_broadcasts_via_payee_ids_plural_key(reg):
    a = reg.create_payee("DirectPluralA")["id"]
    b = reg.create_payee("DirectPluralB")["id"]
    res, proposed = call("set_payees_default_category", payee_ids=[a, b], category="shopping")
    assert proposed is None
    assert res["updated"] == 2
    assert reg.require_payee(a)["default_categories"]["expense"] == "cat_6c4bc0170000"
    assert reg.require_payee(b)["default_categories"]["expense"] == "cat_6c4bc0170000"


def test_set_payees_default_category_old_payee_id_key_still_works(reg):
    pid = reg.create_payee("DirectOldKey")["id"]
    res, _ = call("set_payees_default_category", payee_id=pid, category="entertainment")
    assert reg.require_payee(pid)["default_categories"]["expense"] == "cat_dfcf9f910000"


def test_coerce_stringified_containers_tolerates_an_explicit_null_type_sibling():
    """Regression for the incidentally-found bug above, pinned directly at the registry
    level (not just observed through the payees tool): a call carrying BOTH a plain string
    value for an explicit-null (`type: [X, "null"]`) field AND a plain string for an
    unrelated field must not raise — it did, for ANY string-valued key on this tool,
    before the fix."""
    from backend.ai.tools.registry import coerce_stringified_containers

    out = coerce_stringified_containers(
        "set_payees_default_category", {"payee_ids": "pay_x", "category": "food"}
    )
    assert out == {"payee_ids": "pay_x", "category": "food"}


def test_the_apply_tools_are_gated_by_the_HARNESS_not_a_self_set_flag(reg, monkeypatch):
    """What replaced the three `requires_confirm_true` tests deleted on 2026-08-27.

    Those pinned a `confirm=true` the MODEL set to authorise its own bulk relabel — the
    second confirm mechanism, whose whole shape is that nothing verifies a human saw
    anything (ADR `2026-08-25-one-confirm-mechanism-for-every-tool`). Removing it is only
    safe because the HARNESS gates these calls, so that is what this asserts instead.
    """
    from backend.ai.tools import _confirm_gate as gate

    for tool in ("apply_payee_rules", "apply_payee_rules_to_payees", "apply_payee_categories"):
        assert gate.is_gated_tool(tool), f"{tool} must reach the confirm gate"
        assert "confirm" not in (gate.effective_level(tool) or ""), (
            "sanity: level is a level, not a flag"
        )


def test_no_apply_tool_still_advertises_a_self_set_confirmation(reg):
    """The schema is the model's map. A `confirm` parameter here would re-teach the pattern
    even with the handler guard gone."""
    import json

    import backend.ai.tools  # noqa: F401
    from backend.ai.tools.registry import build_tool_schemas

    for s_ in build_tool_schemas():
        if s_["name"].startswith("apply_payee"):
            assert "confirm" not in json.dumps(s_.get("input_schema") or {}), s_["name"]
