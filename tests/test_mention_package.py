from backend.services.mentions import (
    KNOWN_TYPES,
    MAX_MENTIONS,
    format_mention_context,
    instrument_id_for_brief,
    resolve_mentions,
    session_brief_block,
)


def test_public_api_importable():
    assert callable(resolve_mentions)
    assert callable(format_mention_context)
    assert callable(instrument_id_for_brief)
    assert callable(session_brief_block)
    assert MAX_MENTIONS == 12
    assert "transaction" in KNOWN_TYPES


def test_unknown_type_resolves_to_error_not_raise():
    out = resolve_mentions([{"type": "nope", "ref": "x", "label": "L"}])
    assert out == [{"type": "nope", "ref": "x", "label": "L", "error": "unknown_type"}]


def test_missing_entity_resolves_to_not_found():
    out = resolve_mentions([{"type": "transaction", "ref": "txn_does_not_exist", "label": "T"}])
    assert out[0]["error"] == "not_found"


def test_dedup_and_cap():
    refs = [{"type": "net_worth", "ref": "", "label": "NW"}] * 3
    out = resolve_mentions(refs)
    assert len(out) == 1  # deduped by (type, ref)
    over = [{"type": "transaction", "ref": f"txn_{i}", "label": "x"} for i in range(20)]
    assert len(resolve_mentions(over)) == MAX_MENTIONS


def test_format_block_renders_label_lines():
    import re

    block = format_mention_context(
        [{"type": "account", "ref": "acc_1", "label": "Checking", "error": "not_found"}]
    )
    # P0-C baseline: the block now opens with a nonce fence marker.
    assert re.search(r"«mention:[0-9a-f]{32}»", block), "nonce fence open marker missing"
    assert "Referenced items the user is asking about" in block
    assert "@Checking: (not found)" in block


def test_resolve_mentions_reads_the_registry(monkeypatch):
    """Adding a provider to REGISTRY must make a new type resolvable — proving dispatch
    routes through the registry rather than a hardcoded table."""
    import backend.services.mentions.registry as reg

    fake = reg.MentionProvider(
        type="goal",
        surfaces=frozenset({"entity"}),
        icon="◎",
        resolve=lambda ref: {"name": "Retirement", "ref_echo": ref} if ref else None,
    )
    monkeypatch.setitem(reg.REGISTRY, "goal", fake)

    out = resolve_mentions([{"type": "goal", "ref": "goal_1", "label": "Retirement"}])
    assert out[0]["data"] == {"name": "Retirement", "ref_echo": "goal_1"}

    missing = resolve_mentions([{"type": "goal", "ref": "", "label": "x"}])
    assert missing[0]["error"] == "not_found"
