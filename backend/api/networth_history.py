"""
GET /api/networth/history

Returns monthly net worth reconstructed from the transaction ledger PLUS market-valued
positions (equities, crypto, commodities) priced from the recorded daily series, from the
oldest transaction date to today. The current month uses today's live value.

Each point is decomposed into asset-class bands (the stacked-area data): `classes` maps
band → value (cash [bank-only] / brokerage_cash [brokerage & crypto-exchange settlement cash,
margin debit included] / receivable always; equity/crypto/commodity/property once price
collection began on/before that date — no backfill, per Pillar 2; see ADR
2026-07-31-liquid-net-worth-bands), `liabilities` is the raw-negative total, `accounts` is the
account-only figure (cash+brokerage_cash+receivable+liabilities, a consistent basis for the
headline delta), and `net_worth` = Σ class bands + liabilities. `unvested_base` is unvested
equity (vesting_state="unvested" — forfeited if the grantee leaves) — reported separately,
EXCLUDED from every class band and from `net_worth` (ADR
2026-07-31-unvested-equity-excluded-from-net-worth). `market_tracked` flags points that include
market value.

Response: { history: [{date, classes, liabilities, accounts, net_worth, unvested_base,
            market_tracked}], base_currency: str }
"""

from fastapi import APIRouter

from backend.services import ledger as ledger_service

router = APIRouter()


@router.get("/api/networth/history")
def get_networth_history():
    history = ledger_service.net_worth_history()
    return {
        "history": history,
        "base_currency": ledger_service.base_currency(),
    }
