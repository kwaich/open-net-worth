# Problem reports are user-initiated, previewed, and redacted by two twin implementations

- **Date**: 2026-10-04
- **Status**: Accepted
- **Area**: Privacy / support (`backend/core/report_redact.py`, `backend/services/problem_report.py`,
  `backend/api/problem_reports.py`, `desktop/src/main/problem-report.ts`, `report-dialog.ts`,
  `frontend/src/components/ReportProblemDialog.tsx`; receiving side in the separate `onw-cloud` repo)
- **Agent provenance**: Claude (Opus 5.5), worktree `agent-a52eab83b7539bdd7`, owner brief 2026-10-04
  ("Report a problem" flow).

## Context

The product promise is "no account, no upload, no telemetry". Support still needs a way to learn
what broke. Logs hold the useful detail and can also hold money, names, folders and credentials.
The desktop app can be in states where no book (and so no backend) is running: Welcome, the folder
chooser, setup, the recovery pages.

## Decision

1. **A report is built only by an explicit user action and sent only by Send.** No crash hook, no
   queue, no background retry. Every surface (menu, Settings, error buttons) only OPENS the dialog.
2. **The preview is the payload.** The dialog edits one object; "Show the exact data" prints it;
   Send posts `JSON.stringify` of it. Validation (`ProblemReport` / `validateReport`) never rewrites
   a field, so preview == sent is a tested equality, not a convention.
3. **The desktop main process owns the dialog and the report** (a `data:` page with its own preload
   and isolated session, no network of its own), because the logs and the recovery pages live
   there. The browser path has the backend assemble and redact (`POST /api/problem-reports/draft`)
   and post (`/send`) — a browser cannot reach the portal itself (CORS, `connect-src 'self'`).
4. **Redaction has two implementations and one set of golden cases.** The desktop reads logs before
   any book is open, so it cannot call the backend; the backend cannot be called when it is down.
   `tests/fixtures/problem_report_redaction.json` is run by both the Python and the TypeScript test
   suites. Redaction is pattern-based and best-effort; the preview, not the redactor, is the
   guarantee. Multi-line log entries that are not tracebacks (model replies) are dropped outright.
5. **One destination constant per runtime**, `REPORTS_ENDPOINT` (`core/problem_report.py`,
   `desktop/src/main/report-config.ts`), a test holds them equal. `ONW_REPORTS_URL` overrides it
   (https, or loopback http); the send endpoint never takes a URL from a request.
6. **When sending is impossible the report is never lost:** Copy report, and Email instead (a
   `mailto:` to the support address with the top of the report, the full text on the clipboard).

## Consequences

- A change to a redaction rule is a change to the shared fixture and BOTH implementations.
- A server that is down cannot mask or send; the dialog says so and offers copy/email only.
- The portal keeps only a keyed hash of the sender's IP, for rate limiting.
