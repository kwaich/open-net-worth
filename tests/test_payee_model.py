"""Payee model shape (P4-PAYEE phase 1)."""

from __future__ import annotations

from backend.models.payee import Payee


def test_defaults():
    p = Payee(id="pay_x")
    assert p.name == ""
    assert p.default_categories == {}
    assert p.autocategorize is True
    assert p.hidden is False
    assert p.aliases == []
    assert p.last_used is None
    # txn_count is computed live by list_registry() — not a stored model field
    assert not hasattr(p, "txn_count")


def test_ignores_extra_keys_and_dumps_clean():
    p = Payee(id="pay_x", name="Anthropic", aliases=["anthropic"], legacy_field="ignored")
    d = p.model_dump()
    assert "legacy_field" not in d
    assert d["name"] == "Anthropic"
    assert d["aliases"] == ["anthropic"]


def test_stored_txn_count_ignored_on_parse():
    """Rows that still carry txn_count from before the migration parse cleanly,
    and the field is silently dropped (extra='ignore'). This is the safe-read guarantee
    that makes migration non-urgent for parsing, while the migration ensures the raw
    JSON is also cleaned up."""
    p = Payee(id="pay_x", name="Old", txn_count=42)
    assert not hasattr(p, "txn_count")
    d = p.model_dump()
    assert "txn_count" not in d
