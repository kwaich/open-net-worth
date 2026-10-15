"""Redaction golden cases for problem reports.

The desktop shell's TypeScript redactor runs the SAME file (desktop/tests/report-redact.test.ts):
the two implementations may differ in code, never in behaviour.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.core.problem_report import REPORTS_ENDPOINT, SUPPORT_EMAIL
from backend.core.report_redact import redact_log_lines, redact_text

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads(
    (ROOT / "tests/fixtures/problem_report_redaction.json").read_text(encoding="utf-8")
)


def _context(case: dict) -> dict:
    ctx = case["context"]
    return {
        "accounts": ctx["accounts"],
        "payees": ctx["payees"],
        "paths": [tuple(p) for p in ctx["paths"]],
    }


@pytest.mark.parametrize("case", VECTORS["text"], ids=lambda c: c["name"])
def test_text_vectors(case):
    assert redact_text(case["input"], **_context(case)) == case["expected"]


@pytest.mark.parametrize("case", VECTORS["logs"], ids=lambda c: c["name"])
def test_log_vectors(case):
    assert (
        redact_log_lines(case["input"], limit=case["limit"], **_context(case)) == case["expected"]
    )


def test_redaction_is_idempotent():
    for case in VECTORS["text"]:
        once = redact_text(case["input"], **_context(case))
        assert redact_text(once, **_context(case)) == once


def test_a_report_never_carries_the_raw_secret_or_amount():
    secret = "sk-" + "live-abcdefghijklmnop12345"
    line = (
        f"2026-10-04T10:00:00 INFO backend.x moved $48,210.55 for jane@example.com token={secret}"
    )
    [out] = redact_log_lines([line])
    for leaked in ("48,210", "jane@example.com", secret):
        assert leaked not in out


def test_desktop_and_backend_agree_on_where_reports_go():
    config = (ROOT / "desktop/src/main/report-config.ts").read_text(encoding="utf-8")
    assert re.search(r"REPORTS_ENDPOINT = '([^']+)'", config).group(1) == REPORTS_ENDPOINT
    assert re.search(r"SUPPORT_EMAIL = '([^']+)'", config).group(1) == SUPPORT_EMAIL
