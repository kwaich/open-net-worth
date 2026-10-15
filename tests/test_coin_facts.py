"""Tests for `_parse_coin_facts` — the CoinGecko /coins/{id} → crypto Key-Facts shaper.

Pure parsing (no network); fetch_coin_facts wraps it with caching + the HTTP call.
"""

from backend.services.prices import _parse_coin_facts

# Trimmed real-shape payload for a native L1 coin (asset_platform_id is null).
ETH_PAYLOAD = {
    "asset_platform_id": None,
    "categories": ["Smart Contract Platform", "Layer 1 (L1)", "Ethereum Ecosystem"],
    "genesis_date": "2015-07-30",
    "market_cap_rank": 2,
    "links": {"homepage": ["https://www.ethereum.org/", "", ""]},
    "market_data": {
        "market_cap": {"usd": 451000000000, "sgd": 600000000000},
        "circulating_supply": 120200000,
        "max_supply": None,
        "ath": {"usd": 4878.26},
        "ath_date": {"usd": "2021-11-10T14:24:19.604Z"},
    },
}


def test_parses_native_coin_facts():
    f = _parse_coin_facts(ETH_PAYLOAD)
    assert f["category"] == "Smart Contract Platform"
    assert f["network"] is None  # native L1 → no host chain
    assert f["market_cap"] == 451000000000  # USD, not SGD
    assert f["market_cap_rank"] == 2
    assert f["circulating_supply"] == 120200000
    assert f["max_supply"] is None
    assert f["ath"] == 4878.26
    assert f["ath_date"] == "2021-11-10T14:24:19.604Z"
    assert f["genesis_date"] == "2015-07-30"
    assert f["homepage"] == "https://www.ethereum.org/"


def test_token_reports_its_host_chain():
    payload = {
        "asset_platform_id": "ethereum",
        "categories": ["Meme"],
        "market_cap_rank": 30,
        "market_data": {"market_cap": {"usd": 3_000_000_000}, "max_supply": 420_000_000_000_000},
    }
    f = _parse_coin_facts(payload)
    assert f["network"] == "ethereum"
    assert f["category"] == "Meme"
    assert f["max_supply"] == 420_000_000_000_000


def test_missing_fields_degrade_to_none():
    f = _parse_coin_facts({})
    assert f["category"] is None
    assert f["network"] is None
    assert f["market_cap"] is None
    assert f["ath"] is None
    assert f["homepage"] is None
