"""The ledger trio's shared schema table, and the mechanisms its prose was moved into.

Two things are pinned here:

1. **The factoring is sound** — the shared ``_txn_schema`` table emits a well-formed,
   self-consistent schema per tool, and a field's wording cannot drift between the three
   tools that describe the same data model.

2. **Every rule removed from the schema prose still fires as a real error** — with the
   recovery path in the message. Moving enforcement text out of the tool description is
   only safe if the model actually receives it on the error path; a validator that never
   runs would be strictly worse than the prose it replaced. Each test below submits the
   mistake the prose used to warn about and asserts on what comes back.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from backend.ai.tools import _txn_schema
from backend.ai.tools.ledger import add_transactions, get_transactions, update_transactions
from backend.ai.tools.registry import build_tool_schemas


def _schema(name: str) -> dict:
    return {t["name"]: t for t in build_tool_schemas()}[name]["input_schema"]


def _run(coro):
    # A fresh loop per call. `get_event_loop()` inherits whatever the previously-run test
    # left behind and raises "no current event loop" once any neighbour has closed it —
    # these tests passed alone and failed in the full suite until this changed.
    return asyncio.run(coro)


def _call(tool, payload) -> dict:
    out, _ = _run(tool(payload))
    return json.loads(out)


@pytest.fixture()
def book(db_isolated):
    """A minimal isolated book: two accounts and two expense categories.

    accounts/transactions/import_batches are DB-backed (plan 3 task 4); categories are
    DB-backed too (plan 4a task 2) — db_isolated redirects all of them to the SAME fresh,
    empty database, so a plain ``CategoryStore()``/``AccountStore()`` construction (default
    ``engine=None``) needs no per-instance path patching to land in it.
    """
    from backend.services import categories as categories_svc
    from backend.services import ledger as svc

    checking = svc.accounts.create(
        {"name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    savings = svc.accounts.create(
        {"name": "Savings", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    categories_svc.create_category(label="Food", kind="expense")
    categories_svc.create_category(label="Shopping", kind="expense")
    return {"checking": checking["id"], "savings": savings["id"]}


def _row(book, **over) -> dict:
    base = {
        "description": "STARBUCKS #4412",
        "amount": 10,
        "currency": "SGD",
        "account_id": book["checking"],
    }
    base.update(over)
    return base


# --- 1. The shared table is the single source of truth --------------------------------


# Tool-level CONTROL parameters — not transaction fields, so they are legitimately absent
# from the shared field table (which exists to stop one FIELD being worded two ways).
# `add_transactions` has always carried its own (force/decisions/import_*); `confirm_token`
# is the confirm gate's. Listed explicitly so a stray real field can never sneak in as one.
_CONTROL_PARAMS = {"confirm_token"}


def test_each_tool_builds_its_properties_from_the_shared_table():
    """Every TRANSACTION FIELD a tool emits comes from the table's builder — tool-level
    control parameters are the only permitted extras."""
    assert _schema("add_transactions")["properties"]["transactions"]["items"]["properties"] == (
        _txn_schema.create_item_properties()
    )
    # update_transactions' per-row patch is nested under `updates` (an array — a single edit
    # is one item in a 1-element array, mirroring add_transactions' `transactions`) rather
    # than sitting at the top level.
    update_props = _schema("update_transactions")["properties"]["updates"]["items"]["properties"]
    assert set(update_props) - set(_txn_schema.update_properties()) <= _CONTROL_PARAMS
    assert {
        k: v for k, v in update_props.items() if k not in _CONTROL_PARAMS
    } == _txn_schema.update_properties()
    assert _schema("get_transactions")["properties"] == _txn_schema.filter_properties()


def test_shared_fields_are_worded_identically_across_tools():
    """A field with one meaning has one description — the drift this factoring prevents."""
    create = _txn_schema.create_item_properties()
    update = _txn_schema.update_properties()
    for name in ("description", "memo", "splits", "tags", "date"):
        assert create[name] == update[name], f"{name} drifted between create and update"


def test_the_table_cannot_be_mutated_through_a_builder():
    """Builders deep-copy, so one tool's override can never corrupt another's schema."""
    first = _txn_schema.create_item_properties()
    first["category"]["description"] = "CLOBBERED"
    first["splits"]["items"]["properties"]["amount"]["description"] = "CLOBBERED"
    second = _txn_schema.create_item_properties()
    assert second["category"]["description"] != "CLOBBERED"
    assert second["splits"]["items"]["properties"]["amount"]["description"] != "CLOBBERED"


def test_patch_surface_never_references_create_only_parameters():
    """`update_transactions` accepts no account_id/direction/settled_amount, so its prose
    must not send the model looking for them (the bug that sharing create's wording
    introduced)."""
    props = _txn_schema.update_properties()
    # These are create-only parameters; none may appear on the patch surface...
    absent = {"account_id", "direction", "settled_amount", "amount", "currency"}
    assert absent.isdisjoint(props)
    # ...nor may its prose direct the model at them. Only snake_case parameter references
    # count: 'amount'/'currency' are also ordinary English words here ("Currency of the
    # source leg"), whereas `settled_amount` or `account_id` can only mean the parameter.
    prose = " ".join(v.get("description", "") for v in props.values())
    tokens = set(re.findall(r"[a-z]+(?:_[a-z]+)+", prose))
    for name in {"account_id", "settled_amount"}:
        assert name not in tokens, f"patch prose references the create-only field {name!r}"
    assert "direction=" not in prose, "patch prose points at the create-only `direction`"


def test_every_field_has_a_type_and_a_description():
    for tool_name, props in (
        ("add_transactions", _txn_schema.create_item_properties()),
        ("update_transactions", _txn_schema.update_properties()),
        ("get_transactions", _txn_schema.filter_properties()),
    ):
        for field_name, frag in props.items():
            assert frag.get("description"), f"{tool_name}.{field_name} has no description"
            # A union filter (single value OR a list of them, e.g. account_ids/category)
            # declares its shape with `anyOf` rather than a bare `type`. That is still a
            # complete type declaration, which is what this guard is actually about —
            # every branch must carry its own `type` so no branch is left unspecified.
            branches = frag.get("anyOf")
            if branches:
                assert all(b.get("type") for b in branches), (
                    f"{tool_name}.{field_name} has an anyOf branch with no type"
                )
                continue
            assert frag.get("type"), f"{tool_name}.{field_name} has no type"


def test_no_description_points_at_a_parameter_its_tool_does_not_accept():
    """The structural guard for the whole class of bug this refactor surfaced twice: the
    patch surface inheriting create's `account_id` wording, and `category` telling the model
    to pass `kind='investment'` to a tool that has no `kind` parameter at all.

    Any snake_case token in a description that looks like a parameter reference must be a
    real parameter of that same tool. Prose nouns are allowlisted explicitly.
    """
    prose_nouns = {
        # Real concepts named in prose that are not parameters of the tool in question.
        "base_currency",
        "message_id",
        "total_expenses_base",
        "total_income_base",
        "net_base",
        "list_categories",
        "list_accounts",
        "get_payees",
        "create_payee",
        "diff_statement",
        "email_search_messages",
        "email_get_messages",
        "approve_transactions",
    }
    for tool_name, props in (
        ("add_transactions", _txn_schema.create_item_properties()),
        ("update_transactions", _txn_schema.update_properties()),
        ("get_transactions", _txn_schema.filter_properties()),
    ):
        accepted = set(props)
        for field_name, frag in props.items():
            referenced = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", frag.get("description", "")))
            unknown = referenced - accepted - prose_nouns
            assert not unknown, (
                f"{tool_name}.{field_name} references {sorted(unknown)}, "
                f"which {tool_name} does not accept"
            )


def test_prior_descriptions_are_retrievable_for_an_ab_run():
    """The pre-compression wording stays available so the two variants can be measured."""
    current = _txn_schema.create_item_properties()
    for name, prior in _txn_schema.PRIOR_DESCRIPTIONS.items():
        assert prior.strip(), f"{name} has an empty prior variant"
        if name in current:
            assert current[name]["description"] != prior, (
                f"{name} is listed as compressed but its wording is unchanged"
            )


def test_compressed_teaching_keeps_its_worked_examples():
    """Examples earned the measured accuracy gain — compression may drop restatement, never
    the concrete numbers a model reasons from."""
    create = _txn_schema.create_item_properties()
    assert "USD 10.94" in create["currency"]["description"]
    # settled_amount / original_amount teach one scenario from both directions.
    assert "25.62" in create["settled_amount"]["description"]
    assert "25.62" in create["original_amount"]["description"]
    assert "19" in create["settled_amount"]["description"]
    assert "19" in create["original_amount"]["description"]
    assert "$60" in create["splits"]["description"]
    assert "STARBUCKS" in create["description"]["description"]


# --- 2. Moved enforcement still fires, with the recovery path -------------------------


def test_unknown_category_error_names_the_closest_label_and_the_tool(book):
    """Moved from the `category` prose: 'an unknown category is REJECTED, not created' plus
    'call list_categories'."""
    res = _call(add_transactions, {"transactions": [_row(book, category="Fud")]})
    assert res["ok"] is False
    assert "Fud" in res["error"]
    assert "Food" in res["error"], "the error must suggest the closest existing label"
    assert "list_categories" in res["error"]


def test_the_literal_uncategorized_is_rejected_with_the_same_recovery(book):
    """Moved from the `category` prose: 'never pass uncategorized'."""
    res = _call(add_transactions, {"transactions": [_row(book, category="uncategorized")]})
    assert res["ok"] is False
    assert "uncategorized" in res["error"]
    assert "list_categories" in res["error"]


def test_unset_category_books_as_unsorted_rather_than_failing(book):
    """The domain fact the prose keeps: category is genuinely optional."""
    res = _call(add_transactions, {"transactions": [_row(book)]})
    assert res["ok"] is True
    assert res["transaction"]["category"] is None


def test_a_lone_from_account_id_reports_which_field_to_add(book):
    """Moved from the leg prose: the XOR/anchor rule. The error names the missing field."""
    res = _call(
        add_transactions,
        {
            "transactions": [
                {
                    "description": "T",
                    "amount": 5,
                    "currency": "SGD",
                    "from_account_id": book["savings"],
                }
            ]
        },
    )
    assert res["ok"] is False
    assert "account_id" in res["error"]
    assert "destination" in res["error"], "must say which side account_id supplies"
    assert "ambiguous" in res["error"]


def test_a_lone_to_account_id_reports_the_mirror_case(book):
    res = _call(
        add_transactions,
        {
            "transactions": [
                {
                    "description": "T",
                    "amount": 5,
                    "currency": "SGD",
                    "to_account_id": book["savings"],
                }
            ]
        },
    )
    assert res["ok"] is False
    assert "source" in res["error"], "must say which side account_id supplies"


def test_both_legs_together_book_a_transfer_without_an_anchor(book):
    """The one leg fact the prose keeps: the symmetric pair is accepted."""
    res = _call(
        add_transactions,
        {
            "transactions": [
                {
                    "description": "Move",
                    "amount": 5,
                    "currency": "SGD",
                    "from_account_id": book["checking"],
                    "to_account_id": book["savings"],
                }
            ]
        },
    )
    assert res["ok"] is True
    assert res["transaction"]["kind"] == "transfer"


def test_unknown_account_error_lists_the_real_accounts(book):
    res = _call(add_transactions, {"transactions": [_row(book, account_id="acc_nope")]})
    assert res["ok"] is False
    assert "list_accounts" in res["error"]


def test_missing_currency_error_carries_the_worked_example(book):
    """`currency` prose dropped 'never assume the account's currency' — the error says it,
    with the account's own currency named so the mistake is visible."""
    res = _call(
        add_transactions,
        {"transactions": [{"description": "T", "amount": 5, "account_id": book["checking"]}]},
    )
    assert res["ok"] is False
    assert "currency is required" in res["error"]
    assert "USD 10.94" in res["error"]
    assert "SGD" in res["error"], "the error should name the account's own currency"


def test_split_sum_mismatch_reports_both_totals(book):
    """Moved from the `splits` prose: 'a mismatch is REFUSED, never re-scaled'."""
    res = _call(
        add_transactions,
        {
            "transactions": [
                _row(
                    book,
                    amount=100,
                    splits=[
                        {"category": "Food", "amount": 60},
                        {"category": "Shopping", "amount": 30},
                    ],
                )
            ]
        },
    )
    assert res["ok"] is False
    assert "90" in res["error"] and "100" in res["error"]


def test_unknown_split_category_is_refused(book):
    res = _call(
        add_transactions,
        {"transactions": [_row(book, amount=100, splits=[{"category": "Nope", "amount": 100}])]},
    )
    assert res["ok"] is False
    assert "Nope" in res["error"]


def test_kind_names_as_category_are_still_taught_because_nothing_enforces_them(book):
    """`_validate_reserved_category` is unreachable from the AI path — `_txn_data_from_ai`
    clears a kind-name category before the choke point ever sees it, so the row books
    silently as Uncategorized. That is why the schema still carries the kinds-are-not-categories
    fact: there is no error to move it into. If this ever starts raising, the prose can go."""
    res = _call(add_transactions, {"transactions": [_row(book, category="income")]})
    assert res["ok"] is True
    assert res["transaction"]["category"] is None
    assert "KINDS" in _txn_schema.create_item_properties()["category"]["description"]


# --- 3. A documented behaviour bug, characterized not fixed ---------------------------


def test_category_label_and_id_disagree_on_inferred_kind(book):
    """CHARACTERIZATION of a real bug found while rewriting this prose — NOT desired
    behaviour, and deliberately not fixed in a description/validation refactor.

    ``_txn_data_from_ai`` infers a row's ``kind`` with ``_kind_for(category)`` on the RAW,
    UNRESOLVED ref. ``kind_for`` keys on the category id, so passing the id of an
    investment-kind category yields kind='investment', while passing that same category's
    LABEL — which every schema in this module documents as equally valid ("label or its id;
    both resolve") — falls through to 'expense'. The category itself resolves correctly
    later at the ledger choke; only the kind was already decided from the unresolved string.

    Impact: a caller who names a category by label books a materially different kind than
    one who names it by id. Fix belongs with the ledger owners: resolve the ref before
    inferring kind. When that lands, this test should start failing and become an equality
    assertion.
    """
    from backend.ai.tools._shared import _txn_data_from_ai
    from backend.services import categories as categories_svc

    investment = categories_svc.create_category(label="Brokerage Contribution", kind="investment")

    def inferred_kind(category_ref: str) -> str:
        return _txn_data_from_ai(
            {
                "description": "Buy VOO",
                "amount": 100,
                "currency": "SGD",
                "account_id": book["checking"],
                "category": category_ref,
            },
            "2026-07-19",
        ).get("kind")

    assert inferred_kind(investment["id"]) == "investment"
    # The bug: the documented-equivalent label does NOT produce the same kind.
    assert inferred_kind("Brokerage Contribution") == "expense"


# --- 4. Round trip: create -> filter -> patch -----------------------------------------


def test_create_filter_and_update_still_round_trip(book):
    created = _call(
        add_transactions,
        {"transactions": [_row(book, category="Food", memo="with Sam", tags=["work"])]},
    )
    assert created["ok"] is True
    txn_id = created["transaction"]["id"]

    listed = _call(get_transactions, {"account_id": book["checking"], "category": "Food"})
    assert any(t["id"] == txn_id for t in listed["transactions"])

    patched = _call(
        update_transactions, {"transaction_id": txn_id, "category": "Shopping", "memo": "solo"}
    )
    assert patched["ok"] is True
    assert patched["transaction"]["memo"] == "solo"

    # A transaction_id lookup returns the single record under `transaction`, not a list.
    refetched = _call(get_transactions, {"transaction_id": txn_id})
    assert refetched["transaction"]["memo"] == "solo"
    assert refetched["transaction"]["category"] is not None


def test_update_rejects_an_unknown_category_with_the_same_recovery(book):
    created = _call(add_transactions, {"transactions": [_row(book)]})
    res = _call(
        update_transactions,
        {"transaction_id": created["transaction"]["id"], "category": "Fud"},
    )
    assert res["ok"] is False
    assert "Food" in res["error"] and "list_categories" in res["error"]


# --- 6. update_transactions: the owner-reported bug this tool was merged to fix -------
#
# "update memo for these transactions as 'test' on 7 selected rows" made Lucius issue 7
# separate update_transaction calls, because bulk_update_transactions existed but was never
# CORE-visible. These pin the fix at the ungated write layer (no confirm gate involved —
# that path is covered separately in tests/test_ledger_confirm_gate.py): N rows update in
# ONE call, whether they get the SAME change or each their OWN.


def test_update_transactions_applies_the_same_change_to_many_rows_in_one_call(book):
    """The owner's literal repro: one call, N ids, ONE shared field value."""
    ids = []
    for i in range(7):
        created = _call(
            add_transactions,
            {"transactions": [_row(book, description=f"Row {i}", amount=10 + i)]},
        )
        ids.append(created["transaction"]["id"])

    res = _call(
        update_transactions,
        {"updates": [{"transaction_id": tid, "memo": "test"} for tid in ids]},
    )
    assert res["ok"] is True
    assert res["updated"] == 7
    assert res["total_submitted"] == 7
    assert {r["transaction_id"] for r in res["results"] if r.get("ok")} == set(ids)
    for tid in ids:
        refetched = _call(get_transactions, {"transaction_id": tid})
        assert refetched["transaction"]["memo"] == "test"


def test_update_transactions_applies_a_different_change_to_each_row_in_one_call(book):
    """The gap the pre-merge shared-changes-only shape left: 'set a DIFFERENT category on
    each of these rows' needed one call per row even after discovering the bulk tool."""
    ids = []
    for i in range(3):
        created = _call(
            add_transactions,
            {"transactions": [_row(book, description=f"Row {i}", amount=10 + i)]},
        )
        ids.append(created["transaction"]["id"])

    res = _call(
        update_transactions,
        {
            "updates": [
                {"transaction_id": ids[0], "category": "Food"},
                {"transaction_id": ids[1], "category": "Shopping"},
                {"transaction_id": ids[2], "memo": "no category change"},
            ]
        },
    )
    assert res["ok"] is True
    assert res["updated"] == 3

    r0 = _call(get_transactions, {"transaction_id": ids[0]})["transaction"]
    r1 = _call(get_transactions, {"transaction_id": ids[1]})["transaction"]
    r2 = _call(get_transactions, {"transaction_id": ids[2]})["transaction"]
    assert r0["category"] != r1["category"]
    assert r2["memo"] == "no category change"
    assert r2["category"] is None  # untouched — this row's update never named a category


def test_update_transactions_single_row_still_trivial_no_array_ceremony(book):
    """The non-negotiable: a caller updating ONE row must not need array-of-objects
    ceremony — the bare legacy `update_transaction` shape (no `updates` wrapper at all)
    keeps working, unchanged by the merge."""
    created = _call(add_transactions, {"transactions": [_row(book)]})
    txn_id = created["transaction"]["id"]
    res = _call(update_transactions, {"transaction_id": txn_id, "category": "Shopping"})
    assert res["ok"] is True
    assert res["transaction"]["category"] is not None


# --- 5. The import_session_id procedure is now the harness's job ----------------------


def test_a_multi_row_import_returns_a_session_id_to_reuse(book):
    """The prose used to instruct the model to generate a uuid4 before its first batch.
    The harness mints it and hands it back instead."""
    first = _call(
        add_transactions,
        {
            "transactions": [
                _row(book, description="ROW A", amount=11),
                _row(book, description="ROW B", amount=12),
            ],
            "import_description": "Checking Jan",
        },
    )
    assert first["ok"] is True
    session = first["import_session_id"]
    assert session, "a multi-row import must return a session id for the next batch"

    second = _call(
        add_transactions,
        {
            "transactions": [
                _row(book, description="ROW C", amount=13),
                _row(book, description="ROW D", amount=14),
            ],
            "import_description": "Checking Jan",
            "import_session_id": session,
        },
    )
    assert second["ok"] is True
    assert second["import_session_id"] == session, "a caller-supplied id must be honoured"


def test_the_session_id_is_recorded_on_the_batch(book):
    res = _call(
        add_transactions,
        {
            "transactions": [_row(book, description="X"), _row(book, description="Y")],
            "import_session_id": "imp_fixed_value",
        },
    )
    from backend.services.ledger import import_batches

    batch = [b for b in import_batches.all() if b["id"] == res["batch_id"]][0]
    assert batch["import_session_id"] == "imp_fixed_value"
