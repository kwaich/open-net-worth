# 0021 — Investments IA: hybrid reconciliation of the v2 redesign with shipped reality

- Status: Accepted
- Date: 2026-06-21
- Deciders: orchestrating agent (autonomous "drive E2E" mandate; user AFK and delegated the call)
- Related: RFC `.claude/rfcs/investments-redesign.md`; per-slice designs `docs/rfc/investments/*.md`; ADR 0020 (bonds first-class)

## Context

The v2 Investments handoff specifies a six-tab information architecture. The shipped app diverges: it has five sub-tabs `{Holdings, Allocation, Performance, Income, Equity Comp}`, a standalone top-level **Tax** tab, and a full mention system — several of which are *ahead* of the handoff. A literal build of the handoff would (a) rebuild already-shipped systems (bonds, daily-return series), (b) tear out shipped-ahead features (the mention system, the Tax tab), and (c) churn the IA twice.

The fork — *which IA do we build toward?* — gates the Holdings cockpit, the new Activity tab, and the new Options tab, so it must be decided before those lanes start.

## Decision

Adopt a **hybrid** IA that takes the redesign's structure where it adds value and preserves shipped wins:

1. **Six sub-tabs:** Holdings · Performance · Income · Activity (new) · Options (new) · Equity Comp.
2. **Fold Allocation into Holdings** as an integrated right-cockpit (allocation-vs-target, sector/geo look-through, concentration/risk, rebalancing). Remove the standalone Allocation sub-tab.
3. **Tax stays a top-level main tab** (shipped, ahead of spec). The Activity tab shows a realized-G/L callout that **links out** to Tax and never recomputes tax — honoring the handoff's "tax touchpoint on Activity" as a link, not a duplicate engine.
4. **Keep the shipped mention system** (it powers the ✦ doorways). Ignore the handoff's "mentions deferred."
5. **Bonds are already first-class** → polish only, not a rebuild.
6. **Lucius** gains the floating dock + the `--lucius` violet (`#7C6CF5`) token, with the amber→violet DS-class migration.

## Consequences

- **Positive:** no shipped-ahead feature is reverted; the redesign's strongest ideas (cockpit, Activity, Options, risk metrics, Lucius dock) all land; the IA changes once, not twice.
- **Cost:** folding Allocation into a responsive Holdings cockpit is a medium-effort layout change; the `SEGMENTS` array becomes a serialized integration point (foundation lane owns it — see RFC §4.2).
- **Divergence from handoff (intentional, recorded here):** Tax is a top-level tab rather than an Activity touchpoint; mentions are kept rather than deferred; Bonds/daily-series are treated as done rather than "needs system support."

## Alternatives considered

- **Follow the handoff literally (6 tabs, tax touchpoint inside Activity).** Rejected: partially undoes the shipped standalone Tax tab and re-models the tax surface for no user gain.
- **Minimal churn (keep shipped 5 tabs + just add Options).** Rejected: abandons the cockpit, the Activity ledger, and most of the redesign's value.
- **Defer the IA and ship only IA-independent finishers.** Rejected as the *final* answer (it's a delay, not a decision) but adopted tactically — the independent finishers run in Wave 1 regardless.
