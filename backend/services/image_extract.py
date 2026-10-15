"""Read an attached image with a vision model so a text-only chat model can use it.

The fallback half of the no-vision story (ADR 2026-07-26). When the conversation's model
can't accept image input, the image is not dropped and not silently replaced by a
placeholder: a bounded set of calls to a DIFFERENT, vision-capable model turns it into text
that rides the same turn.

**Extraction, not captioning.** OpenClaw's equivalent pre-pass defaults to a ≤500-char
description, which is the right shape for a chat assistant but the wrong one here: nobody
attaches a photo to a net-worth app to be told "a receipt from a café" — they attach it to get
the merchant, date, total and line items booked. So the prompt asks for verbatim figures and
text, and the output cap is measured in thousands of characters, not hundreds.

**Never a silent partial read.** A multi-page scan is ONE document: page 4 of a 30-page
statement is not a quarter of the answer, it is a wrong answer with a confident tone. So the
policy here is the one `services/statements/parser.py` already established for exactly this
situation — read EVERY page up to `MAX_IMAGES` (40, the same ceiling and the same reasoning:
one vision call per page), and past that REFUSE LOUDLY instead of truncating. Whenever any
page is missing for any reason (unreadable, oversize, transcript budget) the gap is named
explicitly so the total can never be mistaken for complete.

One call PER image, concurrency-bounded — mirroring `tiers.tier3_vision`. A single call
carrying N images shares one token budget between them, which silently truncates a long
document; it also caps how much of each page the model bothers to transcribe.

Two rules borrowed from OpenClaw unchanged, both load-bearing:
  - never run this when the chat model has native vision (it would pay twice for a worse
    result — the caller enforces this);
  - never route the extraction to the blind model itself.

Everything here is BEST-EFFORT: no provider, no configured extractor, a timeout, a refusal,
or an empty reply all degrade to an outcome the caller reports honestly. A failure to read an
attachment must never fail the turn.

The extracted text is UNTRUSTED — it is external content (a document someone else may have
produced) re-entering the model context — so it comes back fenced and sanitized, exactly like
fetched email / web / PDF text elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, NamedTuple

from backend.core.llm_seam import LLMUnavailable, complete
from backend.core.untrusted import fence_untrusted_block, make_fence_nonce, sanitize_untrusted

log = logging.getLogger(__name__)

# Page ceiling. The SAME number and rationale as ``statements/parser._VISION_PAGE_CAP`` —
# one vision call per page, so this is a cost ceiling, and past it we refuse rather than
# read a fraction. Deliberately NOT a smaller "chat is different" number: a document
# attached to a chat turn is the same document, and a partial transcription of it is
# actively misleading rather than merely incomplete.
MAX_IMAGES = 40
# Decoded bytes per image — a real provider limit, not a policy choice. A photo past this is
# reported as unread rather than shipped to a provider that will reject it anyway.
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Per-page output ceiling. Far above a caption budget (see the module docstring).
MAX_CHARS = 4000
# Whole-attachment transcript ceiling. This text rides EVERY later turn of the conversation,
# so it is bounded — but when the bound bites, the pages it excluded are named (see
# ``_assemble``), never quietly dropped.
MAX_TOTAL_CHARS = 40_000
# How many pages are read at once. Sequential reads would make a 30-page scan a multi-minute
# wall for a synchronous chat turn; unbounded would hammer the provider's rate limit.
_CONCURRENCY = 4
_TIMEOUT_SECONDS = 90.0
_MAX_TOKENS = 2048

_SYSTEM = (
    "You transcribe images for a personal-finance assistant that cannot see them. "
    "Report only what is actually visible. Never guess a number, a date, or a currency "
    "that is not legible, and never infer a total you cannot read — write 'unreadable' "
    "for anything unclear. Do not offer advice, opinions, or next steps."
)


def image_count_refusal(total: int) -> str:
    """Return the shared fail-closed reader refusal for an oversized image batch."""
    if total <= MAX_IMAGES:
        return ""
    return f"{total} pages is beyond the {MAX_IMAGES}-page limit for reading an attachment inline"


_PROMPT = (
    "Transcribe this image as structured plain text for downstream bookkeeping.\n\n"
    "Include, when present: the merchant or institution name, every date, every amount "
    "with its currency, account or card identifiers, line items with their individual "
    "amounts, totals, taxes, and fees. Copy figures VERBATIM — same digits, same decimal "
    "separator, same currency symbol.\n\n"
    "If the image is a table (a statement, a transaction list, a portfolio view), preserve "
    "it as rows, one per line, columns separated by ' | '.\n"
    "If the image contains no financial content, describe in one sentence what it shows.\n\n"
    f"Keep the whole reply under {MAX_CHARS} characters."
)


class ExtractOutcome(NamedTuple):
    """What came back, and honestly how much of it.

    ``text`` is the fenced transcription ("" when nothing could be read). ``refusal`` is a
    plain-language reason the caller should relay INSTEAD of any text — set only when we
    declined to read at all. ``gaps`` names every page that is missing from ``text``, so the
    caller can state the incompleteness rather than implying a complete read.
    """

    text: str = ""
    refusal: str = ""
    pages_total: int = 0
    pages_read: int = 0
    gaps: tuple = ()

    @property
    def complete(self) -> bool:
        return bool(self.text) and not self.gaps and self.pages_read == self.pages_total


def _too_big(data: str) -> bool:
    """Base64 payload over the per-image byte ceiling (decoded length is 3/4 of it)."""
    return (len(data) * 3) // 4 > MAX_IMAGE_BYTES


async def _read_one(image: dict, index: int, total: int, model: str) -> "tuple[int, str]":
    """Transcribe ONE image. Returns ``(index, text)`` — text "" on any failure.

    Failures are per-page and never raise: one unreadable page must not discard the other
    29, and the caller reports the gap.
    """
    page_note = f" (page {index + 1} of {total})" if total > 1 else ""
    try:
        reply = await asyncio.wait_for(
            complete(
                _PROMPT + page_note,
                system=_SYSTEM,
                model=model,
                max_tokens=_MAX_TOKENS,
                source="chat_vision",
                images=[image],
            ),
            timeout=_TIMEOUT_SECONDS,
        )
    except LLMUnavailable:
        log.info("image_extract: no LLM provider registered — attachment not read")
        return index, ""
    except asyncio.TimeoutError:
        log.warning("image_extract: %s timed out on page %d", model, index + 1)
        return index, ""
    except Exception as exc:
        # Includes the case that matters most: the configured extractor ALSO has no vision.
        # Log the type only (the message can carry provider payload).
        log.warning(
            "image_extract: %s failed on page %d (%s)", model, index + 1, type(exc).__name__
        )
        return index, ""
    return index, sanitize_untrusted((reply or "").strip(), MAX_CHARS)


def _assemble(parts: "dict[int, str]", total: int, presized_gaps: "list[str]") -> ExtractOutcome:
    """Fence the per-page results in page order, naming every page that is missing.

    The transcript ceiling is applied HERE rather than inside the read, so a page that was
    successfully transcribed but excluded for size is still reported as a gap — the caller
    must never present a size-trimmed document as a whole one.
    """
    gaps: List[str] = list(presized_gaps)
    kept: List[str] = []
    used = 0
    for i in range(total):
        body = parts.get(i, "")
        if not body:
            if f"page {i + 1}" not in " ".join(gaps):
                gaps.append(f"page {i + 1} could not be read")
            continue
        header = f"--- page {i + 1} of {total} ---\n" if total > 1 else ""
        chunk = header + body
        if used + len(chunk) > MAX_TOTAL_CHARS:
            gaps.append(
                f"pages {i + 1}-{total} excluded: the transcription hit its "
                f"{MAX_TOTAL_CHARS}-character limit for one turn"
            )
            break
        kept.append(chunk)
        used += len(chunk)

    if not kept:
        return ExtractOutcome(pages_total=total, pages_read=0, gaps=tuple(gaps))
    return ExtractOutcome(
        text=fence_untrusted_block("image-extract", "\n\n".join(kept), make_fence_nonce()),
        pages_total=total,
        pages_read=len(kept),
        gaps=tuple(gaps),
    )


async def extract_image_text(images: list[dict], *, model: str) -> ExtractOutcome:
    """Read EVERY image in ``images`` with ``model`` and return the fenced transcription.

    ``images`` are ``{"media_type", "data": <base64>}`` entries — the shape both the upload
    store and the statement vision tier already use. ``model`` is a resolved
    ``"provider_key:model_id"`` ref for a vision-capable model; it must never be the
    conversation's own (blind) model.

    Past ``MAX_IMAGES`` pages this REFUSES without making a single call (``refusal`` set,
    ``text`` empty) — the same contract ``statements/parser`` applies to the same situation.
    Anything else missing from the result is named in ``gaps``.

    The fence label is a FIXED constant, never the filename: the label sits inside the
    boundary marker, so interpolating an attacker-chosen name there would hand them the one
    string the fence relies on being server-controlled. Naming the file is the caller's job,
    in its own prose outside the fence.
    """
    total = len(images)
    if not model or total == 0:
        return ExtractOutcome(pages_total=total)
    count_refusal = image_count_refusal(total)
    if count_refusal:
        return ExtractOutcome(
            refusal=count_refusal,
            pages_total=total,
        )

    gaps: List[str] = []
    sendable: "list[tuple[int, dict]]" = []
    for i, img in enumerate(images):
        data = (img or {}).get("data") or ""
        if not data or not isinstance(data, str):
            gaps.append(f"page {i + 1} could not be read (no image data)")
            continue
        if _too_big(data):
            gaps.append(
                f"page {i + 1} could not be read (over {MAX_IMAGE_BYTES // (1024 * 1024)}MB)"
            )
            continue
        sendable.append((i, {"media_type": img.get("media_type") or "image/jpeg", "data": data}))

    if not sendable:
        return ExtractOutcome(pages_total=total, gaps=tuple(gaps))

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _guarded(idx: int, img: dict) -> "tuple[int, str]":
        async with sem:
            return await _read_one(img, idx, total, model)

    results = await asyncio.gather(*(_guarded(i, img) for i, img in sendable))
    return _assemble({i: text for i, text in results}, total, gaps)
