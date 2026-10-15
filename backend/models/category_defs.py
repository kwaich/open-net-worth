"""First-run category template and historical category fixtures.

This is a leaf (no imports). ``STARTER_CATEGORIES`` and ``STARTER_GROUPS`` are the
template for a new book; ``BUILTIN_CATEGORIES`` keeps stable historical IDs used by
the demo and tests. Books with stored categories own those rows and are not rewritten
to match either list.

Categories are pure data (seeded once, then user-owned). The only category id the ledger code
depends on by name is the structural anchor ``split`` (the split sentinel) — everything
else here is ordinary seed data. Category is now OPTIONAL on expense/income (None when
unclassified); the former ``uncategorized`` catch-all sink has been removed.

Each entry: ``{id, label, kind, color}``. ``id`` is a STABLE hardcoded id
(deterministic, NOT ``core.ids.new_id`` — a fresh install must reproduce the same seed ids
every time, and tests reference them directly); every user-created category gets an
opaque ``cat_<12hex>`` minted by ``services.categories.create_category`` instead. ``kind``
carries the classification (``expense`` → budgetable spending; ``income`` → source;
``transfer``/``adjustment`` → plumbing).
"""

from __future__ import annotations

# The first-run budget template. These are ordinary, user-owned expense categories;
# the screenshot's example assignments and target are not seed data.
STARTER_CATEGORIES: list[dict] = [
    {"id": "cat_a31b2c3d4001", "label": "Rent/Mortgage", "kind": "expense", "color": "#3b86dd"},
    {
        "id": "cat_a31b2c3d4002",
        "label": "Phone & Internet",
        "kind": "expense",
        "color": "#3b86dd",
    },
    {"id": "cat_a31b2c3d4003", "label": "Utilities", "kind": "expense", "color": "#3b86dd"},
    {"id": "cat_a31b2c3d4004", "label": "Groceries", "kind": "expense", "color": "#34b08a"},
    {
        "id": "cat_a31b2c3d4005",
        "label": "Medical expenses",
        "kind": "expense",
        "color": "#34b08a",
    },
    {"id": "cat_a31b2c3d4006", "label": "Emergency fund", "kind": "expense", "color": "#34b08a"},
    {"id": "cat_f2ff09940000", "label": "Subscriptions", "kind": "expense", "color": "#9a7fd0"},
    {"id": "cat_a31b2c3d4008", "label": "Dining out", "kind": "expense", "color": "#9a7fd0"},
    {"id": "cat_a31b2c3d4009", "label": "Entertainment", "kind": "expense", "color": "#9a7fd0"},
    {"id": "cat_a31b2c3d400a", "label": "Vacation", "kind": "expense", "color": "#9a7fd0"},
    {
        "id": "cat_a31b2c3d400b",
        "label": "Stuff I forgot to plan for",
        "kind": "expense",
        "color": "#9a7fd0",
    },
    {"id": "split", "label": "Split", "kind": "expense", "color": "#7c8595"},
]

# The first-run envelope groups, each with the starter categories whose 1:1 envelope is minted
# into it (``envelopes.ensure_envelopes_migrated``). A group holds envelopes, never categories —
# these tuples only place the starter envelopes; nothing stores them as group membership.
STARTER_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Bills", tuple(c["id"] for c in STARTER_CATEGORIES[:3])),
    ("Needs", tuple(c["id"] for c in STARTER_CATEGORIES[3:6])),
    ("Wants", tuple(c["id"] for c in STARTER_CATEGORIES[6:11])),
]

# Historical defaults remain available to older demo and characterization fixtures.
# Live books already own their stored rows; the first-run seed uses STARTER_CATEGORIES.
BUILTIN_CATEGORIES: list[dict] = [
    {"id": "cat_13095ba70000", "label": "Food & Dining", "kind": "expense", "color": "#e0a032"},
    {"id": "cat_db5642ab0000", "label": "Transport", "kind": "expense", "color": "#3b86dd"},
    {"id": "cat_504bed6e0000", "label": "Lifestyle", "kind": "expense", "color": "#34b08a"},
    {"id": "cat_6c4bc0170000", "label": "Shopping", "kind": "expense", "color": "#9a7fd0"},
    {"id": "cat_6ea706940000", "label": "Health & Fitness", "kind": "expense", "color": "#e2554f"},
    {"id": "cat_dfcf9f910000", "label": "Entertainment", "kind": "expense", "color": "#22b8cf"},
    {"id": "cat_c19c4c0f0000", "label": "Travel", "kind": "expense", "color": "#1d9e75"},
    {"id": "cat_f2ff09940000", "label": "Subscriptions", "kind": "expense", "color": "#6b7484"},
    {"id": "cat_5b96fe7b0000", "label": "Family & Gifts", "kind": "expense", "color": "#d4537e"},
    {"id": "cat_9bacf56e0000", "label": "Hobby & Projects", "kind": "expense", "color": "#bd7a1a"},
    {"id": "cat_a6b4e3bc0000", "label": "Other", "kind": "expense", "color": "#7c8595"},
    {"id": "cat_3945ca720000", "label": "Tax", "kind": "expense", "color": "#c0392b"},
    # The old {"key": "adjustment", kind: "adjustment"} seed is GONE (owner catch, 2026-07-04):
    # adjustment TRANSACTIONS always carry category=None (stripped at the write choke), so a
    # kind=adjustment category can never be attached to anything — dead seed data. Existing
    # books keep their row (pure data, deletable in Manage categories); fresh books don't get it.
    # category-opaque-ids migration: "equity-compensation" / "options-premium" are NO LONGER
    # seeded as always-present system categories (decision #3) — vest/option-premium income
    # books with category left None/Uncategorized (ADR 2026-07-12 options-tax-facts-not-verdicts:
    # the write path records facts only). A category is only created if the user
    # actually adds one. An existing book that already has one of these categories keeps it (the
    # opaque-id migration re-keys it like any other) — this only changes fresh installs.
    # YNAB-parity: catch-all envelope for unplanned spending that doesn't fit elsewhere
    {
        "id": "cat_83f7b8c70000",
        "label": "Stuff I Forgot to Budget For",
        "kind": "expense",
        "color": "#7c8595",
    },
    # Structural sentinel — non-pickable; the ledger stamps category="split" to mark a
    # split transaction. Its id is the literal string "split" (never a minted cat_xxx) — the
    # ONE row whose id is not opaque, since it is a marker, not a real category.
    {"id": "split", "label": "Split", "kind": "expense", "color": "#7c8595"},
]
