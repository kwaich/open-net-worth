"""Backend port of the frontend `displayName` cases (assetDisplay.test.ts).

`display_name` is now the SINGLE source of truth for stripping the security-type
suffix Yahoo appends to longName; the frontend just renders what the API serves.
These cases mirror the frontend tests 1:1 so the two never drift.
"""

from backend.core.instrument_display import display_name


def test_strips_share_class_and_common_stock_tail():
    assert (
        display_name("Space Exploration Technologies Corp. Class A Common Stock")
        == "Space Exploration Technologies Corp."
    )
    assert display_name("Acme Holdings Inc. Common Stock") == "Acme Holdings Inc."
    assert display_name("Foo Bar Class B Common Stock") == "Foo Bar"


def test_strips_depositary_and_ordinary_share_tails():
    assert (
        display_name("Alibaba Group Holding Limited American Depositary Shares")
        == "Alibaba Group Holding Limited"
    )
    assert display_name("Prosus N.V. Ordinary Shares") == "Prosus N.V."


def test_never_butchers_a_real_name_with_dotted_initials_or_abbreviations():
    assert display_name("U.S. Bancorp") == "U.S. Bancorp"
    assert (
        display_name("J.B. Hunt Transport Services, Inc.") == "J.B. Hunt Transport Services, Inc."
    )
    assert display_name("A. O. Smith Corporation") == "A. O. Smith Corporation"
    assert display_name("The J. M. Smucker Company") == "The J. M. Smucker Company"
    assert display_name("Apple Inc.") == "Apple Inc."
    assert display_name("ONWAI SE") == "ONWAI SE"
    assert (
        display_name("State Street SPDR S&P 500 ETF Trust") == "State Street SPDR S&P 500 ETF Trust"
    )


def test_handles_empty_and_edge_input():
    assert display_name("") == ""
    # no leading separator before the tail token -> kept as-is (fallback to original)
    assert display_name("Common Stock") == "Common Stock"
