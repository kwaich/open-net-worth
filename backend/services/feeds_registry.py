"""RESOLVERS — the small name -> code mapping a ``NewsSourceStore`` row's ``resolver`` field
references (INT9 Phase A, .claude/rfcs/news-source-management.md §1).

A source whose URL is TEMPLATED per-entity (a ticker, a search query) or COMPUTED per-tick
(the GDELT combined topic query) cannot carry a concrete ``url`` in the store — the row is a
POLICY placeholder (enabled/cadence/min_interval_sec) and the actual URL construction stays
typed code. This registry is the single, tested join between a row's ``resolver`` name and
that code, so a row is never silently pointing at nothing.

Phase A does not INVOKE these generically (the fast/slow tiers keep their existing direct
calls into ``services.feeds``/``services.feeds_hot`` unchanged — RFC §5, "fast/slow tiers keep
their current resolver paths"); this registry exists so the seeded rows are self-describing
and verifiably wired —
``tests/test_news_source_store.py::test_every_seeded_resolver_row_is_covered_by_feeds_registry``
asserts every seeded row's ``resolver`` name (if any) is a real key here, so this module is
genuinely consulted rather than aspirational — and is the extension point a later phase's
generic dispatch would use.
"""

from __future__ import annotations

from typing import Callable

from backend.services import feeds, feeds_hot

# name -> the code that actually builds this source's URL(s). Signatures intentionally vary
# (a per-ticker resolver takes one ticker; the topic engine takes a pre-built query string) —
# this registry documents "which function", not a uniform call convention.
RESOLVERS: dict[str, Callable[..., str]] = {
    "yahoo_ticker": feeds.yahoo_ticker_url,
    "google_news_search": feeds.google_news_url,
    "gdelt_topic_engine": feeds_hot.gdelt_doc_url,
}
