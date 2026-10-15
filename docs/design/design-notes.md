# Platform Design — running discussion notes

**Purpose:** a running log of the design *discussions* (options weighed, decisions reached, open questions) so context isn't lost between sessions. Complements — does not replace — `docs/launch/PLATFORM_MASTER_PLAN.md` (the formal sequenced plan) and `docs/research/agentskills-conformance.md`. When a decision here hardens, promote it into the plan or an ADR.

Started 2026-07-08. Append newest thoughts under each thread; don't rewrite history.

---

## Thread A — Automations (rules + scheduled tasks, unified)

### Decisions
- **One object model, not two.** A rule and a scheduled task are the same thing: **Trigger → optional Condition → Action**. They differ only by *trigger type*. One list, one builder. (Formalized in plan P2-C "Rules v2".)
- **Three trigger families:**
  - **On save** — runs INLINE, inside the write, *before* a transaction is persisted; can MUTATE the draft (label it). Fast/deterministic only. Action = set-fields only. (This is today's "rules".)
  - **Event** — runs AFTER a fact happened; reacts, can't mutate the trigger. Can be slow (call an agent). Action = anything except inline set-fields.
  - **Schedule** — time/cron/RRULE.
- **On-save vs Event distinction = "shape it as it's written" vs "react after it happened."** The *action* decides which you need: labeling → on-save; anything else → event. Technically on-save is the synchronous special case of `transaction.created`, but it's distinct enough (timing, capability, atomicity requirement) to be its own type. Categorization MUST stay inline for the write to be atomic (per the rules audit) — not just a UI choice.
- **The model is DOMAIN-WIDE, not transaction-only** (owner push, 2026-07-08). Legacy rules are transaction-only; the event seam emits from *every* choke point. Triggers span: accounts/balances, net worth, investments/allocation/concentration, budget (envelope overspent, RTA negative, income arrives), commitments/bills (installment due, subscription price up), equity (vest approaching), fixed income (coupon), tax (gains cross a bracket, TLH opportunity), connectors/data-health (sync failed, connection expired), intel/market (crash signal, news spike), time. Actions likewise broaden: move money, auto-assign income, propose rebalance, propose TLH, fund envelope, create commitment/goal, generate report, update watchlist, execute mandate, + notify/run-agent/propose/create/flag.
- **The system's OWN automations are just seeded automations.** Crash-watch, vest reminders, coupon reminders, digests, sync-health are hardcoded today; a general model expresses them in the same vocabulary → inspectable, editable, cloneable. Validation test: *can the system's built-in automations be expressed as user automations?* If not, the model is too narrow. (Also lets the ~12 hardcoded scheduler jobs migrate onto the primitive — one pane of glass.)

### Open questions
- One unified "New automation" entry, or two friendly entry points ("New rule" / "New scheduled task") that both drop into the same builder pre-set to a trigger type? (List stays unified either way.)
- Ambiguous case: user picks "a transaction is saved" trigger but a non-labeling action → that's really an *event*; UI must steer gracefully, not feel like a wrong turn.
- Trigger picker organized **by domain area** (Cash, Investments, Budget, Bills, Tax, Data-health, Market, Time), not "transaction stuff + a timer".

### Artifacts
- UI mockup (unified list + builder + dry-run + states): `scratchpad/automations-ui.html` → published artifact.
- Functional design brief drafted (behavior-only, no visuals) — to be rewritten with the full domain-wide surface before handing to a design agent.

---

## Thread B — Budgeting (multi-lens + community engines)

### Decisions / leanings
- **Budget paradigm is pluggable** (plan §8.3) — envelope becomes engine #1, not the only way.
- **Setup cost is NOT a reason to withhold lenses** (owner, 2026-07-08). Lucius drafts any lens's config from history in one sentence (AI-first: draft → review → approve). Caveat: Lucius dissolves *setup* cost, not the *ongoing decision/maintenance* cost of heavy paradigms — but that residual is inherent to the paradigm, and it's an argument *for* multi-lens (easy escape to a lighter lens instead of quitting).
- **Multi-lens, with a designated home lens** (owner leaning + refinement). Budgets are a LENS over shared transaction data (computed-not-stored), so the same month can be viewed as envelope / 50-30-20 / caps / just-track simultaneously — like Notion showing one database as Table/Board/Calendar. Rationale: paradigm mismatch is the #1 reason people abandon budgeting apps; multi-lens makes experimentation safe + non-destructive; Lucius makes it cheap. **Caution: needs ONE canonical "home" lens or it becomes choice-paralysis / budget-shopping.** Multi-lens, singular home — not a democracy of equal views.
- **Lens cost is asymmetric:** *derived* lenses (50/30/20, just-track) need zero config — always available to peek, for free, on any book. *Configured* lenses (envelope assignments, caps) carry owned state → need setup (which Lucius drafts).
- **Community can build new budgeting methods** (extends plan §8.3). Two tiers matching the extension model: *declarative lenses* (map categories→buckets + targets + a render shape) → pure-data domain pack, T0, easy (50/30/20, kakeibo, value-buckets, most proportional methods); *computational engines* (novel fold logic/interactions) → code plugin, P4, trust-gated.
- **The load-bearing requirement: the budget-engine INTERFACE must be paradigm-neutral, not envelope-shaped.** If it's "envelope with variations," only envelope-like things plug in and extensibility is fake. Validation test (mirrors Thread A's): *our own paradigms must be instances of the interface* — envelope is engine #1 implementing the same contract a community engine would, never a privileged special case.

### The paradigms surveyed (effort ↔ control spectrum)
Envelope/zero-based (YNAB) · category caps · 50/30/20 (percentage) · pay-yourself-first/reverse · value-based buckets (Ramit) · goal/sinking-funds · cash-flow/calendar · just-track (anti-budget). Plus rolling/flexible, priority-waterfall.

### Open questions
- Pay-yourself-first: a budget engine, or an **automation** (auto-assign income)? Leaning automation (Thread A), not a lens.
- Goal-based: budgeting, or the **commitments/targets** feature wearing a budget hat?
- Which 3–4 engines ship first? Leaning: envelope (have), caps (mostly built), 50/30/20 (derived), just-track (derived) — spans the spectrum.

### The paradigm-neutral Budget Engine contract (sketch v0 — 2026-07-08)
Principle: **an engine is a pure fold + optional owned state over shared, read-only money data, plus declared verbs.** The host owns the money data, the rendering shapes, and the AI plumbing; the engine owns only its logic, its state schema, and its verbs.

Every engine (ours + community) provides:

1. **Descriptor** — `{ id, name, description, question, kind: "derived" | "configured" }`.
   `question` = the one thing this lens answers ("Does every dollar have a job?" / "Are my proportions healthy?"). `kind` = derived (zero setup) or configured (carries state).

2. **State schema** (configured engines only) — a typed, portable definition of what it stores.
   Envelope: `{ assignments: {envelopeId: amount} }` (per-period). Caps: `{ caps: {categoryId: amount} }`. 50/30/20: `{ targets: {needs,wants,savings}, mapping: {categoryId: bucket} }`. Just-track: none.

3. **fold(period, shared, ownState, priorState) → ViewModel** — the core.
   `shared` = READ-ONLY transactions + income + categories + accounts (engine NEVER owns these). `priorState` = last period's computed state (for carryover, e.g. envelope's `max(0,prev)+assigned+activity`; ignore for per-period lenses).

4. **ViewModel** — the neutral renderable shape every engine emits, so the host renders any engine without knowing the paradigm:
   - `headline: { label, value, status }` — the hero metric (RTA / savings-rate / busted-caps count / net-saved).
   - `groups[]: { label, measures[], status, drilldownRef }` — `measures` = a small typed set (assigned/spent/available, OR target%/actual%, OR just amount).
   - `shape: "rows" | "proportion-bars" | "breakdown" | "gauges"` — pick from a few host-provided layouts (keeps lenses portable + visually consistent).
   - `health` — overall month verdict.

5. **Verbs** — declared actions the engine exposes: `[{ id, label, params }]` (envelope: assign, move, set-target; caps: set-cap; 50/30/20: set-target-pct; just-track: none). Host renders controls for declared verbs. `applyVerb(verbId, params, ownState) → newState` is the write path — routes through the existing budget choke point (`apply_budget_change`). Because verbs are *declared*, Lucius can operate ANY engine with no per-engine AI code.

6. **draft(shared, history) → proposed ownState** — the "set it up in one sentence" hook. Lucius drafts config from history; user approves a diff. Derived engines skip this (no state).

**Why this is multi-lens-native:** `fold` is a pure function over shared read-only data, so N engines can run over the same month at once — that IS multi-lens. Derived engines run for free anytime; configured engines run when they have state. **Why it's community-safe:** a declarative engine is almost all data (mapping + targets + a shape choice); only a computational engine writes a real `fold`, and that goes through the plugin trust tier.

### Open interface questions
- How much rendering can stay host-owned (the `shape` enum) before a genuinely novel lens needs custom UI (→ code plugin + iframe panel)?
- Cross-period state migration when switching home lens (should be free — shared data is untouched; only the *active* lens's state matters).
- Does `ViewModel` need a "sub-groups" level (envelope groups contain envelopes; 50/30/20 buckets contain categories)? Probably yes — one level of nesting.

---

## Thread C — Wealthfolio addon system (competitor reference, studied 2026-07-08)

Wealthfolio (open-source local-first investment tracker, the one shipped finance-app plugin precedent) shipped an addon system. Studied their docs + SDK. Model: addons = TypeScript/React ES modules exporting `enable(ctx)`; `manifest.json` (`id, name, version, main, permissions[], sdkVersion`); brokered Host API (~20 domain APIs: accounts, portfolio, activities, market, secrets, network…); React UI via a shared `@wealthfolio/ui` library; ZIP install + a lightly-moderated Store; a first-party SDK (`@wealthfolio/addon-sdk`), dev-tools CLI, and hot-reload dev server.

### Validates our design (they independently reached — and EVOLVED into — our architecture)
- **They MIGRATED from in-process → iframe sandbox + brokered API.** The older docs describe in-process isolated context; the current docs describe "isolated iframe sandboxes with brokered API access." That evolution is real-world confirmation that in-process is insufficient and iframe+broker is where you end up — de-risks our §6.2 iframe-panel + broker choice.
- Risk-tiered permissions (high/medium/low) declared in manifest + user consent ≈ our capability grants + T0/T1/T2.
- Declared+audited network hosts ≈ our `network.allowedDomains` + reasoning.
- Namespaced routes (`/addons/<id>`) + a shared UI component library ≈ our host-provided design-system panels.

### STEAL (concrete)
1. **Install-time static analysis showing DECLARED vs DETECTED permissions.** They scan the addon's code for actual API usage and show the user *both* what the manifest declared AND what the code actually calls — a mismatch is a surfaced red flag. Cheap, high-signal, catches under-declaration. Adopt in our install pipeline (complements the ClawHavoc-inspired scanning).
2. **Host-injected secrets — never hand the addon the raw secret.** The addon declares it needs to call `api.acme.com`; the host injects the auth header at request time from addon-scoped keyring storage, so addon code never sees the secret. Stronger than "grant the addon the secret." Adopt.
3. **First-party SDK + hot-reload dev loop as a P4 deliverable, not an afterthought.** DX is *why* Wealthfolio is the shipped precedent — they made building easy (typed SDK, CLI, auto-discovered localhost dev server). Plan the SDK + local dev loop as first-class.
4. **Ship finance-specific UI primitives to plugin authors** (`@wealthfolio/ui` has `AmountDisplay`, `GainAmount`, `CurrencyInput`). This is the concrete answer to the owner's earlier "provide our own design system" question — addon UIs look native because they're built from our money-aware primitives. We already have `ui/`; expose it.

### Where we're deliberately AHEAD / different
- **No code signing / provenance / registry trust pipeline** (their docs explicitly say no signing; ZIP + light store moderation). Given ClawHavoc (OpenClaw registry → 11.9% malware), that's an unhit risk at their scale. Our registry trust pipeline (signing, daily rescan, publisher identity, provenance) is ahead — do NOT copy their gap.
- **They allow direct brokered WRITES to core financial data** (`portfolio.update`, `activities.create/import`) under a "high" permission. For a money app that's more permissive than we want — our writes route through choke points + propose/confirm, no silent money movement. We're more conservative on writes, correctly.
- **UI-addon-centric only.** Wealthfolio addons extend the *frontend* (a page a human clicks). Ours has two surfaces: iframe panels (like theirs) AND out-of-process MCP servers (backend tools/data/asset-classes). So a plugin here can bring backend logic (a quant signal provider, a new bank connector), not just a UI panel — broader.
- **No AI dimension.** Their addon adds a page; ours adds capability the AI can wield — plugins contribute skills/agents/tools Lucius uses and panels Lucius composes. This is the fundamental differentiator; Wealthfolio has no analog.

### Net
Their existence de-risks the whole bet (a finance app CAN ship a safe-ish extension system, and they landed on our exact sandbox+broker+capability model). Steal the four DX/trust mechanics above; keep our advantages (registry trust, backend+AI extension). Sources: wealthfolio.app/docs/addons, github.com/wealthfolio/wealthfolio/docs/addons, npm @wealthfolio/addon-sdk.

### Owner ruling 2026-07-08 — plugin permissions = graded trust, NOT gatekeeping
"Don't be too strict about permissions; never stop users from doing anything; safe default, but let them enable anything they want." Reconciliation (this is §8.8 applied to plugins, not a weakening of security):
- **The permission model's job flips from GATEKEEPER → INFORMANT + control surface.** It doesn't restrict the *user*; it gives the user visibility + control over what *third-party code* does on their machine. Removing it wouldn't free the user — it'd free the plugin *author* over the user's money, silently. The model IS the sovereignty mechanism.
- **Nothing is categorically forbidden to a plugin.** Default safe (minimal access); the user can grant anything up to full access / direct writes / money movement. Friction scales with risk (read-only panel = one click; moves-money = deliberate informed grant) but never becomes refusal. Revocable + visible always.
- **CORRECTION to Thread C's "write conservatism":** propose/confirm is the DEFAULT, not a ceiling. A user can grant a plugin capped, audited direct-write (like agent auto-apply); autonomous money movement is expressible via the execution ladder (§8.7), capped + consented.
- **What stays is PRO-user, not anti:** declared-vs-detected + host-injected secrets = visibility that helps the user decide. The integrity core (audit always on, caps enforced in core outside plugin reach, no plugin self-widening its grant) is what makes "enable anything" safe instead of a self-escalation hole — it guarantees the user's grant stays the user's, not the plugin's to rewrite.
- One-liner: **forbid the user nothing; never let a plugin take what the user didn't knowingly give.**

---

## Thread D — Data-model extensibility (the "xlsx model") + extension examples

### Extension examples (span the tiers; each lands in a native surface, not a walled plugin area)
- Kakeibo budget **lens** (data pack, T0) → lens switcher, next to built-ins.
- Watch/whisky **collection** = custom **asset class** + **custom fields** + **panel** (code plugin) → add-holding flow, counts in net worth via declared valuation hook, a Collection panel.
- Niche bank/exchange **connector** (backend MCP plugin) → Connectors, syncs into the same ledger with dedup.
- **"Landlord" bundle** = custom entity `property` + fields + panels + automations + report → one install grows a whole vertical.
- Momentum-signals **quant** = tool + agent + panel → Lucius can query it, a watcher agent, a signals panel (AI-wieldable — the differentiator).
- Weird-bank **import mapping** (data pack) → statement now parses.
- Principle: **the contribution TYPE decides where it lands** (lens→switcher, panel→dashboard picker, connector→Connectors, asset-class→add-holding, skill→slash command, automation→Automations list, tool→Lucius). Extensions weave INTO native surfaces (anti-Obsidian-bolted-on).

### Owner framing 2026-07-08 — data model should be "like xlsx": strict where correctness needs it, open everywhere else
Current state: **NOT extensible** — fixed Pydantic schemas; extras get crammed into `tags[]`/`note` (domain audit finding). Target (owner's text-file-vs-xlsx analogy):
- **A plain text file / spreadsheet is infinitely extensible because it has zero structure + zero correctness — exactly what we CAN'T be** (structureless → net worth/tax/budgets stop being guaranteed correct, which is the whole reason to use us over a spreadsheet).
- **Xlsx is the sweet spot: a currency cell in a SUM is validated; the note cell beside it is freeform; you can add columns and sheets.** That's the architecture:
  1. **Strict typed CORE fields** (amounts, currencies, dates, account links, two-leg structure) — money math depends on them; they stay typed (the cell-in-a-formula).
  2. **Open `custom_fields` bag per entity** (namespaced, typed values) — "add a column"; user/plugin declares a field, core passes it through, renderers show it, it's filterable.
  3. **Plugin-owned entity types** (`property`, `insurance_policy`, `loan_given`) in their own namespaced store, generic CRUD — "add a sheet"; novel things become first-class, not shoehorned.
  4. **Declared participation hooks** (same pattern as the budget-engine contract): a custom asset declares a "current value" → folds into net worth; a custom field declares a type → queryable. Core computes over hooks; never needs to know what a "watch" is.
- **Synthesis:** strict skeleton (invariants) + open extension bags + plugin entities + declared hooks = the only way to be BOTH correct (wealth manager) AND expandable (platform). The xlsx middle.
- **Flags:** (a) this is BIGGER than P3's "custom fields" line — it's the extensibility SUBSTRATE, and it must be designed ALONGSIDE the SQLite migration (P1-A): custom fields = JSON column?, plugin entities = plugin-owned tables?; getting the shape right early avoids a painful re-migration. (b) Open Q resolved below.

### Owner ruling 2026-07-08 — participation "as extensible as possible": full participation via typed INPUTS, core owns the math
Answer to "how much must extension data participate vs be stored/shown": as much as possible, and it's achievable without losing correctness. Principle:
- **Plugins declare FACTS (typed inputs); core computes TRUTHS.** This is `recompute-not-trust` (never trust stored `base_amount`) generalized platform-wide. A plugin never writes a value into net worth; it declares e.g. `{value: {amount, ccy, as_of, source}}` and core's fold reads it like a manual-priced holding — same conversion/plausibility/staleness checks, same code path. The plugin joins the computation graph as an INPUT NODE; it never edits the outputs.
- **Participation is opt-in PER FIELD, set by how much it's typed + role-declared** (the xlsx "a column participates in SUM to the degree it's a number"): untyped text → stored+shown; typed value → filter/sort/search; number+role"aggregate" → reports; base-ccy value+role"asset value" → net worth+allocation; +basis+value-over-time → returns/P&L; +typed taxable events → tax engine. **The ceiling is set by the plugin's typing/declaration, not by a restriction we impose.** Go as far as you type.
- **One boundary (not a real limit): additive, not destructive, for canonical outputs.** Inputs are fully extensible. A plugin can ADD a new metric/lens beside net worth (novel risk score etc., computed over shared data, shown as an option) but can't silently REDEFINE the canonical net-worth number everyone's taxes/decisions depend on. Same additive-vs-destructive line as multi-lens budgeting: one canonical home, infinite additive views.
- **What makes "let everything participate" safe rather than reckless:** core owns the math, plugins own the inputs; declared typed inputs get the same validation as native data. Strict where correctness lives (the computation), open everywhere else (inputs + additive views).
