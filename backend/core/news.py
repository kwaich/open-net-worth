"""News-list quality/dedup helpers shared across the live-query and paid-search paths
(leaf — stdlib only, no `monitoring`/`services` import; see each section below).

## Same-publisher dedup (`dedupe_stories`)

URL-keyed dedup cannot see that two rows are the same story when it arrives via two
provenance paths — a live-search hit carries the direct publisher URL while the RSS
evidence cache stores the Google News redirect wrapper — or when a publisher retitles
a developing story ("… on report of Uber takeover talks" → "… on Uber takeover talks").
Both render as visibly duplicated cards (the 2026-07-15 Uber/Kestrel Dash report).

Story identity here = SAME PUBLISHER (source label or domain) + near-identical
normalized headline + published within a tight window. Cross-publisher lookalikes are
deliberately kept — different outlets covering one event are distinct stories.

## Cross-publisher clustering + quality filters (RFC `intel-signals-data-model`, §5.2)

`cluster_by_content_overlap` (moved from `monitoring/analyst_prompt.py`) and the six
`monitoring/asset_brief.py` quality filters below (`recency_hours`/`news_sort_key`/
`is_stale_news`/`has_known_publish_date`/`is_generic_reference_title`/
`is_filename_shaped_title`) moved HERE so `services/asset_detail.py`'s live-query news
read (`_live_news_for_instrument`) can call them without an illegal `services →
monitoring` import — `services/` may not import `monitoring/` (CLAUDE.md), the identical
reason ADR `0004-intel-deterministic-signals-and-citations` moved `_hostname` to
`core/urls.py`. `monitoring/analyst_prompt.py` and `monitoring/asset_brief.py` both keep
importing these under their OLD private names (`from backend.core.news import
cluster_by_content_overlap`, `recency_hours as _recency_hours`, etc.) so every existing
call site and test in those two files is unchanged.

`_content_tokens`/`_tokenize`/`_OVERLAP_STOPWORDS` below are a DELIBERATE duplicate of
`monitoring/text_sanitize.py`'s `content_tokens`/`tokenize`/`_OVERLAP_STOPWORDS` — small
and stdlib-only, but importing them from `monitoring/text_sanitize.py` here would make
`core/` (a leaf every layer depends on) import `monitoring/`, upside down. Same
duplication discipline as `db/payload_store.py::_assert_single_column_pk` (duplicated,
not imported, because `db` must never import `ai`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from backend.core.urls import hostname as _hostname

# Connective words that vary between retellings of one headline ("on report of …")
# without changing the story. Deliberately tiny — content words must keep their weight.
_STOPWORDS = frozenset(
    {"a", "an", "the", "on", "of", "in", "with", "over", "for", "to", "and", "at", "as", "by"}
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Jaccard similarity two same-publisher headlines must reach to collapse. 0.6 keeps
# genuinely different stories apart (shared ticker words alone score far lower).
_SIMILARITY_THRESHOLD = 0.6

# Same-publisher lookalikes further apart than this are kept — recurring headline
# patterns (daily movers columns) repeat across days and are NOT one story.
_WINDOW_HOURS = 48.0


def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall((title or "").casefold())) - _STOPWORDS


def _publisher_keys(item: dict) -> frozenset[str]:
    return frozenset(
        v
        for v in (
            (item.get("source") or "").casefold().strip(),
            (item.get("domain") or "").casefold().strip(),
        )
        if v
    )


def _published_dt(item: dict) -> datetime | None:
    raw = (item.get("published_at") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _within_window(a: datetime | None, b: datetime | None) -> bool:
    """The window guard only applies when BOTH sides carry a date — a dateless cached
    row must not defeat the collapse."""
    if a is None or b is None:
        return True
    try:
        delta_hours = abs((a - b).total_seconds()) / 3600.0
    except TypeError:  # naive vs aware — treat as unknown rather than blocking
        return True
    return delta_hours <= _WINDOW_HOURS


def dedupe_stories(items: list[dict]) -> list[dict]:
    """Collapse same-publisher near-duplicate stories, keeping the FIRST occurrence
    (callers pass a priority/recency-sorted list). Untitled rows pass through."""
    kept: list[dict] = []
    kept_meta: list[tuple[frozenset[str], frozenset[str], datetime | None]] = []
    for item in items or []:
        tokens = _title_tokens(item.get("title") or "")
        publishers = _publisher_keys(item)
        published = _published_dt(item)
        duplicate = False
        if tokens and publishers:
            for k_tokens, k_publishers, k_published in kept_meta:
                if not (publishers & k_publishers):
                    continue
                if not k_tokens or not _within_window(published, k_published):
                    continue
                union = len(tokens | k_tokens)
                if union and len(tokens & k_tokens) / union >= _SIMILARITY_THRESHOLD:
                    duplicate = True
                    break
        if duplicate:
            continue
        kept.append(item)
        kept_meta.append((tokens, publishers, published))
    return kept


# ── Cross-publisher clustering (moved from monitoring/analyst_prompt.py) ────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Duplicate of monitoring/text_sanitize.py's own stopword set — see module docstring for
# why this is a deliberate copy, not an import.
_OVERLAP_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "that",
        "this",
        "it",
        "its",
        "as",
        "by",
        "not",
        "no",
        "vs",
        "new",
        "says",
        "said",
        "say",
        "report",
        "reports",
        "reported",
        "news",
        "amid",
        "over",
        "into",
        "after",
        "before",
        "up",
        "down",
        "out",
    }
)

_SAME_STORY_OVERLAP = 0.6  # mirrors news_screen._CLUSTER_OVERLAP's threshold


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _content_tokens(text: str, *, subject_name: str = "") -> set[str]:
    toks = _tokenize(text) - _OVERLAP_STOPWORDS
    if subject_name:
        toks -= _tokenize(subject_name)
    return toks


def cluster_by_content_overlap(
    rows: list[dict], *, subject_name: str = "", overlap: float = _SAME_STORY_OVERLAP
) -> list[list[dict]]:
    """Group rows sharing a subject's evidence by normalized-content-token overlap — the SAME
    cheap, no-LLM dedup ``news_screen._cluster_rows`` already uses to spot syndicated copies
    (INT12), generalized here (INT14b) so analyst.py's evidence-read path can reuse ONE
    implementation instead of a second copy drifting apart. A hit with no (content) title starts
    its own cluster. `subject_name` excludes the subject's own name + a small stopword list from
    the token set (SF-2) — otherwise two headlines sharing only the subject's name + grammatical
    filler could false-positive as the same story."""
    clusters: list[list[dict]] = []
    cluster_tokens: list[set[str]] = []
    for row in rows:
        toks = _content_tokens(row.get("title") or "", subject_name=subject_name)
        placed = False
        if toks:
            for i, ctoks in enumerate(cluster_tokens):
                if ctoks and len(toks & ctoks) / min(len(toks), len(ctoks)) >= overlap:
                    clusters[i].append(row)
                    cluster_tokens[i] = ctoks | toks
                    placed = True
                    break
        if not placed:
            clusters.append([row])
            cluster_tokens.append(toks)
    return clusters


# Owner report 2026-08-30: Google News' RSS <description> is frequently just the <title> again
# (sometimes with the publisher name appended via a non-breaking-space separator instead of a
# genuine excerpt) — a real snippet field with essentially no information beyond the title it
# already sits next to. Token-overlap based (reusing cluster_by_content_overlap's own ratio),
# not exact-string, so a trivial separator/punctuation/whitespace difference (dash vs. non-
# breaking space, "Foo - Bar" vs. "Foo  Bar") never defeats the check.
_SNIPPET_REDUNDANT_OVERLAP = 0.85


def is_snippet_redundant_with_title(title: str, snippet: str) -> bool:
    """True iff `snippet` adds essentially no information beyond `title` — see the constant's
    own comment for why this check exists. False (never redundant) for an empty snippet or an
    empty title — nothing to compare, so the caller's own "snippet truthy" gate handles that
    case."""
    if not title or not snippet:
        return False
    t_tokens = _tokenize(title)
    s_tokens = _tokenize(snippet)
    if not t_tokens or not s_tokens:
        return False
    overlap = len(t_tokens & s_tokens) / min(len(t_tokens), len(s_tokens))
    return overlap >= _SNIPPET_REDUNDANT_OVERLAP


# ── News-list quality filters (moved from monitoring/asset_brief.py) ────────────────

# Hosts that mostly publish "is it undervalued?" valuation op-eds or quote/aggregator
# pages rather than hard news. Soft down-rank: they still appear, just below real news
# outlets (Reuters/Bloomberg/WSJ/etc.), so breaking stories lead the feed.
_DOWNRANK_HOSTS: frozenset[str] = frozenset(
    {
        "simplywall.st",
        "stockanalysis.com",
        "marketbeat.com",
        "tipranks.com",
        "kavout.com",
        "fool.com",
        "etoro.com",
        "tradingview.com",
        "barrons.com",
        "marketscreener.com",
        "boerse.de",
    }
)

# Evergreen REFERENCE sources (encyclopaedia / definitions). EXEMPT from the 6-month news
# recency cut: a Wikipedia page reads as "7y ago" yet still usefully feeds the company
# description/facts synthesis, so we keep fetching it. Matched by suffix (en./fr. subdomains).
_REFERENCE_HOSTS: frozenset[str] = frozenset({"wikipedia.org", "investopedia.com"})

# News verticals report article age as a relative string ("3 hours ago", "2 weeks ago",
# "yesterday"); static quote/aggregator pages have an absolute date or none. We use the
# presence of a relative marker to tell real articles apart from quote pages.
_REL_AGO_RE = re.compile(r"\bago\b|yesterday|just now|\btoday\b")
_REL_UNIT_RE = re.compile(
    r"(\d+)\s*(years?|yrs?|months?|mo|weeks?|wks?|days?|hours?|hrs?|minutes?|mins?|[ymwdh])\b"
)

# Asset briefs are about RECENT developments; news datable to older than ~6 months is dropped
# (before page-fetch + LLM synthesis, so stale items never cost anything). Undated pages can't
# be proven old and already sort below real news, so they're kept; evergreen reference sources
# (Wikipedia etc.) are exempt so the company description/facts still benefit from them.
_MAX_NEWS_AGE_HOURS = 183 * 24.0  # ~6 months


def recency_hours(published_at: str) -> float:
    """Approximate article age in HOURS (smaller = newer). Undated/unparseable -> 1e9 (last)."""
    s = (published_at or "").strip().lower()
    if not s:
        return 1e9
    if "just now" in s or "today" in s:
        return 0.0
    if "yesterday" in s:
        return 24.0
    m = _REL_UNIT_RE.search(s)
    if m:
        n = float(m.group(1))
        u = m.group(2)
        if u.startswith(("year", "yr", "y")):
            return n * 8760.0
        if u.startswith(("month", "mo")):
            return n * 730.0
        if u.startswith(("week", "wk", "w")):
            return n * 168.0
        if u.startswith(("day", "d")):
            return n * 24.0
        if u.startswith(("hour", "hr", "h")):
            return n * 1.0
        return n / 60.0  # minutes
    raw = published_at.strip()
    dt = None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        # RSS feeds (Google News / Yahoo) emit RFC-2822 pubDates ("Wed, 27 May 2026 12:00:00
        # +0000"), which fromisoformat can't read. Without this, every RSS article — the bulk
        # of the evidence cache — scores 1e9 and sinks below the few live-search hits, so it
        # never enters the top-12 window fed to the synthesis LLM. parsedate_to_datetime reads it.
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(raw)
        except Exception:
            return 1e9
    if dt is None:
        return 1e9
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def news_sort_key(item: dict) -> tuple[int, float]:
    """Sort: real fresh articles (relative date, non-aggregator host) first, newest-first;
    analysis/quote/undated pages after. Within each tier, newest first."""
    pa = item.get("published_at") or ""
    host = _hostname(item.get("url") or "")
    fresh = bool(_REL_AGO_RE.search(pa.lower()))
    tier = 0 if (fresh and host not in _DOWNRANK_HOSTS) else 1
    return (tier, recency_hours(pa))


def _is_reference_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in _REFERENCE_HOSTS)


def is_stale_news(item: dict) -> bool:
    """True iff the item carries a KNOWN publish date older than ~6 months AND is not an
    evergreen reference source. Undated items (recency 1e9) are never treated as stale."""
    if _is_reference_host(_hostname(item.get("url") or "")):
        return False
    hrs = recency_hours(item.get("published_at") or "")
    return hrs < 1e9 and hrs > _MAX_NEWS_AGE_HOURS


def has_known_publish_date(item: dict) -> bool:
    """True iff `published_at` parses to a real date/age at all — reuses `recency_hours`'s own
    undated/unparseable sentinel (1e9), not a second date parser. A genuine news article carries
    a real publish date; a company-profile/quote/overview page typically never supplies one at
    all. Deliberately a DIFFERENT question from `is_stale_news` (proven old vs. not provably
    old) — that function explicitly KEEPS an undated item because it can't be proven stale; this
    one drops it because the news LIST is for real, dated developments, not evergreen reference
    filler."""
    return recency_hours(item.get("published_at") or "") < 1e9


# KNOWN GENERIC REFERENCE-PAGE TITLE ENDINGS — EXPAND CAUTIOUSLY. Confirmed live 2026-08-29:
# `has_known_publish_date` alone isn't sufficient — some evergreen company-profile/quote pages
# DO carry a real-looking timestamp (apparently a search provider's page-fetch/crawl time, not a
# true article-publish date), so the date-only check let "Kestrel Dash Company Overview"
# (reuters.com) back onto the page with a "19h ago" stamp attached. Those pages share a
# TEMPLATED title ending regardless of publisher (generated from a fixed page layout, not
# written by a reporter about a specific event), so a tight SUFFIX match on that ending is a
# second, narrower signal a real headline essentially never coincidentally satisfies. Only add
# an ending here once it's a CONFIRMED, recurring template seen in real data — never a guess,
# and never a bare word/phrase likely to appear at the end of genuine prose.
_GENERIC_REFERENCE_TITLE_ENDINGS: tuple[str, ...] = (
    "company overview",
    "stock price and performance data",
    "stock price & latest news",
    "stock price quote & news",
    "stock price, quote and news",
    "stock profile and overview",
    "stock quote",
    "quote & history",
    "overview",
)

# \b before the alternation means "overview" only matches as its own trailing WORD (preceded by
# a non-word boundary), not as the tail of some unrelated longer word.
_GENERIC_REFERENCE_TITLE_ENDING_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(e) for e in _GENERIC_REFERENCE_TITLE_ENDINGS) + r")\s*$",
    re.IGNORECASE,
)

# "- Quote <VenueName>" — confirmed live 2026-08-31 (KSTL/marketscreener.com: "Kestrel Dash SE
# Stock (KSTL) - Quote Xetra - MarketScreener"). A SEPARATE regex, not another literal string in
# _GENERIC_REFERENCE_TITLE_ENDINGS above, because the venue name is the variable part — a bare
# "quote xetra" literal would only ever catch Xetra-listed names, never the SAME template for a
# NYSE/Nasdaq/LSE-listed one, and this codebase's own standing rule is genuine solutions here
# must generalize across users/tickers/exchanges, never a per-instance literal (see the ticker-
# collision fix earlier this session for the same principle applied to a different filter). The
# venue token itself is unconstrained (`\w+`) — the template shape ("Quote" immediately followed
# by exactly one more word, then end-of-title) is the actual generic signal, not any specific
# exchange name; "quote" alone (no trailing word) is already covered by the literal list above.
# Requires a LITERAL "-"/"|" separator right before "quote" (matching the confirmed template's
# own punctuation), not bare word-boundary "quote" — a real headline ending in ordinary prose
# like "...with a bold quote today" must never match; a dash immediately before "quote" is a
# template-separator shape organic writing essentially never produces.
_GENERIC_QUOTE_VENUE_TITLE_ENDING_RE = re.compile(r"[-|]\s*quote\s+\w+\s*$", re.IGNORECASE)

# Search results/aggregators often append " - SourceName" or " | SourceName" to a title
# (" - Yahoo Finance", " | Reuters") — sometimes MORE THAN ONE, chained ("... - Xanadu Quantum -
# Robinhood"). Stripped BEFORE the ending check so an attribution tail never hides the underlying
# generic ending from the suffix match. Bug fix (2026-08-30): originally dash-only, so a
# pipe-separated title — e.g. Reuters' own company-quote-page template "(KSTL.DE) | Stock Price &
# Latest News | Reuters" — never got its "| Reuters" tail stripped, leaving the true suffix
# "| Reuters" (not a known generic ending) for the check below, so the row survived filtering.
# `[^-|]` (not just `[^-]`) keeps the match anchored to just the LAST segment either way, so a
# title with both separators still strips correctly.
#
# Bug fix (2026-08-31, live: marketscreener.com's own template, "...Quote Xetra- MarketScreener"
# — no space before that final dash, only after): originally required a space on BOTH sides
# (`\s[-|]\s`), so this asymmetric spacing never stripped, leaving "MarketScreener" attached and
# hiding the real generic ending ("Quote Xetra") from every check below. Now requires a space on
# AT LEAST one side (`(?:\s[-|]|[-|]\s)`) — a genuine separator dash/pipe between two title
# segments always has whitespace on at least one side in real-world scraped titles, whereas a
# compound-word hyphen ("self-driving", "co-founder") never has whitespace on EITHER side, so
# this stays anchored to actual separators, not mid-word punctuation.
_TRAILING_SOURCE_SUFFIX_RE = re.compile(r"(?:\s[-|]|[-|]\s)[^-|]{1,40}$")

# A single strip corrupts a title with only ONE trailing segment where that segment IS the
# generic ending itself, no separate source name after it (e.g. "MSFT: Microsoft Corp - Stock
# Price, Quote and News" — one dash, "Stock Price, Quote and News" is the whole tail): the strip
# removes the ending phrase along with it, leaving a truncated string ("MSFT: Microsoft Corp")
# that matches nothing. Confirmed live 2026-08-31: the SAME CNBC template rendered both WITH a
# trailing " - CNBC" (stripped correctly, one segment left) and WITHOUT one (nothing left to
# strip) across different tickers, so the ending check must not depend on guessing which shape a
# given title took.
_MAX_TRAILING_SUFFIX_STRIPS = 3


def is_generic_reference_title(title: str) -> bool:
    """True iff `title` reads as a generic company-reference/profile/quote-summary LABEL rather
    than a headline describing an event — the second, narrower filter layer that runs alongside
    `has_known_publish_date` (see `_GENERIC_REFERENCE_TITLE_ENDINGS`'s docstring for why the
    date-only check alone isn't enough). Anchored on SUFFIX matching, case-insensitive, tried
    against the title as-is and after each of up to `_MAX_TRAILING_SUFFIX_STRIPS` trailing
    " - SourceName"/" | SourceName" tails are peeled off — checking the UNSTRIPPED title first
    means a generic ending with no separate source tail after it is never destroyed by a strip
    that assumed one was there. Deliberately NOT substring-anywhere, so a real headline that
    merely mentions one of these phrases mid-title (rather than ending with it) survives."""
    t = (title or "").strip()
    if not t:
        return False
    for _ in range(_MAX_TRAILING_SUFFIX_STRIPS + 1):
        if _GENERIC_REFERENCE_TITLE_ENDING_RE.search(
            t
        ) or _GENERIC_QUOTE_VENUE_TITLE_ENDING_RE.search(t):
            return True
        stripped = _TRAILING_SOURCE_SUFFIX_RE.sub("", t).strip()
        if stripped == t:
            break
        t = stripped
    return False


# KNOWN CAMERA/SCREENSHOT DEFAULT-FILENAME PREFIXES — expand cautiously, same discipline as
# _GENERIC_REFERENCE_TITLE_ENDINGS. Confirmed live 2026-08-29: a search provider served an
# UPLOADED IMAGE's raw filename as a page's title/OG-title metadata instead of a real headline
# (e.g. someone posted a screenshot on a publisher's site and the page's title is literally the
# screenshot's filename) — a genuinely fetched, dated, non-garbled, non-generic-reference item,
# so neither `has_known_publish_date` nor `is_generic_reference_title` catches it. These are
# the default filename stems iOS/Android/Windows/common cameras and screenshot tools emit.
_FILENAME_TITLE_PREFIX_RE = re.compile(
    r"^(?:screen\s?shot|img|image|photo|pic(?:ture)?|dsc[nf]?|vid(?:eo)?|mov|clip)[\s_-]*\d",
    re.IGNORECASE,
)

# A title that, once its digit/date/time/separator characters are stripped away, has almost no
# ALPHABETIC content left reads as "mostly a date+time digit string with no real words" — the
# general shape a camera/screenshot filename takes regardless of its specific prefix vocabulary
# (catches a filename convention not on the known-prefix list above). A genuine headline, even a
# terse numbers-heavy one, always leaves more than a couple of real letters behind.
_FILENAME_TITLE_MIN_ALPHA_CHARS = 3


def is_filename_shaped_title(title: str) -> bool:
    """True iff `title` reads as a raw camera/screenshot filename rather than a headline —
    sibling to `is_generic_reference_title`: that filter catches a templated REFERENCE-page
    label, this one catches a filename that happens to land on an otherwise-legitimate, dated,
    non-generic news item. Shape-based (a known default-filename prefix, OR "almost entirely
    digits/separators"), never a match on one specific incident's literal string."""
    t = _TRAILING_SOURCE_SUFFIX_RE.sub("", (title or "").strip()).strip()
    if not t:
        return False
    if _FILENAME_TITLE_PREFIX_RE.match(t):
        return True
    return sum(1 for ch in t if ch.isalpha()) < _FILENAME_TITLE_MIN_ALPHA_CHARS


# Owner report 2026-08-30: an "AI stocks"/sector-roundup article that happens to mention many
# held/watched companies by name (Microsoft, Nvidia, Tesla, Meta, and Oracle, live) gets
# correctly matched to EVERY one of those companies' subjects by the RSS entity-matching
# fan-out (`monitoring.rss_poller_hot._monitor_news_entities` + `services.feeds_hot`'s own
# GDELT match) — by DESIGN, so real company-specific coverage is never missed. But that same
# breadth means a story primarily about a DIFFERENT company still leads THIS company's own
# dedicated feed, diluting it with news that isn't really about it.
#
# Owner correction, same report: a bare "any other tagged company disqualifies it" rule is
# too aggressive — it would ALSO drop a genuine two-company story (an acquisition, an M&A
# pair — the Uber/Kestrel Dash story this whole investigation started from) from BOTH
# companies' feeds, which is exactly wrong: that story belongs on both. The real bar (second
# owner correction) is relevance, not exclusivity: drop a cross-tagged row ONLY when it also
# fails to actually NAME this company anywhere near its own title — `is_too_broad_for_subject`
# is the fan-out signal, `title_mentions_company` is the escape hatch a genuine multi-company
# story passes (its title says so) and a roundup/passing-mention doesn't.
_MAX_OTHER_ASSET_SUBJECTS = 0


def is_too_broad_for_subject(row_subjects: list[str], subject: str) -> bool:
    """True iff `row_subjects` (an evidence row's own `subjects` list) names MORE than
    `_MAX_OTHER_ASSET_SUBJECTS` other asset:-type subjects besides `subject` itself — the
    FAN-OUT half of the two-part check (see the constant's own comment); a row this flags is
    only actually dropped if `title_mentions_company` ALSO comes back False for it. `topic:`/
    `scan:` entries never count (a story can legitimately sit under several topics without
    being any less specific about ITS company)."""
    others = {s for s in row_subjects or [] if s.startswith("asset:") and s != subject}
    return len(others) > _MAX_OTHER_ASSET_SUBJECTS


# Journalistic headlines drop a formal corporate suffix ("Kestrel Dash SE" -> "Kestrel Dash"),
# so every title-text relevance match — this module's `title_mentions_company` AND the ingest
# matcher in `monitoring/rss_poller_hot` — checks the bare form too. ONE implementation: a
# stripped-once copy left "Palantir Technologies Inc." as "Palantir Technologies" and
# "Tesla, Inc." as "Tesla," — neither ever appears in a headline, so real coverage was dropped
# from the asset page and never routed from shared firehoses (owner report 2026-09-24).
_CORPORATE_SUFFIXES = frozenset(
    {
        "se",
        "ag",
        "plc",
        "nv",
        "n.v",
        "sa",
        "s.a",
        "gmbh",
        "llc",
        "inc",
        "corp",
        "corporation",
        "ltd",
        "limited",
        "co",
        "incorporated",
        "&",
    }
)

# Descriptor words headlines also drop ("Palantir Technologies" -> "Palantir"). Stripped only
# when ONE distinctive word of at least _DESCRIPTOR_BARE_MIN_LEN chars remains: "Space
# Exploration Technologies Corp." must never reduce to "Space Exploration", a phrase every
# NASA story would substring-match.
_DESCRIPTOR_WORDS = frozenset(
    {"technologies", "technology", "holdings", "holding", "group", "company"}
)
_DESCRIPTOR_BARE_MIN_LEN = 6


def _suffix_key(word: str) -> str:
    return word.strip(".,").lower()


def bare_company_name(label: str) -> str | None:
    """`label` with every trailing corporate suffix (and stray comma) removed, plus one trailing
    descriptor when a single distinctive word survives it; None when that changes nothing."""
    words = (label or "").split()
    while len(words) > 1 and _suffix_key(words[-1]) in _CORPORATE_SUFFIXES:
        words.pop()
    if words:
        words[-1] = words[-1].rstrip(",")
    if (
        len(words) == 2
        and _suffix_key(words[-1]) in _DESCRIPTOR_WORDS
        and len(words[0].rstrip(",")) >= _DESCRIPTOR_BARE_MIN_LEN
    ):
        words = [words[0].rstrip(",")]
    bare = " ".join(words)
    return bare if bare and bare != (label or "").strip() else None


# A raw TICKER SYMBOL can't be told apart from ordinary prose by case-insensitive matching
# alone whenever it happens to also spell a common English word — "NOW" (ServiceNow) matched
# "5 affordable cities to retire... right now" just as readily as a genuine mention of the stock
# (owner report 2026-08-31, live). This is a structural fact about the term (IS it the literal
# ticker symbol), never something to detect by checking it against a word list — a curated list
# only ever protects the specific tickers someone thought to add, silently missing the next
# holding, for this user or any other, whose symbol happens to collide (first attempt at this
# fix used exactly such a list; owner correction: "must be generic for all users"). Real tickers
# are near-universally rendered in full uppercase in running text ("NOW Stock Surges",
# "(NYSE: NOW)", "$NOW"); ordinary prose using the same spelling essentially never is, even
# sentence-initial ("Now hiring" capitalizes only the first letter) — so ANY ticker symbol,
# unconditionally, matches only as an exact-case whole token against the title's ORIGINAL
# casing, never as a case-insensitive substring.
def _ticker_mentions_title(ticker: str, title: str) -> bool:
    return re.search(rf"\b{re.escape(ticker.strip().upper())}\b", title or "") is not None


# Mirrors `services.feeds_hot._WHOLE_TOKEN_MAX_LEN` (kept as a separate constant, not imported —
# core/ must not import services/): a short string is prone to matching plain-English words as a
# raw substring (SF-2: "ALL" once matched inside "wALL"). Below the floor: whole-token match
# only. At/above it: plain substring, so a multi-word phrase still matches inside a longer
# headline.
_SHORT_TERM_WHOLE_TOKEN_LEN = 6


def _term_in_title(term: str, title_casefolded: str) -> bool:
    t = term.casefold()
    if len(t) < _SHORT_TERM_WHOLE_TOKEN_LEN:
        return re.search(rf"\b{re.escape(t)}\b", title_casefolded) is not None
    return t in title_casefolded


def title_mentions_company(
    title: str, terms: list[str], *, tickers: list[str] | None = None
) -> bool:
    """True iff `title` names this company by any of `terms` (its name and bare/short forms) OR
    any of `tickers` (its raw ticker symbol(s) — see `_ticker_mentions_title`'s own comment for
    why these match differently) — the relevance escape hatch for a row `is_too_broad_for_
    subject` flags (see that function's own docstring): a story genuinely, substantively about a
    company almost always says so in its own headline, even one that ALSO names a counterparty
    ("Uber to Acquire Kestrel Dash" genuinely belongs on Kestrel Dash's page despite being
    cross-tagged to Uber too); a roundup/passing-mention piece ("How Wide Is The Risk Band On
    IBM Stock?", cross-tagged to a DIFFERENT company via body text) usually doesn't.

    `terms` matches case-insensitive, whole-token for a term under `_SHORT_TERM_WHOLE_TOKEN_LEN`
    chars (mirrors `services.feeds_hot._WHOLE_TOKEN_MAX_LEN`'s own SF-2 fix — a short bare name
    a corporate suffix strips down to, e.g. "GO Corp" -> "GO", is exactly as substring-collision-
    prone as a short raw term is: unguarded, "GO" would match mid-word inside "embarGO" the same
    way "ALL" once matched inside "wALL"), plain substring at/above it (so a multi-word phrase
    like "kestrel dash" still matches inside a longer headline). A false negative here just
    means a genuine multi-company story is judged by fan-out alone, the same outcome as before
    this escape hatch existed, never a false positive that hides real coverage. `tickers` is
    matched separately and exact-case (see `_ticker_mentions_title`) — callers should pass a
    term's own ticker/symbol value there instead of folding it into `terms`, or a case-
    insensitive match on the SAME string via `terms` would silently rubber-stamp almost any
    prose as on-topic again."""
    t = (title or "").casefold()
    for ticker in tickers or []:
        if ticker and _ticker_mentions_title(ticker, title):
            return True
    for term in terms:
        if not term:
            continue
        if _term_in_title(term, t):
            return True
        bare = bare_company_name(term)
        if bare and _term_in_title(bare, t):
            return True
    return False


# Every `via` whose row can land on a subject ONLY because server-side title-text matching
# (`services.feeds_hot.match_hits_to_subjects`/`_term_matches_title`) decided so — never because
# the FETCH itself was already scoped to this one company (a per-symbol URL, or a search query
# built from this company's own name). "rss:yahoo" is `services.feeds.yahoo_ticker_url`, the
# only per-TICKER feed this app polls; the rest are `services.feeds_outlets`/`feeds_regulatory`'s
# registries — a fixed set of general "top stories"/regulatory-disclosure firehoses, each fetched
# ONCE per tick and fanned out to EVERY watched entity by the same title-text matcher, with no
# per-entity query narrowing at all (owner report 2026-08-31, live ServiceNow: CNBC's and
# MarketWatch's general top-stories feeds mixed in "5 affordable cities to retire... right now"
# and a pickle-company piece, matched purely because ServiceNow's own ticker term, "NOW", also
# spells a common English word — see `_ticker_mentions_title`'s own comment for the general fix;
# this is the title-content second layer that was supposed to catch what slips through the
# matcher, same as it already did for `rss:yahoo`/NVDA). A Google-News search result
# (`rss:google_news`) is excluded: it was already QUERIED using this company's own name/ticker,
# so absence from its title is expected and not itself suspicious.
#
# Keep this set in sync with feeds_outlets._OUTLET_FEEDS and feeds_regulatory's registry — every
# entry added there is this same risk class by construction (core/ must not import services/, so
# this can't be derived automatically; a new outlet/regulatory feed needs its `via` added here).
_SHARED_FIREHOSE_MATCHED_VIA: frozenset[str] = frozenset(
    {
        "rss:yahoo",
        "rss:cnbc",
        "rss:marketwatch",
        "rss:ft_companies",
        "rss:ft_technology",
        "rss:wsj",
        "rss:techcrunch",
        "rss:fn_adhoc",
    }
)


def is_offtopic_ticker_feed_item(
    row_via: str, title: str, terms: list[str], *, tickers: list[str] | None = None
) -> bool:
    """True when a row from a SHARED, TITLE-TEXT-MATCHED feed (`row_via` in
    `_SHARED_FIREHOSE_MATCHED_VIA`) doesn't relate to this company at all — a DIFFERENT bug than
    `is_too_broad_for_subject` catches: that one drops a row cross-tagged to OTHER asset subjects
    too; this one catches a row tagged to ONLY this one subject, but WRONGLY (owner report
    2026-08-31, live NVDA: Yahoo's per-symbol RSS feed mixed in generic market-mover/prediction
    roundups about Micron, Moderna, SpaceX and a pipeline MLP — none cross-tagged, each tagged
    SOLELY to NVDA's own subject — with zero connection to NVIDIA; the same day, live
    ServiceNow: CNBC's/MarketWatch's general top-stories firehoses did the identical thing
    because ServiceNow's own ticker, "NOW", also spells a common English word — see
    `_SHARED_FIREHOSE_MATCHED_VIA`'s own comment).

    Scoped to feeds whose row can ONLY be under this subject because the matcher's title-text
    check put it there, never because the fetch itself was already scoped to this one company —
    only a feed whose entire premise is "everything here needs the matcher to sort it" earns the
    extra scrutiny. `tickers` is forwarded to `title_mentions_company` for exact-case matching —
    pass a term's own ticker/symbol there rather than folding it into `terms` (see that
    function's own docstring for why). Same escape-hatch asymmetry as `title_mentions_company`:
    a false negative here (an on-topic item whose title happens not to repeat the ticker) just
    means it's judged by the source-scoping signal alone, same as before this check existed —
    never a false positive that hides real coverage of a DIFFERENT, correctly-scoped source."""
    if row_via not in _SHARED_FIREHOSE_MATCHED_VIA:
        return False
    return not title_mentions_company(title, terms, tickers=tickers)
