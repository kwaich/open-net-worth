"""Outlet-headline-feed REGISTRY — a curated set of QUALITY outlet RSS feeds, on the exact same
MarketFeed-registry pattern as ``feeds_regulatory.py`` (read that module first).

**Why this exists** (live finding, 2026-07-16): FT published the "Uber nears EUR12.5bn deal to
acquire Kestrel Dash" scoop at 17:47:40 UTC. Our system's first copy (an aggregator's rewrite)
arrived 69 minutes later. FT's own FREE public RSS carried the EXACT headline AT publication —
headlines + standfirst are public, only the article body is paywalled. The significance screen
(``monitoring/news_screen.py``, INT5) judges materiality from HEADLINES ONLY, so polling
ORIGINATING outlets directly — not waiting for the world to rewrite a scoop — closes the 40-70
min gap for scoop-class news exactly like the regulatory registry closes it for disclosures.

Unlike ``feeds_regulatory.py``, outlet feeds are NOT market-keyed (a suffix-based registry makes
sense for "which regulator covers this ticker" but no sense for "which outlet covers this
story" — every outlet feed is fetched every hot tick and fanned out to every watched entity via
the SAME server-side matcher, ``services.feeds_hot.match_hits_to_subjects``). So this module is
a flat list, not a suffix dict.

**Curation policy — LIVE VERIFICATION, not assumption.** A candidate is kept only if, probed live:
  (a) it returns valid RSS/Atom (parseable by ``services.feeds.parse_rss``);
  (b) its items are FRESH — pubDate/updated within the last ~6h at probe time;
  (c) it is a headline feed of the ORIGINATING outlet (not an aggregator/mirror).
Capped at ~6 feeds shipped (a firehose fetch per tick, so breadth has a real cost). Every entry
below is dated with its probe result; a stale/dead candidate is documented as REJECTED so the
next person doesn't blindly re-add it.

Probed TWICE: 2026-07-16 with a browser UA (initial curation pass), then RE-PROBED 2026-07-16
with the EXACT UA production sends (``monitoring.collect._RSS_UA`` — the declared
``"Mozilla/5.0 (compatible; open-net-worth/1.0; +https://github.com/open-net-worth)"``, code
review SF-3 — a feed can freely allow a browser UA and still block/degrade a declared bot UA).
All 6 KEPT feeds returned identical 200-and-fresh results under BOTH UAs — no feed here is
UA-sensitive, so none needed (or got) a per-feed ``user_agent`` override; we prefer the honest
declared UA over spoofing a browser, and would rather drop a feed than fake identity to keep it.

KEPT (verified fresh under both the browser UA and the shipped `_RSS_UA`):
  * FT Companies      https://www.ft.com/companies?format=rss    — 200, items ~minutes old,
    valid RSS. Carries the exact Uber/Kestrel Dash scoop headline verified live.
  * FT Technology      https://www.ft.com/technology?format=rss   — 200, items ~minutes old,
    valid RSS. ALSO carried the exact scoop headline (tech-sector M&A) — kept alongside
    Companies since both were independently confirmed as the live-finding source.
  * CNBC Top News      https://www.cnbc.com/id/100003114/device/rss/rss.html — 200, items
    minutes-to-hours old, valid RSS ("US Top News and Analysis").
  * MarketWatch Top Stories  https://feeds.marketwatch.com/marketwatch/topstories/ — 200,
    items minutes old, valid RSS.
  * WSJ Markets (Dow Jones public feed host) https://feeds.content.dowjones.io/public/rss/RSSMarketsMain
    — 200, items minutes old, valid RSS, channel title confirms "WSJ.com: Markets".
  * TechCrunch         https://techcrunch.com/feed/  — 200, items minutes old, valid RSS.

REJECTED (documented so a future pass doesn't re-add blind):
  * FT Markets (https://www.ft.com/markets?format=rss) — verified live/fresh/valid, but dropped
    to stay under the ~6-feed cap: Companies + Technology already cover the corporate/tech-deal
    beat this lane exists for.
  * CNBC "Investing" (https://www.cnbc.com/id/15839069/device/rss/rss.html) — verified live/
    fresh/valid, but narrower (options-trading strategy pieces) and largely redundant with CNBC
    Top News; dropped to stay under cap.
  * Reuters (https://www.reutersagency.com/feed/) — HTTP 404, dead. Reuters has no public
    headline RSS at a stable URL as of this probe; reutersagency.com is a corporate PR site, not
    a news feed.
  * WSJ legacy feed (https://feeds.a.dj.com/rss/RSSMarketsMain.xml) — returns 200 but items are
    STALE (dated January 2025 at probe time) — dead/abandoned endpoint. The live WSJ markets
    feed is now served from feeds.content.dowjones.io (kept above).
  * Business Insider (https://www.businessinsider.com/rss, redirects to
    feeds.businessinsider.com/custom/all) — valid, fresh Atom feed, but it is BI's "All Content"
    firehose (celebrity/lifestyle/politics mixed with business) rather than a business/markets
    headline feed — too noisy a signal for a significance screen judging materiality from
    headlines alone. No dedicated BI markets-only public feed was found at a stable URL.

**Extension point**: add one ``OutletFeed`` to ``_OUTLET_FEEDS`` — a URL-returning fn + a parser
+ a distinct `via` (e.g. ``rss:reuters`` if a future feed appears) + an optional custom
User-Agent. Verify freshness live before adding; document a rejected candidate in the docstring
above so the curation record stays honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backend.services.feeds import parse_rss


@dataclass(frozen=True)
class OutletFeed:
    url_fn: Callable[[], str]
    parser: Callable[[bytes], list[dict]]
    via: str
    # None -> the caller's own default RSS User-Agent; no outlet below requires its own.
    user_agent: str | None = None


def _ft_companies_url() -> str:
    return "https://www.ft.com/companies?format=rss"


def _ft_technology_url() -> str:
    return "https://www.ft.com/technology?format=rss"


def _cnbc_top_news_url() -> str:
    return "https://www.cnbc.com/id/100003114/device/rss/rss.html"


def _marketwatch_top_stories_url() -> str:
    return "https://feeds.marketwatch.com/marketwatch/topstories/"


def _wsj_markets_url() -> str:
    return "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"


def _techcrunch_url() -> str:
    return "https://techcrunch.com/feed/"


_OUTLET_FEEDS: list[OutletFeed] = [
    OutletFeed(_ft_companies_url, parse_rss, "rss:ft_companies"),
    OutletFeed(_ft_technology_url, parse_rss, "rss:ft_technology"),
    OutletFeed(_cnbc_top_news_url, parse_rss, "rss:cnbc"),
    OutletFeed(_marketwatch_top_stories_url, parse_rss, "rss:marketwatch"),
    OutletFeed(_wsj_markets_url, parse_rss, "rss:wsj"),
    OutletFeed(_techcrunch_url, parse_rss, "rss:techcrunch"),
]


def all_outlet_feeds() -> list[OutletFeed]:
    """The full curated set — what the hot tier fetches once per tick, each fanned out
    server-side to every watched entity it matches (mirrors ``feeds_regulatory.all_regulatory_
    feeds``, minus the suffix-dedup step since this registry is already a flat, distinct list)."""
    return list(_OUTLET_FEEDS)
