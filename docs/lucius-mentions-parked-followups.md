# Lucius mentions — parked follow-ups

> Two deferred improvements to the `@`-mention **detail popover** and the **transaction chip label**.
> Parked on 2026-06-25 at the user's request ("I'll come back to this soon"). Each is scoped,
> low-risk, and independently shippable. Pick either up directly.

## Context — what already shipped (so you know the baseline)

The chip-tap detail popover (`MentionDetail`) is **id-resolved + dumb** (handoff: `frontend/design_handoffs/handoff_popover_data/`):

- `frontend/src/lucius/objectFields.ts` — `parseCtxFields` / `byId` / `objectFields` / `metaFor` / `chipFallbackRecord`. The single field source, shared with `screenContract.ts`.
- On-screen: `byId(type, id)` reads the row's `data-ctx-fields` (the same fields the assistant sees) → full grid.
- Off-screen: falls back to `chipFallbackRecord` (only what the chip carries) → **partial**. No network call (by the handoff's design).
- Transaction `data-ctx-fields` (built in `frontend/src/components/tabs/accounts/TxnDetail.tsx`, `txnFields`) now carries: `id, date, payee, description, category, amount, account` (or `from_account` + `to_account` for transfers), `note, status, kind`.
- The popover now shows the **`id`** as the last row for every object (`objectFields` `HIDDEN_KEYS` = `{'kind'}` only; `MentionDetail` injects `detail.id` so it appears even off-screen). `kind` stays hidden.

The transaction **chip label** ships payee-first already — see Follow-up 2's status below.

---

## Follow-up 1 — Off-screen detail resolve (popover shows full data for entities not on screen)

**Problem.** When you tap a mention chip for an entity that is NOT currently rendered in the DOM
(scrolled away, a different tab, or a chip in an old chat message whose source screen isn't shown),
`byId` returns null and the popover shows only `chipFallbackRecord` — name + the couple of fields the
chip baked in at staging — NOT the full field grid. The user expects full, fresh data regardless of
whether the entity is on screen.

**Why it's like this.** The popover handoff modeled an all-in-memory prototype. In the real app the
data lives in backend stores; the on-screen DOM is the only in-memory copy, so an off-screen entity has
no full record to read without fetching.

**Proposed fix — lazy resolve-on-tap.** The backend already has per-type resolvers
(`backend/services/mentions/resolve.py`) that turn a `{type, ref}` into a compact field dict (today used
to build the assistant's mention context via `dispatch.format_mention_context`). Expose that to the
client and have the popover use it only on the off-screen path:

- **Backend slice:** add `GET /api/mentionables/resolve?type=&ref=` (thin router → `resolve.py`) that
  returns the resolved field dict for one entity (graceful `{error: 'not_found'}` on a bad ref — the
  resolver already does this). Auth-gated like `/api/mentionables`. Layering: `api → services` only.
- **Frontend slice:** in `MentionDetail`, when `byId(...)` returns null, fetch the resolve endpoint
  (one fetch per deliberate **tap**, not per hover), feed the returned dict through the SAME
  `objectFields`/`metaFor` pipeline, and **cache** it (so re-tapping is instant). Show a brief loading
  state in the popover while the fetch is in flight.

**Trade-off / decision to record.** This bends the handoff's literal "no fetch on hover," but keeps its
spirit (single-source rendering via `objectFields`; one fetch on an explicit tap, never hover-spam) and
is the correct real-app adaptation. **Write an ADR** when implementing (cross-cutting + deviates from a
stated handoff rule).

**Files:** `backend/api/mentionables.py` (+ a resolver-exposing function in `services/mentions/`),
`frontend/src/components/chat/lucius/MentionDetail.tsx`, maybe a tiny `frontend/src/api` client call.
Backend and frontend slices are disjoint → can be built as two parallel agents.

**Acceptance:** tap a chip whose entity is scrolled off-screen / on another tab → the popover shows the
full field grid (fresh from the resolver), not the chip stub; on-screen entities stay instant (no fetch);
a bad/stale ref shows a graceful "not found" instead of erroring.

---

## Follow-up 2 — Transaction chip label: payee-first (type-aware)

**Problem.** The transaction chip label is always the **description**, so payee-style transactions show
a redundant/noisy label (e.g. "Figma annual" instead of "Figma", a long merchant description instead of
the merchant name).

**Proposed change.** Make the label **payee-first with a description fallback** — one rule, no per-type
branching:

```ts
const askLabel = payeeName || t.description || '(no description)'
```

This naturally becomes "depending on transaction type":
- expense / income **with a payee** → the **payee** ("Figma", "Din Tai Fung", "Netflix");
- transfer / adjustment (no payee by model) → the **description** ("EUR bank transfer to IBKR");
- investment buy/sell (no payee) → the **description**, which is the meaningful label
  ("PLTR BUY 10 @ 115.5 – IBKR").

The full description/memo still lives in the detail popover and the picker sublabel, so nothing is lost.

**Small follow-on.** Once the label IS the payee, drop the leading `payee · ` from the picker row
sublabel (`txnMentionSublabel` in `frontend/src/utils/format.ts`) so the payee isn't shown twice
("Figma … Figma · Wise USD · …"). Keep `account · date · amount`.

**Files:** `frontend/src/components/tabs/accounts/TxnRow.tsx` and `TxnCard.tsx` (`askLabel`),
`frontend/src/utils/format.ts` (`txnMentionSublabel` — drop the now-duplicated payee), + update
the relevant tests.

**Acceptance:** a Figma subscription chip reads "Figma" (not "Figma annual"); a transfer chip still reads
its description; the picker sublabel doesn't repeat the payee.

---

## Status

- [ ] Follow-up 1 — off-screen detail resolve (backend endpoint + frontend lazy fetch + ADR)
- [x] Follow-up 2 — payee-first transaction chip label — SHIPPED (`TxnRow.tsx`/`TxnCard.tsx` `askLabel = payeeName || t.description`).
  - Optional remaining: drop the duplicated payee from the **picker-row sublabel** (`txnMentionSublabel`). Skipped for now — the chips (which don't render the sublabel) are already clean; only the open `@`-picker dropdown shows payee in both the label and the sublabel. Doing it changes the `txnMentionSublabel` contract + 3 tests.
