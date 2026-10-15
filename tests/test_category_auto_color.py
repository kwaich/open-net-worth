"""A category created without a colour gets one from the app's palette (owner, 2026-09-25):
tools and bare API calls used to leave it blank, which renders as a grey chip."""

from __future__ import annotations

from backend.services import categories as cat


def test_a_category_created_without_a_color_gets_a_palette_color(db_isolated):
    row = cat.create_category("Haircut")
    assert row["color"] in cat.CATEGORY_PALETTE


def test_a_supplied_color_is_kept(db_isolated):
    assert cat.create_category("Transport", color="#123456")["color"] == "#123456"


def test_a_run_of_new_categories_spreads_across_the_palette(db_isolated):
    colors = [cat.create_category(f"New {i}")["color"] for i in range(len(cat.CATEGORY_PALETTE))]
    # least-used first: nine new categories take the nine palette colours, one each
    assert sorted(colors) == sorted(cat.CATEGORY_PALETTE)
