"""Primitives for emitting Apple Shortcuts (``.shortcut``) property lists.

A ``.shortcut`` file is a plist whose ``WFWorkflowActions`` array holds one dict
per action. Most of the fiddliness lives in how Shortcuts encodes *interpolated
text*: a field that mixes literal characters with variable references is not a
plain string but a ``WFTextTokenString`` — a string in which every variable is
represented by a single U+FFFC OBJECT REPLACEMENT CHARACTER, plus a sidecar map
from ``"{location, length}"`` ranges to attachment dicts.

Ranges are measured in **UTF-16 code units** (Foundation's ``NSRange``), not
Python characters, so an emoji before an attachment shifts every later offset by
two rather than one. :func:`tokens` computes those offsets correctly; build every
interpolated field through it rather than hand-writing ranges.
"""

from __future__ import annotations

import plistlib
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence, Union

OBJ = "￼"

# WFItemType values for dictionary-field entries.
ITEM_TEXT = 0
ITEM_NUMBER = 1
ITEM_ARRAY = 2
ITEM_DICT = 3
ITEM_BOOL = 4


# Action ids only need to be unique *within* a shortcut, and the built files are
# committed, so they are derived from a fixed namespace + a per-shortcut counter.
# Random ids would rewrite every byte of both artifacts on each build and bury
# real changes in diff noise.
_UUID_NS = _uuid.UUID("6f1d5f2c-6a1e-5c3b-9d4e-0a7b2c8e1f30")
_counter = 0
_seed = "default"


def reset_uuids(seed: str) -> None:
    """Restart the deterministic id sequence for one shortcut."""
    global _counter, _seed
    _counter = 0
    _seed = seed


def new_uuid() -> str:
    """The next uppercase UUID in this shortcut's deterministic sequence."""
    global _counter
    _counter += 1
    return str(_uuid.uuid5(_UUID_NS, f"{_seed}:{_counter}")).upper()


class Attachment:
    """A variable reference that occupies one U+FFFC slot inside a text field."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload


def Var(name: str) -> Attachment:
    """Reference a named variable created by a Set Variable action."""
    return Attachment({"Type": "Variable", "VariableName": name})


def Output(action_uuid: str, name: str) -> Attachment:
    """Reference another action's output (a "magic variable")."""
    return Attachment({"Type": "ActionOutput", "OutputUUID": action_uuid, "OutputName": name})


# A date token set to "ISO 8601" + "ISO 8601 Time" — the exact encoding the Shortcuts editor
# writes for that choice (read back from an iOS 27 device, 2026-10-13).
ISO_8601_WITH_TIME = {
    "Type": "WFDateFormatVariableAggrandizement",
    "WFDateFormatStyle": "ISO 8601",
    "WFISO8601IncludeTime": True,
}


def CurrentDate(iso: bool = False) -> Attachment:
    """The special Current Date token; ``iso`` renders it as ISO 8601 with time.

    A bare token renders in the phone's default style ("15 Sep 2026 at 9:41 AM") wherever it
    becomes text — no seconds, no offset — so anything that must keep the second (the capture
    ref) takes ``iso=True``.
    """
    payload: Dict[str, Any] = {"Type": "CurrentDate"}
    if iso:
        payload["Aggrandizements"] = [dict(ISO_8601_WITH_TIME)]
    return Attachment(payload)


def ShortcutInput(prop: str = "") -> Attachment:
    """The Shortcut Input token — what an automation passes in — or one property of it.

    A Wallet Transaction automation passes the transaction itself; its properties are the
    blue Amount / Name / Merchant / Card or Pass tokens in the editor.
    """
    payload: Dict[str, Any] = {"Type": "ExtensionInput"}
    if prop:
        payload["Aggrandizements"] = [
            {"Type": "WFPropertyVariableAggrandizement", "PropertyName": prop}
        ]
    return Attachment(payload)


def TriggerOutput(trigger: str, prop: str, iso: bool = False, user_info: str = "") -> Attachment:
    """One field of what an in-shortcut trigger (iOS 27) hands the run — e.g. the Body of the
    notification that fired ``WFNotificationTrigger``, or the Amount of the Apple Pay tap that
    fired ``WFWalletTransactionTrigger``. ``iso`` renders a Date as ISO 8601 + time;
    ``user_info`` is the extra key some properties carry (a transaction's Name: "WFItemName").
    Encodings read back from an iOS 27 device, 2026-10-13."""
    prop_aggr: Dict[str, Any] = {"Type": "WFPropertyVariableAggrandizement", "PropertyName": prop}
    if user_info:
        prop_aggr["PropertyUserInfo"] = user_info
    aggrandizements: List[Dict[str, Any]] = [prop_aggr]
    if iso:
        aggrandizements.append(dict(ISO_8601_WITH_TIME))
    return Attachment(
        {"Type": "TriggerOutput", "TriggerIdentifier": trigger, "Aggrandizements": aggrandizements}
    )


def notification_trigger(trigger_uuid: str) -> Dict[str, Any]:
    """An iOS 27 "When I receive a notification from …" trigger with no apps chosen yet — the user
    picks their finance apps after installing. Stored in ``WFWorkflowTriggers``."""
    return {
        "WFTriggerIdentifier": "WFNotificationTrigger",
        "WFTriggerUUID": trigger_uuid,
        "WFTriggerSerializedParameters": {"SelectedApps": []},
    }


def wallet_trigger(trigger_uuid: str) -> Dict[str, Any]:
    """An iOS 27 "When … is tapped" (Apple Pay) trigger with no cards chosen yet — the user
    picks their cards after installing. Stored in ``WFWorkflowTriggers``."""
    return {
        "WFTriggerIdentifier": "WFWalletTransactionTrigger",
        "WFTriggerUUID": trigger_uuid,
        "WFTriggerSerializedParameters": {},
    }


Piece = Union[str, Attachment]


def _utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units, which is how NSRange counts."""
    return len(text.encode("utf-16-le")) // 2


def tokens(*pieces: Piece) -> Dict[str, Any]:
    """Build a ``WFTextTokenString`` from literal strings and attachments.

    ``tokens("Spent ", Var("Amount"))`` yields the serialized form of the text
    field a user would see as ``Spent [Amount]`` in the Shortcuts editor.
    """
    parts: List[str] = []
    attachments: Dict[str, Dict[str, Any]] = {}
    offset = 0
    for piece in pieces:
        if isinstance(piece, Attachment):
            attachments["{%d, 1}" % offset] = piece.payload
            parts.append(OBJ)
            offset += 1
        else:
            parts.append(piece)
            offset += _utf16_len(piece)

    value: Dict[str, Any] = {"string": "".join(parts)}
    if attachments:
        value["attachmentsByRange"] = attachments
    return {"Value": value, "WFSerializationType": "WFTextTokenString"}


def attachment(ref: Attachment) -> Dict[str, Any]:
    """A parameter that IS one variable, not text containing one (an action's input).

    Shortcuts serializes a lone variable in a non-text slot as ``WFTextTokenAttachment``;
    a token string there is coerced to text first, which flattens a list into one line.
    """
    return {"Value": ref.payload, "WFSerializationType": "WFTextTokenAttachment"}


def dict_field(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap already-built entries as a ``WFDictionaryFieldValue``."""
    return {
        "Value": {"WFDictionaryFieldValueItems": list(items)},
        "WFSerializationType": "WFDictionaryFieldValue",
    }


def entry(
    key: str, value: Union[str, Dict[str, Any]], item_type: int = ITEM_TEXT
) -> Dict[str, Any]:
    """One key/value row inside a dictionary field.

    ``value`` may be a plain string (wrapped as literal text) or an already-built
    :func:`tokens` result when it interpolates variables.
    """
    return {
        "WFItemType": item_type,
        "WFKey": tokens(key),
        "WFValue": tokens(value) if isinstance(value, str) else value,
    }


def action(identifier: str, params: Dict[str, Any] = None, uuid: str = None) -> Dict[str, Any]:
    """One entry of ``WFWorkflowActions``."""
    parameters = dict(params or {})
    if uuid is not None:
        parameters["UUID"] = uuid
    return {
        "WFWorkflowActionIdentifier": identifier,
        "WFWorkflowActionParameters": parameters,
    }


def comment(body: str) -> Dict[str, Any]:
    """A Comment action — the in-editor documentation a recipient reads first."""
    return action("is.workflow.actions.comment", {"WFCommentActionText": body})


def set_variable(name: str, source: Attachment) -> Dict[str, Any]:
    """Store ``source`` (an action's output) under ``name``.

    The input is bound EXPLICITLY. Left out, the editor shows a greyed "Input" placeholder and
    the variable is stored empty — observed on device, 2026-10-12, which emptied the address,
    token and text of every generated shortcut.
    """
    return action(
        "is.workflow.actions.setvariable", {"WFVariableName": name, "WFInput": attachment(source)}
    )


# Shortcuts' "has any value" condition (``WFCondition`` code 100) on an If action.
HAS_ANY_VALUE = 100


def if_has_value(
    ref: Attachment, then: Sequence[Dict[str, Any]], otherwise: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """If ``ref`` has any value → ``then``, Otherwise → ``otherwise``, End If.

    An If is three ``conditional`` actions sharing a ``GroupingIdentifier``, told apart by
    ``WFControlFlowMode`` (0 start, 1 otherwise, 2 end).
    """
    group = new_uuid()
    flow = "is.workflow.actions.conditional"
    return [
        action(
            flow,
            {
                "GroupingIdentifier": group,
                "WFControlFlowMode": 0,
                "WFCondition": HAS_ANY_VALUE,
                "WFInput": {"Type": "Variable", "Variable": attachment(ref)},
            },
        ),
        *then,
        action(flow, {"GroupingIdentifier": group, "WFControlFlowMode": 1}),
        *otherwise,
        action(flow, {"GroupingIdentifier": group, "WFControlFlowMode": 2}, uuid=new_uuid()),
    ]


def text_action(value: Union[str, Dict[str, Any]], uuid: str = None) -> Dict[str, Any]:
    """A Text action. Its parameter key is the one import questions target."""
    body = tokens(value) if isinstance(value, str) else value
    return action("is.workflow.actions.gettext", {"WFTextActionText": body}, uuid=uuid)


def import_question(
    action_index: int,
    parameter_key: str,
    question: str,
    category: str = "Parameter",
) -> Dict[str, Any]:
    """An on-import prompt that fills one action's parameter.

    This is what makes a shortcut re-installable by someone other than its
    author: rather than shipping a server URL and token baked into the file, the
    recipient is asked for them the moment they add the shortcut.
    """
    return {
        "WFWorkflowImportQuestionActionIndex": action_index,
        "WFWorkflowImportQuestionParameterKey": parameter_key,
        "WFWorkflowImportQuestionCategory": category,
        "WFWorkflowImportQuestionText": question,
    }


# The Shortcuts build an iOS 27 device stamps on the files it writes (2026-10-13).
IOS27_CLIENT_VERSION = "5113.0.1.1.1"

# The full set Shortcuts writes for a shortcut that accepts any input type.
INPUT_CONTENT_CLASSES = [
    "WFAppStoreAppContentItem",
    "WFArticleContentItem",
    "WFContactContentItem",
    "WFDateContentItem",
    "WFEmailAddressContentItem",
    "WFGenericFileContentItem",
    "WFImageContentItem",
    "WFiTunesProductContentItem",
    "WFLocationContentItem",
    "WFDCMapsLinkContentItem",
    "WFAVAssetContentItem",
    "WFPDFContentItem",
    "WFPhoneNumberContentItem",
    "WFRichTextContentItem",
    "WFSafariWebPageContentItem",
    "WFStringContentItem",
    "WFURLContentItem",
]


def workflow(
    actions: Sequence[Dict[str, Any]],
    import_questions: Sequence[Dict[str, Any]] = (),
    glyph: int = 61440,
    color: int = 4282601983,
    triggers: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """Assemble the top-level shortcut plist.

    With ``triggers`` (iOS 27) the shortcut starts itself and declares no input types — that is
    what the device writes for one (no "Receive … from" block); without, it accepts anything,
    the way an automation-run shortcut must.
    """
    doc = {
        # A trigger-bearing file declares the Shortcuts build that writes triggers (read back
        # from an iOS 27 device): a file stamped by an older build is migrated on import, and a
        # migration that predates triggers can drop WFWorkflowTriggers. Older files keep the
        # old stamp, which every iOS since 15 imports.
        "WFWorkflowClientVersion": IOS27_CLIENT_VERSION if triggers else "2607.1.6",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": color,
            "WFWorkflowIconGlyphNumber": glyph,
        },
        "WFWorkflowImportQuestions": list(import_questions),
        "WFWorkflowTypes": ["NCWidget", "WatchKit"],
        "WFWorkflowInputContentItemClasses": [] if triggers else list(INPUT_CONTENT_CLASSES),
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFQuickActionSurfaces": [],
        "WFWorkflowActions": list(actions),
    }
    if triggers:
        doc["WFWorkflowTriggers"] = list(triggers)
    return doc


def to_bytes(plist: Dict[str, Any]) -> bytes:
    """Serialize ``plist`` to the binary encoding Shortcuts itself exports.

    The app accepts XML too, but binary is what it writes, so it is what we emit.
    """
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)


def write_shortcut(plist: Dict[str, Any], path: Path) -> Path:
    """Write ``plist`` as a binary-plist ``.shortcut`` file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_bytes(plist))
    return path
