# Codex runs over the app-server, on an isolated CODEX_HOME

- **Date**: 2026-08-01
- **Status**: Accepted
- **Area**: AI transports (`backend/ai/models/codex_*`)
- **Design**: `.claude/rfcs/2026-08-01-codex-app-server-transport.md`
- **Amends**: `2026-07-22-codex-cli-provider` (transport only; its isolation, statelessness
  and cost-basis rules stand)

## Decision

1. A `codex_cli` turn is served by a **persistent `codex app-server` child over JSON-RPC**,
   with `codex exec` kept as an automatic fallback. `ONW_CODEX_TRANSPORT=exec|app_server` pins
   it.
2. Each model turn opens a **fresh ephemeral thread**. Codex never resumes a thread of ours.
3. Every Codex child runs with **`CODEX_HOME` pointed at `CACHE_DIR/codex-home/`**, seeded with
   an empty `config.toml` and a symlink to the operator's `auth.json`. `HOME` is untouched.
4. No new transport id — this is internal to `CodexCliProvider`.

## Why

Measured on `codex-cli 0.146.0`, on the prompt `"What is 17*23?"`, with the exact flags the
provider passed:

| | input tokens | streaming | thinking |
|---|---|---|---|
| `codex exec` as we invoked it | 53,983 | none — one lump after exit | none |
| `codex exec --ignore-user-config` | 14,009 | none | none |
| app-server + isolated home + `baseInstructions` | **~10,000** | **157 deltas** | **yes** |

Three separate defects, one transport change:

- **Clearing `CODEX_HOME` is not isolation.** The CLI falls back to the operator's real
  `~/.codex`, so their plugins and skills load into every turn — the model was observed running
  `/bin/zsh -lc "sed -n '1,240p' ~/.codex/plugins/cache/…"` to read skill files *before*
  answering the question. `-c mcp_servers={}` and `-c project_doc_max_bytes=0` do not cover
  plugins or skills, and `app-server` has no `--ignore-user-config` at all. Setting the var to a
  tree we own is the only lever that covers every config source.
- **`exec --json` emits no deltas on 0.146.0** — only whole `item.completed` envelopes, so the
  user saw nothing until the subprocess exited.
- **`exec --json` emits no reasoning**, even with `model_reasoning_summary="detailed"` and
  `show_raw_agent_reasoning=true`. The app-server emits `item/reasoning/summaryTextDelta`.

## Why not full delegation

OpenClaw and Hermes both hand Codex the tool loop, thread state and compaction. We keep ours:
every write in this app goes through a service choke point with `txn_audit` provenance and
per-turn cost accounting, all of which live in `assistant.run`. Hermes documents that its own
loop-dependent tools are unavailable on that runtime; the equivalent loss here would be every
mutating tool. Taking the persistent process and the event stream *without* the thread
ownership is the middle path neither of them took.

## The preamble-abandoned turn

An agentic model narrates before it acts ("I'll search the April ledger for that…"), then
calls a native tool. We strip every native tool from the thread, so after the preamble it has
nothing to invoke and `turn/completed` fires — and the preamble gets rendered as the answer.
Owner-reported, 2026-08-01.

Codex classifies such a message `MessagePhase = "commentary"` — *"additional tool calls or
assistant output may follow before turn completion"* — as opposed to `final_answer`. The
transport now reads that phase (from `item/started`, since the delta notification carries
none) and, when a turn ends on `commentary` with **no tool call parsed out of the text**,
resumes it once on the same thread. The decision lives in the provider because only it owns
the `ToolCallScanner`; the loop lives in the client because only it owns the thread.

Bounded to ONE continuation: a model that narrates twice will not act on the third ask, and
every continuation is a real request against a rate-limited quota. `phase` is empty on builds
that do not report it — the schema warns about exactly that — and unknown means *do nothing*.

Paired with a prompt-side fix in `cli_tool_protocol._MANIFEST_HEADER`, which now forbids
announcing intent instead of acting. That half applies to every CLI transport, not just Codex.

## Consequences

- The effort ladder now passes through **1:1** on the app-server (`model/list` advertises
  `xhigh`/`max`/`ultra`). The `exec` path still folds `xhigh`/`max` → `high`, because
  `-c model_reasoning_effort` rejects them. The asymmetry is deliberate and test-guarded.
- `--ignore-user-config` is deliberately **not** passed on the `exec` path: that path exists for
  CLIs too old to have `app-server`, and an unrecognised flag fails the whole turn. The isolated
  home covers it on every version.
- A rotated refresh token stays in one place (the symlink). If a future Codex replaces the link
  via atomic rename, it degrades to a private copy rather than corrupting the operator's login.
- First run seeds `CACHE_DIR/codex-home/`. Deleting it is safe; it is re-seeded.
