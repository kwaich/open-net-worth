# Codex gets our tools as real tool calls — captured, never executed, by the host loop

- **Date**: 2026-10-03
- **Status**: Accepted
- **Area**: AI transports (`backend/ai/models/codex_app_server.py`, `codex_cli_provider.py`, `codex_home.py`)
- **Amends**: `2026-08-01-codex-app-server-transport` (tools no longer ride only in the prompt;
  the text protocol stays as the fallback). `cli_tool_protocol`'s "route tools through MCP moves
  execution out of `assistant.run`" argument still stands — this design does not do that.
- **Agent provenance**: Claude (Sonnet 5.5), worktree `agent-a4f408e13119faab9`, owner brief
  2026-10-03 ("make our tools REAL tool calls"; Codex only — Claude Code's text protocol is
  unchanged).

## Context

Every Codex turn taught our tools as text (`<tool_call>` blocks in `baseInstructions`). On
2026-10-03 gpt-6 models on codex-cli 0.160 ignored them: any native tool out-competes a text
protocol (ChatGPT apps, code mode `exec`, the shell). We switched `apps`, `code_mode_host` and
`shell_tool` off as a backstop, but the model still has no real tool to call.

## Decision

1. **Mechanism: `thread/start.dynamicTools`** (experimental API, `initialize.capabilities.
   experimentalApi`). Each offered tool is registered as a `function` tool with its
   `input_schema`. No MCP bridge is needed.
2. **The host keeps the loop; Codex never executes anything.** When the model calls a tool the
   app-server sends the server->client request `item/tool/call`. The client CAPTURES it (plus any
   siblings arriving within 0.4 s), sends `turn/interrupt`, declines the request, and the provider
   emits ordinary `tool_use_start` / `tool_use` events with OUR ids and `stop_reason: tool_use` —
   the identical events a parsed `<tool_call>` yields. `assistant.run` then runs the same
   `registry.dispatch` path as today, so `tool_permissions`, confirm staging under the
   `tool_use_id`, unattended rules, audit/`tool_call_log`, the discovery gate and result bounding
   apply with no new code path. The result reaches the model next round as transcript text
   (`[called X (id …) with …]` / `[result of …]`, never the `<tool_call>` dialect it could imitate).
3. **Tool-set growth (`find_tools`, the discovery gate): a fresh thread per round, measured.**
   The generated schema carries `dynamicTools` on `thread/start` only — not on `turn/start`,
   `thread/settings/update`, `turn/settings/update`, `thread/resume` or `thread/fork` — so a
   thread's tools cannot be updated in place. We already start a fresh ephemeral thread per model
   round and re-send the transcript, so each round registers the set as it is NOW and a tool loaded
   by `find_tools` is callable natively on the very next round, with no extra mechanism and no
   stable-`call_tool`-dispatcher indirection (rejected: it would hide the per-tool schema from the
   model and make `tool_permissions`/audit name the wrapper instead of the tool). Cost: none new —
   299 registered tools cost 102,372 input tokens natively vs 108,652 as the text manifest
   (gpt-6-luna, one round each); the only added cost is a prompt-cache miss on the round where the
   set changes.
4. **Capability detection, text protocol kept.** Native registration is used only when the
   handshake `userAgent` reports >= 0.160.0 (the verified build), `codex-code-mode-host` is next to
   `codex` or on `PATH`, `ONW_CODEX_NATIVE_TOOLS` is not `0/off/text`, the turn has tools, and every
   tool name is a legal function name. Otherwise — or if `thread/start` rejects the registration
   before anything streamed — the round runs on the text manifest exactly as before.
5. **Code mode stays ON for native turns — proven necessary, then hardened.** In 0.160 dynamic
   tools are delivered through the code-mode host. With it off the model reports "tool host
   unavailable" and calls nothing: measured on gpt-6-luna with `code_mode_host=false` alone,
   plus `code_mode=true`, plus `unified_exec=false`, and on gpt-5.6-sol. So native turns run a
   SEPARATE child with its own `CODEX_HOME` (`<codex-home>/native-tools`); turns without tools and
   the text fallback keep the original home (host off).
   A code-mode script sees EVERY tool the thread holds. A live `Object.keys(ALL_TOOLS)` capture on
   the first cut showed more than ours: `web__run` (which really executed a web search),
   `image_gen__imagegen`, `get_goal`/`create_goal`/`update_goal`, `clock__curr_time`. Hence, on
   native threads only: every Codex built-in feature off in BOTH the home config and the
   thread `config` (`web_search = "disabled"` is honoured only at thread level), `environments: []`,
   an EMPTY dedicated `cwd` (never the home holding the `auth.json` symlink), and `ultra` effort
   clamped to `max` (proactive multi-agent). After this the live capture is exactly our tools plus
   `clock__curr_time` (read-only, no flag removes it). The home file's substitution is asserted at
   import, and a test pins every switch. Any call for a name we did not register this round, any
   `namespace`, and any non-string name is treated as malformed, never mapped onto our registry.
6. Unsupported server requests (approvals etc.) are refused with `-32601`; a tool call for a
   thread nobody owns is declined immediately and frames for released threads are dropped;
   malformed or over-cap calls are never run — they surface `UNPARSED_CALL_NOTICE`. On timeout,
   error or a closed consumer, captured requests are declined and a best-effort `turn/interrupt`
   is sent so the model is not left running on the operator's account.
7. Tool results in the native transcript are framed `[result of <id> #<nonce>]` ... `[end of
   result #<nonce>]` with a per-round random token and `| `-quoted lines, so a hostile result
   cannot close its own frame or pass for a `Human:` turn; the note tells the model the contents
   are data. (The text protocol's `<tool_result>` framing is unchanged — follow-up.)

## Consequences

- gpt-6 models call our tools instead of narrating; verified live on gpt-6-luna (see report).
- One more long-lived `codex app-server` child (only once a tool-bearing turn runs).
- Code mode executes model-written JS inside Codex. Its tool set is ours plus a read-only clock;
  a future build adding built-in code-mode tools would widen it silently, so re-capture
  `Object.keys(ALL_TOOLS)` (prompt a native turn to print it) on every CLI upgrade and extend
  `NATIVE_BUILTIN_FEATURES`. The version floor stays at the one verified build.
- Parallel calls: the model's code usually awaits calls sequentially, so most rounds carry one
  call; siblings are returned together when they do arrive.
- Claude Code remains on the text protocol (out of scope for this change).
