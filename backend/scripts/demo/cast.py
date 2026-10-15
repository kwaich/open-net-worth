"""The demo book's cast: who Alex is, which accounts they hold, and where they shop.

Everything here is FABRICATED. Merchant names are invented rather than borrowed from
real chains — a public demo should never look like it is showing somebody's real
statement, and an invented roster is also the honest signal that the data is staged.
"""

from __future__ import annotations

from datetime import date

from backend.scripts.demo._timeline import Timeline

# ── The owner ──────────────────────────────────────────────────────────────────

DEMO_NOTE = (
    "Demo book — every account, transaction, holding and grant below is fabricated. "
    "Nothing here belongs to a real person."
)


def profile(tl: Timeline) -> dict:
    """The stored profile payload. ``is_demo`` is the sentinel the app's demo banner reads."""
    born = date(tl.today.year - 35, 6, 12)
    return {
        "is_demo": True,
        "name": "Alex Morgan",
        "nickname": "Alex",
        "nationalities": ["US"],
        "countries_of_residence": ["US"],
        "tax_residencies": ["US"],
        "employment_types": ["salaried"],
        "marital_status": "married",
        "base_currency": "USD",
        "risk_tolerance": "moderate",
        "risk_concentration_guideline": 20.0,
        "risk_stress_drop_pct": 30.0,
        "retirement_target_age": 52,
        "date_of_birth": born.isoformat(),
        "monthly_net_income": 9400.0,
        "monthly_expenses": 6200.0,
        "cashflow_low_balance_threshold": 2500.0,
        "liabilities": [],
        "notes": DEMO_NOTE,
        "fire_expected_return_pct": 6.5,
        "fire_withdrawal_rate_pct": 3.8,
        "fire_monthly_retirement_spending": 6166.666666666667,
        "fire_volatility_pct": 15.0,
        "fire_life_expectancy": 92,
        "target_allocation": {"equity": 0.68, "bond": 0.17, "crypto": 0.08, "commodity": 0.07},
        "chat_suggestions_enabled": True,
        "chat_cost_visible": True,
        "briefing_preferences": {
            "holdings_news": True,
            "fx": True,
            "spending": True,
            "accounts": True,
            "upcoming_actions": True,
            "custom_focus": "Am I still on track to retire at 52?",
        },
    }


# ── FX (the demo is offline; these are the rates the book is denominated with) ──
# {currency: how many USD one unit buys}. Plausible, static, and stamped onto every
# foreign-currency row at write time by the historical-rate lock.
FX_RATES = {"USD": 1.0, "EUR": 1.09, "SGD": 0.745, "GBP": 1.27}


# ── Accounts ───────────────────────────────────────────────────────────────────
# ``key`` is the handle the generators use; the real opaque id is minted on create.
# ``opening`` is the signed native-currency balance on the timeline's first day.

ACCOUNTS: list[dict] = [
    {
        "key": "checking",
        "name": "Everyday Checking",
        "type": "cash",
        "institution": "Northbridge Bank",
        "currency": "USD",
        "last_four": "4021",
        "color": "#4f8cff",
        "opening": 16000.0,
        "note": "Main account — salary lands here, everything else is swept out of it.",
    },
    {
        "key": "joint",
        "name": "Joint Household",
        "type": "cash",
        "institution": "Northbridge Bank",
        "currency": "USD",
        "last_four": "7788",
        "color": "#38bdf8",
        "opening": 3200.0,
        "note": "Shared with Sam — utilities and the household run out of here.",
    },
    {
        "key": "emergency",
        "name": "Emergency Fund",
        "type": "cash",
        "institution": "Marlow Savings",
        "currency": "USD",
        "last_four": "9153",
        "color": "#22c55e",
        "apy": 4.35,
        "opening": 21000.0,
        "note": "Six months of runway. Do not touch.",
    },
    {
        "key": "travelfund",
        "name": "Travel Fund",
        "type": "cash",
        "institution": "Marlow Savings",
        "currency": "USD",
        "last_four": "9160",
        "color": "#14b8a6",
        "apy": 4.1,
        "opening": 1800.0,
    },
    {
        "key": "wallet",
        "name": "Cash Wallet",
        "type": "cash",
        "institution": "",
        "currency": "USD",
        "color": "#a3a3a3",
        "opening": 320.0,
    },
    {
        "key": "eur",
        "name": "Euro Account",
        "type": "cash",
        "institution": "Wavelet",
        "currency": "EUR",
        "last_four": "3310",
        "color": "#f59e0b",
        "opening": 1250.0,
        "note": "Freelance invoices from the Berlin client are paid into this one.",
    },
    {
        "key": "sgd",
        "name": "SGD Savings",
        "type": "cash",
        "institution": "Harbour Bank",
        "currency": "SGD",
        "last_four": "2044",
        "color": "#e879f9",
        "apy": 2.9,
        "opening": 6800.0,
        "note": "Left over from two years working in Singapore.",
    },
    {
        "key": "brokerage",
        "name": "Taxable Brokerage",
        "type": "investment",
        "institution": "Meridian Wealth",
        "currency": "USD",
        "color": "#c084fc",
        "opening": 2500.0,
    },
    {
        "key": "roth",
        "name": "Roth IRA",
        "type": "retirement",
        "institution": "Meridian Wealth",
        "currency": "USD",
        "color": "#8b5cf6",
        "opening": 640.0,
    },
    {
        "key": "401k",
        "name": "401(k)",
        "type": "retirement",
        "institution": "Summit Retirement",
        "currency": "USD",
        "color": "#6366f1",
        "opening": 0.0,
        "note": "Employer plan — contributions come straight out of gross pay.",
    },
    {
        "key": "crypto",
        "name": "Crypto Exchange",
        "type": "investment",
        "institution": "Lattice Markets",
        "currency": "USD",
        "color": "#f97316",
        "opening": 150.0,
    },
    {
        "key": "card",
        "name": "Sapphire Card",
        "type": "credit",
        "institution": "Northbridge Bank",
        "currency": "USD",
        "last_four": "6612",
        "color": "#ef4444",
        "credit_limit": 20000.0,
        "statement_close_day": 18,
        "payment_due_day": 12,
        "reminder_days_before": 4,
        "opening": -1240.0,
        "note": "Everything day-to-day goes on this, paid in full every month.",
    },
    {
        "key": "mortgage",
        "name": "Mortgage",
        "type": "loan",
        "institution": "Northbridge Bank",
        "currency": "USD",
        "color": "#dc2626",
        "apr": 5.25,
        "principal": 425000.0,
        "min_payment": 2650.0,
        "opening": -408000.0,
    },
    {
        "key": "studentloan",
        "name": "Student Loan",
        "type": "loan",
        "institution": "Fed Direct",
        "currency": "USD",
        "color": "#b91c1c",
        "apr": 4.1,
        "principal": 41000.0,
        "min_payment": 385.0,
        "opening": -18400.0,
    },
]


# ── Categories ────────────────────────────────────────────────────────────────
# Builtin ids come from models/category_defs.py (stable, hardcoded). The demo adds the
# handful a real household needs that the builtin seed deliberately leaves out.

BUILTIN = {
    "food": "cat_13095ba70000",
    "transport": "cat_db5642ab0000",
    "lifestyle": "cat_504bed6e0000",
    "shopping": "cat_6c4bc0170000",
    "health": "cat_6ea706940000",
    "entertainment": "cat_dfcf9f910000",
    "travel": "cat_c19c4c0f0000",
    "subscriptions": "cat_f2ff09940000",
    "family": "cat_5b96fe7b0000",
    "hobby": "cat_9bacf56e0000",
    "other": "cat_a6b4e3bc0000",
    "tax": "cat_3945ca720000",
    "unplanned": "cat_83f7b8c70000",
}

# (handle, label, kind, color)
CUSTOM_CATEGORIES: list[tuple[str, str, str, str]] = [
    ("home", "Home & Utilities", "expense", "#0891b2"),
    ("pets", "Pet Care", "expense", "#f472b6"),
    ("giving", "Giving", "expense", "#facc15"),
    ("salary", "Salary", "income", "#16a34a"),
    ("freelance", "Freelance", "income", "#65a30d"),
    ("investment_income", "Investment Income", "income", "#0d9488"),
    ("equity_comp", "Equity Compensation", "income", "#7c3aed"),
]


# ── Merchants ─────────────────────────────────────────────────────────────────
# The payee registry mints a payee per distinct description, so a small, repeated
# roster is what makes the Payees screen and the spending analytics look alive.

GROCERS = ["Greenleaf Market", "Corner Grocer", "Harvest Co-op", "Basket & Bay"]
COFFEE = ["Ridgeline Coffee", "Two Birds Espresso", "Meridian Roasters"]
RESTAURANTS = [
    "Blue Kettle Kitchen",
    "Sora Ramen",
    "Nine Yards BBQ",
    "Casa Verde",
    "The Fern & Fig",
    "Pastaio",
]
TAKEOUT = ["Swiftplate Delivery", "Noodle Cart", "Slice Society"]
RIDESHARE = ["Hopp Rides", "CityLine Transit"]
FUEL = ["Vertex Fuel", "Crossroads Gas"]
PHARMACY = ["Alder Pharmacy", "Wellspring Health"]
SHOPS = [
    "Northline Goods",
    "Everyday Depot",
    "Loom & Thread",
    "Pixel Parts",
    "Homestead Hardware",
]
ENTERTAINMENT = ["Odeon Nine Cinema", "Parkview Arena", "Vault Records", "Bouldering Co."]
PETS = ["Paws & Claws Vet", "Kibble Corner"]
HOBBY = ["Aperture Supply", "Makers Yard", "Trailhead Outfitters"]

SUBSCRIPTIONS = [
    ("Nimbus Streaming", 17.99, 6),
    ("Auralis Music", 11.99, 9),
    ("Cloudshelf Storage", 2.99, 3),
    ("The Daily Ledger", 15.00, 21),
    ("Forge IDE", 12.00, 14),
    ("Vertex Wireless", 62.00, 24),
]

UTILITIES = [
    ("Lumen Energy", 112.0, 34.0, 8),
    ("Cascade Water", 46.0, 9.0, 11),
    ("Fiberline Internet", 79.99, 0.0, 16),
]

# The employer, the landlord's successor (the bank), and the two income counterparties.
EMPLOYER = "Cloudflare"
FREELANCE_CLIENT = "Blaubeere Studio GmbH"
