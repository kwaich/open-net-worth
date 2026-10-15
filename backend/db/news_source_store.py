"""NewsSourceStore — the news-source registry (table ``news_sources``).

INT9 Phase A (.claude/rfcs/news-source-management.md §1): a Source is {what to fetch, how
often, how it maps to watched entities, its policy} — one flat row per source, builtin or
user-added. Row shape:

    {id, label, kind, transport, url, resolver, scope, match, cadence, via,
     enabled, min_interval_sec, markets, provenance, notes, added_at}

  * ``id`` — a stable builtin id (``src_ft_companies``) or a minted ``src_<12hex>``
    (``core.ids.new_id("src")``) for a user-added row.
  * ``kind`` — regulatory | outlet | ticker | search | topic_engine | custom.
  * ``transport`` — "poll" (v1 implements poll only; "push" is a reserved future value —
    the RFC's non-goal — so the schema accommodates it without a later migration).
  * ``url`` XOR ``resolver`` — exactly one is set. A concrete, static firehose (the
    regulatory/outlet registries) carries a real ``url``; a templated per-entity source
    (Yahoo-per-ticker, Google-News-per-query) or a computed firehose (the GDELT combined
    query) carries a ``resolver`` name instead — the row is POLICY ONLY, the actual URL
    construction stays typed code (``services.feeds_registry.RESOLVERS``).
  * ``scope`` — firehose (one fetch fans out to every matching watched entity) | per_entity.
  * ``match`` — server_tokens (server-side name/ticker matching, firehose sources) |
    per_entity_subjects (the caller already knows the subject — templated sources).
  * ``cadence`` — hot | fast | slow — which poll tier consults this row.
  * ``via`` — the JOIN key back to the code registry that actually knows how to fetch a
    concrete (url-carrying) source's bytes (parser + optional User-Agent) — e.g.
    ``rss:ft_companies`` matches ``feeds_outlets.OutletFeed.via``. This is also the failure-
    backoff / latency-observability key those code paths already use — unchanged by this store.
  * ``markets`` — Yahoo-ticker exchange suffixes this source is scoped to (regulatory rows);
    ``[]`` for a source that isn't market-keyed (or is the catch-all default).
  * ``provenance`` — "builtin" (seeded, id-stable, seed never overwrites an existing row) |
    "user" (added via REST/AI in a later phase).

Leaf under db/ — imports only core/ (via the payload-table base). Never imports services/ or
api/ — the CODE tables this store's builtin rows describe (services/feeds_outlets.py,
services/feeds_regulatory.py, …) are seeded INTO this store by a services-layer ensure
function (services/news_sources.py), keeping this module pure data access per the dependency
rule (db must not import services).

PLAN 4D TASK 2: DB-backed (Tier-3, table ``news_sources``) via ``PayloadTableStore``. The SSRF
guard ``validate_source(check_reachable=True)`` on user-supplied URLs is PRESERVED verbatim —
the call site in ``create_source`` is unchanged (flagged for the security review gate).
``create_source``'s duplicate-id + ``_MAX_USER_SOURCES`` cap check now runs inside ONE base
transaction (closing a theoretical two-concurrent-creates race the JsonStore-era
``_write_lock`` already serialized, now serialized by this table's lock instead).
"""

from __future__ import annotations

from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import news_sources

_VALID_KINDS = {"regulatory", "outlet", "ticker", "search", "topic_engine", "custom"}
_VALID_TRANSPORTS = {"poll", "push"}  # push reserved — v1 fetches "poll" only
_VALID_SCOPES = {"firehose", "per_entity"}
_VALID_MATCHES = {"server_tokens", "per_entity_subjects"}
_VALID_CADENCES = {"hot", "fast", "slow"}
_VALID_PROVENANCE = {"builtin", "user"}

# A politeness floor — no source may be polled more often than this, builtin or user-added
# (RFC §4 safety envelope: "min_interval floor 60s").
_MIN_INTERVAL_FLOOR_SEC = 60

# INT9 Phase B (RFC §4 safety envelope): "≤20 user sources".
_MAX_USER_SOURCES = 20

# Length caps (security review F1): an unbounded label/url is a cheap DoS/storage-bloat vector
# on a user-writable row — 200 chars comfortably fits any real feed title; 2048 matches the
# conventional practical URL length ceiling (most browsers/servers cap around here too).
_MAX_LABEL_LEN = 200
_MAX_URL_LEN = 2048

# The ONLY fields Phase B's PATCH endpoint (REST + AI tool) may ever change on an existing row,
# builtin or user-added alike — RFC §3 ("enabled, cadence — validated ... rate floors enforced").
_PATCHABLE_POLICY_FIELDS = frozenset({"enabled", "cadence", "min_interval_sec"})


def validate_source(row: dict, *, check_reachable: bool = False) -> dict:
    """Structural validation for a source row — shared by store writes and the REST/AI write
    choke. By default does NO network I/O (no SSRF probe): builtin rows are seeded from trusted
    first-party code tables at boot, and a policy-only PATCH (enabled/cadence/min_interval_sec)
    must never re-probe a URL that isn't changing.

    ``check_reachable=True`` is the Phase B addition — opted into ONLY at the moment a user
    actually supplies a fresh ``url`` (``NewsSourceStore.create_source``, mirroring
    ``WatchlistStore``'s ``custom_feed_urls`` write-choke convention): it runs
    ``core.net_guard.assert_public_url`` and normalizes ``row['url']`` to the guard's cleaned
    result in place, raising ``ValidationError`` (never a bare ``UnsafeUrlError``) on an unsafe
    target."""
    # Every message below is FIELD-PREFIXED ("field: detail") — mirroring WatchlistStore's
    # custom_feed_urls convention — since the REST/AI write choke (Phase B) surfaces `str(exc)`
    # verbatim as the 422 body and the UI routes it straight to the matching form field.
    source_id = (row.get("id") or "").strip()
    if not source_id.startswith("src_"):
        raise ValidationError(f"id: must start with 'src_' (got {source_id!r})")
    label = (row.get("label") or "").strip()
    if not label:
        raise ValidationError("label: is required")
    if len(label) > _MAX_LABEL_LEN:
        raise ValidationError(
            f"label: must be at most {_MAX_LABEL_LEN} characters (got {len(label)})"
        )
    if row.get("kind") not in _VALID_KINDS:
        raise ValidationError(f"kind: must be one of {sorted(_VALID_KINDS)}")
    if row.get("transport") not in _VALID_TRANSPORTS:
        raise ValidationError(f"transport: must be one of {sorted(_VALID_TRANSPORTS)}")
    if row.get("scope") not in _VALID_SCOPES:
        raise ValidationError(f"scope: must be one of {sorted(_VALID_SCOPES)}")
    if row.get("match") not in _VALID_MATCHES:
        raise ValidationError(f"match: must be one of {sorted(_VALID_MATCHES)}")
    if row.get("cadence") not in _VALID_CADENCES:
        raise ValidationError(f"cadence: must be one of {sorted(_VALID_CADENCES)}")
    if row.get("provenance") not in _VALID_PROVENANCE:
        raise ValidationError(f"provenance: must be one of {sorted(_VALID_PROVENANCE)}")
    if not (row.get("via") or "").strip():
        raise ValidationError("via: is required")
    url = (row.get("url") or "").strip()
    resolver = (row.get("resolver") or "").strip()
    if bool(url) == bool(resolver):
        raise ValidationError(
            "url: exactly one of 'url' or 'resolver' must be set (got "
            f"url={row.get('url')!r}, resolver={row.get('resolver')!r})"
        )
    if len(url) > _MAX_URL_LEN:
        raise ValidationError(f"url: must be at most {_MAX_URL_LEN} characters (got {len(url)})")
    min_interval = row.get("min_interval_sec")
    if not isinstance(min_interval, int) or min_interval < _MIN_INTERVAL_FLOOR_SEC:
        raise ValidationError(
            f"min_interval_sec: must be an int >= {_MIN_INTERVAL_FLOOR_SEC} (got {min_interval!r})"
        )
    markets = row.get("markets")
    if not isinstance(markets, list) or not all(isinstance(m, str) for m in markets):
        raise ValidationError("markets: must be a list of strings")
    if check_reachable and row.get("provenance") == "user" and url:
        try:
            row["url"] = assert_public_url(url)
        except UnsafeUrlError as exc:
            raise ValidationError(f"url: {url!r} is not a safe http(s) URL ({exc})") from exc
    return row


class NewsSourceStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(news_sources, id_field="id", engine=engine)

    def get(self, source_id: str) -> dict | None:
        return self.find_by_id(source_id)

    def where(self, *, kind: str | None = None, cadence: str | None = None) -> list[dict]:
        rows = self.all()
        if kind is not None:
            rows = [r for r in rows if r.get("kind") == kind]
        if cadence is not None:
            rows = [r for r in rows if r.get("cadence") == cadence]
        return rows

    def create_source(self, row: dict) -> dict:
        """Insert a new, fully-formed source row. Refuses a duplicate id (builtin re-seed
        goes through ``bulk_insert`` directly and pre-filters instead — see
        ``services.news_sources.ensure_builtin_sources``). A ``provenance="user"`` row's
        ``url`` is SSRF-validated here (``check_reachable=True``) — the ONE moment a fresh
        user-supplied URL is actually accepted (RFC §4). The duplicate check + user-source
        cap now run inside ONE base transaction."""
        validate_source(row, check_reachable=True)
        with self._use_conn(None) as conn:
            if self._row(conn, row["id"]) is not None:
                raise ConflictError(f"News source {row['id']!r} already exists")
            if row.get("provenance") == "user":
                user_count = sum(
                    1
                    for r in conn.execute(select(self._table.c.payload))
                    if r.payload.get("provenance") == "user"
                )
                if user_count >= _MAX_USER_SOURCES:
                    raise ValidationError(
                        f"sources: at most {_MAX_USER_SOURCES} user-added sources allowed "
                        f"(already have {user_count})"
                    )
            self._fire_hook(conn, [(row["id"], None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, row["id"], row)))
        return row

    def set_enabled(self, source_id: str, enabled: bool) -> dict:
        row = self.update_by_id(source_id, {"enabled": bool(enabled)})
        if row is None:
            raise NotFoundError(f"News source {source_id!r} not found")
        return row

    def update_policy(self, source_id: str, changes: dict) -> dict:
        """Patch ``enabled`` / ``cadence`` / ``min_interval_sec`` — the ONLY fields Phase B's
        PATCH endpoint (REST + AI tool) ever changes on an existing row, builtin or user-added
        alike (RFC §3). Re-validates the merged row STRUCTURALLY (``check_reachable`` stays
        False — a policy patch never touches ``url``, so it must never re-probe one) so a
        caller can't leave a row in a structurally invalid state (e.g. an out-of-range
        min_interval_sec)."""
        unknown = set(changes) - _PATCHABLE_POLICY_FIELDS
        if unknown:
            raise ValidationError(
                f"news source: cannot patch field(s) {sorted(unknown)} — only "
                f"{sorted(_PATCHABLE_POLICY_FIELDS)} may be changed"
            )
        with self._use_conn(None) as conn:
            existing = self._row(conn, source_id)
            if existing is None:
                raise NotFoundError(f"News source {source_id!r} not found")
            merged = {**existing["payload"], **changes}
            validate_source(merged)
            self._fire_hook(conn, [(source_id, existing)])
            conn.execute(
                sa_update(self._table).where(self._table.c.id == source_id).values(payload=merged)
            )
        return merged

    def delete_source(self, source_id: str) -> dict:
        """Delete a USER-added row. Refuses a builtin (disable it instead — RFC §2: "builtin
        rows can only be disabled")."""
        row = self.get(source_id)
        if row is None:
            raise NotFoundError(f"News source {source_id!r} not found")
        if row.get("provenance") == "builtin":
            raise ConflictError(
                f"News source {source_id!r} is a builtin — disable it instead of deleting it"
            )
        self.delete(source_id)
        return row
