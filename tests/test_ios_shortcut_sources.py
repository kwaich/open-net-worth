"""A wallet tap and the bank's notification of ONE purchase are compared, not vetoed.

Live 2026-10-15: both phone shortcuts sent ``source: ios-shortcut``, so a purchase reported by
the Apple Pay tap (``wallet-tap:…``) and by the bank's notification (``notif:…``) carried two
distinct refs under ONE authoritative namespace — the match engine read that as two real events
and never scored the pair, and two purchases were each booked twice.

Each trigger is now its own namespace, and a notification ref names its app as the emitter.
Two taps of their own still stay two rows — the reason the namespace is authoritative at all.

Offline + isolated: stores in tmp_path via db_isolated; no LLM, no network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.core.txn_sources import ext_ref_emitters, ref_emitter
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc
from backend.services import matching

TAP = ("ios-wallet", "wallet-tap:2026-10-15T12:26:14+08:00")
NOTIF = ("ios-notification", "notif:2026-10-15T12:26:21+08:00@Harborview Bank")


def _row(*refs: tuple[str, str]) -> dict:
    return {"external_refs": [{"source": s, "ref": r} for s, r in refs]}


def test_a_notification_ref_names_its_app_as_the_emitter():
    assert ref_emitter(*NOTIF) == "harborview bank"
    assert ref_emitter(
        "ios-notification", "notif:2026-10-15T12:26:21+08:00@Harborview Bank"
    ) == ref_emitter("ios-notification", "notif:2026-10-15T13:00:00+08:00@harborview bank"), (
        "one app is one emitter however iOS capitalises it"
    )
    assert ref_emitter("ios-notification", "notif:2026-10-15T12:26:21+08:00") is None
    assert ref_emitter("ios-notification", "notif:2026-10-15T12:26:21+08:00@") is None
    assert ref_emitter(*TAP) is None
    assert ref_emitter("email", "<a@b.example>") is None, "only the notification namespace"
    assert ext_ref_emitters(_row(NOTIF)) == {"ios-notification": {"harborview bank"}}


def test_a_tap_and_a_notification_are_compared():
    assert matching._same_source_event_veto(_row(TAP), _row(NOTIF)) is False


def test_two_taps_stay_two_events():
    other_tap = ("ios-wallet", "wallet-tap:2026-10-15T12:26:40+08:00")
    assert matching._same_source_event_veto(_row(TAP), _row(other_tap)) is True


def test_two_apps_notifying_one_purchase_are_compared():
    merchant = ("ios-notification", "notif:2026-10-15T12:26:25+08:00@Grab")
    assert matching._same_source_event_veto(_row(NOTIF), _row(merchant)) is False


def test_two_notifications_from_one_app_stay_two_events():
    again = ("ios-notification", "notif:2026-10-15T12:40:02+08:00@Harborview Bank")
    assert matching._same_source_event_veto(_row(NOTIF), _row(again)) is True


def test_a_notification_ref_without_its_app_fails_closed():
    old = ("ios-notification", "notif:2026-10-15T12:26:25+08:00")
    assert matching._same_source_event_veto(_row(NOTIF), _row(old)) is True


# ── through the real write path ───────────────────────────────────────────────


@pytest.fixture
def card(db_isolated, fx_rates, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    acct = svc.accounts.create(
        {
            "name": "Harborview Revolution Card",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return acct["id"]


def _add(account_id: str, description: str, amount: float, ref: tuple[str, str]) -> dict:
    text, _ = asyncio.run(
        execute_tool(
            "add_transactions",
            {
                "description": description,
                "amount": amount,
                "category": "Food & Dining",
                "account_id": account_id,
                "currency": "SGD",
                "date": "2026-10-15",
                "external_refs": [{"source": ref[0], "ref": ref[1]}],
            },
        )
    )
    return json.loads(text)


def _booked(*descriptions: str) -> int:
    return sum(
        1
        for t in svc.transactions.all()
        if not t.get("deleted") and t.get("description") in descriptions
    )


@pytest.mark.parametrize(
    ("tap_text", "notif_text"),
    [
        ("Ridgeline Bistro", "RIDGELINE BISTRO"),
        # Two of four words shared — below the description bar, so this one is banded REVIEW,
        # not refused outright. The tool still stops and hands the match back to the caller.
        ("7-Eleven", "7-ELEVEN-EXAMPLEPLACE PLA"),
    ],
)
def test_the_notification_of_a_tapped_purchase_is_not_booked_again(card, tap_text, notif_text):
    first = _add(card, tap_text, 12.40, TAP)
    assert first["ok"] is True, first

    second = _add(card, notif_text, 12.40, NOTIF)

    assert second["ok"] is False, second
    assert second["already_recorded"][0]["existing_id"] == first["results"][0]["id"]
    assert _booked(tap_text, notif_text) == 1


def test_two_identical_taps_are_still_two_purchases(card):
    _add(card, "iJooz", 3.00, TAP)
    second = _add(card, "iJooz", 3.00, ("ios-wallet", "wallet-tap:2026-10-15T12:26:40+08:00"))

    assert second["ok"] is True, second
    assert _booked("iJooz") == 2
