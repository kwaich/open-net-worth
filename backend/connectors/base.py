"""Base types for connectors (inspired by OpenClaw's composition + Hermes's BasePlatformAdapter).

`BaseChannelAdapter` is the ONE outbound contract every channel adapter subclasses. It owns the
three concerns that differ per platform and previously lived nowhere coherent:

  * **format** — canonical markdown → the platform's dialect (`connectors.msgfmt.render`), gated by
    the adapter's capability flags (`supports_markdown` / `supports_links` / `supports_code_blocks`).
  * **length** — each platform's own max message size, measured in the platform's own units
    (Telegram counts UTF-16 code units, not Python code-points — `uses_utf16_len`).
  * **chunk** — splitting an over-length message WITHOUT breaking a tag/fence across the boundary
    (a Telegram HTML message with an unbalanced `<a>`/`<b>` is a hard 400; a split code fence is a
    garbled block). `chunk_message` closes and reopens open tags/fences at each cut.

A subclass declares its flags + limits and implements ONE transport method, `_deliver(channel_id,
part)`; it inherits `format_message` / `prepare` / `send_text`. Channel-specific concerns (inbound
parse, auth, allowlist, proposed-change buttons) stay on the subclass — this base is outbound-text
only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.connectors.msgfmt import render


@dataclass
class ChannelCapabilities:
    native_approval: bool = False  # inline confirm/discard buttons
    markdown: bool = False  # supports rich text / markdown
    media: bool = False  # can receive images / files


def utf16_len(s: str) -> int:
    """Length in UTF-16 code units — how Telegram (and some others) measure a message, so an
    emoji/CJK-heavy message isn't under-counted against the platform cap."""
    return len(s.encode("utf-16-le")) // 2


_HTML_SPLIT_RE = re.compile(r"(<[^>]+>)")
_TAG_NAME_RE = re.compile(r"<\s*(/?)\s*([A-Za-z][\w-]*)")
_VOID_TAGS = frozenset({"br", "hr", "img"})
_CHUNK_RESERVE = 40  # headroom per chunk for reopen prefix + close suffix


def _tag_name(tag: str) -> tuple[str, str]:
    m = _TAG_NAME_RE.match(tag)
    return (m.group(1), m.group(2).lower()) if m else ("", "")


def _close_suffix(stack: list[str]) -> str:
    return "".join(f"</{_tag_name(t)[1]}>" for t in reversed(stack))


def _apply_tag(stack: list[str], tag: str) -> None:
    closing, name = _tag_name(tag)
    if not name or name in _VOID_TAGS or tag.endswith("/>"):
        return
    if closing:
        for i in range(len(stack) - 1, -1, -1):
            if _tag_name(stack[i])[1] == name:
                stack.pop(i)
                return
    else:
        stack.append(tag)


def _chunk_html(text: str, max_length: int, len_fn) -> list[str]:
    """Split HTML text so every emitted chunk has BALANCED tags — open tags at a cut are closed and
    reopened in the next chunk (the `<a href>` reopen carries its full attributes). Splits only at
    tag boundaries or whitespace, never inside a `<...>` literal."""
    tokens = [t for t in _HTML_SPLIT_RE.split(text) if t]
    chunks: list[str] = []
    cur = ""
    stack: list[str] = []
    budget = max_length - _CHUNK_RESERVE

    def flush() -> None:
        nonlocal cur
        chunks.append(cur + _close_suffix(stack))
        cur = "".join(stack)

    for tok in tokens:
        if tok.startswith("<") and tok.endswith(">"):
            if cur.strip() and len_fn(cur + tok) + len_fn(_close_suffix(stack)) > budget:
                flush()
            cur += tok
            _apply_tag(stack, tok)
            continue
        for piece in re.split(r"(\s+)", tok):
            if not piece:
                continue
            if cur.strip() and len_fn(cur + piece) + len_fn(_close_suffix(stack)) > budget:
                flush()
            cur += piece
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def _chunk_fenced(text: str, max_length: int, len_fn) -> list[str]:
    """Split markdown/plain text preferring newline boundaries, reopening a ``` code fence across a
    cut so a split block isn't garbled (Hermes's fence-carry trick)."""
    if len_fn(text) <= max_length:
        return [text]
    budget = max_length - _CHUNK_RESERVE
    chunks: list[str] = []
    remaining = text
    carry_fence = False
    while remaining:
        prefix = "```\n" if carry_fence else ""
        room = budget - len_fn(prefix)
        if len_fn(remaining) <= room:
            chunks.append(prefix + remaining)
            break
        cut = remaining.rfind("\n", 0, room)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, room)
        if cut <= 0:
            cut = room
        head = prefix + remaining[:cut]
        # Odd number of fences in this chunk → it ends inside a code block; close + carry.
        carry_fence = head.count("```") % 2 == 1
        if carry_fence:
            head += "\n```"
        chunks.append(head)
        remaining = remaining[cut:].lstrip("\n")
    return [c for c in chunks if c]


def chunk_message(text: str, max_length: int, len_fn=len, *, html: bool = False) -> list[str]:
    """Split ``text`` into ≤``max_length`` parts (measured by ``len_fn``) without breaking a tag
    (html) or code fence (markdown/plain) across the boundary."""
    if len_fn(text) <= max_length:
        return [text]
    return (
        _chunk_html(text, max_length, len_fn) if html else _chunk_fenced(text, max_length, len_fn)
    )


class BaseChannelAdapter:
    """Outbound-text contract shared by every channel adapter. Declare the capability/limit class
    attributes + implement ``_deliver``; inherit format/chunk/send."""

    platform: str = "plain"
    max_length: int = 4096
    uses_utf16_len: bool = False
    supports_markdown: bool = True
    supports_links: bool = True
    supports_code_blocks: bool = True

    @property
    def _len(self):
        return utf16_len if self.uses_utf16_len else len

    def format_message(self, md: str) -> str:
        """Canonical markdown → this platform's dialect, degraded per capability flags."""
        if not self.supports_markdown:
            return render(md, "plain")
        return render(md, self.platform, links=self.supports_links, code=self.supports_code_blocks)

    def prepare(self, md: str) -> list[str]:
        """Format then chunk — the two-step every send goes through."""
        formatted = self.format_message(md)
        html = self.platform in ("telegram", "telegram_html") and self.supports_markdown
        return chunk_message(formatted, self.max_length, self._len, html=html)

    async def send_text(
        self, channel_id: str, text: str, suggestions: list[str] | None = None
    ) -> None:
        # `suggestions` (follow-up quick-replies) are attached to the reply by a channel that
        # supports them (Telegram overrides this); the base ignores them.
        for part in self.prepare(text):
            await self._deliver(channel_id, part)

    async def _deliver(self, channel_id: str, part: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # Whether this channel can deliver a file attachment (artifacts, exports). Default False;
    # a channel that implements ``send_document`` sets True so callers can degrade gracefully.
    supports_documents: bool = False

    async def send_document(
        self, channel_id: str, content: bytes, filename: str, caption: str = ""
    ) -> None:
        """Send ``content`` as a native file attachment (Hermes's sendDocument model). Base raises
        so a caller can catch and fall back to a text note on channels without file support."""
        raise NotImplementedError

    # Whether this channel supports live edit-in-place streaming (Telegram: editMessageText).
    # WhatsApp has no edit at all; default False keeps every other channel on buffer-and-send.
    supports_streaming: bool = False

    async def stream_send(self, channel_id: str, text: str) -> str | None:
        """Send the FIRST streaming preview; return its message id (or None on failure → the
        caller gives up streaming for the turn and buffer-sends at the end)."""
        raise NotImplementedError

    async def stream_update(self, channel_id: str, message_id: str, text: str) -> None:
        """Edit the streaming preview in place with the accumulated-so-far text. Best-effort:
        a flood/not-modified error is swallowed (the throttle lives in the caller)."""
        raise NotImplementedError

    async def stream_finalize(self, channel_id: str, message_id: str, text: str) -> None:
        """Finalize a streamed reply: an ordinary reply does a final in-place edit; a reply that
        needs rich rendering (table/math) or overflows one message replaces the preview with a
        fresh rich/chunked send (Hermes's ``prefers_fresh_final_streaming``)."""
        raise NotImplementedError

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        raise NotImplementedError

    # Continuous typing indicator during a long turn. Default: no-op / unsupported.
    supports_typing: bool = False

    async def send_typing(self, channel_id: str) -> None:
        return None

    # Native image reply. Default unsupported; a channel that can sets True + implements send_photo.
    supports_media: bool = False

    async def send_photo(self, channel_id: str, content: bytes, caption: str = "") -> None:
        raise NotImplementedError

    # Tappable follow-up suggestions after a reply (generic across connectors; Telegram renders
    # them as an inline keyboard). Default off — a connector opts in by setting the flag,
    # implementing ``send_suggestions``, and returning its own per-connector toggle from
    # ``suggestions_enabled``. The processor only fetches/sends when BOTH are true.
    supports_suggestions: bool = False

    def suggestions_enabled(self) -> bool:
        return False

    async def send_suggestions(self, channel_id: str, suggestions: list[str]) -> None:
        raise NotImplementedError
