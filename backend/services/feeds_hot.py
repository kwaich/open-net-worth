"""Hot-tier topic-engine sources — GDELT (wired) + Bing (resolver only, v1) — plus the shared
name/ticker-token matcher the hot tier uses to fan a SHARED fetch out to many watched entities
(news-ingestion-latency.md §1-2).

GDELT DOC 2.0 gives 15-30 min freshness for topic/entity queries but has a SHARP rate limit
(~1 req/5s per IP; a research probe got banned after 2 calls) — so the hot tier issues exactly
ONE combined query per tick covering every ``monitor_news`` topic (:func:`combined_gdelt_query`),
never a per-topic fan-out. Its JSON response needs its own parser (:func:`parse_gdelt`), not
``services.feeds.parse_rss`` (XML).

Bing News RSS is included as a resolver (a live secondary search lane, per the RFC's source
research) but is NOT yet wired into a poll tier in this lane — no concrete freshness win over
the existing Google/Yahoo/GDELT lanes was identified for v1; kept available for a future
slow-tier addition without another research pass.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone


def gdelt_doc_url(query: str) -> str:
    return (
        "https://api.gdeltproject.org/api/v2/doc/doc?query="
        + urllib.parse.quote(query)
        + "&mode=artlist&maxrecords=50&format=json&timespan=1h&sort=datedesc"
    )


def bing_news_url(query: str) -> str:
    return "https://www.bing.com/news/search?q=" + urllib.parse.quote(query) + "&format=RSS"


def _parse_gdelt_seendate(raw: str) -> str:
    """GDELT's compact 'YYYYMMDDTHHMMSSZ' → ISO-8601 UTC. '' on any parse failure."""
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return ""


def parse_gdelt(json_bytes: bytes) -> list[dict]:
    """GDELT DOC 2.0 ``artlist`` JSON → provider-shaped hits (pure; [] on any error, mirrors
    ``parse_rss``'s never-raise contract). No description/snippet field in artlist mode."""
    if not json_bytes:
        return []
    try:
        data = json.loads(json_bytes)
    except Exception:
        return []
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        return []
    out = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        url = (a.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        out.append(
            {
                "url": url,
                "title": (a.get("title") or "").strip(),
                "content": "",
                "published_date": _parse_gdelt_seendate(a.get("seendate") or ""),
                "source": (a.get("domain") or "").strip(),
                "source_url": "",
                "guid": url,
                "author": "",
                "image": (a.get("socialimage") or "").strip() or None,
                "via": "api:gdelt",
            }
        )
    return out


# A term shorter than this (chars) is prone to matching plain-English words as a raw substring
# (code-review SF-2: "ALL" ticker term matched "Wall Street closes higher" via substring inside
# "wALL"). Below the floor: WHOLE-TOKEN match only (regex `\b…\b` on the lowercased title) — a
# 3-5 char term (many real names/phrases end up this short too) must appear as its OWN word, not
# buried inside a longer one. At/above the floor: keep raw substring matching so a multi-word
# phrase like "kestrel dash" still matches inside a longer headline ("… Kestrel Dash SE …").
# Below the absolute 3-char floor a term never matches at all (too noisy either way). Applies to
# `terms` only — a `tickers` entry (the raw ticker symbol itself, see `_term_matches_title`'s
# `is_ticker` branch) is matched exact-case instead, regardless of length, so this floor's own
# "common word" tradeoff never applies to it.
_WHOLE_TOKEN_MAX_LEN = 6


def _term_matches_title(term: str, title: str, title_l: str, *, is_ticker: bool = False) -> bool:
    t = term.lower()
    if len(t) < 3:
        return False
    if is_ticker:
        # Owner report 2026-08-31, live ServiceNow, owner correction the same day ("must be
        # generic for all users", not a curated word list): a raw ticker symbol is matched
        # exact-case against the title's ORIGINAL casing, unconditionally — never case-
        # insensitively — because this is a STRUCTURAL fact about the term (it IS the literal
        # ticker), not something that needs detecting via a maintained list of which specific
        # symbols happen to collide with English words. Real tickers are near-universally
        # rendered in full uppercase in running text ("NOW Stock Surges", "$NOW"); ordinary
        # prose using the same spelling essentially never is. Protects every ticker, for every
        # user, automatically — including one added tomorrow that nobody has reviewed yet.
        return re.search(rf"\b{re.escape(term.strip().upper())}\b", title) is not None
    if len(t) < _WHOLE_TOKEN_MAX_LEN:
        return re.search(rf"\b{re.escape(t)}\b", title_l) is not None
    return t in title_l


def match_hits_to_subjects(hits: list[dict], entities: list[dict]) -> list[dict]:
    """Server-side name/ticker-token match (RFC: "matched to watched entities by issuer-
    name/ticker keywords") — the shape a SHARED firehose (FN ad-hoc, EDGAR 8-K, a GDELT combined
    query) needs since one fetch must fan out to N different watched entities' subjects, unlike
    a per-entity feed. `entities` is `[{subject, terms, tickers}]`; a hit matches an entity when
    ANY of its `terms` (name/bare-name forms) is a case-insensitive match against the hit's
    title — a short term (< 6 chars, `_term_matches_title`) must land on a whole-token boundary,
    a longer one (multi-word phrases included) keeps plain substring matching — OR any of its
    `tickers` (raw/bare ticker-symbol forms) is an EXACT-CASE whole-token match against the
    title's original casing (`_term_matches_title`'s `is_ticker` branch — always, regardless of
    length or vocabulary, never a case-insensitive check). Cheap, deterministic, no LLM. Hits
    matching no entity are dropped; a hit matching several rides ALL of them (`_subjects` list,
    popped by the caller)."""
    out = []
    for h in hits:
        title = h.get("title") or ""
        title_l = title.lower()
        if not title_l:
            continue
        subs = [
            e["subject"]
            for e in entities
            if any(_term_matches_title(t, title, title_l) for t in (e.get("terms") or []))
            or any(
                _term_matches_title(t, title, title_l, is_ticker=True)
                for t in (e.get("tickers") or [])
            )
        ]
        if subs:
            out.append({**h, "_subjects": subs})
    return out


def combined_gdelt_query(entities: list[dict], max_terms: int = 12) -> str:
    """ONE OR-joined GDELT query across up to `max_terms` watched entities' primary term —
    the rate-limit-safety requirement (news-ingestion-latency.md: "ONE combined query per hot
    tick", verified banned-after-2-calls from one IP). Each entity is `{subject, terms, tickers}`
    (services.feeds_hot match-entity shape); only its FIRST term is used to keep the query
    bounded, preferring a name-shaped `terms` entry and falling back to a `tickers` entry only
    when the entity has no name on file — an outbound GDELT query has no title-casing signal to
    exploit either way, so this query-construction step doesn't need `tickers`' exact-case
    distinction the title-matching side does. '' when there is nothing to query (caller skips
    the fetch entirely)."""
    terms = []
    for e in entities[:max_terms]:
        primary = next(
            (t for t in (e.get("terms") or []) if t),
            next((t for t in (e.get("tickers") or []) if t), None),
        )
        if primary:
            terms.append(f'"{primary}"')
    if not terms:
        return ""
    return " OR ".join(terms)
