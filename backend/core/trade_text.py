"""The one-line text a trade's cash row carries as its description — "Sold 60 KSTL @ 150.00 USD".

Written by the trade choke (``services.lots``) so the register says what the row settled, in
place of the old "Sale proceeds" / "Share purchase" placeholder.
"""

from __future__ import annotations

from decimal import Decimal


def _qty(value: float) -> str:
    return format(Decimal(repr(abs(float(value)))).normalize(), "f")


def _price(value: float) -> str:
    text = f"{float(value):,.8f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def trade_text(
    verb: str,
    symbol: str,
    quantity: float,
    price: float | None,
    currency: str,
    fees: float | None = None,
) -> str:
    parts = [verb, _qty(quantity), symbol]
    ccy = (currency or "").upper()
    if price:
        parts.append(f"@ {_price(price)} {ccy}".rstrip())
    text = " ".join(parts)
    if fees and float(fees) > 0:
        text += f" · fees {_price(fees)} {ccy}".rstrip()
    return text
