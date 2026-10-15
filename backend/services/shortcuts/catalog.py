"""The installable iOS Shortcuts, as data.

Every shortcut ships as ONE generic file, signed once at release (``make ios-shortcuts-sign``)
— iOS refuses an unsigned ``.shortcut`` file, and a signed one cannot be edited afterwards, so
no per-instance value can be baked in at download time. What an instance knows (its address
and API token) reaches the phone as a **setup code** instead: Settings and the phone setup page
show it, and the shortcut's one import question asks for it. ``scripts/shortcuts/build.py``
writes the unsigned placeholders to ``docs/shortcuts/dist/``; ``services.shortcuts.signed``
serves the signed copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from .plist import (
    CurrentDate,
    Output,
    ShortcutInput,
    TriggerOutput,
    Var,
    action,
    attachment,
    comment,
    dict_field,
    entry,
    if_has_value,
    import_question,
    new_uuid,
    notification_trigger,
    reset_uuids,
    set_variable,
    text_action,
    tokens,
    wallet_trigger,
    workflow,
)
from .setup_code import SEPARATOR

# What the setup-code Text says until the owner pastes over it. iOS 27 did not show the install
# question that should fill it (owner, 2026-10-13), so every guide says: open the shortcut, tap
# this text, paste. The question stays for any iOS that does ask it.
PLACEHOLDER_SETUP_CODE = "Paste your setup code here"

SETUP_CODE_QUESTION = (
    "Paste your setup code (Open Net Worth → Settings → Phone shortcuts → Setup code → Copy)"
)


def _setup_code_config() -> List[Dict[str, Any]]:
    """Text(setup code) → split on the separator → ONW Base URL + ONW Token.

    One paste instead of two: an address and a 40-character token typed or pasted
    separately into an import sheet on a phone is the worst step of this feature.
    The Text action is index 1, the one the import question fills.
    """
    code_uuid, split_uuid = new_uuid(), new_uuid()
    first_uuid, last_uuid = new_uuid(), new_uuid()
    parts = attachment(Output(split_uuid, "Split Text"))
    return [
        text_action(PLACEHOLDER_SETUP_CODE, uuid=code_uuid),
        action(
            "is.workflow.actions.text.split",
            {
                "text": attachment(Output(code_uuid, "Text")),
                "WFTextSeparator": "Custom",
                "WFTextCustomSeparator": SEPARATOR,
            },
            uuid=split_uuid,
        ),
        action(
            "is.workflow.actions.getitemfromlist",
            {"WFInput": parts, "WFItemSpecifier": "First Item"},
            uuid=first_uuid,
        ),
        set_variable("ONW Base URL", Output(first_uuid, "Item from List")),
        action(
            "is.workflow.actions.getitemfromlist",
            {"WFInput": parts, "WFItemSpecifier": "Last Item"},
            uuid=last_uuid,
        ),
        set_variable("ONW Token", Output(last_uuid, "Item from List")),
    ]


def _question(index: int, text: str) -> Dict[str, Any]:
    return import_question(action_index=index, parameter_key="WFTextActionText", question=text)


def _spend_sentence() -> List[Dict[str, Any]]:
    """Build the sentence from the Apple Pay tap that fired the trigger → ``Spend``.

    Amount, Name (Wallet's display title), Merchant and Card or Pass are fields of the
    trigger's output (iOS 27) — read as Shortcut Input properties instead, iOS showed them as
    bare "Shortcut Input" (seen on device, 2026-10-13). Some banks fill Name, others Merchant —
    both go in and Lucius uses whichever is there.

    The time is the ISO ``Txn Stamp`` (seconds + UTC offset), not a bare Current Date: a bare
    one renders in the phone's default style ("15 Sep 2026 at 9:41 AM") — no offset, so a
    late-evening tap can land on the wrong day, and its wording follows the phone's language.
    """
    sentence_uuid = new_uuid()
    sentence = tokens(
        "Spent ",
        TriggerOutput(_WALLET, "Amount"),
        " at ",
        TriggerOutput(_WALLET, "Name", user_info="WFItemName"),
        " (",
        TriggerOutput(_WALLET, "Merchant"),
        ") with ",
        TriggerOutput(_WALLET, "Card or Pass"),
        " on ",
        Var("Txn Stamp"),
    )
    return [
        text_action(sentence, uuid=sentence_uuid),
        set_variable("Spend", Output(sentence_uuid, "Text")),
    ]


def _legacy_spend_sentence() -> List[Dict[str, Any]]:
    """iOS 26 and earlier: the Wallet AUTOMATION runs this shortcut and passes the tap in as
    Shortcut Input, so the same four fields are properties of the input — the owner's own
    working iOS 26 shortcut, field for field."""
    sentence_uuid = new_uuid()
    sentence = tokens(
        "Spent ",
        ShortcutInput("Amount"),
        " at ",
        ShortcutInput("Name"),
        " (",
        ShortcutInput("Merchant"),
        ") with ",
        ShortcutInput("Card or Pass"),
        " on ",
        Var("Txn Stamp"),
    )
    return [
        text_action(sentence, uuid=sentence_uuid),
        set_variable("Spend", Output(sentence_uuid, "Text")),
    ]


def _tap_stamp() -> List[Dict[str, Any]]:
    """The tap time to the second, with its UTC offset → ``Txn Stamp``, the idempotency ref.

    Two real taps are at least a second apart, so they never share a ref, while a resend of
    the same run does — a ref built from the purchase (amount + merchant) would drop the
    second of two identical coffees as "Already logged".
    """
    stamp_uuid = new_uuid()
    return [
        action(
            "is.workflow.actions.format.date",
            {
                # ISO on the input too: a bare Current Date can reach Format Date as default-
                # style text, minute precision, and two taps in one minute would share a ref.
                "WFDate": tokens(CurrentDate(iso=True)),
                "WFDateFormatStyle": "Custom",
                "WFDateFormat": "yyyy-MM-dd'T'HH:mm:ssZZZZZ",
            },
            uuid=stamp_uuid,
        ),
        set_variable("Txn Stamp", Output(stamp_uuid, "Formatted Date")),
    ]


# ``ref`` = "<trigger>:<when>" — the trigger that fired, then its time to the second with the UTC
# offset. ``source`` stays ``ios-shortcut`` for both (the CLIENT), so the server namespaces both
# under one emitter while the prefixes keep a tap and a notification in the same second apart.
WALLET_REF_PREFIX = "wallet-tap:"
NOTIFICATION_REF_PREFIX = "notif:"
_NOTIFICATION = "WFNotificationTrigger"
_WALLET = "WFWalletTransactionTrigger"


def _post_and_report(ref: Dict[str, Any]) -> List[Dict[str, Any]]:
    """POST the ``Spend`` sentence to /api/capture, then show Lucius's own reply."""
    request_uuid = new_uuid()
    reply_uuid = new_uuid()
    return [
        action(
            "is.workflow.actions.downloadurl",
            {
                "WFURL": tokens(Var("ONW Base URL"), "/api/capture"),
                "WFHTTPMethod": "POST",
                # A JSON body built from fields, so Shortcuts escapes the values: a merchant
                # such as Say "Cheese" Pizza can no longer end the text string early.
                "WFHTTPBodyType": "JSON",
                "WFJSONValues": dict_field(
                    [
                        entry("text", tokens(Var("Spend"))),
                        entry("source", "ios-shortcut"),
                        entry("ref", ref),
                    ]
                ),
                "WFHTTPHeaders": dict_field(
                    [
                        entry("Authorization", tokens("Bearer ", Var("ONW Token"))),
                        entry("Content-Type", "application/json"),
                    ]
                ),
            },
            uuid=request_uuid,
        ),
        # Lucius's own summary of what it booked. Showing the server's words
        # rather than the phone's guess means the notification reports what
        # actually landed — including the category the phone never knew about.
        # The response as ONE bound variable: a token string here left the editor showing a
        # greyed "Dictionary" placeholder and the step read nothing (seen on device, 2026-10-13).
        action(
            "is.workflow.actions.getvalueforkey",
            {
                "WFInput": attachment(Output(request_uuid, "Contents of URL")),
                "WFDictionaryKey": "reply",
            },
            uuid=reply_uuid,
        ),
        # No reply: say what actually happened instead of a banner with only a title. The app's
        # own errors carry a "detail" ("authentication required" for a wrong token); anything
        # else answering — a tunnel's error page while the app is down — has neither. (A phone
        # that can't connect at all never gets here: iOS shows its own error and stops.)
        *if_has_value(
            Output(reply_uuid, "Dictionary Value"),
            then=[_notify(tokens(Output(reply_uuid, "Dictionary Value")))],
            otherwise=_report_failure(request_uuid),
        ),
    ]


NO_ANSWER_MESSAGE = (
    "Not logged: no answer from Open Net Worth. It may be offline, or the address in your "
    "setup code is wrong."
)


def _report_failure(request_uuid: str) -> List[Dict[str, Any]]:
    detail_uuid = new_uuid()
    detail = Output(detail_uuid, "Dictionary Value")
    return [
        action(
            "is.workflow.actions.getvalueforkey",
            {
                "WFInput": attachment(Output(request_uuid, "Contents of URL")),
                "WFDictionaryKey": "detail",
            },
            uuid=detail_uuid,
        ),
        *if_has_value(
            detail,
            then=[_notify(tokens("Not logged: Open Net Worth said “", detail, "”."))],
            otherwise=[_notify(tokens(NO_ANSWER_MESSAGE))],
        ),
    ]


def _notify(body: Dict[str, Any]) -> Dict[str, Any]:
    return action(
        "is.workflow.actions.notification",
        {"WFNotificationActionTitle": tokens("Open Net Worth"), "WFNotificationActionBody": body},
    )


_CAPTURE_INTRO = (
    "Log Apple Pay to Open Net Worth (iOS 27+)\n\n"
    "Runs on its own on every Apple Pay tap. Builds a sentence from the tap — amount, "
    "merchant, card, time — and sends it to Lucius, who works out the account and category "
    "and books it.\n\n"
    "1. Tap the Text just below (“Paste your setup code here”) and paste your setup code from "
    "Open Net Worth → Settings → Phone shortcuts.\n"
    "2. Add its trigger — installing doesn't keep one: Search (bottom) → Automation → Wallet "
    "(“When I tap a Wallet Card or Pass”) → pick your cards."
)


def build_apple_pay_post() -> Dict[str, Any]:
    """Apple Pay tap (iOS 27 trigger) → POST /api/capture → Lucius decides and books it."""
    reset_uuids("apple-pay-post")
    trigger_uuid = new_uuid()
    actions: List[Dict[str, Any]] = [comment(_CAPTURE_INTRO)]
    question_index = len(actions)
    actions += _setup_code_config()
    actions += _tap_stamp()  # before the sentence, which reads Txn Stamp
    actions += _spend_sentence()
    actions += _post_and_report(tokens(WALLET_REF_PREFIX, Var("Txn Stamp")))
    questions = [_question(question_index, SETUP_CODE_QUESTION)]
    return workflow(
        actions,
        import_questions=questions,
        glyph=59511,
        triggers=[wallet_trigger(trigger_uuid)],
    )


_LEGACY_INTRO = (
    "Log Apple Pay to Open Net Worth (iOS 26 and earlier)\n\n"
    "Builds a sentence from the Apple Pay transaction — amount, merchant, card, time — and "
    "sends it to Lucius, who works out the account and category and books it.\n\n"
    "Run it on every tap:\n"
    "Automation → + → Wallet → When I tap: your cards → Run Immediately → "
    "Do: this shortcut.\n\n"
    "First: tap the Text just below (“Paste your setup code here”) and paste your setup code "
    "from Open Net Worth → Settings → Phone shortcuts. New address or token? Paste the new one."
)


def build_apple_pay_legacy_post() -> Dict[str, Any]:
    """iOS 26 and earlier: a Wallet automation runs this and passes the tap in."""
    reset_uuids("apple-pay-legacy-post")
    actions: List[Dict[str, Any]] = [comment(_LEGACY_INTRO)]
    question_index = len(actions)
    actions += _setup_code_config()
    actions += _tap_stamp()
    actions += _legacy_spend_sentence()
    actions += _post_and_report(tokens(WALLET_REF_PREFIX, Var("Txn Stamp")))
    questions = [_question(question_index, SETUP_CODE_QUESTION)]
    return workflow(actions, import_questions=questions, glyph=59511)


_NOTIFICATION_INTRO = (
    "Log Finance Notifications to Open Net Worth (iOS 27+)\n\n"
    "Runs on its own when one of your bank, wallet, broker or crypto apps sends a "
    "notification, and sends its text to Lucius — who books payments, refunds, money received "
    "and trades, and logs nothing for codes, promotions or statements.\n\n"
    "1. Tap the Text just below (“Paste your setup code here”) and paste your setup code from "
    "Open Net Worth → Settings → Phone shortcuts.\n"
    "2. Add its trigger — installing doesn't keep one: Search (bottom) → Automation → "
    "Notification (“When I receive a notification from”) → tap App → pick your apps.\n\n"
    "Don't pick a bank whose card already runs the Apple Pay shortcut — the same purchase "
    "would be logged twice."
)


def _notification_sentence() -> List[Dict[str, Any]]:
    """The notification as one line → ``Spend``: which app, its title, subtitle and body, and
    when it arrived (ISO, so a late run still books to the right day)."""
    sentence_uuid = new_uuid()
    sentence = tokens(
        "Notification from ",
        TriggerOutput(_NOTIFICATION, "App"),
        ": ",
        TriggerOutput(_NOTIFICATION, "Title"),
        " — ",
        TriggerOutput(_NOTIFICATION, "Subtitle"),
        " — ",
        TriggerOutput(_NOTIFICATION, "Body"),
        " (received ",
        TriggerOutput(_NOTIFICATION, "Date", iso=True),
        ")",
    )
    return [
        text_action(sentence, uuid=sentence_uuid),
        set_variable("Spend", Output(sentence_uuid, "Text")),
    ]


def build_notification_post() -> Dict[str, Any]:
    """A bank app's notification (iOS 27 trigger) → POST /api/capture → Lucius books it.

    The ref is the NOTIFICATION's own time, not the run's: it stays the same however late or
    often the shortcut runs for that alert, so a retry is answered "Already logged".
    """
    reset_uuids("notification-post")
    trigger_uuid = new_uuid()
    actions: List[Dict[str, Any]] = [comment(_NOTIFICATION_INTRO)]
    question_index = len(actions)
    actions += _setup_code_config()
    actions += _notification_sentence()
    actions += _post_and_report(
        tokens(NOTIFICATION_REF_PREFIX, TriggerOutput(_NOTIFICATION, "Date", iso=True))
    )
    questions = [_question(question_index, SETUP_CODE_QUESTION)]
    return workflow(
        actions,
        import_questions=questions,
        glyph=59511,
        triggers=[notification_trigger(trigger_uuid)],
    )


@dataclass(frozen=True)
class ShortcutSpec:
    slug: str
    name: str
    summary: str
    asks: str
    build: Callable[[], Dict[str, Any]]
    # Which iOS the file is for: "27+" (trigger stored in the shortcut) or "26-" (run by a
    # separate Wallet automation). The setup UIs offer the one matching the phone.
    ios: str = "27+"

    @property
    def filename(self) -> str:
        # By slug: the two Apple Pay versions share a display name.
        return f"{self.slug}.shortcut"


SPECS = (
    ShortcutSpec(
        slug="log-apple-pay",
        name="Log Apple Pay to Open Net Worth",
        summary=(
            "iOS 27+. Runs on its own on every Apple Pay tap. Lucius works out the account, "
            "amount and category and books it."
        ),
        asks="setup code",
        build=build_apple_pay_post,
    ),
    ShortcutSpec(
        slug="log-apple-pay-ios26",
        name="Log Apple Pay to Open Net Worth",
        summary=(
            "iOS 26 and earlier. A Wallet automation runs it on every Apple Pay tap. Lucius "
            "works out the account, amount and category and books it."
        ),
        asks="setup code",
        build=build_apple_pay_legacy_post,
        ios="26-",
    ),
    ShortcutSpec(
        slug="log-notifications",
        name="Log Finance Notifications to Open Net Worth",
        summary=(
            "iOS 27+. Runs on its own when your bank, wallet, broker or crypto apps send a "
            "notification. Lucius books payments, refunds, money received and trades."
        ),
        asks="setup code",
        build=build_notification_post,
    ),
)

BY_SLUG = {spec.slug: spec for spec in SPECS}


def build(slug: str) -> Dict[str, Any]:
    """Build one shortcut's plist."""
    spec = BY_SLUG.get(slug)
    if spec is None:
        raise KeyError(slug)
    return spec.build()
