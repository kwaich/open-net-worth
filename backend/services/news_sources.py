"""services/news_sources.py — INT9 Phase A: the SourceStore's seed-ensure + read-side policy
resolution (.claude/rfcs/news-source-management.md); Phase B adds the REST/AI write surface
(create/patch/delete a source) + the latency-joined read (``list_sources``).

``ensure_builtin_sources()`` seeds one NewsSourceStore row per CURRENT hardcoded builtin
registry entry (the regulatory table, the outlet table, the GDELT topic engine, and the two
resolver-backed per-entity lanes) — idempotent, id-stable, NEVER overwrites an existing row
(a user's disabled builtin survives every re-ensure) and picks up a newly-added code-table
entry on a later boot (a fresh id simply wasn't in the store yet). Boot-ordered in main.py
like the envelope/card-payment-envelope migrations.

``active_sources(cadence)`` is the ONE policy read the hot tier now consults: enabled rows for
a cadence, gated by the SAME master ``news_freshness`` on/off switch the scheduler already
uses to register/remove the poll jobs — a defensive second gate so a direct call (e.g. a test,
or a future manual "run now") still respects it even outside the scheduler's own job lifecycle.

Services-layer (not db/) by design: this module imports the CODE tables that describe the
builtins (services.feeds_outlets, services.feeds_regulatory, services.feeds_hot) — db/ must
never import services/, so the seed data has to be assembled here and handed to the store as
plain rows, keeping NewsSourceStore itself a pure data-access leaf. This module must NOT import
``backend.monitoring`` (monitoring already imports THIS module for ``active_sources`` — a
reverse import would cycle); the hot-tier failure-streak join lives at the API layer instead
(see ``api/news_sources.py``'s docstring).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.core.ids import new_id
from backend.db.news_source_store import NewsSourceStore
from backend.services.feeds_outlets import all_outlet_feeds
from backend.services.feeds_regulatory import all_regulatory_feeds
from backend.services.watchlist import read_watchlist

logger = logging.getLogger(__name__)

_HOT_MIN_INTERVAL_SEC = 60  # matches rss_poller_hot._HOT_CACHE_TTL
# GDELT's rate limiter throttled at the old 90s cadence in live operation (2026-07-16: 3-failure
# backoffs recurring) — tuned to 300s (RFC §5 Phase B). NOTE: ``ensure_builtin_sources()`` never
# overwrites an existing row, so an install seeded BEFORE this change keeps its 90s row (plus the
# existing failure-backoff) until the user PATCHes it via the new endpoint/tool — a documented,
# deliberate non-migration (RFC §5), not an oversight.
_GDELT_MIN_INTERVAL_SEC = 300

# Per-cadence default politeness for a user-added custom source (POST /api/intel/sources) —
# mirrors the builtin resolver-policy rows' own defaults (fast=300 matches Yahoo-ticker, slow=
# 1800 matches Google-News-search) rather than inventing a fourth number.
_CADENCE_DEFAULT_MIN_INTERVAL = {"hot": _HOT_MIN_INTERVAL_SEC, "fast": 300, "slow": 1800}

_REGULATORY_LABELS = {
    "rss:fn_adhoc": "EQS ad-hoc disclosures (DE/CH/AT)",
    "atom:edgar": "SEC EDGAR — current 8-K",
}
_REGULATORY_IDS = {"rss:fn_adhoc": "src_fn_adhoc", "atom:edgar": "src_edgar_8k"}
_REGULATORY_MARKETS: dict[str, list[str]] = {
    "rss:fn_adhoc": ["DE", "F", "VI", "SW"],
    "atom:edgar": [],  # the default/catch-all market feed — not suffix-scoped
}

_OUTLET_LABELS = {
    "rss:ft_companies": "FT Companies",
    "rss:ft_technology": "FT Technology",
    "rss:cnbc": "CNBC Top News",
    "rss:marketwatch": "MarketWatch Top Stories",
    "rss:wsj": "WSJ Markets",
    "rss:techcrunch": "TechCrunch",
}
_OUTLET_IDS = {
    "rss:ft_companies": "src_ft_companies",
    "rss:ft_technology": "src_ft_technology",
    "rss:cnbc": "src_cnbc",
    "rss:marketwatch": "src_marketwatch",
    "rss:wsj": "src_wsj",
    "rss:techcrunch": "src_techcrunch",
}


def _regulatory_defs() -> list[dict]:
    """One row per DISTINCT regulatory registry entry (services/feeds_regulatory.py) — the
    code table stays the single source for the URL/parser/User-Agent; this row is policy only."""
    return [
        {
            "id": _REGULATORY_IDS[feed.via],
            "label": _REGULATORY_LABELS[feed.via],
            "kind": "regulatory",
            "transport": "poll",
            "url": feed.url_fn(),
            "resolver": None,
            "scope": "firehose",
            "match": "server_tokens",
            "cadence": "hot",
            "via": feed.via,
            "min_interval_sec": _HOT_MIN_INTERVAL_SEC,
            "markets": _REGULATORY_MARKETS.get(feed.via, []),
            "provenance": "builtin",
            "notes": "",
        }
        for feed in all_regulatory_feeds()
    ]


def _outlet_defs() -> list[dict]:
    """One row per curated outlet feed (services/feeds_outlets.py, INT8)."""
    return [
        {
            "id": _OUTLET_IDS[feed.via],
            "label": _OUTLET_LABELS[feed.via],
            "kind": "outlet",
            "transport": "poll",
            "url": feed.url_fn(),
            "resolver": None,
            "scope": "firehose",
            "match": "server_tokens",
            "cadence": "hot",
            "via": feed.via,
            "min_interval_sec": _HOT_MIN_INTERVAL_SEC,
            "markets": [],
            "provenance": "builtin",
            "notes": "",
        }
        for feed in all_outlet_feeds()
    ]


def _topic_engine_defs() -> list[dict]:
    """The GDELT combined-topic-query engine (services/feeds_hot.py) — a computed firehose,
    so it carries a ``resolver`` (services.feeds_registry.RESOLVERS) instead of a concrete url."""
    return [
        {
            "id": "src_gdelt_topics",
            "label": "GDELT combined topic query",
            "kind": "topic_engine",
            "transport": "poll",
            "url": None,
            "resolver": "gdelt_topic_engine",
            "scope": "firehose",
            "match": "server_tokens",
            "cadence": "hot",
            "via": "api:gdelt",
            "min_interval_sec": _GDELT_MIN_INTERVAL_SEC,
            "markets": [],
            "provenance": "builtin",
            "notes": (
                "One combined query per hot tick across every monitor_news topic — GDELT's "
                "rate limiter bans an IP after ~2 calls, so this stays a single shared source."
            ),
        }
    ]


def _resolver_policy_defs() -> list[dict]:
    """Templated per-entity lanes (Yahoo per-ticker, Google News search) — policy rows only in
    Phase A: the fast/slow tiers keep resolving these directly in code (RFC §5); these rows
    exist so the source universe is complete and future config (Phase B) has something to
    toggle."""
    return [
        {
            "id": "src_yahoo_ticker",
            "label": "Yahoo Finance per-ticker headlines",
            "kind": "ticker",
            "transport": "poll",
            "url": None,
            "resolver": "yahoo_ticker",
            "scope": "per_entity",
            "match": "per_entity_subjects",
            "cadence": "fast",
            "via": "rss:yahoo",
            "min_interval_sec": 300,
            "markets": [],
            "provenance": "builtin",
            "notes": "Templated per-ticker URL; fast tier still resolves in code in Phase A.",
        },
        {
            "id": "src_google_news_search",
            "label": "Google News search",
            "kind": "search",
            "transport": "poll",
            "url": None,
            "resolver": "google_news_search",
            "scope": "per_entity",
            "match": "per_entity_subjects",
            "cadence": "slow",
            "via": "rss:google_news",
            "min_interval_sec": 1800,
            "markets": [],
            "provenance": "builtin",
            "notes": "Templated per-query URL; slow tier still resolves in code in Phase A.",
        },
    ]


def _all_builtin_defs() -> list[dict]:
    return _regulatory_defs() + _outlet_defs() + _topic_engine_defs() + _resolver_policy_defs()


def ensure_builtin_sources() -> dict:
    """Idempotent boot-ensure: insert any builtin row not already present (by id). NEVER
    touches an existing row — a user's disabled builtin stays disabled across upgrades, and a
    code table's NEW entry (a market/outlet added later) simply appears as a fresh id on the
    next boot. Safe to call every boot; a no-op once every builtin id exists.

    Uses ``store.bulk_insert`` directly rather than ``NewsSourceStore.create_source`` — this
    module's own ``_*_defs()`` builders are code-controlled (never user input), so skipping
    ``validate_source``'s per-row check here is a deliberate, low-risk trade-off, not an
    oversight; a REST/AI write in a later phase goes through ``create_source`` and IS validated.

    NOTE (removed-builtin orphans): if a code-table entry is ever REMOVED (a market/outlet
    dropped from ``feeds_regulatory``/``feeds_outlets``/etc.), this function does not delete the
    now-orphaned store row — it simply stops being re-offered, staying inert (no `via` in any
    code table can ever match it, so it never fetches). Cleanup is a Phase B/C UI concern (the
    row would surface there as a stale, un-runnable entry); noted as an open question in the RFC."""
    store = NewsSourceStore()
    existing_ids = {r.get("id") for r in store.all()}
    to_seed = [d for d in _all_builtin_defs() if d["id"] not in existing_ids]
    if not to_seed:
        return {"seeded": 0}
    now = datetime.now(timezone.utc).isoformat()
    rows = [{**d, "enabled": True, "added_at": now} for d in to_seed]
    store.bulk_insert(rows)
    return {"seeded": len(rows), "ids": [r["id"] for r in rows]}


def active_sources(cadence: str, *, store: NewsSourceStore | None = None) -> list[dict]:
    """Enabled rows for `cadence`, gated by the master news_freshness switch (news_freshness ==
    "off" -> []). This is the ONE thing a poll tier consults to decide "should this source run
    this tick" — everything else (parser, User-Agent, failure backoff) stays in the code table
    the row's `via` joins back to."""
    config = read_watchlist().get("config") or {}
    if config.get("news_freshness", "fast") == "off":
        return []
    rows = (store or NewsSourceStore()).all()
    return [r for r in rows if r.get("cadence") == cadence and r.get("enabled", True)]


def active_custom_firehose_sources(*, store: NewsSourceStore | None = None) -> list[dict]:
    """Every ENABLED `kind="custom"`/`scope="firehose"` row, regardless of `cadence` — the hot
    tier's dedicated custom-firehose lane (`monitoring.rss_poller_hot._run_custom_sources`).

    Deliberately NOT filtered by `cadence == "hot"` like `active_sources()` (review fix,
    2026-07-16, INT9 Phase B): a freshly POSTed source defaults to `cadence="slow"` (safety-first
    for an unvetted URL), and a cadence-filtered read would leave it genuinely DORMANT until the
    user manually PATCHes it — the exact "dormant feed" gap a security/code review caught. Instead
    this lane fetches every enabled custom row unconditionally and relies on `_due_for_interval`
    (keyed off the row's own `min_interval_sec`) for politeness — a "slow" row's 1800s default
    still means it starts working within ONE hot tick, just infrequently, rather than never.
    Builtin registries (regulatory/outlet/topic_engine) are UNCHANGED — they still go through
    `active_sources("hot")`, since their `cadence` field is a real, deliberate tier assignment,
    not a default a user might not have opted into.

    Gated by the SAME master `news_freshness` switch as `active_sources` (news_freshness == "off"
    -> [])."""
    config = read_watchlist().get("config") or {}
    if config.get("news_freshness", "fast") == "off":
        return []
    rows = (store or NewsSourceStore()).all()
    return [
        r
        for r in rows
        if r.get("kind") == "custom" and r.get("scope") == "firehose" and r.get("enabled", True)
    ]


# ---------------------------------------------------------------------------
# Phase B: the REST/AI config surface (RFC §3) — thin pass-throughs over the store, which stays
# the real write choke (validation, SSRF, caps). Kept here (not called directly from api/ai/tools)
# so both callers share ONE layer-correct path per the dependency rule.
# ---------------------------------------------------------------------------


def list_sources(*, days: int = 7, store: NewsSourceStore | None = None) -> list[dict]:
    """Every source row joined with its per-`via` publish→arrival latency
    (``services.news_latency.lag_stats``, 7d default) AND `last_ok_at` — the UNBOUNDED per-`via`
    latest evidence timestamp (``services.news_latency.last_ok_at``), which distinguishes "dead
    beyond the 7d window" from "never produced a hit" — the pure-services half of GET
    /api/intel/sources' read (RFC §3, UI contract item 1+4). The OTHER half — the hot-tier
    poller's in-process failure-streak state — is merged in at the API layer, not here (see this
    module's docstring for why)."""
    from backend.services.news_latency import lag_stats
    from backend.services.news_latency import last_ok_at as _last_ok_at

    rows = (store or NewsSourceStore()).all()
    stats = lag_stats(days=days)
    last_ok = _last_ok_at()
    return [
        {**row, "latency": stats.get(row.get("via")), "last_ok_at": last_ok.get(row.get("via"))}
        for row in rows
    ]


def create_user_source(*, label: str, url: str, cadence: str = "slow") -> dict:
    """Add a user-supplied custom firehose feed (POST /api/intel/sources, RFC §3 + the Phase C
    UI contract) — kind is always "custom"/scope "firehose"/match "server_tokens" (server-side
    name/ticker matching, exactly like an outlet feed), with a freshly-minted, distinct `via`
    (``rss:custom_<8hex>``) so it never collides with a builtin, another user row, OR the
    shared per-item `custom_feed_urls` pipe's literal ``rss:custom`` — reusing that would make
    every panel-added feed indistinguishable in the latency/health join, defeating the panel's
    purpose.

    Default cadence is **"slow"** (deliberately the LEAST aggressive tier for an unvetted
    URL — UI contract item 3, overriding this module's earlier "default hot"). Regardless of
    `cadence`, the hot tier's dedicated custom-firehose lane
    (`active_custom_firehose_sources`/`_run_custom_sources`) fetches every ENABLED custom row —
    a "slow" row's 1800s `min_interval_sec` default just makes it politely infrequent, not
    dormant (review fix, 2026-07-16: an earlier cadence-gated design left a freshly POSTed
    source unfetched until a manual PATCH — the `cadence` field is display/future-tier metadata
    here, not a hot-tier gate, for this one kind)."""
    row = {
        "id": new_id("src"),
        "label": label,
        "kind": "custom",
        "transport": "poll",
        "url": url,
        "resolver": None,
        "scope": "firehose",
        "match": "server_tokens",
        "cadence": cadence,
        "via": f"rss:custom_{uuid.uuid4().hex[:8]}",
        "enabled": True,
        "min_interval_sec": _CADENCE_DEFAULT_MIN_INTERVAL.get(cadence, _HOT_MIN_INTERVAL_SEC),
        "markets": [],
        "provenance": "user",
        "notes": "",
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    return NewsSourceStore().create_source(row)


def update_source_policy(source_id: str, changes: dict) -> dict:
    """PATCH /api/intel/sources/{id} — enabled / cadence / min_interval_sec, builtin or user.

    A ``cadence`` patch with NO explicit ``min_interval_sec`` also refreshes the interval to that
    cadence's default (``_CADENCE_DEFAULT_MIN_INTERVAL``, the same table ``create_user_source``
    seeds from) — the store's own ``update_policy`` only merges the keys it's handed, so without
    this a cadence flip (e.g. slow→hot) would silently leave the OLD interval behind, and the
    Phase C UI's "no more than every X" hint beside the row would go stale (review fix — the
    interval must track the tier the user actually picked). An explicit ``min_interval_sec`` in
    the SAME patch always wins (never overwritten)."""
    if not changes:
        raise ValidationError("news source: no fields to update")
    if "cadence" in changes and "min_interval_sec" not in changes:
        default_interval = _CADENCE_DEFAULT_MIN_INTERVAL.get(
            changes["cadence"], _HOT_MIN_INTERVAL_SEC
        )
        changes = {**changes, "min_interval_sec": default_interval}
    return NewsSourceStore().update_policy(source_id, changes)


def delete_user_source(source_id: str) -> dict:
    """DELETE /api/intel/sources/{id} — user rows only (the store refuses a builtin)."""
    return NewsSourceStore().delete_source(source_id)
