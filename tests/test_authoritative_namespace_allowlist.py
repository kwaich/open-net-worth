"""``is_authoritative_source`` fails CLOSED — RFC 2026-08-01 §9.

The function decides whether two DISTINCT refs under one namespace are two distinct real events
(invariant 1). It used to be an exclusion list: anything not named was authoritative. That
default is backwards for the hazard it guards, because the two error directions are not
symmetric:

  false negative  a real feed treated as non-authoritative -> F03 does not veto -> the pair is
                  SURFACED for review. A human decides. Cost: noise.
  false positive  a content hash treated as native -> F03 VETOES -> a second row is written
                  with no human in the loop. Cost: a silent double-count in net worth.

Offline: pure functions. No store, no LLM, no network.
"""

from __future__ import annotations

import pytest

from backend.core.txn_sources import AUTHORITATIVE_ID_NAMESPACES, is_authoritative_source


@pytest.mark.parametrize(
    "ns",
    [
        "email",
        "ofx",
        "simplefin",
        "gocardless",
        "plaid",
        "enable_banking",
        "pluggy",
        "yodlee",
        "ibkr",
        "lunch_money",
        "ynab",
        "vest",
        "option-premium",
        "option-close",
        "ios-shortcut",
        "ios-wallet",
        "ios-notification",
    ],
)
def test_native_per_event_namespaces_are_authoritative(ns):
    assert is_authoritative_source(ns) is True


@pytest.mark.parametrize("ns", ["csv", "qif", "xlsx", "pdf", "mt940", "camt", "statement"])
def test_content_hash_namespaces_are_not(ns):
    """These refs are hashes WE minted — two different refs mean different bytes, not two
    different real-world events."""
    assert is_authoritative_source(ns) is False


@pytest.mark.parametrize("ns", ["stmt", "anthropic", "acme_bank", "", None])
def test_an_UNCLASSIFIED_namespace_is_not_authoritative(ns):
    """The load-bearing property. Under the old exclusion default, a namespace nobody had
    classified — a typo, a new integration, or a RETIRED name resurfacing from a checkpoint
    revert / restored backup / unmigrated merge journal — was authoritative, so F03 would veto
    a genuine duplicate and write a second row silently. Now it is merely inert."""
    assert is_authoritative_source(ns) is False


def test_the_allowlist_is_the_only_source_of_truth():
    """No caller may re-derive this from the retired exclusion set."""
    for ns in AUTHORITATIVE_ID_NAMESPACES:
        assert is_authoritative_source(ns) is True
    assert is_authoritative_source("definitely-not-on-the-list") is False
