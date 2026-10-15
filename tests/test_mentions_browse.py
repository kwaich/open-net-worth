"""Browsing the @ picker with nothing typed, and asking for a type by name.

Every provider's rows used to be concatenated in registry order, so an empty @ (limit 40) was
filled by the first few types and later ones — Documents among them — never showed (owner: "the
mention don't show all the types here, just some"). With nothing typed each type now takes its
turn. Typing a type's word ("@docs", "@files") lists that type. Synthetic data only.
"""

from __future__ import annotations

from types import SimpleNamespace

import backend.services.mentions.registry as registry_mod
from backend.services.mentions._common import _type_synonym_match
from backend.services.mentions.search import search
from backend.services.mentions.types import SearchQuery


def _provider(type_: str, n: int) -> SimpleNamespace:
    rows = [{"type": type_, "id": f"{type_}-{i}", "label": f"{type_} {i}"} for i in range(n)]
    return SimpleNamespace(surfaces=frozenset({"entity"}), search=lambda _q: rows)


def _query(q: str, limit: int) -> SearchQuery:
    return SearchQuery(q=q, types=None, surfaces=None, limit=limit, cursor=None)


def test_nothing_typed_gives_every_type_a_turn(monkeypatch):
    registry = {"holding": _provider("holding", 30), "document": _provider("document", 3)}
    monkeypatch.setattr(registry_mod, "REGISTRY", registry)
    page = search(_query("", limit=6))["items"]
    assert [r["type"] for r in page] == [
        "holding",
        "document",
        "holding",
        "document",
        "holding",
        "document",
    ]
    # Each type keeps its own order.
    assert [r["label"] for r in page if r["type"] == "holding"] == [
        "holding 0",
        "holding 1",
        "holding 2",
    ]


def test_a_typed_query_still_ranks_by_how_well_the_label_matches(monkeypatch):
    registry = {"holding": _provider("holding", 3), "document": _provider("document", 3)}
    monkeypatch.setattr(registry_mod, "REGISTRY", registry)
    page = search(_query("document 2", limit=3))["items"]
    assert page[0]["label"] == "document 2"


def test_a_documents_word_lists_documents():
    for q in ("doc", "docs", "document", "Documents", "fil", "files"):
        assert _type_synonym_match("document", q), q
    assert _type_synonym_match("document", "deed") is None
