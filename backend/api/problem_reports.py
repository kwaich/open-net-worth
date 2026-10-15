"""Problem-report API — the browser's half of "Report a problem" (the desktop app builds its own).

    POST /api/problem-reports/draft   the report as it would be sent, logs redacted, for the preview
    POST /api/problem-reports/send    deliver exactly the previewed (and user-edited) report
    GET  /api/problem-reports/redaction-terms   the names the desktop shell masks in the logs it reads

Nothing here fires by itself: the page calls ``draft`` when the user opens the dialog and ``send``
only when they press Send. A thin router over ``services.problem_report``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.api._deps import _is_loopback
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.problem_report import ProblemReport, ProblemReportDraftRequest
from backend.services import problem_report

router = APIRouter(prefix="/api/problem-reports")


@router.post("/draft")
def draft(body: ProblemReportDraftRequest, request: Request):
    return problem_report.build_draft(
        body, remote=not _is_loopback(request), user_agent=request.headers.get("user-agent", "")
    )


@router.get("/redaction-terms")
def redaction_terms():
    return problem_report.redaction_terms()


@router.post("/send")
def send(body: ProblemReport):
    try:
        return problem_report.send_report(body)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message) from exc
