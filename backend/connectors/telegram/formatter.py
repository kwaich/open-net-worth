"""Text formatting utilities for the Telegram connector."""

from __future__ import annotations

import html
import re

CHUNK_SIZE = 4000  # Telegram max message length


def chunk_text(text: str) -> list[str]:
    """Split text into ≤4000-char chunks, preferring newline boundaries."""
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks = []
    while text:
        if len(text) <= CHUNK_SIZE:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, CHUNK_SIZE)
        if split_at <= 0:
            split_at = CHUNK_SIZE
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def markdown_to_telegram_html(text: str) -> str:
    """Convert AI markdown to Telegram HTML for use with ParseMode.HTML."""
    lines = text.split("\n")
    result = []
    in_code_block = False
    code_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Fenced code blocks
        if stripped.startswith("```"):
            if in_code_block:
                content = html.escape("\n".join(code_lines))
                result.append(f"<pre>{content}</pre>")
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Horizontal rules
        if re.match(r"^[-*_]{3,}$", stripped):
            result.append("─────────────")
            continue

        # Table separator rows (|---|---|)
        if re.match(r"^\|[-|\s:]+\|$", stripped):
            continue

        # Table data rows
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if len(cells) == 2:
                result.append(f"{_inline_to_html(cells[0])}: {_inline_to_html(cells[1])}")
            else:
                result.append("  ".join(_inline_to_html(c) for c in cells))
            continue

        # ATX headers (## Heading)
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            result.append(f"<b>{_inline_to_html(m.group(2))}</b>")
            continue

        result.append(_inline_to_html(line))

    # Unclosed code block fallback
    if in_code_block and code_lines:
        result.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")

    return "\n".join(result)


_RICH_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$")
_RICH_TASK_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)")
_RICH_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)")
_RICH_NUM_RE = re.compile(r"^\s*\d+\.\s+(.*)")


def _rich_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_rich_html(text: str) -> str:
    """Convert AI markdown to the EXTENDED Telegram rich-message HTML (Bot API 10.2
    ``InputRichMessage.html``) — a superset of the legacy sendMessage HTML that adds real
    ``<table>``, ``<ul>/<ol>``, task-list ``<input type=checkbox>``, ``<details>`` and
    ``<tg-math-block>``. Using HTML here (not the raw-markdown field) keeps ONE rendering
    philosophy across the legacy and rich paths (owner directive 2026-07-20). Inline spans reuse
    the same ``_inline_to_html`` the legacy path uses, so bold/italic/code/links are byte-identical."""
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):  # fenced code
            flush()
            j, code = i + 1, []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            out.append(f"<pre>{html.escape(chr(10).join(code))}</pre>")
            i = j + 1
            continue

        if stripped.startswith("$$"):  # block math
            flush()
            if stripped.count("$$") >= 2:
                out.append(f"<tg-math-block>{html.escape(stripped.strip('$'))}</tg-math-block>")
                i += 1
                continue
            j, math_lines = i + 1, []
            while j < len(lines) and "$$" not in lines[j]:
                math_lines.append(lines[j])
                j += 1
            out.append(f"<tg-math-block>{html.escape(chr(10).join(math_lines))}</tg-math-block>")
            i = j + 1
            continue

        if re.match(r"^</?(details|summary)\b", stripped, re.IGNORECASE):  # passthrough HTML
            flush()
            out.append(stripped)
            i += 1
            continue

        # pipe table: a row followed by a |---|---| separator
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and _RICH_TABLE_SEP_RE.match(lines[i + 1])
        ):
            flush()
            head = "".join(f"<th>{_inline_to_html(c)}</th>" for c in _rich_cells(line))
            rows = [f"<tr>{head}</tr>"]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                body = "".join(f"<td>{_inline_to_html(c)}</td>" for c in _rich_cells(lines[j]))
                rows.append(f"<tr>{body}</tr>")
                j += 1
            out.append("<table bordered striped>" + "".join(rows) + "</table>")
            i = j
            continue

        if _RICH_TASK_RE.match(line) or _RICH_BULLET_RE.match(line):  # bullet / task list
            flush()
            items = []
            while i < len(lines):
                mt, mb = _RICH_TASK_RE.match(lines[i]), _RICH_BULLET_RE.match(lines[i])
                if mt:
                    chk = " checked" if mt.group(1) in ("x", "X") else ""
                    items.append(
                        f'<li><input type="checkbox"{chk}> {_inline_to_html(mt.group(2))}</li>'
                    )
                elif mb:
                    items.append(f"<li>{_inline_to_html(mb.group(1))}</li>")
                else:
                    break
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if _RICH_NUM_RE.match(line):  # numbered list
            flush()
            items = []
            while i < len(lines) and _RICH_NUM_RE.match(lines[i]):
                items.append(f"<li>{_inline_to_html(_RICH_NUM_RE.match(lines[i]).group(1))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)", line)  # heading → bold line
        if m:
            flush()
            out.append(f"<p><b>{_inline_to_html(m.group(2))}</b></p>")
            i += 1
            continue

        if re.match(r"^[-*_]{3,}$", stripped):  # horizontal rule
            flush()
            out.append("<hr>")
            i += 1
            continue

        if not stripped:  # blank → paragraph break
            flush()
            i += 1
            continue

        para.append(_inline_to_html(line))
        i += 1

    flush()
    return "".join(out)


def _inline_to_html(text: str) -> str:
    """Convert inline markdown (bold, italic, code) to HTML, escaping entities."""
    # Split on inline code spans first so we don't mangle their contents
    segments = re.split(r"(`[^`\n]+`)", text)
    out = []
    for seg in segments:
        if seg.startswith("`") and seg.endswith("`") and len(seg) >= 2:
            out.append(f"<code>{html.escape(seg[1:-1])}</code>")
        else:
            s = html.escape(seg)
            # Markdown links [text](http(s)://url) → <a href>. Stashed to a sentinel FIRST so the
            # bold/italic passes below can't mangle url punctuation (a base64 Google-News redirect
            # URL is full of - and _), then restored last. text/url are already html-escaped by the
            # escape() above (html.escape leaves []() intact, and &→&amp; is correct inside href).
            links: list[tuple[str, str]] = []

            def _stash(m: "re.Match[str]") -> str:
                links.append((m.group(1), m.group(2)))
                return f"\x00L{len(links) - 1}\x00"

            s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _stash, s)
            # Bold **text** (html.escape doesn't touch *, so patterns still match)
            s = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{m.group(1)}</b>", s)
            # Italic _text_ (avoid mid-word underscores)
            s = re.sub(r"(?<!\w)_(.+?)_(?!\w)", lambda m: f"<i>{m.group(1)}</i>", s)
            for i, (ltext, lurl) in enumerate(links):
                s = s.replace(f"\x00L{i}\x00", f'<a href="{lurl}">{ltext}</a>')
            out.append(s)
    return "".join(out)
