"""An incomplete vision-page read must never masquerade as a complete one.

A truncated page reply (output-token cap) is re-asked for its remainder — bounded, raised-cap
continuations — instead of accepted as a silent partial read. A page still incomplete after the
continuation budget is kept but flagged: ``meta.extra['parse_complete'] = False`` and
``incomplete_pages`` name it, so the ledger-diff gate downstream (decisions.py) never treats a
partial read as a clean reconciliation. See
docs/adr/2026-09-09-an-incomplete-parse-withholds-the-ledger-diff.md.

Continuation replies carry an OUT-OF-BAND proof they resumed from the right place: a
``resumed_after`` object naming the anchor row, separate from ``transactions`` (which holds
ONLY new rows, every one of them kept). Two earlier designs both risked a real charge:
content-based dedup silently stripped a genuine repeat right after the anchor (ADR 2026-09-08
"an identical row at a page boundary is a real charge"), and a positional "drop index 0"
design still made one array slot do double duty as proof and data."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core import llm_seam
from backend.services.statements import tiers
from backend.services.statements.canonical import CanonicalRow
from backend.services.statements.parser import parse_statement
from backend.services.statements.tiers import _ROW_MAX_TOKENS_CONTINUATION, _echoes_anchor

_PAGE1_REPLY = {
    "meta": {"issuer": "Testville Bank", "currency": "USD"},
    "transactions": [],
}


def _row(date: str, amount: float, description: str) -> dict:
    return {
        "date": date,
        "amount": amount,
        "direction": "OUT",
        "payee": description,
        "description": description,
        "confidence": 0.9,
    }


_ROW1 = _row("2026-05-22", 58.86, "ROW A")
_ROW2 = _row("2026-05-23", 10.00, "ROW B")
_ROW3 = _row("2026-05-24", 20.00, "ROW C")  # the last COMPLETE row before truncation
_ROW4 = _row("2026-05-25", 30.00, "ROW D")
_ROW5 = _row("2026-05-26", 40.00, "ROW E")

# A cut-off array: rows 1-3 complete, row 4 starts and never closes (mirrors
# test_salvage_row_objects_recovers_truncated_array's construction).
_PAGE2_TRUNCATED = (
    "[" + ",".join(json.dumps(r) for r in (_ROW1, _ROW2, _ROW3)) + ',{"date": "2026-05-25able'
)


def _resumed_after(anchor: dict) -> dict:
    return {
        "date": anchor["date"],
        "description": anchor["description"],
        "amount": anchor["amount"],
    }


def _cont_reply(anchor: dict, *new_rows: dict, cut: bool = False) -> str:
    """A well-formed (or, with ``cut=True``, cap-truncated) continuation reply: the
    ``{"resumed_after": ..., "transactions": [...]}`` object every continuation must return."""
    body = "{" + f'"resumed_after": {json.dumps(_resumed_after(anchor))}, "transactions": ['
    body += ",".join(json.dumps(r) for r in new_rows)
    if cut:
        return body + (',{"date": "2026-05-2xtra' if new_rows else '{"date": "2026-05-2xtra')
    return body + "]}"


@pytest.fixture(autouse=True)
def _vision_capable(monkeypatch):
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: True)
    monkeypatch.setattr(llm_seam, "_parse_json", None)
    yield


def _run(monkeypatch, pages, replies):
    calls: list[dict] = []

    async def _complete(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return replies[len(calls) - 1]

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    result = asyncio.run(parse_statement({"kind": "pdf_pages", "images": pages}))
    return result, calls


# ── _echoes_anchor: out-of-band proof, canonical compare ────────────────────────────────


def test_echoes_anchor_recognises_a_reformatted_echo():
    anchor = CanonicalRow(date="2026-05-22", direction="OUT", amount=20.10, description="ROW C")
    reformatted = {"date": "22/05/2026", "amount": -20.10, "description": "ROW C"}
    assert _echoes_anchor(reformatted, anchor) is True


def test_echoes_anchor_rejects_an_unrelated_object():
    anchor = CanonicalRow(date="2026-05-22", direction="OUT", amount=20.10, description="ROW C")
    unrelated = {"date": "2026-05-26", "description": "ROW E", "amount": 40.00}
    assert _echoes_anchor(unrelated, anchor) is False


def test_echoes_anchor_rejects_a_missing_or_malformed_object():
    anchor = CanonicalRow(date="2026-05-22", direction="OUT", amount=20.10, description="ROW C")
    assert _echoes_anchor({}, anchor) is False
    assert _echoes_anchor(None, anchor) is False  # type: ignore[arg-type]


def test_echoes_anchor_recognises_a_truncated_long_description():
    """A >200-char narration used to always fail: the prompt shows the model a description
    truncated to `_ANCHOR_DESC_MAX_CHARS`, but the comparison held it to the FULL,
    untruncated original, so the model's (necessarily truncated) echo could never match —
    two continuations burned, then the page marked incomplete for nothing (security review
    follow-up). Both sides now truncate through the SAME helper."""
    long_desc = "MPOS*EXAMPLE MERCHANT REFERENCE " + "X" * 300
    anchor = CanonicalRow(date="2026-05-22", direction="OUT", amount=20.10, description=long_desc)
    resumed_after = {
        "date": "2026-05-22",
        "amount": 20.10,
        "description": long_desc[: tiers._ANCHOR_DESC_MAX_CHARS],
    }
    assert _echoes_anchor(resumed_after, anchor) is True


# ── vision tier: page-2 (continuation-page) truncation ──────────────────────────────────


def test_truncated_page_continuation_completes_it(monkeypatch):
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    continuation_reply = _cont_reply(_ROW3, _ROW4, _ROW5)
    replies = [json.dumps(_PAGE1_REPLY), _PAGE2_TRUNCATED, continuation_reply]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 3, "page 1 + page 2's truncated read + exactly one continuation"
    cont_call = calls[2]
    assert cont_call.get("max_tokens") == _ROW_MAX_TOKENS_CONTINUATION
    # The continuation prompt names the last complete row so the model can echo it.
    assert "2026-05-24" in cont_call["prompt"]
    assert "ROW C" in cont_call["prompt"]
    assert "20.00" in cont_call["prompt"]

    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", "ROW C", "ROW D", "ROW E"]
    assert result.meta.extra.get("parse_complete") is True
    assert "incomplete_pages" not in result.meta.extra
    assert result.meta.extra.get("reasoning_ate_budget") is True
    assert any("reasoning" in w for w in result.meta.warnings)


def test_a_real_repeat_charge_identical_to_the_anchor_survives(monkeypatch):
    """The regression both earlier designs risked: a genuine second charge identical to the
    anchor must never be mistaken for the echo, since the echo now lives in ITS OWN field —
    every row in `transactions` is new, full stop."""
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    # resumed_after names row 3; transactions[0] is a REAL second charge identical to row 3.
    continuation_reply = _cont_reply(_ROW3, _ROW3, _ROW4)
    replies = [json.dumps(_PAGE1_REPLY), _PAGE2_TRUNCATED, continuation_reply]

    result, _ = _run(monkeypatch, pages, replies)

    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", "ROW C", "ROW C", "ROW D"]
    assert result.meta.extra.get("parse_complete") is True


def test_truncated_page_continuation_echoes_correctly_with_a_long_description(monkeypatch):
    """End-to-end version of the `_echoes_anchor` unit test above: the anchor row's own
    description is >200 chars, the continuation reply echoes it back TRUNCATED (as the
    prompt showed it), and the page must still complete rather than burn continuations and
    give up (security review follow-up)."""
    long_desc = "MPOS*EXAMPLE MERCHANT REFERENCE " + "X" * 300
    row_long = _row("2026-05-24", 20.00, long_desc)
    truncated_anchor = {**row_long, "description": long_desc[: tiers._ANCHOR_DESC_MAX_CHARS]}
    page2_truncated = (
        "["
        + ",".join(json.dumps(r) for r in (_ROW1, _ROW2, row_long))
        + ',{"date": "2026-05-25able'
    )
    continuation_reply = _cont_reply(truncated_anchor, _ROW4)
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    replies = [json.dumps(_PAGE1_REPLY), page2_truncated, continuation_reply]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 3, "the continuation must fire exactly once and succeed"
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", long_desc, "ROW D"]
    assert result.meta.extra.get("parse_complete") is True


def test_continuation_that_does_not_echo_is_reasked_then_marked_incomplete(monkeypatch):
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    # resumed_after names the WRONG row (row 5, never read) — neither attempt echoes row 3.
    bad_continuation = _cont_reply(_ROW5, _ROW4)
    replies = [json.dumps(_PAGE1_REPLY), _PAGE2_TRUNCATED, bad_continuation, bad_continuation]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 4, "page 1 + the truncated read + one re-ask (both failed to echo)"
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", "ROW C"]  # nothing from either bad reply kept
    assert result.meta.extra.get("parse_complete") is False
    assert result.meta.extra.get("incomplete_pages") == [2]


def test_page_still_truncated_after_the_per_page_continuation_budget_is_kept_and_flagged(
    monkeypatch,
):
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    # Every continuation properly echoes the anchor but keeps truncating right after it — the
    # read never fully catches up within the per-page budget (3 continuations).
    initial = "[" + json.dumps(_ROW1) + ',{"date": "2026-05-2xtra'
    cont1 = _cont_reply(_ROW1, _ROW2, cut=True)
    cont2 = _cont_reply(_ROW2, _ROW3, cut=True)
    cont3 = _cont_reply(_ROW3, _ROW4, cut=True)
    replies = [json.dumps(_PAGE1_REPLY), initial, cont1, cont2, cont3]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 5, "page 1 + the initial read + exactly 3 continuations (budget used up)"
    assert all(c.get("max_tokens") == _ROW_MAX_TOKENS_CONTINUATION for c in calls[2:])
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", "ROW C", "ROW D"]  # every echoed+new row kept
    assert result.meta.extra.get("parse_complete") is False
    assert result.meta.extra.get("incomplete_pages") == [2]
    assert any("cut off mid-list" in w for w in result.meta.warnings)


# ── vision tier: a page that truncates with NOTHING collected (security review — crash) ──


def test_a_page_that_truncates_before_any_complete_row_is_reasked_not_crashed(monkeypatch):
    """Before the fix, the "nothing collected yet, re-ask plainly" branch only covered page 1
    — a later page truncating before even one complete row reached `collected[-1]` on an
    empty list, raised IndexError, and the blanket `except Exception` in tier3_vision reported
    the whole scan as "the AI vision reader is unavailable". 3 pages: page 2 never manages a
    complete row across all its retries, but page 1 and page 3 still read fine."""
    pages = [
        {"media_type": "image/png", "data": "aaa"},
        {"media_type": "image/png", "data": "bbb"},
        {"media_type": "image/png", "data": "ccc"},
    ]
    page2_attempt = '[{"date": "2026-05-2xtra'  # cut before even one object closes, every time
    page3_reply = json.dumps([_row("2026-05-27", 12.34, "ROW F")])
    replies = [
        json.dumps(_PAGE1_REPLY),  # page 1
        page2_attempt,  # page 2 initial
        page2_attempt,  # page 2 re-ask 1
        page2_attempt,  # page 2 re-ask 2
        page2_attempt,  # page 2 re-ask 3 (budget exhausted for this page)
        page3_reply,  # page 3
    ]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 6
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW F"]  # page 2 contributed nothing, page 3 still read
    assert result.meta.extra.get("parse_complete") is False
    assert result.meta.extra.get("incomplete_pages") == [2]


# ── vision tier: page 1's own truncation (security review — was undetected) ─────────────

_P1_META = {"issuer": "Testville Bank", "currency": "USD", "account_last4s": []}


def _page1_object(*complete_rows: dict, cut: bool) -> str:
    """A page-1 ``{"meta": ..., "transactions": [...]}`` reply — ``account_last4s: []``
    deliberately printed before ``transactions`` (the exact shape that used to fool
    ``reply.find("[")`` into landing on the empty meta array and reporting a clean,
    zero-row read)."""
    body = "{" + f'"meta": {json.dumps(_P1_META)}, "transactions": ['
    body += ",".join(json.dumps(r) for r in complete_rows)
    if cut:
        return body + (',{"date": "2026-05-2xtra' if complete_rows else '{"date": "2026-05-2xtra')
    return body + "]}"


def test_page_1_truncation_is_detected_and_the_continuation_completes_it(monkeypatch):
    pages = [{"media_type": "image/png", "data": "aaa"}]
    initial = _page1_object(_ROW1, _ROW2, cut=True)
    continuation = _cont_reply(_ROW2, _ROW3)  # echoes row 2, then the genuinely new row 3
    replies = [initial, continuation]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 2, "the truncated page-1 read + one continuation — it must have fired"
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", "ROW C"]
    assert result.meta.issuer == "Testville Bank"  # meta salvaged though the object never closed
    assert result.meta.extra.get("parse_complete") is True
    assert "incomplete_pages" not in result.meta.extra


def test_page_1_still_truncated_after_its_continuation_budget_stays_incomplete(monkeypatch):
    pages = [{"media_type": "image/png", "data": "aaa"}]
    initial = _page1_object(_ROW1, cut=True)
    cont1 = _cont_reply(_ROW1, _ROW2, cut=True)
    cont2 = _cont_reply(_ROW2, _ROW3, cut=True)
    cont3 = _cont_reply(_ROW3, _ROW4, cut=True)
    replies = [initial, cont1, cont2, cont3]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 4
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["ROW A", "ROW B", "ROW C", "ROW D"]
    assert result.meta.extra.get("parse_complete") is False
    assert result.meta.extra.get("incomplete_pages") == [1]


# ── vision tier: the per-document continuation budget ───────────────────────────────────


def test_the_document_wide_continuation_budget_bounds_every_page_together(monkeypatch):
    """40 pages x 3 continuations each with no shared ceiling was the unbounded-cost finding
    (security review) — pin it small (2) so the fixture stays tiny: page 1 needs and gets its
    one continuation, page 2 needs two but only one is left in the pool, so it is marked
    incomplete by the DOCUMENT budget, not its own per-page one."""
    monkeypatch.setattr(tiers, "_MAX_CONTINUATIONS_PER_DOC", 2)
    b1, b2, b3, b4 = (
        _row("2026-06-01", 10.00, "BROW A"),
        _row("2026-06-02", 20.00, "BROW B"),
        _row("2026-06-03", 30.00, "BROW C"),
        _row("2026-06-04", 40.00, "BROW D"),
    )
    pages = [{"media_type": "image/png", "data": "aaa"}, {"media_type": "image/png", "data": "bbb"}]
    page1_initial = _page1_object(b1, cut=True)
    page1_cont = _cont_reply(b1, b2)  # closes cleanly — page 1 needed exactly 1 continuation
    page2_initial = "[" + json.dumps(b3) + ',{"date": "2026-06-0Xtra'
    page2_cont = _cont_reply(b3, b4, cut=True)
    replies = [page1_initial, page1_cont, page2_initial, page2_cont]

    result, calls = _run(monkeypatch, pages, replies)

    assert len(calls) == 4
    descriptions = [r.description for r in result.rows]
    assert descriptions == ["BROW A", "BROW B", "BROW C", "BROW D"]
    assert result.meta.extra.get("parse_complete") is False
    assert result.meta.extra.get("incomplete_pages") == [2]
    assert any("continuation" in w and "budget" in w for w in result.meta.warnings)
