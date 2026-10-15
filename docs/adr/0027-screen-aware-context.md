# 0027 — Screen-aware Lucius: snapshot + on-demand handles

- Status: Accepted
- Date: 2026-06-24
- Deciders: user (provided the brief + the transparency-panel design, drove it AFK) + implementing agent
- Related: `backend/models/screen.py`, `backend/services/screen_context.py`, `backend/ai/tools/screen.py` (`get_screen_detail`), `backend/api/chat.py` (the `screen` turn field), `backend/ai/assistant.py` (`screen_handles` session meta + per-turn fetch-budget reset); FE `frontend/src/state/screenSnapshot.tsx`, `frontend/src/components/chat/ScreenContextPanel.tsx`; design spec `docs/superpowers/specs/2026-06-24-screen-aware-context-design.md`; generalizes the asset-brief session binding (ADR 0026 / `services/mentions/resolve.session_brief_block`)

## Context

`@`-mentions give Lucius only the entities the user explicitly picks. Most of a screen isn't an
entity — the price chart, analyst consensus, bull/bear issues, a news summary, derived figures
("29% of book"), the active tab/filter — so "what am I looking at?", "is this concentrated?",
"what does this story say?" can't be answered. We want Lucius **screen-aware** and able to **pull
deeper content on demand**, without bloating every turn or shipping the whole screen.

## Decision

**Two layers by cost, reusing the existing turn-context + tool-loop seams.**

1. **Snapshot (cheap, every turn).** Each screen publishes a *structured* `ScreenSnapshot`
   `{id, title, view_state, facts, visible_text, handles[], as_of}` from its **own view-model**
   (NOT DOM-scraped). The frontend attaches it to `POST /api/chat` as an optional `screen` field;
   `services/screen_context.format_screen_context` renders a bounded `[Currently on screen …]`
   block merged onto the **user message** (reusing `_merge_mention_context`), so it costs tokens
   only that turn and never busts the prompt cache — identical to how mentions/the asset brief ride.
2. **Handles (expensive, on demand).** Anything shown only as a summary/visual is a
   `ScreenHandle {id, type, label, descriptor, source, pinned}` — **metadata only** in the snapshot.
   The model calls the new `get_screen_detail(handle_id)` tool; the backend resolves the handle from
   the turn's manifest (persisted to session meta) and fetches the body **server-side** (news URL →
   `fetch_page` + the `net_guard` SSRF check; price series → `PriceHistoryStore`), returning content
   + a `source` to cite. This mirrors the existing interactive discovery-gate two-pass loop.

### Resolved open questions (from the brief)

- **Source of truth:** structured view-model data, with a bounded `visible_text` fallback. No DOM scrape.
- **Fetch trust boundary:** server-side hydrate via existing services + `fetch_page` (SSRF-guarded on
  every hop). Client-side hydrate for *explicit* attach is deferred.
- **Auto-fetch policy:** auto-hydrate on by default; **hard cap 3 fetches/turn** (a ContextVar
  budget reset at turn start, alongside `set_session_id`); each fetch is user-visible.
- **Citations:** every hydrated handle returns a `source` (and `url` when it has one); the model cites it.
- **Actions:** read-only context for v1.
- **Staleness:** snapshot is point-in-time, carries `as_of`; live values captured at send-time.
- **Sensitivity / consent:** v1 follows the **existing trust model** — Lucius is the user's own
  assistant and already receives figures via mentions, so the snapshot is consistent. The *new* thing
  is automatic per-turn sending, gated behind a **toggle** (default on, localStorage) + a **visible
  "Lucius can see this screen" panel** that lists exactly what's passed inline and what's available
  on demand (the user can `+ attach` a handle to force-fetch it). Per-field masking is deferred.

## Consequences

- "What am I looking at / what does this story say / is this concentrated" now answerable; the model
  stops guessing visual/summary content and fetches the real thing with a citation.
- New per-conversation state (`screen_handles` in the session index) + one interactive tool. The tool
  is **NOT** in `HEADLESS_ALLOWED_TOOLS` (a headless turn has no screen). Layering stays clean
  (5 import-linter contracts KEPT): the service is pure (models only); the network fetch lives in the
  tool (the `ai` layer, allowed to reach the search provider).
- Only `asset-detail` ships a snapshot in v1 (news_story + price_series handles); other screens add a
  `useRegisterScreenSnapshot` call to opt in. Pinned-handle force-hydrate on the request path,
  client-side hydrate, off-screen-rows/vision/file handle types, and per-field consent are phase 2.

## Provenance

Built in one AFK session from the user's written brief + the "Lucius can see this screen" mockup.
Tested: `tests/test_screen_context.py` (formatter, budget, tool incl. SSRF) + FE
`screenSnapshot.test.ts` / `ScreenContextPanel.test.tsx`. The design deliberately generalizes the
asset-brief mechanism (cached rich context bound to a conversation, riding every turn with an `as_of`
label and a "prefer cached, don't re-fetch" steer) from "an asset" to "the current screen."
