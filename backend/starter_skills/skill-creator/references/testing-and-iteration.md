# Testing & iteration

## 1. Decide whether to test

Skills with objectively checkable outputs (file transforms, fixed report shapes, computations)
benefit from a test loop. Skills with subjective outputs (tone, style) are better judged
qualitatively. Suggest the right default, let the user decide — and if they say "skip the
evals, just vibe with me", vibe.

## 2. Write the test prompts

Draft 2-3 realistic test prompts — the kind of thing this user would actually type. Concrete
and specific: real account names, date ranges, a little backstory; some casual, some with
typos; mixed lengths; edge cases over clear-cut cases. "Test the skill" is not a test prompt.

Show them before running: "Here are a few test cases I'd like to try. Do these look right, or
do you want to add more?" Bad test prompts lead to bad skills — this sign-off matters.

## 3. Run them

Run each prompt with `activate_skill`, one at a time. You wrote the skill and you're also running
it, so you have full context — that's less rigorous than an independent run, and the human
review step is what compensates. So: show the user each prompt and its output BEFORE
evaluating it yourself — get the results in front of the human first. Then ask: "How does this
look? Anything you'd change?"

Where a check is mechanical (a count, a sum, a format), state the check and its result
explicitly rather than eyeballing.

Also watch what the skill made the run DO, not just the final answer — if it wastes steps or
re-derives the same thing every time, the body needs trimming or restructuring.

## 4. How to think about improvements

1. **Generalize from the feedback.** A skill is meant to be used many times across many
   prompts; you and the user are iterating on a few examples only because it's fast. A skill
   that only works on those examples is useless. Rather than fiddly, overfitty patches or
   constrictive MUSTs for a stubborn issue, try a different framing or a different pattern of
   working — it's cheap to try and sometimes lands somewhere great.
2. **Keep the body lean.** Remove things that aren't pulling their weight. If the skill makes
   the run waste effort, cut the part causing it and see what happens.
3. **Explain the why.** Even when feedback is terse or frustrated, work out what the user
   actually needs and transmit that understanding into the instructions as reasons, not
   shouted rules. All-caps ALWAYS/NEVER is the yellow flag again.
4. **Look for repeated work.** If every test run re-derives the same deterministic
   computation, that's the signal to bundle that piece as a script via `save_code_as_skill`,
   or to give the body an exact template so runs stop reinventing it.

Thinking time is not the blocker — write a draft revision, then look at it anew and improve
it. Get into the head of the user.

## 5. When to stop

- The user says they're happy.
- The feedback comes back empty (everything looks good).
- Iteration stops producing meaningful progress.

## 6. Trigger-testing the description

Offer this AFTER the skill itself works. The automatic catalog contains the description plus any
Claude `when_to_use`, so test that combined trigger text as a thought experiment:

- Draft ~6-10 realistic queries: half SHOULD trigger (varied phrasings, formal and casual,
  cases that clearly need the skill without naming it), half should NOT.
- The valuable negatives are near-misses — queries sharing keywords with the skill but needing
  something different. Obviously-irrelevant negatives test nothing.
- Have the user sign off on the set, then reason through which ones the current description
  would catch, and tighten the description from the failures (via `update_skill`).
