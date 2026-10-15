"""Regulatory-disclosure source REGISTRY — per-market, data-driven (owner directive
2026-07-15, mid-build amendment): Open Net Worth is open-source and must not hardcode the
German/US case the July-14 Uber/Kestrel Dash incident happened to involve.

A market's regulatory firehose is a table entry, not an inline branch: ``_REGISTRY`` maps a
Yahoo-ticker EXCHANGE SUFFIX — the same convention ``feeds._SUFFIX_LOCALE`` already uses, so
"which market is this ticker in" has one answer across locale + regulatory routing — to a list
of :class:`MarketFeed` entries. The hot tier (monitoring/rss_poller.py) fetches
:func:`all_regulatory_feeds` ONCE per tick (a firehose covers every watched entity in one
fetch — see ``services.feeds.match_hits_to_subjects`` for the server-side matching) regardless
of how many markets/entities are configured.

v1 ships two entries:
  * DE/CH/AT (suffixes DE/F/VI/SW) → finanznachrichten.de's public mirror of the EQS ad-hoc
    firehose (all German/Swiss/Austrian regulatory disclosures, low volume, one RSS URL).
  * Everything else (the default/US case) → SEC EDGAR's "current" 8-K Atom feed (near-
    real-time; requires a DECLARED User-Agent per SEC fair-use policy or requests are dropped).

**Extension point**: to add a market (Korea DART, Japan TDnet, UK RNS, …), add one
``MarketFeed`` entry to ``_REGISTRY`` under the relevant suffix key(s) — a URL-returning fn +
a parser + a `via` tag + an optional custom User-Agent. No poller/core change is needed; the
hot tier and ``all_regulatory_feeds`` are already suffix-agnostic.

Supported regulatory markets as of this lane: **DE/CH/AT** (EQS via the finanznachrichten.de
mirror) and **US** (EDGAR 8-K). Every other market has no dedicated regulatory lane yet — its
watched entities fall back to Google/Yahoo/GDELT (topics) plus whatever ``custom_feed_urls``
the user supplies (services.feeds.resolve_custom_feeds), until a registry entry is contributed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backend.services.feeds import parse_rss


@dataclass(frozen=True)
class MarketFeed:
    url_fn: Callable[[], str]
    parser: Callable[[bytes], list[dict]]
    via: str
    # None → the caller's own default RSS User-Agent. Set only when a source mandates its own
    # (EDGAR's SEC fair-use policy) so the registry entry is fully self-describing.
    user_agent: str | None = None


def _eqs_adhoc_url() -> str:
    return "https://www.finanznachrichten.de/rss-aktien-adhoc/"


def _edgar_current_8k_url() -> str:
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&"
        "dateb=&owner=include&count=100&output=atom"
    )


# SEC fair-use policy REQUIRES a declared, identifying User-Agent or requests are dropped
# outright. There is no owner-contact-email field in profile.json today (RFC open question) —
# v1 uses a fixed placeholder; swap for a real profile field if SEC follow-up ever requires one.
EDGAR_USER_AGENT = "OpenNetWorth/1.0 (contact: opennetworth-app@proton.me)"

_EQS_FEED = MarketFeed(_eqs_adhoc_url, parse_rss, "rss:fn_adhoc")
_EDGAR_FEED = MarketFeed(
    _edgar_current_8k_url, parse_rss, "atom:edgar", user_agent=EDGAR_USER_AGENT
)

# Suffix → market feeds, reusing feeds._SUFFIX_LOCALE's own exchange-suffix convention.
_REGISTRY: dict[str, list[MarketFeed]] = {
    "DE": [_EQS_FEED],
    "F": [_EQS_FEED],
    "VI": [_EQS_FEED],
    "SW": [_EQS_FEED],
}
_DEFAULT_MARKET_FEEDS: list[MarketFeed] = [_EDGAR_FEED]


def regulatory_feeds_for_market(suffix: str) -> list[MarketFeed]:
    """Market feeds for a Yahoo-ticker exchange suffix (e.g. 'DE' from 'KSTL.DE'), else the
    US/default set — mirrors ``feeds.locale_for_ticker``'s own default-to-US fallback."""
    return _REGISTRY.get((suffix or "").upper(), _DEFAULT_MARKET_FEEDS)


def all_regulatory_feeds() -> list[MarketFeed]:
    """The DISTINCT set of registered regulatory feeds across every market — what the hot tier
    actually fetches (once per tick each), since a shared firehose fans out to every entity that
    matches it rather than needing a fetch per watched entity."""
    seen: dict[str, MarketFeed] = {}
    for feeds in (*_REGISTRY.values(), _DEFAULT_MARKET_FEEDS):
        for f in feeds:
            seen[f.via] = f
    return list(seen.values())
