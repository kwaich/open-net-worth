# ADR — Settings overlay redesign: faithful port of the design handoff

- **Status**: Accepted
- **Date**: 2026-08-05
- **Agent provenance**: Decided by the orchestrator driving the Settings redesign from
  `frontend/design_handoffs/settings_handoff/`, on the owner's instruction to use the
  `port-design-handoff` skill and "continue until everything is done".

## Context

The handoff ships a complete, interactive **prototype** (its own React/babel app + the
`rd-` stylesheet `shell.css`, built on the real design tokens) plus a twelve-section prose
spec. It supersedes the earlier Profile and Settings notes.

Three ideas carry the surface: **Settings is an overlay, not a place**; **every setting is one
row** (label · consequence · control); **nothing is saved by a button** unless the change is
only meaningful as a set.

The app already had a Settings tab with 7 sections and a separate top-level **Profile** tab.
The backend was ~80% ready — 7 of the 9 designed sections re-skin against endpoints that
already existed.

## Decisions

1. **Faithful port, not re-derive.** The prototype is the reference implementation: its JSX
   became typed TSX with the same DOM and the same class names, and `shell.css` came across
   rule-for-rule. The `.rd-set-body` flattening rules key off that exact DOM (a panel's body
   becomes the card; each direct child becomes a hairline row), so the markup *is* the layout
   contract — re-deriving on `components/ui/` primitives would have broken every row.

2. **The whole ported sheet is scoped under `.onw-settings`.** `tabs/automations.css` already
   ships its OWN `.rd-input` (fixed 34px height) and `.rd-dot`; both sheets are unlayered, so
   unscoped the winner would be whichever tab React happened to load first — non-deterministic
   across route entry points. Scoping is **cascade-only**: no DOM and no class name changes, so
   pixel fidelity is untouched, and the four other redesigns still to come from the same
   `shell.css` can't collide either.

3. **The dialog is hosted on the design-system `Modal size="workspace"`.** It already owns the
   portal, focus trap, scroll lock, Escape layering, the `--chat-width` inset and — the reason
   it matters here — the Lucius-dock exemption that keeps `.lx-dock`/`.lx-side` clickable *and
   focusable* from inside a modal. The prototype's own `.rd-set-scrim`/`.rd-set-modal` are
   standalone-shell scaffolding, in the same category as its `.onw-*` nav rail, and were
   stripped; `settings.css` restyles the DS shell to the prototype's exact geometry
   (`min(1060px,100%) × min(760px,100%)`, `--modal-bg`, `--radius-2xl`, 28px scrim padding).

4. **Settings keeps its route.** The design calls it "an overlay, not a destination", but
   `/settings?sub=` is a real contract: Overview's "Import a statement" deep-links into it, and
   `Settings.urlSync.test.tsx` pins the shape. So the route stays and RouterShell renders the
   **last real tab underneath** the dialog, which is what "over whatever you were doing" means
   in an app that has to survive a cold load straight to `/settings`.

5. **`?sub=` has exactly one writer per direction.** Mirroring `section` into the URL from an
   effect on `[section]` fought the inbound deep-link and produced a TanStack update-depth loop
   (see Consequences). Outbound writes now happen where the user acts; an inbound `?sub=` only
   ever sets state, and seeds the *initial* section rather than correcting it after first paint.

6. **Vision capability comes from the provider.** The prototype's `TEXT_ONLY` array is
   explicitly a stand-in — the handoff's own note says an unknown model ID is *unknown*, not
   incapable, or the warning cries wolf on every custom gateway. The app already had exactly
   that contract (`ModelOption.vision`, backend-derived; only a positive `false` warns), so the
   port uses `modelRejectsImages` instead of a hardcoded list.

7. **One FIRE editor, two entry points.** The handoff is emphatic: *"Do not fork this modal per
   tab. If Plan and Profile can disagree about the withdrawal rate, one of them is lying to the
   user about their retirement date."* The two surfaces genuinely render different chrome (the
   design draws Plan's `pl-` dialog and Settings' `rd-` dialog differently), so what is shared
   is everything that could ever *disagree*: the field set, the seeding, the SWR formula, the
   derived FIRE number and the exact PUT payload — extracted from Plan's private modal into
   `plan/fireSettings.ts`.

8. **Build the genuine gaps, document the rest.** Eight gaps were real and were built with
   tests: `profile.nickname`; the three display-format prefs; memory facts list/forget;
   backup download; backup restore; backup auto-vs-manual provenance; cost `by_provider`;
   extension enable/disable. A ninth ("forget all dedup decisions") turned out not to be in the
   design and was not built. Everything else wires to endpoints that already existed.

9. **Material Symbols Rounded is loaded, as the design specifies.** The rail's nine icons are a
   real icon set, not hand-drawn paths. The app already loads Plus Jakarta Sans from Google
   Fonts, so this adds no new third-party origin. `display=block` rather than `swap`, because
   the glyphs are ligatures — a fallback render would spell out "person"/"smart_toy" in the rail.

10. **Profile stops being a tab.** It is section 1 of Settings. `profile` is removed from
    `CONFIG_TABS` (so it leaves the desktop rail and the mobile More sheet) but stays in `TABS`,
    so `/profile` keeps working as a direct URL and opens Settings on that section.

11. **A backup DOWNLOAD is not the backup.** The security review found the first cut of
    `GET /api/backup/{name}/download` streaming the raw DR archive — which, by design,
    contains `auth.json` (session-signing HMAC key, API bearer token, password hash) and every
    connector/broker/LLM credential on the box. One authenticated GET was an exfiltration
    primitive for all of it. The archive stays whole on disk (that is what makes it disaster
    recovery); what leaves over the wire is **re-zipped with every credential stripped**, and
    the response names what it left out in `X-Onw-Stripped`. This is the same policy
    `GET /api/export` already had via its allowlist.

12. **A RESTORE must not roll back an approval, a nonce or an audit trail.** The first cut
    protected only `auth.json`. `ai/checkpoint` has maintained the full list of "state where
    going backwards is the hazard" for a long time — and `services` cannot import `ai`. That
    knowledge now lives in `core.sensitive`, which both layers can reach, with
    `tests/test_sensitive_paths.py` failing if the two drift. Restoring no longer resurrects a
    revoked tool grant (revoke → restore → the grant is back is privilege escalation through an
    unrelated feature), un-pops a single-use MCP nonce (approvals never expire, so an
    already-applied external money movement would become applicable a second time), re-authorises
    a connector sender the owner removed, or truncates `txn_audit.json`.

13. **Both new backup routes carry the CSRF header requirement.** `AUTH_TRUST_LOOPBACK`
    defaults true, so on a local install the auth gate returns early for anything from
    127.0.0.1 — and a plain `<form>` POST is a CORS *simple* request that needs no preflight.
    Without the guard, any page the operator had open could roll their data back; the attacker
    cannot read the response, but the side effect is the whole attack.

## Consequences

- **A pre-existing time-bomb was fixed on the way in.** `_prune_old_backups` read the real clock
  while `run_backup` stamped a caller-supplied one, so a backup could prune itself the instant
  real time drifted past retention from its stamp. It had already broken four tests in
  `tests/test_backup_service.py`, and it made `restore_backup` delete the archive it was
  restoring. Pruning now uses the same clock, and restore opens the archive before the safety
  backup runs.

- **Restore deliberately does not roll back `auth.json`.** Instance credentials are not "your
  data": reverting a password change can lock the operator out of their own instance, and there
  is no reset email to recover through.

- **One deliberate deviation from the prototype**, commented at the rule in `settings.css`: a
  task-model row carries two captions (its description, plus the warn line a vision task gets on
  a text-only model) and `shell.css` pins both to the same grid row, so they render exactly on
  top of each other. Verified against the running prototype — it does this too. An unreadable
  warning is not a warning, so they stack.

- **25 legacy components are now unreachable** and are kept-and-flagged for one release rather
  than deleted, so the redesign can be reverted without archaeology. Fifteen of them still have
  passing tests — green against code no user can open, which is why each carries a notice.
  Listed for removal in `PARITY_AND_GAPS.md`.

- Display-format preferences are applied by choosing the Intl **locale** that produces the asked
  convention, so every existing formatter honours them without being rewritten. Anything that
  formats a date or number outside `utils/format.ts` does not yet follow the setting — enumerated
  in `PARITY_AND_GAPS.md`.

## Alternatives considered

- **Renaming the ported classes** (`st-` instead of `rd-`) to dodge the `automations.css`
  collision. Rejected: it breaks the "same DOM, same class names" property that makes a
  verbatim port checkable against the prototype, and it would have to be repeated for each of
  the four remaining redesigns from the same sheet.
- **Hand-rolling the scrim from Radix** (as `PlanningModal` does) instead of using the DS Modal.
  Rejected: it would duplicate the Lucius-exemption logic, which exists in exactly one place
  today and is load-bearing for keeping the assistant reachable over a dialog.
- **Making Settings pure UI state with no route.** Rejected: it would break the Overview
  deep-link and every existing `/settings?sub=` link.
