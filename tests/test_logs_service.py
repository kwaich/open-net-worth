"""
Unit tests for backend.services.logs.tail_log (P2-LOGS).

The service is a pure file reader — all tests use tmp_path to write synthetic
log files so they never touch the live app.log.  No network, no LLM, no full app.

Run:
    pytest tests/test_logs_service.py -v
"""

from __future__ import annotations

from pathlib import Path

from backend.services.logs import (
    _MAX_LINE_BYTES,
    _MAX_TOTAL_BYTES,
    MAX_LINES,
    _truncate_line,
    tail_log,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _write_log(path: Path, lines: list[str]) -> Path:
    """Write *lines* to *path* in the same format the RotatingFileHandler uses."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _make_lines(n: int, *, level: str = "INFO", prefix: str = "backend") -> list[str]:
    return [f"2026-06-07T12:{i:02d}:00 {level:<8} {prefix}.service line {i}" for i in range(n)]


# ── missing file ──────────────────────────────────────────────────────────────


def test_missing_file_returns_empty(tmp_path):
    result = tail_log(n=10, log_file=tmp_path / "nonexistent.log")
    assert result == []


def test_empty_file_returns_empty(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("", encoding="utf-8")
    result = tail_log(n=10, log_file=log)
    assert result == []


# ── last-N semantics ──────────────────────────────────────────────────────────


def test_returns_all_lines_when_file_smaller_than_n(tmp_path):
    log = _write_log(tmp_path / "app.log", _make_lines(5))
    result = tail_log(n=10, log_file=log)
    assert len(result) == 5


def test_returns_last_n_lines(tmp_path):
    all_lines = _make_lines(50)
    log = _write_log(tmp_path / "app.log", all_lines)
    result = tail_log(n=10, log_file=log)
    assert len(result) == 10
    # The last 10 lines should be lines 40-49.
    assert result[0] == all_lines[40]
    assert result[-1] == all_lines[49]


def test_oldest_first_order(tmp_path):
    lines = _make_lines(20)
    log = _write_log(tmp_path / "app.log", lines)
    result = tail_log(n=5, log_file=log)
    assert result == lines[15:]  # oldest (line 15) to newest (line 19)


def test_large_file_last_n(tmp_path):
    """Exercises the reverse-chunk walk (file > 4 * _CHUNK = 16 KB)."""
    # 1000 lines at ~60 chars each ≈ 60 KB — well above the 16 KB small-file threshold.
    lines = _make_lines(1000)
    log = _write_log(tmp_path / "app.log", lines)
    result = tail_log(n=50, log_file=log)
    assert len(result) == 50
    assert result[-1] == lines[-1]
    assert result[0] == lines[950]


# ── MAX_LINES clamp ───────────────────────────────────────────────────────────


def test_n_clamped_to_max_lines(tmp_path):
    lines = _make_lines(MAX_LINES + 100)
    log = _write_log(tmp_path / "app.log", lines)
    result = tail_log(n=MAX_LINES + 9999, log_file=log)
    assert len(result) <= MAX_LINES


# ── level filter ─────────────────────────────────────────────────────────────


def _mixed_log(tmp_path: Path) -> tuple[Path, list[str]]:
    rows = [
        "2026-06-07T12:00:00 INFO     backend.main GET / -> 200  (5ms)",
        "2026-06-07T12:00:01 WARNING  backend.service watch limit hit",
        "2026-06-07T12:00:02 INFO     backend.ledger tx created",
        "2026-06-07T12:00:03 ERROR    backend.ingest uid jump: 42 -> 99",
        "2026-06-07T12:00:04 INFO     backend.main GET /api/portfolio -> 200  (10ms)",
        "2026-06-07T12:00:05 ERROR    backend.main GET /api/prices -> 500  (200ms)",
    ]
    path = tmp_path / "app.log"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path, rows


def test_level_filter_info_only(tmp_path):
    log, rows = _mixed_log(tmp_path)
    result = tail_log(n=100, level="INFO", log_file=log)
    assert len(result) == 3
    assert all("INFO" in line for line in result)


def test_level_filter_error_only(tmp_path):
    log, rows = _mixed_log(tmp_path)
    result = tail_log(n=100, level="ERROR", log_file=log)
    assert len(result) == 2
    assert all("ERROR" in line for line in result)


def test_level_filter_warning_only(tmp_path):
    log, rows = _mixed_log(tmp_path)
    result = tail_log(n=100, level="WARNING", log_file=log)
    assert len(result) == 1


def test_level_filter_case_insensitive_input(tmp_path):
    """The API normalises to upper-case; service should handle both."""
    log, _ = _mixed_log(tmp_path)
    result_upper = tail_log(n=100, level="ERROR", log_file=log)
    result_lower = tail_log(n=100, level="error", log_file=log)
    assert result_upper == result_lower


# ── substring filter (q) ──────────────────────────────────────────────────────


def test_q_filter_matches_substring(tmp_path):
    log, _ = _mixed_log(tmp_path)
    result = tail_log(n=100, q="uid jump", log_file=log)
    assert len(result) == 1
    assert "uid jump" in result[0]


def test_q_filter_case_insensitive(tmp_path):
    log, _ = _mixed_log(tmp_path)
    result_lower = tail_log(n=100, q="get /api", log_file=log)
    result_upper = tail_log(n=100, q="GET /api", log_file=log)
    assert result_lower == result_upper


def test_q_filter_no_match_returns_empty(tmp_path):
    log, _ = _mixed_log(tmp_path)
    result = tail_log(n=100, q="xyzzynotpresent", log_file=log)
    assert result == []


# ── combined level + q filter ─────────────────────────────────────────────────


def test_combined_level_and_q(tmp_path):
    log, _ = _mixed_log(tmp_path)
    # Only ERROR lines containing "uid"
    result = tail_log(n=100, level="ERROR", q="uid", log_file=log)
    assert len(result) == 1
    assert "uid jump" in result[0]


# ── bounded return regardless of filters ──────────────────────────────────────


def test_returns_at_most_n_after_filtering(tmp_path):
    # 100 INFO lines — asking for 10 should give 10 (not all 100).
    lines = _make_lines(100, level="INFO")
    log = _write_log(tmp_path / "app.log", lines)
    result = tail_log(n=10, level="INFO", log_file=log)
    assert len(result) == 10


# ── per-line truncation (MEDIUM security fix) ─────────────────────────────────


def test_truncate_line_short_line_unchanged():
    """Lines within the cap are returned verbatim."""
    short = "2026-06-07T12:00:00 INFO backend.main GET / -> 200"
    assert _truncate_line(short) == short


def test_truncate_line_long_line_is_truncated():
    """A line longer than _MAX_LINE_BYTES (8 KB) is cut and marked."""
    # Build a line that is definitely over the cap.
    long_line = "A" * (_MAX_LINE_BYTES + 1000)
    result = _truncate_line(long_line)
    assert result.endswith("…[truncated]")
    # The raw byte count of the kept prefix must not exceed the cap.
    kept = result[: -len("…[truncated]")]
    assert len(kept.encode("utf-8")) <= _MAX_LINE_BYTES


def test_tail_log_long_line_file_small(tmp_path):
    """tail_log caps each line from a small file (slurp path)."""
    # One normal line and one enormous line (no newline within it).
    normal = "2026-06-07T12:00:00 INFO backend.main normal line"
    big = "B" * (_MAX_LINE_BYTES * 2)
    log = tmp_path / "app.log"
    log.write_text(normal + "\n" + big + "\n", encoding="utf-8")

    result = tail_log(n=10, log_file=log)
    assert len(result) == 2
    # The big line must be capped.
    assert len(result[1].encode("utf-8")) <= _MAX_LINE_BYTES + len("…[truncated]".encode())
    assert result[1].endswith("…[truncated]")


def test_tail_log_long_line_file_large(tmp_path):
    """tail_log caps each line from a large file (reverse-chunk path)."""
    # Build a file big enough to trigger the chunk-walk path (> 4 * _CHUNK = 16 KB).
    # Use 500 normal lines + one huge line at the end.
    normal_lines = _make_lines(500)
    big_line = "C" * (_MAX_LINE_BYTES * 2)
    log = tmp_path / "app.log"
    log.write_text("\n".join(normal_lines) + "\n" + big_line + "\n", encoding="utf-8")

    result = tail_log(n=10, log_file=log)
    assert len(result) == 10
    # The last element is the truncated big line.
    assert result[-1].endswith("…[truncated]")
    assert len(result[-1].encode("utf-8")) <= _MAX_LINE_BYTES + len("…[truncated]".encode())


def test_tail_log_total_bytes_cap(tmp_path):
    """When all lines together exceed _MAX_TOTAL_BYTES, oldest are dropped."""
    # Each line is ~(_MAX_LINE_BYTES) bytes; 3 such lines exceed the 1 MiB cap.
    big_line = "D" * (_MAX_LINE_BYTES - 1)  # just under per-line cap
    lines = [big_line] * ((_MAX_TOTAL_BYTES // _MAX_LINE_BYTES) + 5)
    log = tmp_path / "app.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = tail_log(n=MAX_LINES, log_file=log)
    total_bytes = sum(len(ln.encode("utf-8")) for ln in result)
    assert total_bytes <= _MAX_TOTAL_BYTES, (
        f"Total bytes {total_bytes} exceeds cap {_MAX_TOTAL_BYTES}"
    )
