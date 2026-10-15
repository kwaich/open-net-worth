# ADR: Response calibration — depth follows the message; effort is a ceiling

**Date:** 2026-07-11
**Status:** Accepted
**Design:** `docs/superpowers/specs/2026-07-11-response-calibration-design.md`

## Context

Lucius over-answered trivial messages: "hello" produced an ~800-token narration of the
Tax screen; "how are you?" produced a portfolio status plus a five-bullet menu of
suggested topics. The owner asked for a systemic fix, explicitly not a one-off patch.

Root causes were structural: (1) the persona block demands proactive, data-driven advice
with no conversational calibration anywhere in the prompt; (2) every turn injects a
data-rich context (portfolio snapshot, profile, up to 30 memory facts, screen snapshot)
with nothing marking it as reference material — a small model narrates what it is handed;
(3) the effort style lines read as targets ("Be thorough. Depth is expected."), so a high
effort mode endorses walls of text regardless of the message; (4) no test failed when a
greeting produced a data dump.

## Decision

Adopt the layered pattern mature assistants use (OpenAI Model Spec's scale-to-the-question
principle; Claude.ai's response-format section with few-shot examples; production
golden-prompt eval suites):

1. **RESPONSE CALIBRATION contract** in the STATIC (cached) half of
   `build_system_prompt`, directly after the persona intro: depth follows the user's
   MESSAGE, never the held context; conversational messages get 1–2 plain sentences with
   no data/bullets/headers/topic menus; injected context is reference material, never
   narrated unprompted; few-shot examples of correct short replies plus one contrast
   example ("hi — how's my portfolio doing?" → data IS asked for).
2. **Reference-only framing** on every injected context block: the per-turn portfolio
   snapshot header, the memory block header (`_fmt_memory`), and the screen-context head
   (`format_screen_context` — "do not narrate or summarize this screen unprompted").
3. **Effort is redefined as a CEILING, never a target** (`_EFFORT_STYLE`): every tier's
   style line states that a conversational message still gets 1–2 plain sentences. The UI
   dial is unchanged; only the semantics of what a tier permits (vs demands) changed. The
   `RESPONSE STYLE (current effort mode: …)` wire format is unchanged.
4. **Harness-level smalltalk override** (`backend/ai/smalltalk.py` +
   `assistant._resolve_style`, added at the owner's follow-up "how about harness?"): when
   the ENTIRE typed message is pure smalltalk (≤40 chars, no digits, fixed
   greeting/thanks/farewell lexicon, no mention context / attachments / non-screen
   blocks), the per-turn RESPONSE STYLE line is deterministically swapped for
   `SMALLTALK_STYLE`, regardless of the effort dial. Safe by construction: any substance
   disqualifies, so a real request can never be down-styled; flow-control words
   ("yes"/"ok"/"confirmed") are excluded because they answer confirm-gated writes; no
   context is ever dropped. One choke in `_run_turn` covers web chat, connectors, and
   responder turns.
5. **Behavior is pinned by tests**: `tests/test_response_calibration_prompt.py` +
   `tests/test_smalltalk_override.py` (always-run: prompt string-contains guards, the
   classifier truth table, the `_resolve_style` wiring) and
   `tests/test_conversational_calibration.py` (`@integration` behavioral eval: greeting +
   Tax-screen bait through the real provider on haiku, asserting word cap / no currency
   figures / no structure, at effort medium AND high — deliberately WITHOUT the harness
   override in play, so the prompt contract is evaluated on its own).

## Alternatives rejected

- **Context gating** (heuristic drops the screen/portfolio snapshot on detected
  chitchat): a misclassified turn would lose data access. The shipped smalltalk override
  swaps only the style LINE and only when the message is provably contentless — the worst
  case of a false positive is a terse reply, never an under-informed one.
- **Intent-router architecture** (pre-turn classifier picks prompt/context assembly):
  adds a model call and latency to every turn; overkill for a single-user app.
- **max_tokens clamping by effort**: truncates instead of shaping — garbles tables/JSON
  mid-emission; the provider's 32K ceiling stays a safety net only.

## Consequences

- One-time prompt-cache bust on deploy (static block bytes changed); steady-state caching
  is unchanged — the contract lives in the cached half.
- `@agent` responder turns inherit the calibrated style lines (universal in
  `assistant.run`) but not the Lucius static contract — responder agents carry their own
  personas (`build_agent_system_prompt` is untouched: headless agents don't chit-chat).
- Any future prompt edit that weakens the contract fails
  `test_response_calibration_prompt.py` in CI; behavioral drift on new models is caught by
  running the integration eval before shipping prompt/model changes.

## Provenance

Agent-authored (Claude) from an owner bug report with screenshots; owner delegated design
decisions ("decide everything yourself, ship e2e") after selecting the eval-backed
direction over gating/routing in brainstorming.
