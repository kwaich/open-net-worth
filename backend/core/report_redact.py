"""Best-effort scrubbing of a log excerpt before it can be shown in a problem report.

A report is built from the application log, which carries operational detail but can still
mention a book's money, a payee, a home folder or a credential. Everything here is
deterministic and pattern-based: it narrows what leaves the machine, it does not prove the
excerpt clean — the user reads and edits the result before anything is sent.

The desktop shell has its own copy (``desktop/src/main/report-redact.ts``) because its logs are
read before any book is open. Both run the same golden cases in
``tests/fixtures/problem_report_redaction.json`` so the two cannot drift.

Layer: leaf — stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MAX_LOG_LINES = 200
MAX_LINE_CHARS = 400

_CURRENCIES = (
    "USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|VND|SGD|HKD|NZD|SEK|NOK|DKK|THB|MXN|BRL|KRW|ZAR|PLN|CZK"
    "|IDR|MYR|PHP|AED|SAR|TRY|ILS|RUB|TWD"
)
_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"
_SECRET_KEY = (
    r"api[_-]?key|apikey|token|secret|passw(?:or)?d|authorization|credential"
    r"|private[_-]?key|access[_-]?key|cookie"
)
_MONEY_KEY = (
    r"amount|balance|price|value|total|cost|fee|salary|income|net_?worth|quantity|qty|shares"
    r"|proceeds|payment|deposit|withdrawal|debit|credit"
)

_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/?#@]+@")
_URL_TAIL = re.compile(r"(\bhttps?://[^\s?#\"'<>)]+)[?#][^\s\"'<>)]*")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_AUTH_SCHEME = re.compile(r"\b(Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=\-]{8,}")
_SECRET_PAIR = re.compile(
    rf"(?<![A-Za-z0-9])([A-Za-z0-9_.\-]*(?:{_SECRET_KEY})[A-Za-z0-9_.\-]*)"
    r"([\"']?[ \t]*[:=][ \t]*)(\[[a-z]+\]|\"[^\"]*\"|'[^']*'|[^\s\"',;&}\])]+)",
    re.IGNORECASE,
)
_KNOWN_TOKEN = re.compile(
    r"\bsk-[A-Za-z0-9_\-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|\bxox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_\-]{30,}"
    r"|\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"
    r"|\b\d{8,10}:[A-Za-z0-9_\-]{30,}"
)
_LONG_RUN = re.compile(r"(?<![A-Za-z0-9_+=])[A-Za-z0-9_+=]{32,}(?![A-Za-z0-9_+=])")
_HOME_UNIX = re.compile(r"/(?:Users|home)/[^/\s\"']+")
_HOME_WINDOWS = re.compile(r"[A-Za-z]:(?:\\+|/)Users(?:\\+|/)[^\\/\s\"']+", re.IGNORECASE)
_MONEY_PREFIXED = re.compile(rf"(?:[$€£¥₹₩₫₽₺฿₪]|\b(?:{_CURRENCIES})\b)[ \t]?{_NUMBER}")
_MONEY_SUFFIXED = re.compile(rf"{_NUMBER}[ \t]?\b(?:{_CURRENCIES})\b")
_MONEY_PAIR = re.compile(
    rf"([A-Za-z_]*(?:{_MONEY_KEY})[A-Za-z_]*[\"']?[ \t]*[:=][ \t]*[\"']?)({_NUMBER})(?![\w%])",
    re.IGNORECASE,
)
_MONEY_GROUPED = re.compile(r"(?<![\w.,:/\-])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w.,])")
_MONEY_CENTS = re.compile(r"(?<![\w.:/\-])-?\d+\.\d{2}(?![\w.])")
_LINE_START = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_TRACE_LINE = re.compile(
    r"^(?:Traceback\b|[ \t]+File \"|[ \t]{2,}\S|[\w.]*(?:Error|Exception|Warning)\b)"
)

_PLACEHOLDER = re.compile(r"^\[[a-z]+\]$")
_MIN_TERM = 4


def _mask_secret_pair(match: re.Match[str]) -> str:
    key, separator, value = match.groups()
    if _PLACEHOLDER.match(value) or (value.isdigit() and len(value) <= 6):
        return match.group(0)
    return f"{key}{separator}[redacted]"


def _word_pattern(terms: Iterable[str]) -> re.Pattern[str] | None:
    unique = sorted(
        {t.strip() for t in terms if len(t.strip()) >= _MIN_TERM}, key=len, reverse=True
    )
    if not unique:
        return None
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(t) for t in unique) + r")(?!\w)", re.IGNORECASE
    )


def _mixed(run: str) -> bool:
    return any(c.isdigit() for c in run) and any(c.isalpha() for c in run)


def _omitted(count: int) -> str:
    return f"[{count} free-text line{'s' if count != 1 else ''} omitted]"


class Redactor:
    """One book's masking rules: its account and payee names and the folders it should hide.

    ``paths`` pairs a known folder (home, the book) with the token that replaces it; the generic
    ``/Users/<name>`` shapes are masked on top for folders this process was never told about.
    Built once per report so the name patterns compile once, not per log line."""

    def __init__(
        self,
        *,
        accounts: Iterable[str] = (),
        payees: Iterable[str] = (),
        paths: Iterable[tuple[str, str]] = (),
    ) -> None:
        names = ((_word_pattern(accounts), "[account]"), (_word_pattern(payees), "[payee]"))
        self._names = [(pattern, token) for pattern, token in names if pattern]
        self._paths = sorted(
            (p for p in paths if len(p[0]) >= 2), key=lambda item: len(item[0]), reverse=True
        )

    def text(self, text: str) -> str:
        """``text`` with names, paths, contact details, credentials and amounts masked."""
        for pattern, token in self._names:
            text = pattern.sub(token, text)
        for path, token in self._paths:
            for variant in {path, path.replace("\\", "\\\\"), path.replace("\\", "/")}:
                text = re.sub(re.escape(variant), lambda _m, t=token: t, text, flags=re.IGNORECASE)
        text = _HOME_WINDOWS.sub("~", text)
        text = _HOME_UNIX.sub("~", text)
        text = _URL_USERINFO.sub(r"\1[redacted]@", text)
        text = _URL_TAIL.sub(r"\1?[redacted]", text)
        text = _EMAIL.sub("[email]", text)
        text = _AUTH_SCHEME.sub(r"\1 [redacted]", text)
        text = _SECRET_PAIR.sub(_mask_secret_pair, text)
        text = _KNOWN_TOKEN.sub("[token]", text)
        text = _LONG_RUN.sub(lambda m: "[token]" if _mixed(m.group(0)) else m.group(0), text)
        text = _MONEY_PREFIXED.sub("[amount]", text)
        text = _MONEY_SUFFIXED.sub("[amount]", text)
        text = _MONEY_PAIR.sub(r"\1[amount]", text)
        text = _MONEY_GROUPED.sub("[amount]", text)
        return _MONEY_CENTS.sub("[amount]", text)

    def log_lines(self, lines: Iterable[str], *, limit: int = MAX_LOG_LINES) -> list[str]:
        """Scrub raw log lines and keep the newest ``limit`` of them.

        A line that does not open with a timestamp continues the entry above it: a traceback, or
        free text such as a model's reply. Only the traceback shape is kept; the rest collapses
        into one marker so a pasted document never rides along. Every kept line is capped."""
        kept: list[str] = []
        omitted = 0
        for raw in lines:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            if not _LINE_START.match(line) and not _TRACE_LINE.match(line):
                omitted += 1
                continue
            if omitted:
                kept.append(_omitted(omitted))
                omitted = 0
            text = self.text(line)
            kept.append(text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + " …")
        if omitted:
            kept.append(_omitted(omitted))
        return kept[-limit:]


def redact_text(
    text: str,
    *,
    accounts: Iterable[str] = (),
    payees: Iterable[str] = (),
    paths: Iterable[tuple[str, str]] = (),
) -> str:
    return Redactor(accounts=accounts, payees=payees, paths=paths).text(text)


def redact_log_lines(
    lines: Iterable[str],
    *,
    limit: int = MAX_LOG_LINES,
    accounts: Iterable[str] = (),
    payees: Iterable[str] = (),
    paths: Iterable[tuple[str, str]] = (),
) -> list[str]:
    return Redactor(accounts=accounts, payees=payees, paths=paths).log_lines(lines, limit=limit)
