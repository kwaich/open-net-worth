---
title: A chat turn sent from a screen loads that screen's tools into the session
status: Accepted
date: 2026-09-26
deciders: orchestrating agent (Claude) + repo owner
---

# 2026-09-26. A screen loads its own tools

## Context

The interactive tool offer is CORE (15 tools) plus whatever the session has loaded; every other
tool must be loaded with `find_tools` first (ADR `2026-07-19-core-tool-offer-diet`). The system
prompt's tool catalog says so, and the dispatch loop's discovery gate auto-loads a known tool
called blind. Both depend on the model reaching outside its declared tool list, and a model that
treats that list as its whole toolset never does.

On 2026-09-26 the owner, on Plan → Budget (50/30/20), asked Lucius (GLM-5.3 Flash) to "set caps
for these envelopes, use the targets I have from Zero-Based". The tool that does exactly that,
`apply_budget_verb` (now `apply_budget_operation`) → `copy_targets_to_caps`, was never loaded, and neither was `list_envelopes`.
For twenty turns Lucius re-read `get_profile`, re-activated a stale skill, and told the owner it
had no budget tool. The only targets it saw were keyed by opaque envelope ids, so it paired the
amounts with the envelope names on screen by guesswork: three different pairings, and "Others:
300 + 200", which were Utilities' and Subscriptions' targets.

The screen snapshot already rides every chat turn sent from a screen (ADR/spec
`2026-06-24-screen-aware-context`), with the screen's title (`data-ctx-title`, e.g.
`Plan — Budget`).

## Decision

We will map a screen title to the tools that screen is about (`ai/tools/_screen_tools.py`,
`SCREEN_TOOLS`). When a chat turn arrives with a snapshot, `api/chat` calls
`assistant.load_screen_tools`, which adds those names to the session's `extra_tool_names`. That
is the same sticky, additive set a `find_tools` load persists, so the turn's offer carries them.

`Plan — Budget` loads `get_budget_view`, `list_envelopes`, `apply_budget_operation`,
`set_envelope_targets` and `assign_budget`.

Loading is an offer, never a permission: every write still stages its confirm card. A later
screen never unloads anything.

## Consequences

### Positive
- The screen's tools reach any model that chats from the screen, with no `find_tools` step.
- Nothing new to trust: the names are fixed server-side. A client can only choose among mapped
  screens, and loading grants nothing.

### Negative
- The session's offer grows by those schemas for the rest of the session: about 5 tools for the
  budget screen.
- Keyed on a display title. Renaming the screen's `data-ctx-title` silently drops its preload,
  which `tests/test_screen_tools.py` catches only for the mapped strings it names.

### Follow-ups
- Map other screens as the need shows (Accounts → reconcile, Investments → rebalance). Add each
  from evidence, like CORE.

## Alternatives considered
- **Put the budget tools in CORE** — rejected. Every turn on every screen would pay for their
  schemas, against the CORE diet's evidence rule.
- **Let an activated skill load its `allowed-tools`** — rejected. Skills carry no tool list
  (ADR `2026-09-26-skills-carry-no-allowed-tools`), and the skill in this chat was itself the
  stale part.
- **Only strengthen the prompt's `find_tools` instruction** — rejected as the whole fix. The
  catalog header already says "load it first". A model that ignores its catalog ignores a
  louder line too.

## Agent provenance
- **Decided by:** orchestrating Claude agent, on the owner's "tell me how the hell is it?" about
  that chat.
- **Lane / branch:** `main` (owner's standing rule).
- **Alternatives weighed:** CORE membership; skill-driven loading; prompt wording only.
- **Driving artifacts:** session `bff982fc` (2026-09-25/26 UTC); ADRs `2026-07-19-core-tool-offer-diet`,
  `2026-09-26-budget-and-category-tools`.
- **Why this way (one line):** the screen the user asks from already names what they are asking
  about, so its tools should be in hand before the model has to find them.
