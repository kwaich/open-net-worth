"""Where a problem report goes — the one place the destination is written down.

Nothing is ever sent unless the user presses Send on a previewed report. ``ONW_REPORTS_URL``
points a self-hoster's reports at their own portal; unset, they go to Open Net Worth's.
The desktop shell keeps the same two values in ``desktop/src/main/report-config.ts``
(a test holds them equal).

Layer: leaf — stdlib only.
"""

from __future__ import annotations

import os

REPORTS_ENDPOINT = "https://cloud.opennetworth.ai/api/problem-reports"
SUPPORT_EMAIL = "support@opennetworth.ai"
REPORTS_URL_ENV = "ONW_REPORTS_URL"


def reports_endpoint() -> str:
    return os.environ.get(REPORTS_URL_ENV, "").strip() or REPORTS_ENDPOINT
