"""Log-tail service — pure file read, no persistence (P2-LOGS).

``tail_log(n, level, q)`` is the only public function.  It reads the fixed
``LOG_FILE`` path (never a caller-supplied path — no traversal), extracts the
last ``n`` lines, and optionally filters by level and case-insensitive substring.

Design choices:
* Fixed path only — the caller cannot name an arbitrary file.
* Reads in chunks from the end of the file (a simple reverse-seek) rather than
  loading the entire file, so a large log doesn't blow resident memory.
* Secret-shaped substrings (the API token, Bearer values, provider keys, long
  base64url runs) are masked BEFORE filtering — the output goes to the model.
* Missing / unreadable file → empty list (never raises to the API layer).
* ``n`` is clamped to ``MAX_LINES`` (2 000) at the service boundary; the API
  applies the same clamp independently.
* Each line is capped at ``_MAX_LINE_BYTES`` (8 KB) — a single no-newline line
  cannot blow up the LLM context window or resident memory.
* Total returned payload is capped at ``_MAX_TOTAL_BYTES`` (~1 MB) after per-line
  truncation; lines are dropped from the oldest end if the cap is hit.
* Lines are returned oldest-first (the natural log order); callers that want
  newest-first should reverse.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from backend.core.paths import LOG_FILE
from backend.core.redact import redact_token_shapes

# Hard ceiling — prevents accidentally loading 100k lines via the API.
MAX_LINES = 2_000

# Chunk size for the reverse-read walk.  4 KB comfortably holds ~20 lines;
# we read in multiples of this until we have enough.
_CHUNK = 4_096

# Per-line cap (bytes).  A single log line should never exceed this; if it does
# it is likely binary junk or an accidental very long stack frame.  Truncated
# lines get an "…[truncated]" marker so the reader knows data was dropped.
_MAX_LINE_BYTES = 8_192

# Total payload cap.  2 000 lines × 8 KB = 16 MB theoretical max; cap at 1 MB
# so the response stays manageable for both the HTTP layer and the LLM context.
_MAX_TOTAL_BYTES = 1_048_576  # 1 MiB


def tail_log(
    n: int = 200,
    level: Optional[str] = None,
    q: Optional[str] = None,
    *,
    log_file: Optional[Path] = None,
) -> list[str]:
    """Return the last *n* lines from the application log file.

    Args:
        n:        Number of lines to return.  Clamped to ``MAX_LINES``.
        level:    If given (case-insensitive), only lines whose second
                  whitespace-separated token matches this level label
                  (e.g. ``"INFO"``, ``"WARNING"``, ``"ERROR"``) are included.
                  Matching is exact (not prefix) — ``"WARN"`` does NOT match
                  ``"WARNING"`` since Python uses the full names.
        q:        If given, only lines that contain this substring
                  (case-insensitive) are included.
        log_file: Override the file path (test injection only).

    Returns:
        A list of log line strings, oldest-first, with trailing newlines
        stripped.  Each line is at most ``_MAX_LINE_BYTES`` wide (longer lines
        are truncated with an ``…[truncated]`` marker).  Returns an empty list
        if the file is missing or unreadable.
    """
    n = max(1, min(n, MAX_LINES))
    path = log_file if log_file is not None else LOG_FILE

    try:
        raw_lines = _read_last_n_raw(path, n, level=level, q=q)
    except OSError:
        # Missing file, permission error, etc. — degrade gracefully.
        return []

    return raw_lines


# ── Internal helpers ──────────────────────────────────────────────────────────


def _known_secrets() -> tuple[str, ...]:
    """Secrets a log line must never repeat verbatim — read once per tail, not per line."""
    try:
        from backend.db.auth_store import AuthStore

        return (AuthStore().api_token(),)
    except Exception:  # noqa: BLE001 — redaction falls back to shape-matching alone
        return ()


def _clean_line(line: str, secrets: tuple[str, ...]) -> str:
    """Cap the line, then redact what is left of it. This runs BEFORE the ``q``/level filters,
    so a filter can never be used as a substring oracle against a secret; a token cut by the
    cap is masked over its surviving prefix."""
    return redact_token_shapes(_truncate_line(line), secrets)


def _truncate_line(line: str) -> str:
    """Truncate *line* to ``_MAX_LINE_BYTES``, appending a marker when cut."""
    encoded = line.encode("utf-8")
    if len(encoded) <= _MAX_LINE_BYTES:
        return line
    # Slice to cap, then decode safely (errors=replace handles mid-char cuts).
    return encoded[:_MAX_LINE_BYTES].decode("utf-8", errors="replace") + "…[truncated]"


def _apply_total_cap(lines: list[str]) -> list[str]:
    """Drop oldest lines until the total UTF-8 byte size fits ``_MAX_TOTAL_BYTES``."""
    total = sum(len(ln.encode("utf-8")) for ln in lines)
    while lines and total > _MAX_TOTAL_BYTES:
        dropped = lines.pop(0)
        total -= len(dropped.encode("utf-8"))
    return lines


def _read_last_n_raw(
    path: Path,
    n: int,
    *,
    level: Optional[str],
    q: Optional[str],
) -> list[str]:
    """Read lines from *path*, applying optional filters, returning the last *n*.

    Uses a reverse-chunk walk to avoid loading the full file for large logs.
    Falls back to a simple readall when the file is small.
    """
    # Normalise filter args once.
    level_upper = level.upper() if level else None
    q_lower = q.lower() if q else None

    secrets = _known_secrets()
    size = os.path.getsize(path)
    if size == 0:
        return []

    # For small files just slurp everything; the chunk walk has overhead and
    # seeking backward in a 10-line file isn't worth it.
    if size <= _CHUNK * 4:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = [_clean_line(ln.rstrip("\n\r"), secrets) for ln in text.splitlines()]
        result = _apply_filters(lines, n, level_upper, q_lower)
        return _apply_total_cap(result)

    # Reverse-chunk walk: read blocks from the end until we have > n matching
    # lines (or exhaust the file), then slice.
    buf: list[str] = []
    pos = size
    leftover = b""

    with path.open("rb") as fh:
        while pos > 0:
            read_size = min(_CHUNK, pos)
            pos -= read_size
            fh.seek(pos)
            chunk = fh.read(read_size) + leftover
            # Split into lines; the first element may be a partial line.
            parts = chunk.split(b"\n")
            leftover = parts[0]  # incomplete line at the start of the chunk
            # The rest are complete lines — prepend them in reverse order.
            for raw in reversed(parts[1:]):
                line = _clean_line(raw.decode("utf-8", errors="replace").rstrip("\r"), secrets)
                if _matches(line, level_upper, q_lower):
                    buf.append(line)
                if len(buf) >= n:
                    break
            if len(buf) >= n:
                break

        if leftover:
            line = _clean_line(leftover.decode("utf-8", errors="replace").rstrip("\r"), secrets)
            if len(buf) < n and _matches(line, level_upper, q_lower):
                buf.append(line)

    # buf is newest-first; reverse to oldest-first before returning.
    buf.reverse()
    result = buf[-n:]
    return _apply_total_cap(result)


def _apply_filters(
    lines: list[str],
    n: int,
    level_upper: Optional[str],
    q_lower: Optional[str],
) -> list[str]:
    """Filter a pre-loaded line list and return the last *n* matches."""
    filtered = [ln for ln in lines if _matches(ln, level_upper, q_lower)]
    return filtered[-n:]


def _matches(line: str, level_upper: Optional[str], q_lower: Optional[str]) -> bool:
    """True if the line passes both (optional) filters."""
    if not line:
        return False
    if level_upper is not None:
        # Log line format: "2026-06-07T12:34:56 LEVELNAME  logger.name message"
        # The level token is the second whitespace-separated field.
        parts = line.split()
        if len(parts) < 2 or parts[1].rstrip(":").upper() != level_upper:
            return False
    if q_lower is not None and q_lower not in line.lower():
        return False
    return True
