---
title: Notification channels are chosen per kind of update, and each price alert saves its own
status: Accepted
date: 2026-09-30
deciders: repo owner (chose the per-kind grid + self-contained alerts) + Claude (implementation)
---

# 2026-09-30. Notification channels are chosen per kind of update, and each price alert saves its own

## Context

One list, `global_config.alert_channels`, decided where every external push went: price
alerts, watch alerts, news analyses and both daily digests. A price alert's `notify`
(`push` / `in_app` / `push_email`) could only say "that list", "nothing" or "that list +
email". So:

- The same alert label ("In-app + Telegram") changed meaning when someone flipped a toggle on
  another screen, and nothing on the alert showed it.
- Some routes were impossible to express: email-only for one alert while Telegram was on, or
  digests by email but alerts on Telegram.
- A channel that was off or removed was skipped silently.

## Decision

1. **Per-kind routes.** `global_config.notify_routes = {price_alerts, watch_alerts, news,
   digests}`, each a list of external channel ids. Settings › Intelligence › Alerts & digests
   shows it as one grid: kind × channel, with in-app a locked "always".
   - `watch_alerts` covers the explained watch alerts (`watch_analyst`) and deterministic
     crash-watch (`price_monitor` crash lane).
   - `news` covers the analyst's news and crash analyses.
   - `digests` covers pre-market and end-of-day.
2. **Price alerts own their channels.** Each alert stores `channels` (ids; `[]` = in-app
   only). The `price_alerts` route is only the DEFAULT a new alert starts from. It is read at
   create time (`services.alerts.apply_alert_change`) and never at fire time, so changing it
   never changes an existing alert.
3. **Unknown ids are refused, not dropped.** `services.notify_routes.normalize_channels`
   raises on an id the connector model doesn't know (REST 422, tool `ok: false`). A known
   channel that is merely off is kept: it is skipped at send time, flagged in amber in the
   grid, and shown as "(off)" on the alert.
4. **One zero-delivery policy.** Every producer (price alerts, crash-watch, watch alerts, news)
   goes through `push_retry.handle_push_result`:
   - every channel off or removed → an in-app "Alert not delivered" notice now;
   - otherwise → the bounded retry, and a notice when it is exhausted.
5. **Migration** `202609301800_notification_channels_per_kind_and_per_alert` keeps every
   destination:
   - each kind is seeded with the old list;
   - each alert's `notify` is resolved once against its book's list (`in_app` → `[]`,
     `push` → list, `push_email` → list + `email`).

   `downgrade()` maps back.

## Consequences

- An alert shows exactly where it goes, and nothing links it to another screen.
- Settings answers "where do digests, news and watch alerts go" per kind.
- A user who turns Telegram off in Settings can still get a Telegram ping from a price alert
  that chose Telegram itself. The grid labels the price-alert row "Default for new alerts.
  Each alert can change its own." and every alert names its channels, so this is visible, not
  hidden.
- Lucius: `update_monitoring_config` takes `notify_routes` (partial, per kind) instead of
  `alert_channels`; `set_price_alerts` / `update_price_alerts` take `channels`.

## Alternatives rejected

- **Keep one list and relabel it:** the coupling stays, so a label still changes meaning
  elsewhere.
- **Drop the per-alert choice (alerts always follow Settings):** the owner wants per-alert
  control.
- **A per-alert "send outside the app" switch over one global list:** it adds a control
  without making routes expressible (still no email-only alert).

## Agent provenance

Claude (Opus 5.5), interactive session on `main`, after the owner rejected the relabel and
switch variants and chose this design.
