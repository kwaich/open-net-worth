# Agent Skills compliance

ONW stores and exchanges standard Agent Skills folders.

| Area | ONW behavior |
|---|---|
| Required definition | `SKILL.md` with `name` and `description` |
| Optional standard frontmatter | `license`, `compatibility`, string-map `metadata`; the experimental `allowed-tools` is accepted on import and dropped |
| Optional folders | `scripts/`, `references/`, `assets/` |
| OpenAI metadata | `agents/openai.yaml` sections `interface`, `dependencies`, and `policy` |
| Claude Code metadata | Documented Claude-only `SKILL.md` frontmatter is preserved except the `disallowed-tools` tool list, which is dropped; invocation controls and arguments are honored by ONW |
| Discovery | Enabled names and descriptions are added to a budgeted model catalog |
| Implicit invocation | Allowed by default; disabled by `policy.allow_implicit_invocation: false` |
| Explicit invocation | Deterministic `/skill-id` or structured Skills-tab selection |
| Full instruction loading | Canonical `SKILL.md` is loaded only after selection, for the current turn |
| Multiple skills | Up to six unique skills per message; duplicate IDs are deduplicated and rendered instructions are aggregate-capped |
| Tool policy | A skill carries no tool list (`allowed-tools` / `disallowed-tools` are never stored or written); ordinary tool discovery and security policy apply |
| Claude arguments | `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N`, and declared `$name` |
| Model-authored arguments | Bounded and fenced as untrusted data; never interpolated with user authority |
| Assets | Arbitrary binary assets round-trip byte-for-byte through upload/export |
| Paused turns | Approval resumes preserve the originating turn's loaded skill bodies, including after reload |
| ONW-only templates | Unsupported; no `parameters` schema or `{{placeholder}}` substitution |

App bookkeeping such as `enabled`, `author`, timestamps, and invocation counts is stored separately
from the portable skill definition and is not written into `SKILL.md`.

Legacy ONW-only fields can be present in an imported file without making the upload fail, but they
are ignored. ONW rewrites the installed `SKILL.md` canonically and will not export those fields.
Claude Code-only execution metadata (`context`, `agent`, `background`, `hooks`, `model`, `effort`,
`paths`, and `shell`) round-trips for Claude Code but does not alter ONW's provider, subagent,
filesystem, shell, or permission runtime.
