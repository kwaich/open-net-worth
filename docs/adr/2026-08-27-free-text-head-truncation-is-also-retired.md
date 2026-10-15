# 2026-08-27 — Free-text head-truncation is also retired (with a bounded-at-the-source replacement)

**Status:** accepted (owner-directed, 2026-08-27)
**Follows:** `docs/adr/2026-08-27-age-decay-and-supersession-shrink-are-both-retired.md`
(same day, same lane — that ADR's own "not reconsidered here" line about
`_compact_tool_result`'s remaining transform is exactly what this one reconsiders)
**Where:** `backend/ai/assistant.py` (`_compact_tool_result` DELETED outright;
`_TRUNCATE_TOOL_NAMES`/`MAX_TOOL_RESULT_CHARS` DELETED), `backend/ai/search/base.py`
(NEW: `MAX_FETCH_PAGE_CHARS`, `fetch_result_payload`), `backend/ai/tools/misc.py`
(`fetch_page`), `backend/ai/tools/web_search_providers.py` (`_fetch_via`),
`tests/test_history_compaction.py`, `tests/test_misc_tool_sanitize.py`,
`tests/test_web_search_providers.py`, `docs/INVARIANTS.md`

## Context

`_compact_tool_result`'s free-text head-truncation (`web_search`/`fetch_page` results
cut to `MAX_TOOL_RESULT_CHARS=4,000` chars, applied on every render inside
`_prepare_messages`) was the one transform the prior ADR left standing, on the
reasoning that it bounds a single result's own size rather than deciding whether that
result still matters. Asked directly — "if we truncate here, how can Lucius know what
the remaining is?" — the honest answer, checked against the code, was: **it can't.**
The cut appended a bare `"\n…[truncated]"` marker with no count of what was missing,
and `fetch_page`'s only input is `url` — no offset/pagination parameter exists, so
re-calling the tool just re-fetches the same page and gets re-truncated identically,
every time, forever. That is the exact same failure category the prior ADR retired:
a mechanical rule with no understanding of content, destroying the model's ability to
reason about something (here, what a fetched page actually said) using its own
memory — just scoped to two tools instead of everything.

It also directly violated a rule already written down in this codebase
(`docs/INVARIANTS.md`, "Tool results are BOUNDED AT THE TOOL"): *"every bounded read
reports `showing` + `total` and offers a way to reach the rest."* This mechanism did
neither.

**Owner's directive:** "let's just remove this mechanism for now! i will add later
back if needed, i wouldra tahrer be more expensive but work than tryng to opitmize
cost here."

### A wrinkle found mid-implementation: this wasn't purely a cost question

Before landing the removal as a pure deletion, the owner asked whether an unbounded
`fetch_page` could return something absurd — and it could. `plain_get_fetch` (the
shared fallback for 4 of 6 providers) caps the wire read at `_MAX_FETCH_BYTES=4,000,000`
bytes; at this codebase's own 4-bytes-per-token estimate
(`assistant._approx_token_count`), that is a worst case of **~1,000,000 tokens from a
single tool call** — large enough to fail the request outright, not just cost more.
Two providers (Tavily's `/extract`, Firecrawl's `/scrape`) had no cap in our code at
all, deferring entirely to a third-party API's own response size.

Checking real usage (`tool_call_log.result_chars`, this instance's own history)
showed the worst case had never actually happened — real `fetch_page` calls averaged
4,941 chars, max ever seen 10,854 — but also surfaced a SEPARATE, pre-existing silent
cap nobody had discussed: `sanitize_untrusted`'s own default `max_len=MAX_VALUE_LEN`
(`backend/core/untrusted.py`) is 10,000 chars, applied unconditionally at both
`fetch_page` call sites with the identical defect being retired here — a bare
`cleaned[:max_len]` slice, no signal, no way to get more. That explains why the
theoretical 1M-token cliff was never observed: an entirely different, undocumented
truncation was already there the whole time.

Given that, the owner asked for a real cap rather than leaving `fetch_page` fully
open: **15,000 chars**, applied at the tool's own output — not history-rendering —
so it is honest instead of repeating the mistake just retired.

## Decision

1. `_compact_tool_result`, `_TRUNCATE_TOOL_NAMES`, and `MAX_TOOL_RESULT_CHARS` are
   DELETED from `backend/ai/assistant.py`, not merely emptied. `_prepare_messages` no
   longer calls anything per-result at all — the only remaining per-message
   transforms are orphan-drop (a correctness invariant, unrelated) and fold's
   summary-prepend. Everything inside the sliding window (`MAX_HISTORY_MESSAGES`) is
   now sent fully verbatim for every reason there is — age, size, tool, or repeat
   call — completing the prior ADR's "nothing inside the window is ever replaced with
   a stub" rule for the one case it had left standing.
2. **A tool's own output may still be bounded at the SOURCE** — this is a different
   mechanism, not a reinstatement of the retired one. `fetch_page` (`ai/tools/misc.py`)
   and every `<provider>_fetch_page` tool (`ai/tools/web_search_providers.py::
   _fetch_via`) now share `ai/search/base.py::fetch_result_payload(url, content)`:
   sanitizes untrusted page text (unchanged behavior) and head-caps at
   `MAX_FETCH_PAGE_CHARS=15,000` chars — raised from the prior silent 10,000-char
   `sanitize_untrusted` default, explicit now rather than incidental. When a cap
   fires, the result carries `content_chars_shown`/`content_chars_total` so the model
   knows there's more; when it doesn't, the payload is the plain `{url, content}`
   shape unchanged. This is the "report `showing`+`total`" half of the
   `bound-tool-results-filter-first` rule this codebase already claims to follow; the
   "offer a way to reach the rest" half (an offset/pagination parameter on
   `fetch_page`) is NOT built here — flagged as real follow-up work, not done.
3. `web_search`/`x_search` (result-list tools, not single-page fetch) are unchanged —
   real usage showed them well under any cap (avg 2,454 / max 5,721 chars for
   `web_search`) and they were never the tool this conversation was about.

## What this does NOT fix

- **No pagination.** A page that legitimately exceeds 15,000 chars still has its tail
  permanently unreachable — the model now knows it's missing something (unlike
  before), but still has no tool call that gets it. The honest half of the fix
  shipped; the complete half (offset/`from_char` on `fetch_page`) did not.
- **Tavily's `/extract` and Firecrawl's `/scrape` remain uncapped upstream of our own
  code** — `fetch_result_payload` bounds what reaches the model, but a
  memory/latency cost from an unbounded third-party response before that point is
  still possible. Not observed in practice; not specifically guarded against either.
- **The `_MAX_FETCH_BYTES=4,000,000` wire cap is unchanged** and is still, on paper, a
  ~1,000,000-token worst case for the 4 `plain_get_fetch`-backed providers before
  `fetch_result_payload` ever sees the content — `fetch_result_payload` bounds the
  OUTPUT of the fetch, not the memory cost of fetching it. Real usage suggests this
  never actually triggers; it was not re-examined as part of this change.

## Consequences

- `web_search`/`fetch_page`-family results inside the sliding window are no longer
  truncated by history rendering at all — a page fetched two messages ago is no
  longer permanently frozen at its first 4,000 (nor, previously, silently, 10,000)
  chars for the rest of the conversation.
- `fetch_page`'s effective cap moved from an undocumented, incidental 10,000
  (`sanitize_untrusted`'s default `max_len`) to an explicit, documented, and higher
  15,000 — plus, for the first time, an honest signal when it fires.
- Test surface: `tests/test_history_compaction.py` lost its 5 remaining
  supersession-shrink tests (already broken/vacuous on this branch since the prior
  ADR's removal never touched this file — a pre-existing gap closed as part of this
  same pass) and its one truncation test, replaced with 2 tests proving a huge
  `web_search`/`fetch_page` result now survives untouched in history. New tests
  in `test_misc_tool_sanitize.py` and `test_web_search_providers.py` pin the 15k
  cap and its honest-signal shape for both the bulk `fetch_page` tool and every
  per-provider `<id>_fetch_page` tool.

## Agent provenance

- **Decided by:** debugging agent (Claude), same session and lane as the prior ADR —
  reached by the owner asking a direct, concrete question ("how can Lucius know what
  the remaining is") about the one mechanism the prior ADR had left unexamined, then
  catching that a naive full removal would trade a bounded-but-blind problem for an
  effectively-unbounded one, before settling on a bounded-and-honest replacement.
- **Lane / branch:** `lane/decay-exempt-writes`, worktree
  `/Users/owner/code/worktrees/decay-exempt-writes` off `main`.
- **Alternatives weighed:** leaving `fetch_page` fully uncapped after removing
  history-level truncation (rejected once the ~1,000,000-token wire-cap worst case
  was computed — a correctness/stability risk, not a cost tradeoff, so the owner's
  "I'd rather be more expensive" reasoning didn't apply to it); building real
  offset/pagination now instead of a flat cap (deferred — bigger, separate work, not
  what was asked for this pass).
- **Driving artifacts:** live query against this instance's own `tool_call_log`
  (`result_chars` for `fetch_page`/`web_search`/`x_search`, 2026-08-27) — real max
  `fetch_page` size ever seen: 10,854 chars, avg 4,941; confirms the 15,000 cap has
  real headroom over observed usage while still refusing the theoretical worst case.
- **Why this way (one line):** removing a blind cap should not mean removing every
  cap — the fix for "no signal" is a signal, not the absence of any bound at all.
