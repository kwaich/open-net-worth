"""Locale-aware number & date parsing for statement rows (guard D4.4).

The decimal/thousands convention and the date order are inferred ONCE per document from the
data itself, then applied uniformly — never re-guessed per row, never silently defaulted when
genuinely ambiguous (`1.234,56` vs `1,234.56`; `03/04` Mar-4 vs Apr-3). Pure stdlib.
"""

from __future__ import annotations

import re
from datetime import datetime

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
_NUMERIC_DATE = re.compile(r"^(\d{1,4})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?$")
_TEXT_DMY = re.compile(r"^(\d{1,2})[\s\-]([A-Za-z]{3,9})\.?[\s\-]?(\d{2,4})?$")
_TEXT_MDY = re.compile(r"^([A-Za-z]{3,9})\.?[\s\-](\d{1,2})(?:,?\s*(\d{2,4}))?$")
_AMOUNT_JUNK = re.compile(r"[^\d,.\-()'−]")
_GROUPED_3 = re.compile(r"^\d{1,3}([.,'  ]\d{3})+$")


def infer_number_convention(samples: list[str]) -> dict:
    """Vote a decimal separator from raw amount strings. ``{"decimal": "." | ","}``.

    Evidence per token: both separators present → the LAST one is the decimal; a single
    separator followed by 1-2 digits (and not a groups-of-3 pattern) is a decimal; a
    groups-of-3 pattern is a thousands separator (so the OTHER char is the decimal).
    Majority wins; tie/no evidence → ".".
    """
    votes = {".": 0, ",": 0}
    for raw in samples:
        s = _AMOUNT_JUNK.sub("", str(raw or "")).strip("()-−'")
        if not s or not any(c in s for c in ",."):
            continue
        if "," in s and "." in s:
            votes["." if s.rfind(".") > s.rfind(",") else ","] += 2
            continue
        sep = "," if "," in s else "."
        other = "." if sep == "," else ","
        if _GROUPED_3.match(s):
            votes[other] += 1
        elif re.search(rf"\{sep}\d{{1,2}}$", s) and s.count(sep) == 1:
            votes[sep] += 1
    if votes["."] == votes[","]:
        return {"decimal": "."}
    return {"decimal": max(votes, key=lambda k: votes[k])}


def parse_signed(raw, convention: dict | None = None) -> float | None:
    """Parse one raw amount cell into a SIGNED float; None if unusable/zero.

    Handles thousands/decimal per ``convention`` (default US), parentheses / leading minus /
    Unicode minus as negative, trailing CR (credit → positive) and DR/DB (debit → negative)
    markers, and currency symbols/codes."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if val != 0 else None
    s = str(raw).strip()
    if not s:
        return None
    sign = 1.0
    upper = s.upper()
    if re.search(r"\bCR\.?$", upper):
        s = s[: upper.rfind("CR")]
    elif re.search(r"\bD[RB]\.?$", upper):
        sign = -1.0
        s = s[: upper.rfind("D")]
    s = s.strip()
    if (s.startswith("(") and s.endswith(")")) or s.startswith(("-", "−")):
        sign = -1.0
    s = _AMOUNT_JUNK.sub("", s).strip("()-−")
    if not s:
        return None
    decimal = (convention or {}).get("decimal", ".")
    thousands = "," if decimal == "." else "."
    s = s.replace("'", "").replace(" ", "").replace(" ", "").replace(thousands, "")
    s = s.replace(decimal, ".")
    try:
        val = float(s)
    except ValueError:
        return None
    return sign * val if val != 0 else None


def infer_date_order(samples: list[str]) -> str | None:
    """Infer numeric date order from raw date strings: "DMY" | "MDY" | "YMD" | None (ambiguous).

    A 4-digit leading token → YMD. Any first-token > 12 → DMY; any second-token > 12 → MDY.
    Textual-month dates are self-describing and cast no vote. All days ≤ 12 with no other
    evidence → None: genuinely unresolvable, the caller must ask, never silently pick."""
    dmy = mdy = ymd = 0
    for raw in samples:
        token = str(raw or "").strip().split()[0] if str(raw or "").strip() else ""
        m = _NUMERIC_DATE.match(token)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 999:
            ymd += 1
        elif a > 12 >= b:
            dmy += 1
        elif b > 12 >= a:
            mdy += 1
    if ymd and not (dmy or mdy):
        return "YMD"
    if dmy and not mdy:
        return "DMY"
    if mdy and not dmy:
        return "MDY"
    return None


def _expand_year(y: str | None, default_year: int | None) -> int | None:
    if not y:
        return default_year
    n = int(y)
    return n if n > 99 else 2000 + n


def parse_date(raw, order: str | None = None, default_year: int | None = None) -> str | None:
    """Parse one raw date cell → ISO ``YYYY-MM-DD``; None if unparseable or ambiguous.

    Numeric forms follow ``order`` (from ``infer_date_order``); when order is None a numeric
    date is accepted ONLY if self-evident (a component > 12 or a 4-digit year first). Textual
    months (``21 MAY``, ``May 21, 2026``) are unambiguous. Trailing time is ignored."""
    if raw is None:
        return None
    s = str(raw).strip().split("\n")[0].strip()
    if not s:
        return None
    s = re.sub(r"[T ]\d{1,2}:\d{2}(:\d{2})?.*$", "", s).strip()
    m = _TEXT_DMY.match(s)
    if m and m.group(2).lower()[:3] in _MONTHS:
        month = _MONTHS[m.group(2).lower()[:3]]
        year = _expand_year(m.group(3), default_year)
        return f"{year:04d}-{month:02d}-{int(m.group(1)):02d}" if year else None
    m = _TEXT_MDY.match(s)
    if m and m.group(1).lower()[:3] in _MONTHS:
        month = _MONTHS[m.group(1).lower()[:3]]
        year = _expand_year(m.group(3), default_year)
        return f"{year:04d}-{month:02d}-{int(m.group(2)):02d}" if year else None
    m = _NUMERIC_DATE.match(s)
    if not m:
        return None
    a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
    if a > 999:  # YYYY-mm-dd
        if c is None:
            return None
        return _validated(a, b, int(c))
    year = _expand_year(c, default_year)
    if year is None:
        return None
    eff = order
    if eff is None:
        if a > 12 >= b:
            eff = "DMY"
        elif b > 12 >= a:
            eff = "MDY"
        else:
            return None  # genuinely ambiguous — caller escalates, never guesses
    day, month = (a, b) if eff == "DMY" else (b, a)
    return _validated(year, month, day)


def _validated(year: int, month: int, day: int) -> str | None:
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None
