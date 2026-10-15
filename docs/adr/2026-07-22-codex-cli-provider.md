# ADR — Codex CLI as a fourth AI transport

- **Date:** 2026-07-22
- **Status:** Accepted
- **Agent provenance:** built by an agent (task 63) as the deliberate twin of the Claude Code
  CLI transport shipped the same day. The Claude one was verified empirically against
  `claude` 2.1.216; **this one was not** — `codex` is not installed on the build machine, so
  the wire contract is written from OpenAI's documented `codex exec` interface and defended
  by tolerating both of its published event schemas. See "Unverified surface" below.
- **RFC:** `.claude/rfcs/2026-07-22-codex-cli-provider.md`
- **Precedent:** `docs/adr/2026-07-22-claude-code-cli-provider.md` (read that first — every
  decision here either mirrors one of its decisions or explicitly diverges from it)

## Context

`claude_cli` gave a Claude Pro/Max subscriber a way to spend that subscription here instead
of buying API credits. The identical argument applies to a ChatGPT Plus/Pro/Business
subscriber: OpenAI's `codex` CLI authenticates with `codex login` and runs non-interactively
(`codex exec`), and — as with Anthropic — replaying subscription OAuth against `api.openai.com`
from a third-party HTTP client is not a sanctioned path. **Spawning OpenAI's own CLI is.**

## Decisions

### 1. A fourth transport (`codex_cli`), mirroring `claude_cli` exactly

`VALID_PROVIDERS` + one `PROVIDER_CATALOG` entry + one `get_provider` branch. `base_url` is
again repurposed as the **CLI path**, so `_infer_provider_key` (backend) and
`inferCatalogKey` (frontend) both resolve these transports by transport rather than by
base_url — the branch is now shared by the two of them rather than duplicated. The batch seam
already degrades an unrecognised transport to `sync`, which is right: there is no batch
endpoint behind a CLI and guessing a wire format is the 2026-07-15 xAI incident.

### 2. One tool protocol, not two

`ai/models/claude_cli_tools.py` was renamed to **`ai/models/cli_tool_protocol.py`** (the old
module stays as a re-export shim, since the Claude RFC/ADR and its tests name it) and is now
shared by both CLI providers unchanged: `<tool_call>{…}</tool_call>` rendered into the system
text and scanned incrementally out of the streamed reply. The reasoning is identical and
already recorded in the Claude ADR §2 — routing our ~272 tools through MCP would move tool
execution out of `assistant.run` and lose `mutates=` domains, the write-receipt chip, the
confirm gate and the discovery gate. A second copy of that protocol would have been a fork
that drifts; a shared one keeps a fix in one place.

### 3. The system prompt rides in the body, because there is no flag for it

`codex exec` has no `--system-prompt`. The system text (plus the tool manifest) is rendered
into the single stdin prompt under an explicit `# System instructions` heading, with the
transcript under `# Conversation`, so the model can still tell standing instructions from the
turn it is answering. Rejected: `-c experimental_instructions_file=…` — an experimental key
whose presence varies by version, for no behavioural gain over a heading.

### 4. Cost is DERIVED, and stamped with the same basis

Unlike `claude -p`, Codex reports **no dollar figure** — token counts only. So cost comes
from the existing OpenAI price table (`pricing.cost_usd_openai`), exactly as the
`openai_compatible` transport already does, and an unknown model id yields `0.0` with the
table's own "unmeasured, not free" warning rather than a fabricated number.

`cost_basis` (added by the Claude lane) is reused, with a **different derivation**: Claude
reads `system/init.apiKeySource`, which Codex has no equivalent of, so here the signal is
whether the user stored an API key for this provider at all.

- no stored key → `"plan_included"` (running on `codex login`, i.e. a ChatGPT plan — the
  figure is a list-price equivalent for usage the plan already covers)
- a stored key → `"estimate"` (metered, but still a table estimate rather than an invoice)

### 5. Isolation is part of the contract, not hygiene

Codex is a coding agent with a shell, a patch tool, web search and MCP. Neutralising all of
it is the contract, and each of these is pinned by test:

| Flag | Why |
|---|---|
| `--sandbox read-only` | a model backend must never edit a file |
| `-c approval_policy="never"` | never stall on an approval prompt in a headless process |
| `-c mcp_servers={}` | the user's own MCP servers must not widen the model's reach |
| `-c tools.web_search=false` | the host owns the tool loop (we have our own `web_search`, with its own accounting) |
| `-c project_doc_max_bytes=0` | never read an `AGENTS.md` — the `--setting-sources ""` lesson, which was *observed live* on the Claude transport |
| `-c history.persistence="none"` | don't litter the user's `~/.codex` with our turns |
| `--json`, `--color never`, `--skip-git-repo-check` | a machine-readable stream from a non-repo cwd |

Plus: `cwd` is a fresh empty temp dir, the prompt goes over **stdin** (trailing `-`), and the
child env is scrubbed of `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_PROJECT` /
`CODEX_HOME` / `CODEX_API_KEY` / `OTEL_*` / `ONW_*`. `OPENAI_API_KEY` matters most: an
inherited `.env` containing one makes Codex bill the **API** instead of the plan — the exact
silent-rerouting failure the Claude scrub exists to prevent, with the roles of the two auth
modes swapped.

### 6. Effort maps to Codex's three rungs, and says so

`model_reasoning_effort` accepts `low|medium|high`. `capabilities.effort_levels` therefore
advertises exactly those three (owner rule: don't lie about available efforts), and the
provider folds our `xhigh`/`max` to `high` rather than passing through a value the CLI would
reject mid-turn.

### 7. No version floor, unlike the Claude transport

The Claude probe refuses `< 2.1.208` because a specific truncation bug was *verified* there.
No equivalent verified bug exists for Codex, and inventing a floor would fail a working
install over a guess — so `/api/models` validates this transport by locating the binary and
running `--version`, nothing more. The probe table (`_CLI_TRANSPORTS`) makes the floor
per-transport and optional, so adding one later is one field.

## Unverified surface (the honest part)

`codex` was not installed on the build machine, so unlike its Claude twin **this transport
has not been exercised against the real binary**. Two consequences, both deliberate:

1. **Both published event schemas are accepted** — the typed `thread.started` / `item.*` /
   `turn.completed` stream and the older `{"id":…, "msg":{"type":…}}` envelope
   (`agent_message_delta` / `token_count` / `task_complete`). Pinning one and being wrong
   would make every turn fail; normalising both costs one function and is robust to the
   schema still moving. An unrecognised event is ignored, not fatal.
2. **Structured output degrades to prompt-and-parse.** Recent builds do have
   `--output-schema`, but passing a flag an installed version doesn't have fails the whole
   turn, and every caller of `complete(response_format=…)` already instructs JSON in the
   prompt and parses tolerantly. So `response_format` appends an instruction instead. If the
   flag is later confirmed present across supported versions, switching is a small change.

A stream that ends with **no** terminal event is an error, not a free turn — same rule as the
Claude transport, and the main thing that will surface a schema mismatch loudly on a real
install rather than silently booking empty turns.

## Consequences

- A self-hoster with `codex` installed and logged in gets a second key-less provider;
  everyone else is unaffected (opt-in, one catalog row).
- Tool calling here is prompt-emulated, so less reliable than native tool use — the same
  caveat as the Claude CLI transport, and the provider is chosen deliberately, never
  defaulted to.
- Cost rows are price-table estimates, stamped `plan_included` on a subscription login. The
  cost UI still renders every row identically (the same open follow-up the Claude ADR names).
- Latency is ~1–2 s higher per turn (process spawn + CLI boot).
- ~~Vision is refused loudly (`statement_vision` must be pinned to an API-key provider)~~
  **Corrected 2026-08-08** — `complete(images=…)` works via `exec -i`; see
  `docs/adr/2026-08-08-cli-transports-accept-images-via-complete.md`. There is still no
  batch lane.

## Not done

Resume/CLI-owned sessions, MCP tool routing, batch, vision, `--output-schema`, a cost-UI
treatment for `cost_basis`, and empirical verification against a real `codex` install — the
last one is the first thing to do when the binary is available.
