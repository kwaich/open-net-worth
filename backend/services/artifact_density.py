"""How much a REPORT page may say — checked when the page is saved, so a report reads on a phone.

Why it exists: given a free page, the monthly report grew to 1,266 words and 171 numbers with no
bars, against 810 words, 115 numbers and 4 charts from the designed template (owner,
2026-10-07: "ugly ... too much numbers, text"). A rule in the skill is advice; this is a limit.
A page opts in with ``class="onw-report"`` on its ``<main>`` (the report templates do), so any
other page Lucius makes is never checked. Like ``check_filled``, a refusal NAMES what is too
long, so the model fixes the page and saves it again.

The limits are a ceiling, deliberately above what the skill asks for (a ~50-word summary,
~8-word context lines, about 6 rows), so a page that follows the skill never meets them.
"""

from __future__ import annotations

import html
import re

from backend.core.errors import ValidationError

MARK = 'class="onw onw-report"'
SUMMARY_MAX_WORDS = 80
SUB_MAX_WORDS = 12
SUB_MAX_NUMBERS = 3
ROWS_MAX = 8
PARAGRAPHS_MAX = 2
PARAGRAPH_MAX_WORDS = 60
NOTES_MAX = 4
NOTE_MAX_WORDS = 30

_SUMMARY = re.compile(r'<div class="onw-summary">(.*?)</div>', re.S)
_SUB = re.compile(r'<span class="onw-sub[^"]*">(.*?)</span>', re.S)
# A list the reader expects in full (every bill, every subscription) carries data-complete and
# is exempt from ROWS_MAX — owner, 2026-10-07: "users care about bills and subscriptions a lot".
_ROWS = re.compile(r'<table class="onw-rows"(?![^>]*data-complete)[^>]*>(.*?)</table>', re.S)
_ROW = re.compile(r"<tr(?![^>]*onw-bar-row)[^>]*>", re.S)
_PLAIN_P = re.compile(r"<p>(.*?)</p>", re.S)
_NOTES = re.compile(r'<details class="onw-notes">(.*?)</details>', re.S)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
_TAG = re.compile(r"<[^>]{0,500}>")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# A date is not a figure: "in 31 Aug–6 Sep" is one comparison window, not three numbers.
# The live weekly report was refused for "78.2% below SGD 2,445.25 in 31 Aug–6 Sep".
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_DATE = re.compile(
    rf"\b\d{{1,2}}(?:\s+{_MONTH})?(?:\s*[–-]\s*\d{{1,2}})?\s+{_MONTH}\b(?:\s+\d{{4}})?"
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:,?\s+\d{{4}})?\b|\b{_MONTH}\s+\d{{4}}\b"
)


def _words(fragment: str) -> int:
    return len(html.unescape(_TAG.sub(" ", fragment)).split())


def _numbers(fragment: str) -> int:
    text = _DATE.sub(" ", html.unescape(_TAG.sub(" ", fragment)))
    return len(_NUMBER.findall(text))


def _short(fragment: str) -> str:
    text = " ".join(html.unescape(_TAG.sub(" ", fragment)).split())
    return text[:40] + ("…" if len(text) > 40 else "")


def check_density(page: str) -> None:
    if MARK not in page:
        return
    problems: list[str] = []
    summary = _SUMMARY.search(page)
    if summary and _words(summary.group(1)) > SUMMARY_MAX_WORDS:
        problems.append(
            f"the summary is {_words(summary.group(1))} words (at most {SUMMARY_MAX_WORDS}: "
            "the few findings that matter, each with its number)"
        )
    body = _SUMMARY.sub("", page)
    long_subs = [
        s for s in _SUB.findall(body) if _words(s) > SUB_MAX_WORDS or _numbers(s) > SUB_MAX_NUMBERS
    ]
    if long_subs:
        problems.append(
            f"{len(long_subs)} context line(s) over {SUB_MAX_WORDS} words or "
            f'{SUB_MAX_NUMBERS} numbers, e.g. "{_short(long_subs[0])}" — one short line, '
            "one number"
        )
    for table in _ROWS.findall(body):
        rows = len(_ROW.findall(table))
        if rows > ROWS_MAX:
            problems.append(
                f"a list of {rows} rows (at most {ROWS_MAX}: the largest first, name the rest in "
                "one line)"
            )
    paragraphs = _PLAIN_P.findall(body)
    if len(paragraphs) > PARAGRAPHS_MAX:
        problems.append(
            f"{len(paragraphs)} plain paragraphs (at most {PARAGRAPHS_MAX}: show it as rows, "
            "bars or a chart)"
        )
    problems += [
        f'a paragraph of {_words(p)} words (at most {PARAGRAPH_MAX_WORDS}): "{_short(p)}"'
        for p in paragraphs
        if _words(p) > PARAGRAPH_MAX_WORDS
    ]
    problems += _note_problems(body)
    if problems:
        raise ValidationError(
            "the report says too much to read on a phone — "
            + "; ".join(problems[:6])
            + ". Cut it down and save again."
        )


def _note_problems(body: str) -> list[str]:
    """Data notes are for a caveat that changes how a figure reads — the September report
    carried eight, most of them method ("all figures are SGD", what the tools exclude)."""
    notes = [n for block in _NOTES.findall(body) for n in _ITEM.findall(block)]
    out = []
    if len(notes) > NOTES_MAX:
        out.append(
            f"{len(notes)} data notes (at most {NOTES_MAX}: only a caveat that changes how a "
            "figure on the page reads — never the currency, what tools exclude, or a check that "
            "found nothing)"
        )
    long_notes = [n for n in notes if _words(n) > NOTE_MAX_WORDS]
    if long_notes:
        out.append(
            f'{len(long_notes)} data note(s) over {NOTE_MAX_WORDS} words, e.g. "{_short(long_notes[0])}"'
        )
    return out
