"""Structural checks on the generated iOS Shortcuts.

These files are plists we assemble by hand and cannot execute in CI, so the
tests stand in for the device: they assert the invariants that would otherwise
only surface as a shortcut that fails to import or posts a malformed body.

The payload test is the important one — it reconstructs the JSON the shortcut
would send and pins it against the real ``CaptureRequest`` contract. That
model ignores unknown keys rather than rejecting them, so a payload naming a
field that does not exist would POST 200 and silently drop the value.
"""

import plistlib

import pytest

from backend.services.shortcuts import setup_code
from backend.services.shortcuts.catalog import NO_ANSWER_MESSAGE, SPECS
from backend.services.shortcuts.plist import OBJ

SHORTCUTS = {spec.slug: spec.build for spec in SPECS}

# Sample values substituted for each variable when reconstructing the payload.
# "Amount" is bare because from_amount is a float in the API model.
SAMPLES = {
    "Spend": "Spent $12.40 at Starbucks (Starbucks) with Apple Card on 2026-07-28T09:15:04+08:00",
    "Txn Stamp": "2026-07-28T09:15:04+08:00",
    "ONW Base URL": "http://10.0.0.2:8080",
    "ONW Token": "tok_example",
}


# What an iOS 27 notification trigger hands the run, per field.
NOTIFICATION_SAMPLES = {
    "App": "DBS digibank",
    "Title": "Card transaction",
    "Subtitle": "",
    "Body": "You spent SGD 12.40 at STARBUCKS with card ending 1234",
    "Date": "2026-07-28T09:15:04+08:00",
}


# What the iOS 27 triggers hand the run, per trigger and field.
TRIGGER_SAMPLES = {
    "WFNotificationTrigger": NOTIFICATION_SAMPLES,
    "WFWalletTransactionTrigger": {
        "Amount": "<Amount>",
        "Name": "<Name>",
        "Merchant": "<Merchant>",
        "Card or Pass": "<Card or Pass>",
    },
}


@pytest.fixture(params=sorted(SHORTCUTS), ids=sorted(SHORTCUTS))
def shortcut(request):
    """Each generated shortcut, round-tripped through the binary plist encoder."""
    built = SHORTCUTS[request.param]()
    return plistlib.loads(plistlib.dumps(built, fmt=plistlib.FMT_BINARY))


def _walk(node):
    """Yield every dict nested anywhere inside ``node``."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _token_strings(plist):
    """Every serialized text token in the shortcut."""
    for node in _walk(plist):
        if node.get("WFSerializationType") == "WFTextTokenString":
            yield node["Value"]


def _utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def test_attachment_ranges_land_on_placeholders(shortcut):
    """Every attachment range must address a real U+FFFC at that UTF-16 offset.

    Shortcuts reads the sidecar ranges positionally; an off-by-one renders the
    variable as literal text or drops it, which is invisible until it runs.
    """
    for value in _token_strings(shortcut):
        string = value["string"]
        for range_key in value.get("attachmentsByRange", {}):
            location, length = (int(p) for p in range_key.strip("{}").split(","))
            assert length == 1, f"{range_key} in {string!r} spans more than one slot"
            units = string.encode("utf-16-le")
            char = units[location * 2 : (location + length) * 2].decode("utf-16-le")
            assert char == OBJ, f"{range_key} points at {char!r}, not a placeholder in {string!r}"


def test_placeholder_count_matches_attachment_count(shortcut):
    """No orphan placeholders and no orphan attachments."""
    for value in _token_strings(shortcut):
        placeholders = value["string"].count(OBJ)
        attachments = len(value.get("attachmentsByRange", {}))
        assert placeholders == attachments, (
            f"{placeholders} placeholders vs {attachments} attachments in {value['string']!r}"
        )


def test_action_output_references_resolve(shortcut):
    """A magic variable must point at a UUID some action actually declares."""
    declared = {
        a["WFWorkflowActionParameters"]["UUID"]
        for a in shortcut["WFWorkflowActions"]
        if "UUID" in a["WFWorkflowActionParameters"]
    }
    for node in _walk(shortcut):
        if node.get("Type") == "ActionOutput":
            assert node["OutputUUID"] in declared, (
                f"dangling reference to {node['OutputUUID']} ({node.get('OutputName')})"
            )


def test_variables_are_set_before_use(shortcut):
    """Every Var(...) reference names a variable a Set Variable action creates."""
    created = {
        a["WFWorkflowActionParameters"]["WFVariableName"]
        for a in shortcut["WFWorkflowActions"]
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.setvariable"
    }
    for node in _walk(shortcut):
        # An If's input wrapper is also {"Type": "Variable"} — it holds a reference, not a name.
        if node.get("Type") == "Variable" and "Variable" not in node:
            assert node["VariableName"] in created, f"unset variable {node['VariableName']}"


def test_import_questions_target_real_text_actions(shortcut):
    """An import prompt addresses an action by index; the index must be right.

    This is what makes the shortcut re-installable — if a question points at the
    wrong action, the recipient's answer silently overwrites something else.
    """
    actions = shortcut["WFWorkflowActions"]
    for question in shortcut["WFWorkflowImportQuestions"]:
        index = question["WFWorkflowImportQuestionActionIndex"]
        assert 0 <= index < len(actions), f"import question index {index} out of range"
        target = actions[index]
        assert target["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettext"
        key = question["WFWorkflowImportQuestionParameterKey"]
        assert key in target["WFWorkflowActionParameters"], (
            f"question targets {key}, absent from action {index}"
        )
        assert question["WFWorkflowImportQuestionText"].strip()


# Stand-ins for the attachment types that have no sample value of their own.
_PLACEHOLDERS = {
    "ExtensionInput": "<input>",
    "CurrentDate": "<now>",
}


def _render(value):
    """Reconstruct a token string with SAMPLES substituted for its variables."""
    string = value["string"]
    attachments = value.get("attachmentsByRange", {})
    replacements = {}
    for range_key, attachment in attachments.items():
        location = int(range_key.strip("{}").split(",")[0])
        kind = attachment["Type"]
        if kind == "Variable":
            replacements[location] = SAMPLES[attachment["VariableName"]]
        elif kind == "TriggerOutput":
            prop = attachment["Aggrandizements"][0]["PropertyName"]
            replacements[location] = TRIGGER_SAMPLES[attachment["TriggerIdentifier"]][prop]
        elif kind == "ExtensionInput" and attachment.get("Aggrandizements"):
            replacements[location] = f"<{attachment['Aggrandizements'][0]['PropertyName']}>"
        elif kind == "ActionOutput":
            replacements[location] = f"<{attachment.get('OutputName') or 'output'}>"
        else:
            replacements[location] = _PLACEHOLDERS.get(kind, "<?>")

    out = []
    offset = 0
    for char in string:
        out.append(replacements[offset] if char == OBJ else char)
        offset += _utf16_len(char)
    return "".join(out)


def _capture_request(plist):
    """The Get Contents of URL action of the capture shortcut."""
    for a in plist["WFWorkflowActions"]:
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl":
            return a["WFWorkflowActionParameters"]
    return None


def _payload(plist):
    """The JSON body as the server would receive it, with SAMPLES substituted."""
    params = _capture_request(plist)
    items = params["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
    return {_render(i["WFKey"]["Value"]): _render(i["WFValue"]["Value"]) for i in items}


def test_capture_payload_matches_the_api_contract():
    """The POSTed body must satisfy CaptureRequest.

    The shortcut sends a sentence, not a transaction — the account, amount,
    currency and category are all resolved server-side. This pins the wire
    contract so a rename on either side fails here rather than on someone's
    phone.
    """
    built = SHORTCUTS["log-apple-pay"]()
    params = _capture_request(built)
    # A JSON body built from fields: Shortcuts escapes each value, so a merchant like
    # Say "Cheese" Pizza cannot end the text string early the way a hand-built body could.
    assert params["WFHTTPBodyType"] == "JSON"

    payload = _payload(built)

    from backend.api.capture import CaptureRequest

    accepted = set(CaptureRequest.model_fields)
    assert set(payload) <= accepted, f"unknown fields: {set(payload) - accepted}"

    assert payload["text"] == SAMPLES["Spend"]
    assert payload["source"] == "ios-wallet"
    CaptureRequest(**payload)


def test_capture_ref_is_the_tap_time_to_the_second():
    """``ref`` is the idempotency key the API checks BEFORE Lucius runs, so two real taps
    must never share one — a ref derived from the purchase's content (amount, merchant)
    would drop the second of two identical coffees as "Already logged."."""
    built = SHORTCUTS["log-apple-pay"]()
    assert _payload(built)["ref"] == f"wallet-tap:{SAMPLES['Txn Stamp']}"

    stamps = [
        a["WFWorkflowActionParameters"]["WFDateFormat"]
        for a in built["WFWorkflowActions"]
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.format.date"
    ]
    assert any(":ss" in fmt for fmt in stamps), "the ref stamp must carry seconds"


def test_capture_payload_carries_no_ledger_fields():
    """The point of the capture endpoint is that the client stays dumb.

    If an account id or amount ever reappears here, the shortcut has started
    duplicating knowledge that belongs server-side — which is the coupling this
    design removed.
    """
    payload = _payload(SHORTCUTS["log-apple-pay"]())
    for ledger_field in ("from_account_id", "from_amount", "from_currency", "payee", "category"):
        assert ledger_field not in payload


def test_capture_shortcut_asks_one_question_and_splits_the_setup_code():
    """A signed file cannot carry this instance's address or token, so they arrive as ONE
    pasted setup code that the shortcut splits — on the same separator the server joins on."""
    built = SHORTCUTS["log-apple-pay"]()
    assert len(built["WFWorkflowImportQuestions"]) == 1

    actions = built["WFWorkflowActions"]
    split = next(
        a for a in actions if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.text.split"
    )
    assert split["WFWorkflowActionParameters"]["WFTextCustomSeparator"] == setup_code.SEPARATOR

    picks = [
        (a["WFWorkflowActionParameters"]["WFItemSpecifier"], actions[i + 1])
        for i, a in enumerate(actions)
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.getitemfromlist"
    ]
    assert [(spec, nxt["WFWorkflowActionParameters"]["WFVariableName"]) for spec, nxt in picks] == [
        ("First Item", "ONW Base URL"),
        ("Last Item", "ONW Token"),
    ]


def test_a_real_setup_code_splits_into_exactly_address_and_token():
    from backend.core.security import generate_token

    token = generate_token()
    code = setup_code.make("https://box.example.com:8443/", token)
    assert code.split(setup_code.SEPARATOR) == ["https://box.example.com:8443", token]


def test_no_shortcut_carries_an_instance_value():
    """Every shortcut is ONE generic signed file — nothing instance-specific may be baked in."""
    for name, builder in SHORTCUTS.items():
        literals = [v["string"] for v in _token_strings(builder())]
        assert not any("http://" in t or "https://" in t for t in literals), name


def test_capture_shortcut_shows_the_servers_own_reply():
    """The notification must report what was booked, not what the phone assumed."""
    built = SHORTCUTS["log-apple-pay"]()
    keys = [
        a["WFWorkflowActionParameters"]["WFDictionaryKey"]
        for a in built["WFWorkflowActions"]
        if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.getvalueforkey"
    ]
    assert keys[0] == "reply"


@pytest.mark.parametrize("slug", sorted(SHORTCUTS))
def test_reply_reads_the_response_and_a_failure_says_what_happened(slug):
    """The reply step's input is the response itself, bound as one variable — a token string
    left the editor's "Dictionary" slot greyed and empty (device, 2026-10-13). No reply never
    means a title-only banner: the app's own error detail is shown, else a no-answer message
    that doesn't blame the setup code alone (the app may simply be down)."""
    acts = SHORTCUTS[slug]()["WFWorkflowActions"]
    ids = [a["WFWorkflowActionIdentifier"] for a in acts]
    params = [a["WFWorkflowActionParameters"] for a in acts]
    gets = [p for p, i in zip(params, ids) if i == "is.workflow.actions.getvalueforkey"]
    assert [g["WFDictionaryKey"] for g in gets] == ["reply", "detail"]
    for g in gets:
        assert g["WFInput"]["WFSerializationType"] == "WFTextTokenAttachment"
        assert g["WFInput"]["Value"]["OutputName"] == "Contents of URL"

    flow = [p for p, i in zip(params, ids) if i == "is.workflow.actions.conditional"]
    groups: dict = {}
    for f in flow:
        groups.setdefault(f["GroupingIdentifier"], []).append(f["WFControlFlowMode"])
    assert sorted(groups.values()) == [[0, 1, 2], [0, 1, 2]]
    starts = [f for f in flow if f["WFControlFlowMode"] == 0]
    assert all(f["WFCondition"] == 100 for f in starts)
    tested = [f["WFInput"]["Variable"]["Value"]["OutputUUID"] for f in starts]
    assert tested == [g["UUID"] for g in gets]

    bodies = [
        p["WFNotificationActionBody"]["Value"]["string"]
        for p, i in zip(params, ids)
        if i == "is.workflow.actions.notification"
    ]
    assert len(bodies) == 3
    assert bodies[2] == NO_ANSWER_MESSAGE
    assert "setup code at the top" not in " ".join(bodies)


def test_build_is_reproducible():
    """Two builds must be byte-identical, or every rebuild churns the artifacts."""
    for builder in SHORTCUTS.values():
        first = plistlib.dumps(builder(), fmt=plistlib.FMT_BINARY)
        second = plistlib.dumps(builder(), fmt=plistlib.FMT_BINARY)
        assert first == second


def test_committed_artifacts_match_the_generator():
    """The checked-in .shortcut files are what users download — keep them in step."""
    from scripts.shortcuts.build import DIST

    for name, builder in SHORTCUTS.items():
        path = DIST / f"{name}.shortcut"
        assert path.exists(), f"missing artifact {path}; run `make ios-shortcuts`"
        assert path.read_bytes() == plistlib.dumps(builder(), fmt=plistlib.FMT_BINARY), (
            f"{path.name} is stale; run `make ios-shortcuts`"
        )


@pytest.mark.parametrize("slug", ["log-apple-pay", "log-apple-pay-ios26"])
def test_the_sentence_is_the_owners_wallet_payload(slug):
    """Mirrors the owner's working shortcut field for field (2026-10-12): the shortcut builds
    the sentence from the Wallet transaction itself — the automation only runs it. Name AND
    Merchant both go in: banks fill one or the other."""
    built = SHORTCUTS[slug]()
    sentences = [_render(v) for v in _token_strings(built) if v["string"].startswith("Spent ")]
    # The time is the ISO stamp (seconds + offset) — never a bare Current Date, which renders in
    # the phone's default style with no offset (seen on device, 2026-10-13).
    assert sentences == [
        "Spent <Amount> at <Name> (<Merchant>) with <Card or Pass> on 2026-07-28T09:15:04+08:00"
    ]


def test_the_shipped_shortcuts():
    """Apple Pay for iOS 27+ (trigger in the file) and iOS 26 and earlier (run by an automation),
    and (iOS 27) finance notifications — by the owner's call (2026-10-13)."""
    assert sorted(SHORTCUTS) == ["log-apple-pay", "log-apple-pay-ios26", "log-notifications"]


NOTIFY = "log-notifications"


def test_notification_shortcut_carries_its_own_trigger_and_no_input_block():
    """iOS 27 stores the trigger IN the shortcut; with one, the device declares no input types
    (no "Receive … from" block). Apps are left for the owner to pick after installing."""
    built = SHORTCUTS[NOTIFY]()
    assert built["WFWorkflowTriggers"] == [
        {
            "WFTriggerIdentifier": "WFNotificationTrigger",
            "WFTriggerUUID": built["WFWorkflowTriggers"][0]["WFTriggerUUID"],
            "WFTriggerSerializedParameters": {"SelectedApps": []},
        }
    ]
    assert built["WFWorkflowInputContentItemClasses"] == []
    assert "WFWorkflowTriggers" not in SHORTCUTS["log-apple-pay-ios26"]()


def test_notification_payload_is_the_notification_text_and_its_own_time():
    built = SHORTCUTS[NOTIFY]()
    payload = _payload(built)
    sentences = [_render(v) for v in _token_strings(built) if v["string"].startswith("Notif")]
    assert sentences == [
        "Notification from DBS digibank: Card transaction —  — You spent SGD 12.40 at "
        "STARBUCKS with card ending 1234 (received 2026-07-28T09:15:04+08:00)"
    ]
    # Its own source, not the Wallet one's: a tap and a notification of one purchase are two
    # reports of one event, and a shared namespace vetoed their comparison (live 2026-10-15).
    assert payload["source"] == "ios-notification"
    # The NOTIFICATION's time, not the run's — a late or repeated run keeps the same ref — then
    # the app that sent it, which the server reads as the emitter.
    assert payload["ref"] == "notif:2026-07-28T09:15:04+08:00@DBS digibank"
    from backend.api.capture import CaptureRequest

    CaptureRequest(**payload)


def test_notification_dates_are_iso_with_time_and_it_never_reads_wallet_fields():
    for node in _walk(SHORTCUTS[NOTIFY]()):
        assert node.get("Type") not in ("ExtensionInput", "CurrentDate"), node
        if node.get("Type") == "TriggerOutput" and any(
            a.get("PropertyName") == "Date" for a in node["Aggrandizements"]
        ):
            assert {"WFDateFormatStyle": "ISO 8601", "WFISO8601IncludeTime": True}.items() <= (
                node["Aggrandizements"][1].items()
            )


def test_apple_pay_ios27_reads_the_trigger_and_ios26_reads_shortcut_input():
    """iOS 27 hands the tap to the shortcut's own trigger — read as Shortcut Input there, the
    fields showed as bare "Shortcut Input" (seen on device, 2026-10-13). iOS 26 and earlier
    pass it in as Shortcut Input from a separate automation."""
    ios27 = SHORTCUTS["log-apple-pay"]()
    assert [t["WFTriggerIdentifier"] for t in ios27["WFWorkflowTriggers"]] == [
        "WFWalletTransactionTrigger"
    ]
    assert ios27["WFWorkflowTriggers"][0]["WFTriggerSerializedParameters"] == {}
    assert ios27["WFWorkflowInputContentItemClasses"] == []
    kinds27 = {n.get("Type") for n in _walk(ios27)}
    assert "ExtensionInput" not in kinds27 and "TriggerOutput" in kinds27
    names = [
        n["Aggrandizements"][0]
        for n in _walk(ios27)
        if n.get("Type") == "TriggerOutput"
        and n["Aggrandizements"][0].get("PropertyName") == "Name"
    ]
    assert names and all(a.get("PropertyUserInfo") == "WFItemName" for a in names)

    ios26 = SHORTCUTS["log-apple-pay-ios26"]()
    assert "WFWorkflowTriggers" not in ios26
    kinds26 = {n.get("Type") for n in _walk(ios26)}
    assert "TriggerOutput" not in kinds26 and "ExtensionInput" in kinds26


def test_both_apple_pay_versions_share_a_name_and_the_wallet_ref():
    from backend.services.shortcuts.catalog import BY_SLUG

    assert BY_SLUG["log-apple-pay"].name == BY_SLUG["log-apple-pay-ios26"].name
    for slug in ("log-apple-pay", "log-apple-pay-ios26"):
        payload = _payload(SHORTCUTS[slug]())
        assert payload["ref"].startswith("wallet-tap:")
        assert payload["source"] == "ios-wallet"
