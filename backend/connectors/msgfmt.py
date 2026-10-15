"""Canonical-markdown → per-channel dialect renderer (the multi-channel formatting layer).

The assistant emits ONE canonical markdown string; each outbound channel renders it to that
platform's own flavour — the pattern Hermes Agent uses (a per-adapter ``format_message``) and
OpenClaw uses (a shared markdown core rendered per target).

Scope is the INLINE + code surface that actually differs per platform and that the model actually
emits: bold, italic, inline code, fenced code blocks, and links. Block structure (headings, bullets,
tables) is passed through untouched — every target renders a ``- item`` / ``# H`` line acceptably as
text, and mangling them per-dialect is not worth the risk. Telegram is delegated to its existing,
tested formatter unchanged, so this module never alters the one channel that already worked.

Dialects:
  * telegram → ``<b>`` / ``<i>`` / ``<code>`` / ``<a href>`` (via connectors.telegram.formatter)
  * plain    → markers stripped, ``text (url)`` for links (SMS / unknown / fallback)
"""

from __future__ import annotations

import html
import re

_CODEBLOCK_RE = re.compile(r"```([^\n`]*)\n?(.*?)```", re.DOTALL)
_CODESPAN_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Single * or _ emphasis, not mid-word (so a snake_case token / a stray * isn't italicised)
_ITALIC_RE = re.compile(r"(?<!\w)[_*](?=\S)(.+?)(?<=\S)[_*](?!\w)")


# Per-target dialect: (bold_open, bold_close), (italic_open, italic_close), (code_open, code_close),
# link(text, url) → str, fence(lang, body) → str, escape(text) → str | None.
_DIALECTS: dict[str, dict] = {
    "plain": {
        "b": ("", ""),
        "i": ("", ""),
        "code": ("", ""),
        "link": lambda t, u: f"{t} ({u})",
        "fence": lambda lang, body: body,
        "escape": None,
    },
    # Email + Matrix are both HTML dialects — text is html-escaped, spans become real tags. Email
    # renders FULL HTML (any client); Matrix custom HTML (`org.matrix.custom.html`) is a subset that
    # covers exactly these tags. They differ only in newline handling, applied in `render()`: email
    # wraps in a `white-space:pre-wrap` div (preserve \n); Matrix converts \n → <br/> outside <pre>.
    "email": {
        "b": ("<strong>", "</strong>"),
        "i": ("<em>", "</em>"),
        "code": ("<code>", "</code>"),
        "link": lambda t, u: f'<a href="{html.escape(u, quote=True)}">{t}</a>',
        "fence": lambda lang, body: f"<pre>{html.escape(body)}</pre>",
        "escape": html.escape,
    },
    "matrix": {
        "b": ("<strong>", "</strong>"),
        "i": ("<em>", "</em>"),
        "code": ("<code>", "</code>"),
        "link": lambda t, u: f'<a href="{html.escape(u, quote=True)}">{t}</a>',
        "fence": lambda lang, body: f"<pre>{html.escape(body)}</pre>",
        "escape": html.escape,
    },
}


def _matrix_line_breaks(html_text: str) -> str:
    """Convert newlines to <br/> OUTSIDE <pre> blocks (inside a <pre>, whitespace is already
    preserved, so a <br/> would double the break)."""
    parts = re.split(r"(<pre>.*?</pre>)", html_text, flags=re.DOTALL)
    return "".join(p if p.startswith("<pre>") else p.replace("\n", "<br/>\n") for p in parts)


def _inline(text: str, d: dict) -> str:
    """Render inline spans (links, bold, italic) of one non-code-block text run to dialect ``d``.
    Each span is stashed behind a NUL sentinel as it's rendered, so a later pass (bold/italic
    regex, or the whole-run escape) can't re-touch already-rendered marker characters — the same
    stash trick the Telegram formatter uses for links."""
    esc = d["escape"]
    stash: list[str] = []

    def _put(rendered: str) -> str:
        stash.append(rendered)
        return f"\x00S{len(stash) - 1}\x00"

    def _esc(s: str) -> str:
        return esc(s) if esc else s

    # Inline code FIRST — its content is literal, never bold/italic/link-processed.
    text = _CODESPAN_RE.sub(lambda m: _put(f"{d['code'][0]}{_esc(m.group(1))}{d['code'][1]}"), text)
    text = _LINK_RE.sub(lambda m: _put(d["link"](_esc(m.group(1)), m.group(2))), text)
    text = _BOLD_RE.sub(lambda m: _put(f"{d['b'][0]}{_esc(m.group(1))}{d['b'][1]}"), text)
    text = _ITALIC_RE.sub(lambda m: _put(f"{d['i'][0]}{_esc(m.group(1))}{d['i'][1]}"), text)
    if esc:
        text = esc(text)
    # Restore sentinels; loop because a bold/link sentinel can nest inside another span's output.
    for _ in range(4):
        if "\x00S" not in text:
            break
        for i, rendered in enumerate(stash):
            text = text.replace(f"\x00S{i}\x00", rendered)
    return text


def _render_dialect(text: str, d: dict) -> str:
    """Split off fenced code blocks (rendered verbatim per dialect, never inline-processed), and
    inline-render everything between them."""
    out: list[str] = []
    pos = 0
    for m in _CODEBLOCK_RE.finditer(text):
        out.append(_inline(text[pos : m.start()], d))
        out.append(d["fence"]((m.group(1) or "").strip(), m.group(2).rstrip("\n")))
        pos = m.end()
    out.append(_inline(text[pos:], d))
    return "".join(out)


def render(text: str, target: str, *, links: bool = True, code: bool = True) -> str:
    """Render canonical markdown ``text`` to ``target``'s dialect. Telegram delegates to its own
    tested HTML formatter (unchanged). An unknown target falls back to ``plain`` (markers stripped)
    rather than shipping raw markdown.

    ``links=False`` / ``code=False`` are the graceful-degradation levers driven by an adapter's
    capability flags (``supports_links`` / ``supports_code_blocks``): a channel that can't render a
    hyperlink gets ``text (url)`` instead, one that can't render code gets the bare text — the model
    still emits the same markdown, the renderer just downgrades what a given platform can't show."""
    if not text:
        return text
    is_telegram = target in ("telegram", "telegram_html")
    # Telegram's HTML fast-path only applies at full capability; a downgrade routes it through the
    # generic dialect (plain markers) so the link/code degrade below can take effect.
    if is_telegram and links and code:
        from backend.connectors.telegram.formatter import markdown_to_telegram_html

        return markdown_to_telegram_html(text)
    base = "plain" if is_telegram else target
    d = dict(_DIALECTS.get(base, _DIALECTS["plain"]))
    if not links:
        d["link"] = _DIALECTS["plain"]["link"]
    if not code:
        d["code"] = ("", "")
        d["fence"] = _DIALECTS["plain"]["fence"]
    out = _render_dialect(text, d)
    if base == "email":
        return f'<div style="white-space:pre-wrap;font-family:sans-serif">{out}</div>'
    if base == "matrix":
        return _matrix_line_breaks(out)
    return out
