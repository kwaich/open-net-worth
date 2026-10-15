"""Exact-match EDIT shape for large stored text (RFC 2026-07-20 §14.2, D7 revised).

WHY THIS EXISTS — the argument is CORRECTNESS, not token cost.

A tool that takes the ENTIRE new value of a stored text body (``update_skill``'s
``changes.body``) is a LOST UPDATE waiting to happen:

    T1  the model reads the prompt
    T2  the USER hand-edits it in the UI
    T3  the model writes back the full version it composed from what it read at T1

The user's T2 edit is gone, silently, because the model never knew it existed. Nothing in a
full-replace payload can detect this — the write is well-formed and the store has no way to
tell an intentional rewrite from an oblivious one.

An exact-match edit FAILS in that situation instead of overwriting, because the surrounding
text no longer matches. That is the entire safety property, and it is why Claude Code's
``Edit`` requires a prior ``Read`` and an exact ``old_string``. Token cost is a real but
third-order benefit; it is not the reason this module exists.

WHY IT IS SECURITY-RELEVANT.

An agent prompt is the highest-leverage mutable text in the system: it is the instruction set
that agent follows on every future UNATTENDED run, so it is where a prompt injection would
most want to land, because it PERSISTS. A full-rewrite path lets a single tool call replace
all of it while the confirm card shows the user 280 collapsed characters. An edit is bounded
and legible: the payload IS the delta, so the card renders the actual change with no
derivation and nothing truncated.

THE REFUSAL RULES — ambiguity is the failure mode that makes an edit tool dangerous.

  * ``old`` not found      -> REFUSE. Never a "closest match", never a fuzzy fallback.
  * ``old`` found N>1 times -> REFUSE. Never silently pick the first.

A loud refusal is the safety property. Both refusals are actionable per the S2 error contract
(``core.errors.recovery_hint``'s convention): they say whether it was 0 or N matches and name
the tool that reads the current value, so the model's next move is obvious.

WHERE RESOLUTION HAPPENS — at the WRITE CHOKE, not at propose time.

``resolve_text_edits`` is called from a write choke (``services.skills.apply_skill_change``
op=update; ``ai.tools.skills.apply_skill_change`` skill_update), NOT only when the confirm
card is built. That is deliberate: the confirm window is exactly when a human is most likely
to hand-edit the very text being proposed. Resolving at propose time and storing the
flattened result would reintroduce the lost update between propose and apply. The propose
path validates too — so the model gets an immediate refusal instead of one after the user
clicks approve — but the value it stores in the change stays the EDIT, and the choke
re-resolves against whatever is stored at write time.

This is the same "stage, then re-resolve and refuse on mismatch" contract the ledger confirm
gate uses (ADR 2026-07-19-ledger-confirm-gate-one-tool-stored-args).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from backend.core.errors import ValidationError


class TextEditError(ValidationError):
    """An edit that could not be applied unambiguously. The message is user/model facing.

    Subclasses ``ValidationError`` so a refusal travels the paths that already exist: the
    REST edge maps it to 422 and the AI tools return ``{ok: false, error: …}``. A refusal is
    a rejected input, not an internal fault.
    """


class TextEdit(NamedTuple):
    """An exact substring replacement against the CURRENT stored value."""

    old: str
    new: str


def parse_text_edit(value: Any) -> TextEdit | None:
    """Return a ``TextEdit`` if ``value`` is the edit shape, else ``None`` (a full replace).

    The edit shape is ``{"old": str, "new": str}``. Anything else — a plain string, ``None``,
    a number — is a full replacement and is passed through untouched, which is what keeps the
    replace path working: a genuine rewrite ("make this agent about fraud instead") is not an
    edit, and Claude Code ships ``Edit`` AND ``Write`` for exactly this reason.

    A dict carrying ``old``/``new`` with a non-string value, or ``old`` missing entirely, is
    a MALFORMED edit rather than a replace — returning ``None`` there would silently store a
    dict as the body. Those raise.
    """
    if not isinstance(value, dict):
        return None
    # A DICT IS NEVER A FULL REPLACE (F1/F2, security review 2026-07-20).
    #
    # This used to `return None` when the dict carried neither key, and the caller reads
    # None as "replace the whole value". Two live defects came out of that one line:
    #
    #   F1 — update_skill(changes={"body": {}}) SILENTLY ERASED the entire skill body:
    #        None -> full replace -> db/skill_files.py does (record.get("body") or "").strip()
    #        and {} is falsy -> "". The card showed only changed_fields:["body"] with no
    #        preview, so the erasure was invisible on web AND connector.
    #   F2 — {"old_string": ..., "new_string": ...} -> neither key -> None -> full replace
    #        -> .strip() on a dict -> uncaught AttributeError, AFTER the user approved.
    #        Those are the literal parameter names of Claude Code's Edit tool, which this
    #        design is modelled on, so it is the single most likely model mistake.
    #
    # A caller passing a dict is trying to edit. If we cannot read it as an edit, that is a
    # malformed edit and must refuse — never fall through to overwriting the whole field.
    if "old" not in value or "new" not in value:
        raise TextEditError(
            'edit must be {"old": "<exact text to replace>", "new": "<replacement>"} — '
            f"got keys {sorted(value)!r}. NOTHING was changed. (Note the keys are `old` and "
            "`new`, not `old_string`/`new_string`.) To replace the whole value instead, pass "
            "a plain string."
        )
    old, new = value.get("old"), value.get("new")
    if not isinstance(old, str) or not isinstance(new, str):
        raise TextEditError(
            'edit must be {"old": "<exact text to replace>", "new": "<replacement>"} '
            "with both values as strings; pass a plain string instead to replace the whole value"
        )
    if old == "":
        raise TextEditError(
            "edit `old` must not be empty — an empty string matches everywhere and cannot "
            "identify a location; pass a plain string instead to replace the whole value"
        )
    return TextEdit(old=old, new=new)


def apply_text_edit(current: str, edit: TextEdit, *, field: str, read_tool: str) -> str:
    """Apply one exact, single-occurrence substring replacement to ``current``.

    REFUSES (``TextEditError``) on 0 matches or on >1 matches. There is deliberately no
    fuzzy/normalized/closest-match fallback: silently editing text the caller did not
    actually name is precisely the failure this shape exists to prevent.

    ``field`` and ``read_tool`` only shape the error message — they make the refusal
    actionable (which field, and which tool re-reads the current value).
    """
    text = current or ""
    # OVERLAPPING count, not str.count (F4, security review 2026-07-20). str.count is
    # non-overlapping, so current="Rule.\n\n\nRule2." with old="\n\n" has TWO valid
    # positions but counts ONE — and the edit applied silently at the first. Blank-line runs
    # are common in prompt bodies, so the "ambiguity always refuses" invariant was not total.
    hits = 0
    _i = text.find(edit.old)
    while _i != -1:
        hits += 1
        _i = text.find(edit.old, _i + 1)

    if hits == 0:
        raise TextEditError(
            f"edit refused: `old` ({len(edit.old)} chars) does not appear in the current "
            f"`{field}` ({len(text)} chars). NOTHING was changed. The stored text may have "
            f"been edited since you read it — re-read it with {read_tool} and build the edit "
            f"from the value you get back. Do not retry with the same `old`."
        )
    if hits > 1:
        raise TextEditError(
            f"edit refused: `old` appears {hits} times in the current `{field}`; an edit must "
            f"identify exactly ONE location and this one is ambiguous. NOTHING was changed. "
            f"Extend `old` with enough surrounding text to make it unique (re-read the current "
            f"value with {read_tool} if you need more context)."
        )
    return text.replace(edit.old, edit.new, 1)


def resolve_text_edits(
    changes: dict,
    current: dict,
    *,
    fields: tuple[str, ...],
    read_tool: str,
) -> dict:
    """Flatten any edit-shaped entry in ``changes`` against the CURRENT stored record.

    Returns a NEW dict — ``changes`` is never mutated, because the caller's copy is what the
    confirm card renders and what a retry re-reads. Every non-edit entry passes through
    untouched, so this is safe to call unconditionally at a write choke: a full-replace
    payload is returned byte-identical.

    Call this at the WRITE CHOKE. See the module docstring for why resolving at propose time
    instead would reintroduce the lost update across the confirm window.
    """
    out = dict(changes)
    for field in fields:
        if field not in out:
            continue
        edit = parse_text_edit(out[field])
        if edit is None:
            continue
        out[field] = apply_text_edit(
            current.get(field) or "", edit, field=field, read_tool=read_tool
        )
    return out


def edit_display(edit: TextEdit, *, context: int = 240) -> dict:
    """Card payload for an edit — the fragment itself, NOT a derived diff.

    This is most of why the RFC prefers the edit shape: the model supplied the delta, so the
    card can render exactly what was asked for with no derivation and no truncation of the
    surrounding body. Long fragments are still capped for the card (a 20K "edit" that is
    really a rewrite should not blow up the confirm UI), and the cap is reported honestly via
    ``old_truncated`` / ``new_truncated`` rather than silently eliding.
    """

    def _cap(text: str) -> tuple[str, bool]:
        if len(text) <= context:
            return text, False
        return text[: context - 1].rstrip() + "…", True

    old_text, old_cut = _cap(edit.old)
    new_text, new_cut = _cap(edit.new)
    return {
        "old": old_text,
        "new": new_text,
        "old_truncated": old_cut,
        "new_truncated": new_cut,
        "old_chars": len(edit.old),
        "new_chars": len(edit.new),
    }
