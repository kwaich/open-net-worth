"""Live-price tool."""

from __future__ import annotations

import json
from typing import List, Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of


class GetLivePricesInput(ToolInput):
    """Schema-of-record for ``get_live_prices`` (see ``schema_from_model``)."""

    tickers: List[str] = Field(min_length=1, description="e.g. ['MSFT', 'BTC-USD']")
    currencies: Optional[List[str]] = Field(default=None, description="e.g. ['USD', 'EUR', 'VND']")


@tool(
    "get_live_prices",
    description=(
        "Fetch current market prices for a list of tickers (Yahoo Finance / CoinGecko). Use "
        'the `resolved` field for "what is the price right now" — it already prefers a live '
        "pre/post-market print over the regular-session `prices` value when one is active, "
        "matching what the app's own UI shows. `prices`/`extended` are the raw split behind "
        "it, for a caller that specifically wants the regular-session value or the extended "
        "print separately."
    ),
    input_schema=schema_of(GetLivePricesInput, tool_name="get_live_prices"),
    # A genuine read since 2026-08-23: it passes ``record_history=False``, so asking what a
    # price is no longer appends to price_history. The series is the portfolio's record of
    # itself, accumulated by the paths that VALUE holdings (portfolio rows, asset detail, the
    # prices API, the scheduled price check) — a chat question is not an event in it.
)
async def get_live_prices(input_data: dict):
    import backend.ai.tools as _root

    tickers = input_data.get("tickers", [])
    currencies = input_data.get("currencies", [])
    result = _root.fetch_live(tickers, currencies, include_names=True, record_history=False)
    return json.dumps(result), None


# ── Direct single-provider tools ────────────────────────────────────────────────
#
# get_live_prices above is the general, cross-provider path (Yahoo-first with an automatic
# CoinGecko fallback for crypto) — the right default for ordinary portfolio-style pricing.
# These two are for when the user asks for a price FROM ONE NAMED PROVIDER specifically: no
# fallback substitution, so a provider that can't price the symbol reports that honestly
# instead of quietly answering from the other one. Genuinely READ-ONLY (unlike
# get_live_prices, neither caches nor records into price_history — see the module note in
# services/prices.py above `fetch_yahoo_quotes`/`fetch_coingecko_quotes` for why).


class YahooGetTickerPricesInput(ToolInput):
    """Schema-of-record for ``yahoo_get_ticker_prices``."""

    tickers: List[str] = Field(
        min_length=1,
        description=(
            "Yahoo Finance symbols — equities/ETFs (e.g. 'MSFT'), indices (e.g. '^GSPC'), "
            "currency pairs (e.g. 'EURUSD=X'), or a Yahoo-priceable crypto pair (e.g. "
            "'BTC-USD')."
        ),
    )


@tool(
    "yahoo_get_ticker_prices",
    description=(
        "Query Yahoo Finance DIRECTLY for one or more tickers — no CoinGecko fallback. "
        "Yahoo covers far more than equities: indices, currency pairs, ETFs and "
        "Yahoo-priceable crypto pairs are all valid tickers through the same interface. "
        "Use this when the user asks for a price specifically FROM Yahoo Finance; for "
        "general portfolio-style pricing (which falls back to CoinGecko for crypto Yahoo "
        "can't price) use get_live_prices instead. A ticker Yahoo can't price comes back "
        "state='unavailable', never silently substituted from another provider."
    ),
    input_schema=schema_of(YahooGetTickerPricesInput, tool_name="yahoo_get_ticker_prices"),
    connector="yahoo_finance",
)
async def yahoo_get_ticker_prices(input_data: dict):
    import backend.ai.tools as _root

    tickers = input_data.get("tickers", [])
    result = _root.fetch_yahoo_quotes(tickers)
    return json.dumps(result), None


class CoingeckoGetCryptoPricesInput(ToolInput):
    """Schema-of-record for ``coingecko_get_crypto_prices``."""

    coins: List[str] = Field(
        min_length=1,
        description=(
            "Crypto assets to price — a ticker (e.g. 'BTC-USD'), a CoinGecko coin id "
            "(e.g. 'bitcoin'), or a name/symbol to resolve via CoinGecko's own search "
            "(e.g. 'solana')."
        ),
    )
    vs_currency: Optional[str] = Field(
        default=None, description="Quote currency, e.g. 'usd' or 'eur'. Defaults to USD."
    )
    include_facts: Optional[bool] = Field(
        default=None,
        description=(
            "When true, also return market cap, rank, circulating/max supply and ATH for "
            "each resolved coin."
        ),
    )


@tool(
    "coingecko_get_crypto_prices",
    description=(
        "Query CoinGecko DIRECTLY for crypto asset prices — no Yahoo-first routing, even "
        "for a major pair the general pricing path would normally check Yahoo for first. "
        "Each input resolves via a small known-ticker map, then CoinGecko's own /search "
        "for a name or symbol not already known — the resolved CoinGecko id for every "
        "input rides back in `resolved_ids`. Set include_facts=true for market cap, rank, "
        "supply and ATH per coin. Use this when the user asks for a price specifically "
        "FROM CoinGecko; for general portfolio-style pricing (Yahoo-first, CoinGecko "
        "fallback) use get_live_prices instead."
    ),
    input_schema=schema_of(CoingeckoGetCryptoPricesInput, tool_name="coingecko_get_crypto_prices"),
    connector="coingecko",
)
async def coingecko_get_crypto_prices(input_data: dict):
    import backend.ai.tools as _root

    coins = input_data.get("coins", [])
    vs_currency = input_data.get("vs_currency") or "usd"
    include_facts = bool(input_data.get("include_facts"))
    result = _root.fetch_coingecko_quotes(coins, vs_currency, include_facts=include_facts)
    return json.dumps(result), None


# ── Direct single-provider price HISTORY tools ──────────────────────────────────
#
# The two direct quote tools above answer "what does THIS PROVIDER say the price is right
# NOW" — these two answer "what did THIS PROVIDER say the price was OVER TIME". Same
# no-fallback-substitution contract, same reasoning for staying off the general path: a
# chart is asset-detail's own concern (services.asset_detail / fetch_history_result), and a
# provider that can't chart a symbol reports that honestly rather than answering from the
# other one. Genuinely READ-ONLY: fetch_history_result caches in an in-memory TTL dict only
# (services/prices.py's `_history_cache`), never the durable `price_history` table.

_TIMEFRAME = Literal["1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"]


class YahooGetPriceHistoryInput(ToolInput):
    """Schema-of-record for ``yahoo_get_price_history``."""

    ticker: str = Field(
        # Same shape prices._assert_yahoo_symbol enforces server-side (the actual guard —
        # dispatch only type-checks input_schema, see registry.py's _IGNORED_KEYWORDS; this
        # pattern is a model-facing hint, not itself the enforcement point).
        pattern=r"^[A-Za-z0-9.\-^=]{1,20}$",
        description=(
            "Yahoo Finance symbol — equity/ETF (e.g. 'MSFT'), index (e.g. '^GSPC'), "
            "currency pair (e.g. 'EURUSD=X'), or a Yahoo-priceable crypto pair (e.g. "
            "'BTC-USD')."
        ),
    )
    timeframe: _TIMEFRAME = Field(description="Chart window to fetch.")


@tool(
    "yahoo_get_price_history",
    description=(
        "Fetch historical price points FROM YAHOO FINANCE DIRECTLY for one ticker — no "
        "CoinGecko fallback. Same symbol coverage as yahoo_get_ticker_prices (equities, "
        "indices, FX pairs, Yahoo-priceable crypto pairs). A ticker Yahoo can't chart comes "
        "back state='unavailable', never silently substituted from another provider. For "
        "the 1D timeframe specifically, a venue with no bars yet (e.g. a non-US exchange "
        "viewed before its regular session opens) can come back state='stale' with "
        "degraded_reason='previous_session' and a 'session_date' field — those bars are "
        "the LAST COMPLETE session, not today's; read session_date before treating them as "
        "current. Use this for a price CHART over time; for the current spot price use "
        "yahoo_get_ticker_prices or get_live_prices instead."
    ),
    input_schema=schema_of(YahooGetPriceHistoryInput, tool_name="yahoo_get_price_history"),
    connector="yahoo_finance",
)
async def yahoo_get_price_history(input_data: dict):
    import backend.ai.tools as _root

    ticker = input_data.get("ticker", "")
    timeframe = input_data.get("timeframe", "")
    result = _root.fetch_history_result(ticker, timeframe, "yahoo")
    return json.dumps(result), None


class CoingeckoGetPriceHistoryInput(ToolInput):
    """Schema-of-record for ``coingecko_get_price_history``."""

    coin: str = Field(
        description=(
            "Crypto asset to chart — a ticker (e.g. 'BTC-USD'), a CoinGecko coin id "
            "(e.g. 'bitcoin'), or a name/symbol to resolve via CoinGecko's own search "
            "(e.g. 'solana')."
        )
    )
    timeframe: _TIMEFRAME = Field(description="Chart window to fetch.")
    vs_currency: Optional[str] = Field(
        default=None, description="Quote currency, e.g. 'usd' or 'eur'. Defaults to USD."
    )


@tool(
    "coingecko_get_price_history",
    description=(
        "Fetch historical price points FROM COINGECKO DIRECTLY for one crypto asset — no "
        "Yahoo routing, even for a major pair the general pricing path would normally check "
        "Yahoo for first. Resolves the input the same way coingecko_get_crypto_prices does "
        "(a small known-ticker map, then CoinGecko's own /search for a name or symbol not "
        "already known) — the resolved CoinGecko id rides back in `resolved_id`. An "
        "identifier that can't be resolved reports state='unavailable' rather than guessing "
        "a different coin. Use this for a price CHART over time; for the current spot price "
        "use coingecko_get_crypto_prices or get_live_prices instead."
    ),
    input_schema=schema_of(CoingeckoGetPriceHistoryInput, tool_name="coingecko_get_price_history"),
    connector="coingecko",
)
async def coingecko_get_price_history(input_data: dict):
    import backend.ai.tools as _root

    coin = input_data.get("coin", "")
    timeframe = input_data.get("timeframe", "")
    vs_currency = input_data.get("vs_currency") or "usd"
    coin_id, _display = _root._resolve_coingecko_coin(coin)
    if coin_id is None:
        result = {
            "requested": coin,
            "resolved_id": None,
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "no_data",
            "points": [],
        }
        return json.dumps(result), None
    history = _root.fetch_history_result(coin_id, timeframe, "coingecko", vs_currency)
    result = {"requested": coin, "resolved_id": coin_id, **history}
    return json.dumps(result), None
