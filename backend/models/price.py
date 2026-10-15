"""The resolved-price read chokepoint's return shape.

Before this existed, "what is this ticker's price right now" was answered three
separate times — ``positions.hydrated()``, ``asset_detail._price_block()``, and
``market_ticker._build()`` each independently picked between a regular-session
quote and a pre/post-market one from the same raw fetch maps, with no guarantee
the three picks agreed. ``prices.resolve_price_quote()`` is now the single place
that decision gets made; this is its return type, carrying every value a
consumer could need instead of a bare float.

``models/`` is a leaf beside ``core/``: stdlib + pydantic + ``backend.core`` only.
"""

from __future__ import annotations

from backend.models.base import ONBaseModel


class PriceQuote(ONBaseModel):
    """One resolved live quote for a ticker (price_source_id).

    ``price`` is the resolved "use this" value — an extended-hours (pre/post)
    print when one is live for this ticker, else the regular-session price.
    ``regular_price`` is ALWAYS the regular-session value regardless of which
    one won, so a caller that must never use an extended price (the daily
    price-history write in ``prices.py``, which anchors net-worth-over-time and
    every future day's ``prev_close``) has an explicit, unambiguous field to
    read instead of ``price``.

    ``prev_close`` and therefore ``change_abs``/``change_pct`` are ALWAYS
    anchored to the regular prior close, never an extended session's own
    baseline — matches Yahoo's own ``postMarketChangePercent`` convention, so
    "today's change" keeps meaning today's change whether or not the market is
    currently open.
    """

    price: float
    source: str  # "extended" | "live"
    is_extended: bool
    extended_session: str | None = None  # "pre" | "post" | None
    regular_price: float | None = None
    prev_close: float | None = None
    change_abs: float | None = None
    change_pct: float | None = None
    as_of: str | None = None
