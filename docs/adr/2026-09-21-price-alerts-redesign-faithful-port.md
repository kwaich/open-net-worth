# ADR — Price alerts: faithful port of the "manage + create" redesign

- **Status**: Accepted
- **Date**: 2026-09-21
- **Agent provenance**: Decided by the orchestrator porting the Claude Design page
  "Price Alerts Redesign" (project "Open Net Worth UI", updated 2026-09-21) on the owner's
  request "pull the latest design for alert management and implement it". The owner's stated
  intent in the design chat: "there are two different things — one is a manage alerts which
  should show all alerts of a ticker, one is a form to create."

## Context

The shipped `PriceAlertsModal` was one long form: every alert on a ticker rendered as an
editable row (op select, value, after-it-fires select, instructions textarea), plus a mute
switch and an "Advanced: news sources" section, all committed by ONE Save button that
deleted every alert on the ticker and re-created the set. It carried no per-alert name, no
per-alert on/off, no notification choice, and no sense of where a level sat against the
price. The design replaces it with two views on one host:

- **Manage** — the ticker's live price and day change, a 52-week track with every level
  ticked on it, Lucius's suggested SET (each with the reasoning that justifies it, Add / Skip /
  Add all / Suggest again), the alert list (name, cooldown, channel, distance to fire, an
  on/off switch per row), and the ticker-level settings (Mute, News sources) — every
  control commits itself, there is no Save.
- **Form** — one alert: name, criteria (target price with a direction pair, or a % day
  move), cooldown, notifications, the default query Lucius runs, and additional
  instructions; Create / Save / Delete.

## Decisions

1. **Faithful port, not re-derive.** The prototype's JSX → `priceAlerts/ManageAlerts.tsx` and
   `priceAlerts/AlertForm.tsx`; its `<style>` → `priceAlerts/priceAlerts.css` verbatim (the
   standalone-shell scaffolding stripped, the two DS overrides scoped under `.pa-modal`).
   Same DOM, same class names. `PriceAlertsModal.tsx` keeps its public props and becomes the
   host that owns state and the data seam, so the three callers (Intel watchlist rail,
   All monitors, the asset-detail rail card) needed only a `backLabel`/`changePct`/
   `instrumentId` addition.
2. **Per-control commits, no reconcile.** Each write is exactly one request against the alert
   choke: a row toggle → `PATCH {enabled}`; Create → one `POST`; Save → one `PATCH` on that id;
   Delete → one `DELETE`; a suggestion's Add → one `POST`; Mute / a feed row → one watchlist-item
   `PATCH`. The delete-all-then-recreate reconcile is gone, so an untouched alert keeps its id,
   its `last_fired_at` and its armed state.
3. **Three genuine backend gaps built, not faked.** The store, API, monitor and AI tools gain:
   - `name` — the alert's own short name (`label` stays the ticker's display name);
   - `kind: price | move` — for `move`, `value` is a percent-of-day-move threshold vs the
     previous close (`>=` rises by, `<=` falls by); the monitor evaluates it against the
     `prev_prices` it already fetches, keyed `f"{op}{value}%"` so a `>= 5` price alert and a
     `>= 5%` move alert never share armed state, and narrates it as a move;
   - `notify: push | in_app | push_email` — per-alert delivery: the configured
     `alert_channels`, in-app only (no external push), or those channels plus email when the
     email channel is available.
   Existing price alerts read back unchanged (defaults `price` / `push` / `""`).
4. **The 52-week track reads the real quote stats.** `GET /api/intel/assets/{id}/stats`
   (the same endpoint QuoteStats renders) supplies `week52_low/high` and `prev_close`; the
   day change falls back to the asset-detail quote when the caller has it. With no instrument
   id the track still draws from the price and the levels, and the range line is omitted.
5. **Lucius's suggestions stay client-side** (`luciusSuggestAlerts`, unchanged maths) and now
   carry the design's short names ("Take-profit zone", "Protect the gain", …) so a suggestion
   Added lands as a named alert. Adding creates the alert immediately, as the design does.
6. **Document, don't drop silently.** Every deliberate deviation and every prototype affordance
   not backed by the app lives in `docs/price-alerts-redesign/PARITY_AND_GAPS.md`.

## Consequences

- `docs/qa/acceptance/price-alerts-decoupled.md` PAD-5 (reconcile semantics) is rewritten
  to the per-control contract; `price-alerts-redesign.md` (PAR-*) is the new acceptance file.
- The legacy modal's `intel-pa-*` list/row styles are removed from `index.css`; the
  `intel-pa-*` footer/mute/intro rules survive because `TopicDetailModal` still uses them.
- The AI tools accept `name`; `kind`/`notify` are REST-only for now (a follow-up can expose
  them once Lucius has a reason to set a day-move alert).
