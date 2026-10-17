"""RETIRED — a no-op. It once wrote the trade into the memo of earlier trade cash legs.

Shipped 2026-10-17 morning, when the trade choke wrote "Sold 60 KSTL @ 150.00 USD" into the memo;
the owner moved that text to the DESCRIPTION the same day ("I don't buy 'Share purchase' or 'Sale
proceeds' — we need to give the details there"), and 202610171500 now writes it there. Left
running, this revision would plant a memo on every older leg, and 202610171500 can only take it
back where the description was still the placeholder — a leg whose description someone had
rewritten would keep a memo nobody asked for. So it does nothing. It ran on exactly one book,
whose generated memos 202610171500 then moved and cleared. The revision id stays: that book's
chain passes through it.

Revision ID: 202610171200_trade_cash_legs_carry_their_trade_memo
Revises: 202610151200_trade_cash_legs_store_their_trade
Create Date: 2026-10-17
"""

from __future__ import annotations

revision = "202610171200_trade_cash_legs_carry_their_trade_memo"
down_revision = "202610151200_trade_cash_legs_store_their_trade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
