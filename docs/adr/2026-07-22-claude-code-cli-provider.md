# ADR — Claude Code headless CLI as a third AI transport

- **Date:** 2026-07-22
- **Status:** Accepted
- **Agent provenance:** built by an agent (task 45) after researching how OSS projects wrap
  the headless CLI (OpenClaw's `claude-cli` backend, claw-orchestrator, the unmerged Hermes
  Agent proposal) and verifying the contract empirically against `claude` 2.1.216.
- **RFC:** `.claude/rfcs/2026-07-22-claude-code-cli-provider.md`

## Context

The provider seam had exactly two transports — `anthropic` (SDK + API key) and
`openai_compatible` (any OpenAI-shaped HTTP endpoint). A self-hoster with a Claude Pro/Max
subscription could not spend it here; they had to buy API credits for the same models.

The `claude` CLI's headless mode (`claude -p`) authenticates with the user's existing login
and emits NDJSON. Anthropic has banned third-party HTTP clients replaying subscription OAuth
tokens against `api.anthropic.com`, so **spawning Anthropic's own CLI is the sanctioned
path** — that, not convenience, is why this is a subprocess and not an HTTP adapter.

## Decisions

### 1. A third transport (`claude_cli`), not a fourth abstraction

`VALID_PROVIDERS` + one `PROVIDER_CATALOG` entry + one `get_provider` branch. Per-agent
overrides (`claude_cli:opus`), per-task pinning, the routing engine, the model picker and
the cost log all key on the catalog, so they work with no further change. The batch seam
already degrades an unrecognised transport to `sync`, which is exactly right — the CLI has
no batch endpoint and guessing a wire format is the 2026-07-15 xAI incident.

`base_url` is repurposed as the **CLI path**. It is the one free-form per-provider string
the store round-trips, and "where is the endpoint" is what it means for the other two.
Consequence: `_infer_provider_key` needs an explicit `claude_cli` branch, because a
non-default path never exact-matches the catalog's blank default.

### 2. Tool calling is emulated in the prompt, NOT routed through MCP

The CLI cannot be handed arbitrary tool schemas. The alternative — expose our ~272 tools via
an MCP server and let the CLI drive the loop (what OpenClaw does) — would move tool
**execution** out of `assistant.run`, losing `mutates=` freshness domains, the write-receipt
chip, the confirm gate and the discovery gate. That is a second architecture, not a provider
swap.

So `--tools ""` disables the CLI's own tools, the schemas are rendered into the system
prompt, and a `<tool_call>{…}</tool_call>` sentinel is scanned incrementally out of the
streamed text (`ai/models/claude_cli_tools.py`) into the normal `tool_use_start`/`tool_use`
events. The host loop is untouched.

This is knowingly less reliable than native tool use, and it is the same approach Hermes
Agent's proposal reached (though for a wrong reason — it claimed stream-json carries no
structured tool calls, which is false for the CLI's *own* tools). The honest trade-off:

- **Rejected: `--json-schema` forcing `{"reply", "tool_calls"}`.** It gives *validated*
  structured output, which is strictly better than a text sentinel — but it makes the whole
  response a JSON document, so there is nothing to stream as prose. Streaming is a
  first-class chat requirement here, so the sentinel wins. Revisit if the CLI ever streams
  structured output incrementally.
- A malformed block is surfaced as literal text, never dropped: a silent drop reads to the
  user as "the assistant said nothing".

### 3. Stateless per call — never `--resume`

`assistant.run` owns the transcript (folding, decay, checkpoints, rehydration). A CLI-side
session would silently diverge from it after any restore or fold. So the full history is
re-flattened into one user message every turn and `--no-session-persistence` is set. Cost of
this choice: no cross-turn prompt cache on the transcript. Accepted — this transport exists
for users whose marginal token cost is zero.

### 4. Cost is the CLI's own `total_cost_usd`, **stamped with a basis**

The final `result` event carries `total_cost_usd` plus the full usage block. We trust it
over any price table — the same rule already applied to OpenRouter's `usage.cost` and
LiteLLM's response-cost header. (Two of the three surveyed projects re-derive cost from a
hand-maintained table instead; one of them never reads `cache_creation_input_tokens` at all
and so under-bills every cache-heavy session. Not copying that.)

The subtlety nobody in the survey handles: on a Pro/Max login the figure is a **locally
computed list price for usage already covered by the plan** — worse than `0.0`, because it
looks like a real charge. So `cost_tracker.record` gained an optional `cost_basis` field,
written only when a caller knows it is not metered spend:

- `"plan_included"` — CLI on a subscription login (`system/init.apiKeySource == "none"`).
- `"estimate"` — CLI with an explicit API key.
- absent — every pre-existing row, i.e. metered spend. No migration, no behaviour change.

The number is still recorded, because zeroing it would hide real consumption of a
rate-limited quota. Stamping it means a reader can tell an estimate from an invoice.

### 5. Isolation is part of the contract, not hygiene

Four flags are load-bearing and pinned by test:

| Flag | Why |
|---|---|
| `--tools ""` | the CLI is a model backend here, not a coding agent |
| `--setting-sources ""` | **verified**: without it a user `SessionStart` hook prepended kilobytes of unrelated instructions to the first turn |
| `--strict-mcp-config` | the user's own MCP servers must not widen the model's reach |
| `--verbose` | required by the CLI alongside `stream-json` in print mode |

Plus: `cwd` is a fresh empty temp dir (no repo access, nothing for `CLAUDE.md` discovery);
the prompt goes over **stdin** (no argv limit, no prompt in the host's process list); and the
child env is scrubbed of `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CONFIG_DIR`
/ `CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY}` / `OTEL_*` / `ONW_*`. That scrub is not
paranoia: the CLI consults those **before** its own login state, so an inherited `.env` would
silently reroute a subscription run to a metered endpoint.

**`--bare` was rejected** despite being the documented recommendation for scripted calls: it
skips OAuth and keychain reads, so it requires `ANTHROPIC_API_KEY` — mutually exclusive with
the entire point of this transport.

### 6. Connector-based chat needs no connector change, but four guards

Telegram/Slack/Discord/WhatsApp turns run through the same `assistant.run` → provider path.
What changes is that every turn is now a **subprocess**:

1. A global semaphore (`ONW_CLAUDE_CLI_MAX_CONCURRENCY`, default **2** — a subscription's
   rate limit is per-account, so parallel spawns buy nothing) bounds fan-out.
2. A hard timeout (`ONW_CLAUDE_CLI_TIMEOUT`, default 300 s) kills the process instead of
   hanging a connector turn forever.
3. A `/stop` cancels the turn → `assistant.run`'s finally `aclose()`s the generator → our
   `finally` kills and **reaps** the child and removes its temp dir. Pinned by test;
   without the reap every cancelled turn leaks a `claude` process.
4. ~~`complete(images=…)` raises loudly rather than silently downgrading to text — the
   skip-don't-reroute contract. Only the `statement_vision` task is affected; pin it to an
   API-key provider.~~ **Corrected 2026-08-08** — this was never actually re-tested against
   a real image-bearing call; it does work. See
   `docs/adr/2026-08-08-cli-transports-accept-images-via-complete.md`.

Also guarded: a stream that ends without a `result` event is an ERROR, not a free turn (a
known pre-2.1.208 truncation bug when stdout is piped would otherwise book a turn with no
cost and no stop reason), and `/api/models` validates this provider by locating the binary
and refusing a version below 2.1.208.

## Consequences

- A self-hoster with the CLI installed gets a key-less provider that spends their existing
  subscription; everyone else is completely unaffected (opt-in, one catalog row).
- Tool calling on this transport is less reliable than on the other two. Documented in the
  Settings help text and here; the provider is chosen deliberately, never defaulted to.
- Cost rows from this transport are estimates. The `cost_basis` field is the honest signal;
  the cost UI does not yet read it (follow-up — it currently renders every row identically).
- Latency is ~1–2 s higher per turn (process spawn + CLI boot).

## Not done

`--resume`/CLI-owned sessions, MCP tool routing, batch, vision, and a cost-UI treatment for
`cost_basis`.
