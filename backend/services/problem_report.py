"""Problem reports — assemble a redacted draft for the page to preview, and send what the user
approved.

Nothing here runs on its own: the draft is built when the user opens "Report a problem", and the
send happens only when they press Send on the previewed text. The draft carries the application
log tail with money, names, folders and credentials masked (``core.report_redact``); the book
database and attachments are never read.

Layer: services. Reads the account and payee names (``db``) only to mask them out of the logs.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from backend.core.errors import ConfigurationError, ExternalServiceError
from backend.core.paths import DATA_DIR, LOGS_DIR
from backend.core.pinned_http import https_context, strip_auto_redirect
from backend.core.problem_report import reports_endpoint
from backend.core.report_redact import MAX_LOG_LINES, Redactor
from backend.core.version import APP_VERSION
from backend.db.account_store import AccountStore
from backend.db.payee_store import PayeeStore
from backend.models.problem_report import ProblemReport, ProblemReportDraftRequest
from backend.services.logs import tail_log

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 15
# More raw lines than we keep: free-text continuation lines are dropped before the cap applies.
_RAW_LOG_LINES = 800
_MAX_NAME_TERMS = 1000
# Order matters: Edge's user agent also says Chrome and Safari, Chrome's also says Safari.
_BROWSERS = (("Edg", "Edge"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("Safari", "Safari"))


def _longest(terms: list[str]) -> list[str]:
    return sorted({t for t in terms if t}, key=len, reverse=True)[:_MAX_NAME_TERMS]


def _names() -> tuple[list[str], list[str]]:
    accounts = [a.get("name") or "" for a in AccountStore().all()]
    payees: list[str] = []
    for row in PayeeStore().all():
        payees.append(row.get("name") or "")
        payees.extend(row.get("aliases") or [])
    return _longest(accounts), _longest(payees)


def redaction_terms() -> dict[str, list[str]]:
    """The book's account and payee names, for a caller (the desktop shell) that reads the logs itself."""
    accounts, payees = _names()
    return {"accounts": accounts, "payees": payees}


def _known_paths() -> list[tuple[str, str]]:
    return [(str(Path.home()), "~"), (str(DATA_DIR), "<data>"), (str(LOGS_DIR), "<logs>")]


def _browser(user_agent: str) -> str:
    for token, name in _BROWSERS:
        match = re.search(rf"\b{token}/(\d+)", user_agent)
        if match:
            return f"{name} {match.group(1)}"
    return ""


def _platform(user_agent: str) -> str:
    system = f"{platform.system()} {platform.machine()} {platform.release()}".strip()
    browser = _browser(user_agent)
    return f"{system}; {browser}" if browser else system


def build_draft(request: ProblemReportDraftRequest, *, remote: bool, user_agent: str = "") -> dict:
    """The report as it will be sent, minus the description the user has yet to write.

    ``remote`` is whether the browser reached this server from another machine: a book served
    to its own machine is "web", one served over a network is "server"."""
    accounts, payees = _names()
    redactor = Redactor(accounts=accounts, payees=payees, paths=_known_paths())
    logs = None
    if request.include_logs:
        logs = "\n".join(redactor.log_lines(tail_log(n=_RAW_LOG_LINES), limit=MAX_LOG_LINES))
    return {
        "description": "",
        "contact_email": None,
        "app_version": APP_VERSION,
        "platform": _platform(user_agent)[:128],
        "mode": "server" if remote else "web",
        "screen": redactor.text(request.screen),
        "error": redactor.text(request.error) if request.error else None,
        "logs": logs,
    }


def _endpoint() -> str:
    url = reports_endpoint()
    parts = urllib.parse.urlsplit(url)
    local = (parts.hostname or "") in {"localhost", "127.0.0.1", "::1"}
    insecure = parts.scheme != "https" and not (parts.scheme == "http" and local)
    if insecure or parts.username or parts.password:
        raise ConfigurationError("The report address must be an https URL without credentials.")
    return url


def _refusal(status: int) -> str:
    if status == 429:
        return "Too many reports from this connection. Try again in a while."
    if status in (400, 413, 422):
        return "The report server refused this report as too large or malformed. Shorten it and try again."
    return "The report server could not take this report. Try again."


def send_report(report: ProblemReport) -> dict:
    """POST exactly ``report`` — the previewed, user-edited payload — to the report endpoint."""
    url = _endpoint()
    body = report.model_dump_json().encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — operator-configured URL, scheme checked above
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=https_context()))
    strip_auto_redirect(opener)
    try:
        with opener.open(request, timeout=_SEND_TIMEOUT_S) as response:
            payload = json.loads(response.read(4096) or b"{}")
    except urllib.error.HTTPError as exc:
        logger.warning("problem report refused: status=%s", exc.code)
        raise ExternalServiceError(_refusal(exc.code)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("problem report not delivered: %s", type(exc).__name__)
        raise ExternalServiceError(
            "Couldn't reach the report server. Check your connection."
        ) from exc
    return {"sent": True, "id": payload.get("id") if isinstance(payload, dict) else None}
