# Authoring guide

## 1. Capture intent

Start by understanding what the user actually wants. The current conversation might already
contain the workflow they want to capture (they say "turn this into a skill") — if so, extract
the answers from the history first: the tools used, the sequence of steps, corrections the user
made, the input/output formats observed. Have the user confirm the gaps before proceeding.

Otherwise, establish four things:

1. What should this skill enable Lucius to do?
2. When should it trigger? (what user phrases/contexts)
3. What's the expected output format?
4. Should we set up test prompts to verify it works? Skills with objectively checkable outputs
   (sums, counts, fixed report shapes) benefit from test prompts; skills with subjective
   outputs often don't. Suggest a default based on the skill; the user decides.

Interview and research: proactively ask about edge cases, input/output formats, and success
criteria. Research before interrogating — call the read tools the skill will use (e.g.
`get_transactions`, `list_accounts`) on the user's real data so you come prepared and reduce
the burden on the user. If the topic is vague, ask one or two focused questions — don't guess.

Mind the audience: users range from engineers to people who have never said "parameter" out
loud. Match their vocabulary, and briefly explain any term of art you do need.

## 2. Description is the portable trigger

Trigger information lives in `description`, not the body — the body is only read AFTER the skill
triggers. Include both what the skill does and the specific contexts or phrases that should invoke
it. For a Claude Code-compatible skill, optional `when_to_use` frontmatter can add Claude-specific
phrases; ONW combines it with `description` for automatic selection too.

Skills under-trigger far more than they over-trigger. Make the description a little pushy:
name the adjacent phrasings a user might say without naming the skill, not just the obvious
one. See `references/examples.md` for a flat-vs-pushy makeover.

A skill is consulted for substantive, multi-step asks — a trivial one-step request tends to be
handled directly without consulting any skill. Write the description for the substantive ask,
not the keyword.

Shape it as an instruction about the user's intent, not the skill's mechanics: "Use this skill
to … Use it for …, even when the user doesn't say '…'. Not for … (do X instead)." The "Not for"
sentence names the near-misses — a sibling skill, a one-off question the model should just
answer — so the skill doesn't fire on them. Hard limit 1024 characters; a few sentences is right.

## 3. Skills are instructions, not tool policy

Name the ordinary ONW tools the workflow should call in the body, and confirm EVERY one is a real
registered tool. A skill never grants, adds, hides, denies, or preapproves tools: tool discovery,
MCP policy, dispatch checks, and confirmations remain owned by the app. The standard experimental
`allowed-tools` frontmatter may be preserved when importing or exporting a portable bundle, but
Lucius treats it as inert compatibility metadata. Do not add it to control runtime behavior. If a
needed tool does not exist or is unavailable, say so and stop; never invent a tool name.

## 4. Bundling a script (optional)

When the skill needs a deterministic computation the model can't do reliably in its head
(combinatorics, precise decimal math, parsing a fiddly format), bundle a short script via
`save_code_as_skill`. The script reads its JSON input from the `SKILL_INPUT` env var (a file
path) and prints its result as JSON to stdout. Python, Bash, and Node are all supported; keep
it short and standard-library-only. Saved DISABLED — it cannot run until the user enables it
in Settings → Skills AND code execution is on in Settings → Developer. Only bundle a script
when existing tools genuinely can't compute the result.

## 5. Task input

Prefer reading task-specific input from the current conversation. When the user explicitly wants a
Claude Code-compatible command, use Claude's native `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N`, or named
arguments declared in `arguments` frontmatter. Never create an ONW `parameters` schema or
`{{placeholder}}` template language.

## 6. Guardrails and the principle of no surprise

A skill never applies a money change or external action the user hasn't agreed to: it proposes
the change and asks first. Whether the app then shows its own approval card depends on the
user's permission settings — the same write may ask on one install and run straight away on
another. So never write that a step "shows a confirm card", and never rely on one to catch a
mistake; the skill's own ask is the one that always happens.

A skill's contents must never surprise the user given its name and description: no hidden side
effects, nothing the description doesn't admit to. Refuse to author a skill designed to
mislead.

## 7. Write the body

- Use the imperative form, and write a clear, numbered flow: which tools to call in what
  order, how to read the results, what to report.
- Explain the WHY behind each instruction instead of stacking heavy-handed MUSTs. Today's
  models have good theory of mind: a reason transmits intent further than a rule. If you find
  yourself writing ALWAYS or NEVER in all caps, that's a yellow flag — reframe with the
  reasoning so the model understands why it matters.
- If the output shape matters, give an exact template (a literal `## Heading` skeleton beats a
  description of one). Paired Input → Output examples work well for formats.
- Keep it general, not overfitted: the skill should work across many future prompts, not just
  the example you're iterating on today.
- Keep it lean: the whole body is loaded on every run. Aim for about 100 lines (the Agent
  Skills limit is 500 lines / 5,000 tokens). For each line ask "would the model get this wrong
  without it?" — if not, cut it.
- Lead with a **Gotchas** section: the concrete, non-obvious corrections — the mistakes you
  watched the model make while testing, the data quirks of this book. It is the highest-value
  part of most skills; add to it every time a test run needs correcting.
- Give defaults, not menus: one way to do each step, with an escape hatch if needed.
- Currency-neutral — surface amounts in whatever base currency the tools report; never
  hardcode a currency.
- Draft it, then reread it with fresh eyes and improve it before proposing.

## 8. Split what is needed once from what is needed every run

Detail the model needs at ONE step — a long field catalogue, a lookup table, worked examples —
goes in `references/<name>.md`, and the body says WHEN to read it: "read `references/slots.md`
when you fill the template", never a vague "see references/". A fixed output design (an HTML
report, a CSV layout) goes in `assets/` as a template with named slots, and the body says to
fill it rather than describing the format in prose. Pass both through `create_skill`'s `files`
(or `update_skill`'s `changes.files`): text files only, under `references/` or `assets/`.

When the output has rules the model keeps breaking (leftover placeholders, a required field),
prefer a check the tools already enforce — `create_artifact` refuses a design-system page with
unfilled `{{SLOTS}}` or template markers and names them — over another paragraph of rules.

## 9. Create it

Call `create_skill` (instructions-only) or `save_code_as_skill` (with a bundled script — pass
name, source, instructions, description). Present the draft and ask the user before you save
it; their permission settings decide whether the app also shows an approval card. If a script
is bundled, say plainly that saving it does not enable code execution: the operator must turn on
Bundled script skills in Settings → Developer, with a separate switch for unattended runs.

Then move to testing: `references/testing-and-iteration.md`.
