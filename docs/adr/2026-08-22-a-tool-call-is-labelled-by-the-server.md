# A tool call is labelled by the server, and grouped by the client

**Date:** 2026-08-22
**Status:** Accepted
**Owner decision** (mid-build, overruling an earlier draft of this design)

## Context

The chat UI answered "what do I call this tool call?" itself, from `TOOL_PILL_LABELS` — a
15-entry table against a **290-tool registry** plus an unbounded set of external MCP tools.
Everything outside those 15 rendered its raw name: `mcp__playwright__browser_navigate`.

There were in fact **three** independent derivations of the same thing:

| where | what it produced |
|---|---|
| `MessageBubble.TOOL_PILL_LABELS` | 15 friendly labels, then the raw name |
| `MessageBubble` legacy flat pill row | the raw name, always |
| `automation_runner_adapter._tool_label` | `"Mcp  playwright  browser navigate"` |

The first design for this work kept the labelling client-side and promised no backend
change. The owner rejected it mid-build:

> "I'd rather let the tool call stream back with metadata that allows it to show everything,
> instead of client side having to aggregate or use different data."

## Decision

**The server owns semantics. The client owns pixels.**

`backend/ai/tools/display.py::describe_tool_call(name, input)` returns one `display` block —
`label` / `verb` / `noun` / `owner_id` / `owner_name` / `detail` — attached at three points:
the live `tool_use_start` forward, `rehydrate_messages`, and the automation run-step list.
All three previously disagreed; there is now nothing left to disagree.

The client keeps exactly one job that genuinely cannot move: **grouping**. Whether two calls
are adjacent on screen, and whether one is still streaming, are facts only the renderer has
— the server emits events one at a time and never learns when a group closed. So it ships
the atoms (`verb`, `noun`, `owner_id`) and `cluster.ts` counts and joins them. It holds no
vocabulary of its own.

## Why server-side is the better half of the trade

**Labelling happens on READ, not on write.** A transcript stores the tool NAME. Running the
labeller during rehydration means a conversation recorded *before* this module existed
renders exactly like one recorded after it. That is what removes the legacy path from the
client entirely — there is no "old transcripts look different" case to carry, and no second
copy of the vocabulary to drift, which is the scar behind `services.connectors._SHORT_DESC`
being read *through* by the frontend catalog rather than re-authored there (2026-08-14).

The costs are real and accepted: one new hand-maintained table (`_DISPLAY_NAMES`, guarded
against drift by `tests/test_connector_display_names.py`, which caught two on its first run)
and a `display` field on the event contract.

## What the label is, and is not

**Derived, never narrated.** Every field comes from the tool name, its declared input and
the connector registry. The model never writes a label. `ai/turn_activity`'s rotating word
makes the same argument for the working indicator: a plausible-sounding step name states
something the reader cannot check. A label here is a restatement of the call, verifiable
against the Request the row expands to show.

**Amendment, same day — the derivation needs no word list at all.** The first version mapped
each verb prefix to a PAST-TENSE word (`get_` → "Read"), which made that map a *contract*: a
verb missing from it fell through to "Used <noun>", so every new tool risked needing an entry
somebody had to remember. The owner rejected that maintenance ("I don't want a hardcoded list
we have to remember for every new tool — make sure we need zero changes").

Past tense was the only thing requiring the lookup. Dropping it removes the table:
`export_tax_report` → "Export tax report", `flag_suspicious_transactions` → "Flag suspicious
transactions" — any name this repo's own `<verb>_<noun>` convention produces reads correctly
the day it is added. Present tense is also the more honest tense, since a row is drawn while
its call is still running and "Read live prices" claims something that has not happened yet.

What remains is optional: an icon-hint map (a missing prefix costs a slightly less specific
icon and nothing else) and a handful of overrides for names that genuinely mislead. Both can
be emptied without breaking anything, which
`tests/test_tool_display.py::test_every_tool_labels_itself_with_no_table_entry` asserts by
doing exactly that against all 290 tools.

**The icon now comes from `mutates`, not from the name.** `open_option` — a trade writing
options + portfolio + ledger — matched the browse prefix and rendered with the globe glyph a
fetched web page gets. The fix is not an entry for `open_option`: `_icon_class` refuses a
read/search/browse icon for anything that declares `mutates`, so a future write tool named
`get_…` is handled the day it is added. This repo already forces every tool to declare
`mutates`, so it adds no obligation — and it is the same fact dispatch uses.

Three derivation rules exist only because the whole registry was run through the module and
these were the failures it found:

* a multi-token owner prefix hides the verb (`lunch_flow_get_status` → "Used …");
* only what FOLLOWS the verb is the object — so `browser_navigate` opens a *page*, never a
  "browser", which is also what stops the group summary claiming "Opened 2 browsers";
* an owner's own words are never repeated beside a breadcrumb already saying them
  (`tavily_search` → "Searched", not "Searched searches").

Coverage is 290/290, pinned by `tests/test_tool_display.py`.

## Consequences

* **Version skew has one graceful path.** A client newer than its backend TYPESETS the raw
  name (`mcp__interactive-brokers-mcp__get_positions` → "Get positions"): the same words the
  name already contains, minus the transport prefix and underscores. It guesses no verb and
  no owner, so the row shows a neutral glyph and no breadcrumb. It states nothing new.
* **An MCP server's own tool title is preferred** (`discovered_tools`, written only by
  `record_discovery` from a real handshake — ADR `2026-08-14`) and is therefore
  server-chosen text: sanitized and capped before it can reach a DOM, so a server cannot
  forge the fence that means *the operator is speaking* (ADR `2026-08-13`).
* **Failures are classified from two shapes, not one.** The S2 `{"error": …}` contract, and
  a plain-text result that announces an error at its very start — which is the only shape an
  external MCP tool returns. Matching only the first made every MCP failure render as a
  success (owner report, IBKR MCP). The prefix is anchored: a result may legitimately
  *discuss* errors.
* **`connector=` gains a third job.** It already gated discovery and named the permission
  owner; it now also resolves the row's brand mark and breadcrumb.
