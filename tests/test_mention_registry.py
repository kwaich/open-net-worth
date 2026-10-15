from backend.services.mentions import KNOWN_TYPES, REGISTRY, MentionProvider, SearchQuery


def test_registry_covers_all_registered_types():
    expected = {
        "net_worth",
        "holding",
        "instrument",
        "account",
        "transaction",
        "category",
        "budget_month",
        "target",
        "allocation_slice",
        "returns",
        # P4 entity types
        "payee",
        "watchlist",
        "automation",
        # P3 multi-surface routing type
        # P2 verb surface (/ skill invocation + talk-about entity)
        "skill",
        # Phase 2a — generic-provider types
        "commitment",
        "notification",
        "alert",
        # Phase 2b — generic-provider investment money facts
        "income",
        "lot",
        "grant",
        "vest",
        # generic-provider type over the connectors service
        "connector",
        # generic-provider TYPE CATALOG over services/reports
        "report",
        # envelope — the Plan row identity (envelope⊥category split)
        "envelope",
        # generic-provider investment money fact — realized disposals
        "disposal",
        # 2026-08-23 — appended by the commitment-sub-entity work (5efe84ef)
        "installment",  # commitment sub-entity — one scheduled instalment of a commitment
        "funding_source",  # commitment sub-entity — where a commitment is paid from
        "commitment_action",  # commitment sub-entity — an action taken on a commitment
        "profile",  # the book's own profile row
        "position",  # a held position (distinct from `holding`, which is the aggregate view)
        "option_contract",  # an option contract (OptionContractStore)
        "asset_brief",  # a per-asset written brief (AssetBriefStore)
        "news_source",  # a configured news source (NewsSourceStore)
        # 2026-09-06 — ADR 2026-09-06-assets-tab-faithful-port, decision 11
        "asset",  # an owned asset (OwnedAssetsStore) — property/vehicle/watch/art/metal/...
        # 2026-09-26 — ADR 2026-09-26-vault-is-a-view-over-existing-stores
        "document",  # one item in Documents (file / saved artifact / statement / receipt)
    }
    assert set(REGISTRY) == expected
    assert KNOWN_TYPES == frozenset(REGISTRY)


def test_the_skill_provider_is_multi_surface():
    """Multi-surface providers are the interesting case in this registry — one type serving
    two @-surfaces. This pinned `agent` (talk-about + talk-to) until Agents were retired;
    `skill` is the other one, and the only one left: talk-about (entity) + invoke (verb)."""
    p = REGISTRY["skill"]
    assert p.surfaces == frozenset({"entity", "verb"})
    assert p.icon
    assert callable(p.search)
    assert callable(p.resolve)


def test_every_provider_is_well_formed():
    for type_, p in REGISTRY.items():
        assert isinstance(p, MentionProvider)
        assert p.type == type_
        assert "entity" in p.surfaces  # all S1 types are entities
        assert isinstance(p.surfaces, frozenset)
        assert p.icon  # non-empty glyph
        assert callable(p.resolve)
        # P1: every provider has a search function wired in.
        assert callable(p.search)


def test_search_query_is_constructible():
    q = SearchQuery(q="apple", types=None, surfaces=frozenset({"entity"}), limit=10, cursor=None)
    assert q.q == "apple"
    assert q.limit == 10


def test_resolve_through_provider_matches_known_type():
    # net_worth is a singleton resolver that always returns a dict
    data = REGISTRY["net_worth"].resolve("")
    assert isinstance(data, dict)
    assert "net_worth" in data


def test_matches_is_punctuation_and_whitespace_insensitive():
    """A query that differs from the label only in punctuation/spacing still hits.

    Regression: searching "vantage brokerage(eur" or "vantagebrokerageeur" must surface
    an account named "Vantage Brokerage (EUR)" — raw substring missed it on the space/paren.
    """
    from backend.services.mentions.providers import _matches

    label = "Vantage Brokerage (EUR)"
    # Raw substring (with the space) already worked — keep it working.
    assert _matches("vantage brokerage (eur", label)
    # The reported failures — no space, and no punctuation at all — now match.
    assert _matches("vantage brokerage(eur", label)
    assert _matches("vantagebrokerageeur", label)
    assert _matches("VANTAGE BROKERAGE", label)
    # A genuine miss still returns False (no false-positive from normalization).
    assert not _matches("meridian", label)


def test_matches_empty_needle_does_not_false_match():
    from backend.services.mentions.providers import _matches

    # A punctuation-only needle normalizes to "" and must not match everything.
    assert not _matches("()", "Vantage Brokerage (EUR)")
