"""Tests for the tool-name scope in _extra_groups_scope.py.

Each test runs in an isolated context via contextvars.copy_context() so
no state leaks between cases.
"""

import contextvars

from backend.ai.tools._extra_groups_scope import (
    add_extra_groups,
    add_extra_names,
    get_extra_groups,
    get_extra_names,
    set_extra_groups,
    set_extra_names,
)


def _run(fn):
    """Run *fn* inside a fresh copy of the current context and return its result."""
    ctx = contextvars.copy_context()
    return ctx.run(fn)


def test_default_is_empty_frozenset():
    def _():
        assert get_extra_names() == frozenset()

    _run(_)


def test_set_then_get():
    def _():
        set_extra_names(frozenset({"add_transaction", "get_portfolio"}))
        assert get_extra_names() == frozenset({"add_transaction", "get_portfolio"})

    _run(_)


def test_add_is_additive():
    def _():
        set_extra_names(frozenset({"add_transaction"}))
        result = add_extra_names(frozenset({"get_portfolio"}))
        assert result == frozenset({"add_transaction", "get_portfolio"})
        assert get_extra_names() == frozenset({"add_transaction", "get_portfolio"})

    _run(_)


def test_add_never_shrinks():
    def _():
        set_extra_names(frozenset({"a", "b", "c"}))
        add_extra_names(frozenset({"b"}))
        assert get_extra_names() == frozenset({"a", "b", "c"})

    _run(_)


def test_add_from_empty():
    def _():
        set_extra_names(frozenset())
        result = add_extra_names(frozenset({"telegram_send_message"}))
        assert result == frozenset({"telegram_send_message"})
        assert get_extra_names() == frozenset({"telegram_send_message"})

    _run(_)


def test_add_without_prior_set():
    """add_extra_names before set_extra_names treats the default as empty."""

    def _():
        result = add_extra_names(frozenset({"list_accounts"}))
        assert result == frozenset({"list_accounts"})
        assert get_extra_names() == frozenset({"list_accounts"})

    _run(_)


def test_name_scope_independent_of_group_scope():
    """Setting names must not affect groups and vice versa."""

    def _():
        set_extra_names(frozenset({"add_transaction"}))
        set_extra_groups(frozenset({"equity_writes"}))
        assert get_extra_names() == frozenset({"add_transaction"})
        assert get_extra_groups() == frozenset({"equity_writes"})

    _run(_)


def test_add_names_does_not_affect_groups():
    def _():
        set_extra_groups(frozenset({"equity_writes"}))
        add_extra_names(frozenset({"telegram_send_message"}))
        assert get_extra_groups() == frozenset({"equity_writes"})

    _run(_)


def test_add_groups_does_not_affect_names():
    def _():
        set_extra_names(frozenset({"telegram_send_message"}))
        add_extra_groups(frozenset({"equity_writes"}))
        assert get_extra_names() == frozenset({"telegram_send_message"})

    _run(_)


def test_contexts_are_isolated():
    """Changes in one copied context must not bleed into another."""

    def set_a():
        set_extra_names(frozenset({"a"}))
        return get_extra_names()

    def set_b():
        set_extra_names(frozenset({"b"}))
        return get_extra_names()

    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()
    result_a = ctx_a.run(set_a)
    result_b = ctx_b.run(set_b)
    assert result_a == frozenset({"a"})
    assert result_b == frozenset({"b"})
