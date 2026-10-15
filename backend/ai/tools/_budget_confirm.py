"""The confirm funnel shared by the budget-writing tool groups (``planning`` + ``envelopes``).

Both groups write through the SAME choke (``services.planning.apply_budget_change``) and show
the same confirm card, so they need the same three functions: stage-or-write for a single op,
the batch twin, and the writer both resolve to. Those lived in ``planning.py`` with a copy of
the first one in ``envelopes.py``, whose docstring explained the duplication as "avoiding a
cross-module dependency between two sibling tool-group files".

That reason stopped being true the moment staging replaced ``{"proposed": True}``: the copy
could no longer be self-contained (it needs the writer), and keeping it meant a deferred
import inside a function body — a circular-import dodge hiding a module that wanted to exist.
This is that module. Both groups import it at module scope; nothing imports back.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from backend.ai.tools import registry
from backend.core.errors import OpenNetWorthError
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import planning as planning_service

logger = logging.getLogger(__name__)

# Batch-shaped ``type``s the writer LOOPS rather than dispatching directly to the service —
# see ``apply_batch``. Each is staged by the matching propose tool's plural field.
BATCH_CHANGE_TYPES = frozenset(
    {
        "assign_batch",
        "move_money_batch",
        "set_target_batch",
        "group_rename_batch",
        "group_delete_batch",
        "move_category_batch",
        "envelope_add_category_batch",
        "envelope_split_category_batch",
        "envelope_update_batch",
    }
)

# Keys a propose tool may attach to the change for the MODEL's benefit rather than the
# service's — echoed back on the approval's result, stripped before the service sees it.
# A staged call has no result text of its own (it pauses), so this is the only way a note
# about the staging survives to the turn that resumes.
_NOTE_KEYS = ("no_op_group_ids",)


_RTA = "__rta"


def _named_key(key: str) -> Optional[tuple[str, str]]:
    """``(store, name key)`` for a display key holding an id (or a list of them), else None.

    ``envelope_id`` / ``from_envelope_id`` / ``envelope_ids``… point at envelopes; ``group_id(s)``
    at groups; ``category_id(s)`` at categories. ``category`` / ``categories`` are the budget
    displays' pre-envelope keys and hold ENVELOPE ids (``move_envelopes_to_group``,
    ``reorder_envelopes_in_group``)."""
    if key.endswith("envelope_id"):
        return "envelope", f"{key[:-3]}_name"
    if key.endswith("envelope_ids"):
        return "envelope", f"{key[:-4]}_names"
    fixed = {
        "group_id": ("group", "group_name"),
        "group_ids": ("group", "group_names"),
        "category_id": ("category", "category_name"),
        "category_ids": ("category", "category_names"),
        "category": ("envelope", "category_name"),
        "categories": ("envelope", "category_names"),
    }
    return fixed.get(key)


def with_names(display: dict) -> dict:
    """The card's display with each envelope / group NAME beside the id it was staged with —
    ``envelope_id`` gains ``envelope_name``, ``from_envelope_id`` gains ``from_envelope_name``,
    ``group_id`` gains ``group_name``, recursing into a batch's ``rows`` / ``updates``. A card
    that showed ``env_4c9a…`` (or, before that, nothing) now names the envelope.

    Display only: the staged args approval re-runs are the caller's own, and ``write`` strips
    ``display`` before the service sees it. An id that no longer resolves keeps no name rather
    than failing the stage."""
    names: dict[str, dict[str, str]] = {}

    def lookup(kind: str, ref: object) -> Optional[str]:
        if not isinstance(ref, str) or not ref:
            return None
        if kind == "envelope" and ref == _RTA:
            return "Ready to Assign"
        if kind not in names:
            if kind == "category":
                rows = categories_service.all_category_rows()
                names[kind] = {r["id"]: r.get("label") or "" for r in rows if r.get("id")}
            else:
                rows = (
                    envelopes_service.all_envelopes()
                    if kind == "envelope"
                    else planning_service.list_groups()
                )
                names[kind] = {r["id"]: r.get("name") or "" for r in rows if r.get("id")}
        return names[kind].get(ref) or None

    def visit(value: object) -> object:
        if isinstance(value, list):
            return [visit(v) for v in value]
        if not isinstance(value, dict):
            return value
        out = {k: visit(v) for k, v in value.items()}
        for key, ref in value.items():
            named = _named_key(key)
            if named is None:
                continue
            kind, name_key = named
            if isinstance(ref, list):
                # A batch lists its refs (`group_ids`): names in the same order, an
                # unresolvable one falling back to its id so the two lists stay aligned.
                out.setdefault(name_key, [lookup(kind, r) or r for r in ref])
            elif name := lookup(kind, ref):
                out.setdefault(name_key, name)
        return out

    try:
        return visit(display)  # type: ignore[return-value]
    except Exception:  # a name is a nicety; never let it block a confirm card
        logger.warning("budget card: could not resolve envelope/group names", exc_info=True)
        return display


class _NamedArgsPreview:
    """The generic gate's card preview for every budget-writing tool: the call's own arguments
    with each envelope / group name beside its id (``with_names``).

    Budget tools are staged by the dispatch-level gate (``stage_generic``), not by their own
    handler, so the card a user actually sees shows the ARGUMENTS — envelope ids — and
    ``proposed``'s named display never reaches it. This adapter is how names do. It is
    display only and never refuses an approval: ``validate`` passes, because a stale name is
    no reason to block what the user approved, and approval runs the stored args regardless.
    """

    def build(self, args: dict) -> dict:
        return {"kind": "named_args", "data": with_names(args), "raw_arguments": args}

    def validate(self, preview: dict) -> Optional[str]:
        return None


NAMED_ARGS_PREVIEW = _NamedArgsPreview()


def proposed(change: dict, display: dict) -> tuple[Optional[str], Optional[dict]]:
    """Stage one budget change as a confirm card — or WRITE it.

    Writing is correct in exactly two cases and they must behave identically: an unattended
    caller (no card to show) and the SERVER redeeming an approval (the card was shown and
    answered). ``should_preview_write()`` answers both, and it is False under a redemption by
    construction — see ADR ``2026-08-25-one-confirm-mechanism-for-every-tool``.

    Before that ADR this returned ``{"proposed": True}`` and stored nothing: the frontend
    answered a ✓ by asking the MODEL to call ``apply_budget_change`` with arguments it
    RECONSTRUCTED, so what committed was whatever it remembered.
    """
    full = {**change, "idempotency_key": str(uuid.uuid4()), "kind": "budget", "display": display}
    if registry.should_preview_write():
        # The CALLER'S arguments, never the change derived from them: approval re-runs this
        # same handler, and a handler re-derives its change from its arguments.
        return registry.stage_write({**full, "display": with_names(display)})
    return write(full)


def proposed_batch(
    batch_type: str, items: list[dict], display: dict, note: Optional[dict] = None
) -> tuple[Optional[str], Optional[dict]]:
    """The batch twin of ``proposed``. ``items`` are fully-formed single-op change dicts (each
    carrying its own ``type``); the writer loops them through the SAME choke a single confirm
    uses, one call per item, never a parallel write path."""
    full = {
        "type": batch_type,
        "items": items,
        "idempotency_key": str(uuid.uuid4()),
        "kind": "budget",
        "display": display,
        **(note or {}),
    }
    if registry.should_preview_write():
        return registry.stage_write({**full, "display": with_names(display)})
    return write(full)


def write(full: dict) -> tuple[str, None]:
    """Commit a staged budget change through ``services.planning.apply_budget_change``."""
    note = {k: v for k, v in full.items() if k in _NOTE_KEYS}
    carrier = ("idempotency_key", "kind", "display", *note)
    change = {k: v for k, v in full.items() if k not in carrier}
    ctype = change.get("type")
    if ctype in BATCH_CHANGE_TYPES:
        result = apply_batch(change.get("items") or [])
        logger.info(
            "budget change %s items=%d applied=%d", ctype, result["count"], result["applied"]
        )
        return json.dumps({"ok": True, **result, **note}, ensure_ascii=False), None
    try:
        result = planning_service.apply_budget_change(change)
    except OpenNetWorthError as exc:
        return json.dumps({"ok": False, "error": exc.message or str(exc)}), None
    logger.info("budget change %s", ctype)
    return json.dumps({"ok": True, **result, **note}, ensure_ascii=False), None


def apply_batch(items: list) -> dict:
    """Loop ``items`` through ``services.planning.apply_budget_change`` — the SAME choke a
    single confirm calls — one item at a time. Best-effort, NOT atomic: a budget batch is N
    independent edits (assign envelope A, assign envelope B, …), not one transaction, so one
    bad item (an unknown envelope, a stale group id) is reported without discarding the N-1
    that succeeded — mirrors ``bulk_update_transactions``'s per-id result reporting."""
    results: list[dict] = []
    applied = 0
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"index": i, "ok": False, "error": f"expected an object, got {item!r}"})
            continue
        try:
            r = planning_service.apply_budget_change(item)
            results.append({"index": i, "ok": True, **r})
            applied += 1
        except OpenNetWorthError as exc:
            results.append({"index": i, "ok": False, "error": exc.message or str(exc)})
    return {"count": len(items), "applied": applied, "results": results}
