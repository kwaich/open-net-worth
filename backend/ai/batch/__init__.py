"""Provider batch implementations — Anthropic, OpenAI, and xAI batch APIs.

``dispatch.build_batch_hooks()`` is imported by ``ai/tools/__init__`` at startup and
registered on ``core.llm_seam.register_batch``. ``routing.resolve_adapter_key``
decides which adapter module (``anthropic_batch``/``openai_batch``/``xai_batch``)
handles the ACTIVE config, by verified endpoint capability rather than transport
label — see ``routing.py``'s docstring for why (the 2026-07-15 xAI incident: an
``openai_compatible`` transport is not one shape). ``_openai_shape.py`` is a shared
leaf helper for the two adapters that both speak genuine ``/v1/chat/completions``
JSONL (``openai_batch``, ``xai_batch``).

Gemini/Ollama/GLM/OpenRouter/LiteLLM/unknown endpoints never resolve to an adapter
— the seam's sync fallback fires instead. See ``docs/adr/
2026-07-15-batch-seam-capability-routing.md``.
"""

from __future__ import annotations
