"""Telegram Rich Messages (Bot API 10.2, `sendRichMessage`) — send the model's RAW MARKDOWN so
every construct renders natively (tables, lists, headings, quotes, code, math, details), with ZERO
capability loss and no HTML pre-render. `InputRichMessage` takes exactly one of html/markdown/blocks;
we use `markdown` and hand Telegram the agent's own markdown (the approach Hermes uses).

python-telegram-bot has no wrapper for this two-week-old method, so we call the raw Bot API. Every
failure is swallowed and reported as False so the caller falls back to the legacy sendMessage+HTML
path — a safety net for an old client / API rejection, NEVER a content-based capability gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request

from backend.core.pinned_http import https_context

logger = logging.getLogger("backend.connectors.telegram")

# --- Which markdown constructs actually NEED the rich path (copied from Hermes's
# `_needs_rich_rendering`, plugins/platforms/telegram/adapter.py). Ordinary replies (plain prose,
# bold/italic, simple lists) stay on the legacy path for consistent typography across clients; the
# rich endpoint is reserved for constructs the legacy MarkdownV2/HTML path degrades: pipe TABLES
# (no table syntax → bullet-list rewrite), GFM TASK LISTS, collapsible <details>, and block MATH.
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$")
_TASK_LIST_RE = re.compile(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+")
_DETAILS_RE = re.compile(r"(?m)^<details\b|^</details>|^<summary\b|^</summary>")


def needs_rich_rendering(content: str) -> bool:
    """True iff `content` contains a construct the legacy path degrades (Hermes's exact rule):
    a pipe-table separator line, a GFM task list, a <details>/<summary> block, or block math."""
    if not content:
        return False
    if any(_TABLE_SEPARATOR_RE.match(line) for line in content.splitlines()):
        return True
    if _TASK_LIST_RE.search(content):
        return True
    if _DETAILS_RE.search(content):
        return True
    return "$$" in content


# Client-crash / garble guards + hard cap (Hermes's `_has_telegram_desktop_*` + `_content_fits_
# rich_limits`). Content matching these must SKIP the rich path (fall back to legacy HTML) because
# it would break the RECIPIENT's client, not just fail our send.
_RICH_MAX_CHARS = 32768  # Bot API rich-message text cap
_RICH_DETAILS_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.IGNORECASE | re.DOTALL)
_RICH_MATH_RE = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|"
    r"\\(?:sum|frac|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|int|prod|sqrt|lim|infty|"
    r"begin\{(?:equation|align|matrix|cases)\})",
    re.IGNORECASE | re.DOTALL,
)
# CJK ranges — TDesktop garbles CJK in rich messages (Hermes #47653).
_CJK_RE = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")


def rich_unsafe(content: str) -> bool:
    """True when a rich message would break the RECIPIENT's client or exceed the hard cap, so the
    caller must fall back to the legacy HTML path (Hermes's up-front skip):
      * math inside a <details> block crashes Telegram Desktop 6.9.1 (tdesktop#30808),
      * CJK text garbles in TDesktop rich rendering (#47653),
      * content over the 32,768-char rich cap."""
    if not content:
        return False
    if len(content) > _RICH_MAX_CHARS:
        return True
    if any(_RICH_MATH_RE.search(b) for b in _RICH_DETAILS_RE.findall(content)):
        return True
    return bool(_CJK_RE.search(content))


def rich_payload(chat_id: str | int, rich_html: str, reply_markup: dict | None = None) -> dict:
    """The sendRichMessage JSON body: rendered rich HTML as InputRichMessage.html. HTML (not the
    raw-markdown field) so there is ONE rendering philosophy across the legacy + rich paths, and
    HTML's 3-char escaping is more reliable than markdown's for LLM output (owner directive
    2026-07-20). ``reply_markup`` (a serialized keyboard dict) rides along so a rich TABLE can still
    carry follow-up suggestions — sendRichMessage supports reply_markup, unlike editMessageText."""
    body: dict = {"chat_id": chat_id, "rich_message": {"html": rich_html}}
    if reply_markup:
        body["reply_markup"] = reply_markup
    return body


async def send_rich_html(
    token: str, chat_id: str | int, rich_html: str, reply_markup: dict | None = None
) -> bool:
    """POST one rich message (HTML body). Returns True on API ``ok``, False on ANY failure (caller
    falls back to the legacy path). The token is never logged."""
    if not token or not rich_html.strip():
        return False

    def _post() -> bool:
        url = f"https://api.telegram.org/bot{token}/sendRichMessage"
        data = json.dumps(rich_payload(chat_id, rich_html, reply_markup)).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(  # noqa: S310 fixed telegram host
            req, timeout=15, context=https_context()
        ) as resp:
            return bool(json.load(resp).get("ok"))

    try:
        return await asyncio.to_thread(_post)
    except Exception as exc:  # noqa: BLE001 — any failure → fall back to the HTML path
        logger.info(
            "telegram: sendRichMessage failed (%s) — falling back to HTML", type(exc).__name__
        )
        return False


async def edit_rich_html(
    token: str, chat_id: str | int, message_id: str | int, rich_html: str
) -> bool:
    """Edit an existing message IN PLACE into a rich message (Bot API 10.2 editMessageText
    ``rich_message`` param) — used to finalize a streamed preview into a table without deleting +
    re-sending (no flicker, message keeps its place). False on any failure so the caller can fall
    back to delete + fresh send."""
    if not token or not rich_html.strip():
        return False

    def _post() -> bool:
        url = f"https://api.telegram.org/bot{token}/editMessageText"
        data = json.dumps(
            {"chat_id": chat_id, "message_id": int(message_id), "rich_message": {"html": rich_html}}
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(  # noqa: S310 fixed telegram host
            req, timeout=15, context=https_context()
        ) as resp:
            return bool(json.load(resp).get("ok"))

    try:
        return await asyncio.to_thread(_post)
    except Exception as exc:  # noqa: BLE001
        logger.info("telegram: editMessageText(rich) failed (%s)", type(exc).__name__)
        return False
