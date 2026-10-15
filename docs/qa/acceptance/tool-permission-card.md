# Acceptance — Tool-permission card (allow once / always allow / deny)

> **Amended 2026-08-14 — read this before using the file.** Two changes landed after it was
> written and this pass corrects the file for BOTH rather than leaving it describing a screen
> that no longer exists.
>
> 1. **The `session` scope was removed on 2026-08-02** (owner: a card offers Allow once and
>    Always allow, nothing else). The file described three scopes; TPC1.1, TPC2.2, TPC2.3 and
>    TPC3.3 were already unreachable before this amendment — that staleness is PRE-EXISTING,
>    not caused by the permission merge below.
> 2. **The two permission stores merged on 2026-08-14** (ADR
>    `2026-08-14-one-permission-store-for-every-tool`). "Always allow" no longer writes a grant
>    row: it writes `level: "always_allow"` into the ONE permission table, which is the same
>    row the Connectors ladder and Settings › Permissions display. `GrantStore` /
>    `POST /api/tool-grants` are gone; the endpoint is `PUT /api/tool-permissions/{tool}`.
>
> The card's own shape — the buttons, the note, the deny message — is UNCHANGED by either.

The confirm card that asks permission to run a gated tool, and the choice that stops it
asking again. Verify on the running app via `make qa-e2e` / `make qa-cu`, never `:5174` /
`:8080` directly.

Design of record: `.claude/rfcs/2026-07-20-approval-and-review-model.md` — §13 (the model),
§15 (six owner decisions), §16 (the store).

**The bar to clear.** The predecessor system failed by being INVISIBLE: the per-agent write
envelope refused writes with no way to see the boundary in advance and no way to lift it, so
the only way to discover it was to hit it mid-task (§1.1). Every criterion below is therefore
about *the user being able to see and change what they granted* — a card that gates correctly
but leaves the user unable to tell what they agreed to has not passed this file.

**Scope of this lane.** The card + the connector rendering (`frontend/src/components/chat/**`,
`backend/connectors/processor.py`, `backend/connectors/chat/{confirm,grants}.py`). The gate
that RAISES the card (`ai/tools/_confirm_gate.py`), the permission store
(`db/tool_permission_store.py`), `PUT /api/tool-permissions/{tool}`
and the Settings › Permissions tab are parallel lanes — criteria that depend on them are
marked **[cross-lane]** and cannot go green until those land.

## TPC1 — The card offers a real choice, not two defaults

- **TPC1.1** GIVEN an interactive chat turn WHEN the model calls a gated tool THEN the
  `ChangePreview` card renders **Allow once · Always allow · Deny** — and NO bare "Confirm"
  button (two defaults would make the primary action ambiguous). *(Amended: this said four
  controls including "This session", a scope removed 2026-08-02.)*
- **TPC1.2** GIVEN a card for a change that is NOT gating a tool (no `grant_tool`) THEN the
  ordinary two-way Confirm / Dismiss card renders unchanged — the permission vocabulary is
  additive and must not alter any existing card.
- **TPC1.3** GIVEN any permission card THEN a note under the buttons names the tool, states
  the grant covers **every future call, any amount**, and names **Settings › Permissions** as
  where to revoke. A grant has no amount ceiling, no account set and no argument matching
  (§13.3b), so "Always" must never be left to read as narrower than it is.
- **TPC1.4** GIVEN an UNTAGGED read tool is called THEN **no card appears at all** — an
  unscoped read never prompts. *(Amended 2026-08-14: a CONNECTOR-tagged read does prompt, at
  the ladder's uniform `needs_approval` default — see `connectors-redesign.md` G11.)*
- **TPC1.5** GIVEN `send_email` is called THEN a card DOES appear, even though it writes no
  local domain: the predicate is "writes OR has an effect outside this install" (§13.3), and a
  `bool(mutates)` reading would miss the one tool here that cannot be undone. **[cross-lane]**

## TPC2 — Each scope does what its label says

- **TPC2.1** GIVEN a card WHEN **Allow once** is tapped THEN the call proceeds and **nothing
  is persisted** — the very next call of the same tool asks again.
- **TPC2.2** ~~This session~~ — RETIRED 2026-08-02. The scope no longer exists; a stored
  `session` row is not honoured (it fails closed) and none is migrated.
- **TPC2.3** ~~Session expiry~~ — retired with TPC2.2.
- **TPC2.4** GIVEN a card WHEN **Always allow** is tapped THEN the call proceeds and the tool
  does not ask again in any later session, including after a restart.
- **TPC2.5** GIVEN a decision made on web THEN it applies on Telegram, and vice versa —
  permissions are GLOBAL across surfaces (§16.4).
- **TPC2.6** *(new 2026-08-14)* GIVEN **Always allow** is tapped on a card THEN the resulting
  decision is VISIBLE in Settings › Permissions AND, for a connector-scoped tool, in that
  connector's own ladder — one store, so a decision made on the card cannot be invisible to
  the surfaces that claim to list it. This is the property the merge exists to give: before
  it, the card wrote a row neither screen could see.
- **TPC2.6** GIVEN a tool is granted `always` WHEN that tool is RENAMED in a later release THEN
  the user is asked again (§15.1) — a grant is keyed on the canonical name at grant time,
  because a rename here is frequently a semantic change (`delete_account` → `delete_accounts`
  turned one target into a batch). **[cross-lane]**
- **TPC2.7** GIVEN a grant WHEN the user later runs `restore_checkpoint` to a point before the
  grant was made THEN the grant is **unaffected** — `grants.json` is checkpoint-EXCLUDED, so
  the model cannot restore its way back to a revoked permission (§16.1). **[cross-lane]**

## TPC3 — Deny is a human decision, not a tool failure

- **TPC3.1** GIVEN a card WHEN **Deny** is tapped THEN nothing is written and **no grant is
  recorded**.
- **TPC3.2** GIVEN a denial THEN the card resolves to **"✗ Denied"** (not "Dismissed"), and
  that state survives a reload / conversation switch.
- **TPC3.3** GIVEN a denial THEN the message the model receives states, explicitly: that the
  USER declined, that this is **not** a tool failure or a missing capability, and that it must
  not retry — it should acknowledge and ask what the user would prefer.
- **TPC3.4** GIVEN a denial THEN the model **does not re-issue the same call**, and **does not
  explain that the app is unable to do the thing.** This is the documented board failure the
  wording exists to prevent: when a fix is out of tool reach this model confabulates a
  mechanism rather than reporting the gap, so a bare "refused" produces either a retry loop or
  an invented limitation. Verify by reading the model's actual next turn, not the card.
- **TPC3.5** GIVEN a denial THEN the confirm token is **not** sent back to the model.

## TPC4 — The connector is not weaker than web

Per ADR `2026-07-19-confirm-gate-surface-matrix`, a channel must not be quietly weaker.

- **TPC4.1** GIVEN a gated write proposed over Telegram THEN the message carries **all four**
  choices as inline buttons (Once / This session on row 1, Always / Deny on row 2 — four on one
  row truncate their labels on a phone).
- **TPC4.2** GIVEN a Telegram permission card THEN the change summary renders as a **monospace
  `<pre>` block** with column alignment intact — a real diff, not wrapped prose.
- **TPC4.3** GIVEN a summary containing markup-like text (a payee literally named
  `<b>Approved</b>`) THEN it renders as **visible text**, never as markup. A permission prompt
  is the single most valuable message for an injected line to appear in.
- **TPC4.4** GIVEN a Telegram allow tap THEN the acknowledgement names the scope AND where to
  revoke it — a connector user has no Settings tab in front of them, so the text has to say it.
- **TPC4.5** GIVEN a Telegram **Deny** tap THEN nothing is applied and the session note carries
  the same human-decision refusal the web path sends (TPC3.3).
- **TPC4.6** GIVEN a tap by a user who is NOT the proposer THEN it is refused unchanged — the
  existing IDOR guard applies to permission taps exactly as it does to confirms.

## TPC5 — Empty / loading / error states

- **TPC5.1 (loading)** GIVEN a grant is being saved THEN the tapped button shows **"Saving…"**
  and **all four buttons are disabled**, so a double-tap cannot record two grants or approve
  twice.
- **TPC5.2 (error)** GIVEN the grant request FAILS (server down, 500) THEN: the call is **still
  approved** (the user tapped it and the card showed exactly what it does), AND a visible
  warning says the standing permission could not be saved and they will be asked again. A user
  who believes they hold a permission they do not is worse off than one who is asked again.
- **TPC5.3 (error)** GIVEN a grant failure THEN the card must NOT report the WRITE as failed —
  the write and the permission are separate outcomes and the message must not conflate them.
- **TPC5.4 (empty)** GIVEN a permission card whose change has no renderable body THEN the
  generic key/value fallback renders — a permission card is never a blind header + buttons.
- **TPC5.5 (resolved)** GIVEN a card already answered THEN re-rendering it (reload, panel
  reopen) shows the resolved ✓/✗ state with no live buttons — an answered card can never fire
  twice.

## TPC6 — The paused turn still LOOKS like a turn in progress

Added 2026-07-22 after a live owner report: a permission card ENDS the backend stream (the
tool_use is left result-less until answered), and approving it opens a fresh stream that
resumes the same work. The UI treated the two streams as two finished turns — "no loading
indicator … makes users feel like the chat is already finished", and a cost chip that appeared
"after the first tool call finished, but then the task was still moving on". One logical turn
must read as one logical turn.

- **TPC6.1** GIVEN a turn that ends on a permission card THEN the assistant bubble shows **no
  cost chip** — the chip is a turn's full stop, and the model has not finished.
- **TPC6.2** GIVEN the user approves THEN the SAME assistant bubble re-enters the streaming
  state immediately (typing cursor back, copy/cost footer hidden) for the whole continuation —
  never a bubble that renders as finished while text is still landing in it.
- **TPC6.3** GIVEN the resumed stream completes THEN exactly ONE cost chip appears, and its
  cost/token numbers are the **sum** of every segment of that turn (before the card + after the
  approval) — approving must never make the pre-card spend disappear from the chip.
- **TPC6.4** GIVEN the continuation itself pauses on a SECOND permission card THEN TPC6.1–6.3
  hold again, accumulating across all segments.
- **TPC6.5** GIVEN a card answered WITHOUT the resolve endpoint (a legacy `propose_*` card that
  confirms by sending a chat message) THEN the bubble's cost chip is released once every card
  on it has been answered — a paused bubble never ends up permanently chip-less.
- **TPC6.6** GIVEN the continuation streams THEN it lands on the SAME assistant bubble as the
  card — approval never opens a second bubble for one action.

## Verification status

| Criterion | How verified | Status |
|---|---|---|
| TPC1.1–1.3, TPC2.1 (client half), TPC3.1–3.3, TPC3.5, TPC5.1–5.3, TPC5.5 | `frontend/src/components/chat/ChangePreview.grant.test.tsx` (13 tests, interaction-level — buttons clicked, request bodies asserted) | ✅ automated |
| TPC1.2 | same file — ordinary card unchanged | ✅ automated |
| TPC4.1–4.5 | `tests/test_chat_confirm_grants.py` (12) + `tests/test_permission_card_render.py` (5) | ✅ automated |
| TPC4.6 | pre-existing `tests/test_chat_confirm.py` IDOR coverage, unchanged by this lane | ✅ automated |
| TPC1.4, TPC1.5, TPC2.2–2.7 | require the gate + `GrantStore` + REST route | ⛔ **[cross-lane]** blocked |
| TPC3.4 | requires a real model turn after a denial | ⛔ **not automatable** — needs `make qa-cu` against a running app |
| TPC5.4 | generic fallback already covered by `ChangePreview.test.tsx` confirm-safety suite | ✅ automated |
| TPC6.1–6.3, TPC6.5, TPC6.6 | `frontend/src/hooks/useChat.resume.test.ts` (4 tests — asserts the message state MID-resume-stream, not just at the end; verified to fail without the fix) | ✅ automated |
| TPC6.4 | follows from the same code path as 6.1–6.3 (each segment is an ordinary `turn_done`); not separately driven | ⚠️ by construction |
| **Browser E2E (`make qa-e2e`) for every criterion above** | — | ⛔ **NOT RUN in this lane** — see below |

**`make qa-e2e` was NOT run and this file does not claim the UI gate is passed.** The card
cannot be raised end-to-end yet: `GATED_TOOL_NAMES` is empty and nothing emits `grant_tool`
until the gate lane lands, so a browser run would have no card to drive. The interaction-level
vitest suite above exercises every click and asserts the resulting request body and message —
which is real evidence about behaviour, and is NOT a substitute for the browser gate. Run
`make qa-e2e` for TPC1–TPC5 once the gate + store + route are merged, and attach screenshots
then.
