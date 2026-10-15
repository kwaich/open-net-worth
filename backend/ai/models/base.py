from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List


class AIProvider(ABC):
    """Abstract interface for AI chat providers. The provider is chosen in Settings → AI Model."""

    @abstractmethod
    async def stream_chat(
        self,
        messages: List[dict],
        tools: List[dict],
        system: str,
        model: str,
        effort: str = "medium",
    ) -> AsyncGenerator[dict, None]:
        """
        Yield normalized events:
          {type: "text_delta",   text: str}
          {type: "tool_use",     id: str, name: str, input: dict}
          {type: "tool_result",  tool_use_id: str, content: str}  # not from model, injected by assistant
          {type: "done"}
        """
        ...

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,
        source: str = "complete",
        images: list[dict] | None = None,
        response_format: dict | None = None,
        effort: str = "",
    ) -> str:
        """
        Single-shot, non-streaming completion. Returns the model's full text reply.
        Used for background tasks like memory extraction/consolidation — no tools, no streaming.

        ``images`` (optional) is a list of ``{"media_type": str, "data": <base64 str>}`` page
        images sent as multimodal content ahead of the prompt text — the vision path for
        scanned-statement parsing. A non-vision model surfaces the provider's own error loudly
        (skip-don't-reroute), never a silent text-only downgrade.
        """
        ...

    @abstractmethod
    async def stream_complete(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        max_tokens: int = 2048,
        source: str = "complete",
        effort: str = "",
    ) -> AsyncGenerator[dict, None]:
        """
        Single-shot completion, STREAMED — the streaming twin of ``complete()`` (2026-08-27,
        built for the compacting-progress bar). Yields ``{"type": "text_delta", "text": str}``
        chunks as they arrive; records cost internally exactly like ``complete()`` does (unlike
        ``stream_chat``, which pushes a ``usage`` event to the caller — there is no turn-level
        consumer for a background single-shot call to do that recording instead).

        Deliberately narrower than ``complete()``: no ``images``, no ``response_format`` — this
        exists for background text summarization (fold), which needs neither. A caller that
        needs multimodal or forced-JSON background completion still uses ``complete()``.
        """
        ...
