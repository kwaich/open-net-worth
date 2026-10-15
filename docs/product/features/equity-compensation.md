---
title: Equity compensation
description: Add an RSU, ESOW or LTIP grant, record each vest and share delivery, and see what is vested versus still locked.
category: features
order: 100
audience: [user]
status: beta
related: [features/investments, concepts/net-worth, concepts/positions-lots-and-cost-basis]
keywords: [equity, RSU, ESOW, LTIP, vest, grant, stock compensation, release, tranche]
---

# Equity compensation

If part of your pay is company stock — RSUs, ESOW, or a performance (LTIP) grant — add the grant
once and Open Net Worth tracks its vesting schedule, reminds you what vests next, and turns each
vest into shares you own. Unvested shares aren't yours yet (you'd lose them if you left), so they
are **not** counted in net worth; they show separately as unvested value.

You'll find it under **Investments → Equity Compensation**.

**Beta:** fully working, but newer than the ledger and budgeting — double-check the figures
against your broker statement.

## Add a grant

1. Open **Investments → Equity Compensation** and click **+ Add grant**.
2. Fastest: paste a line from your grant letter into **Paste grant letter or describe it** — for
   example *"1,000 NVDA RSUs, quarterly over 4 years from 2026-09-15"* — and click **Fill from
   text**. It fills in what it recognises; check the fields below.
3. Or fill it in yourself:
   - **Instrument / ticker** — the company's stock.
   - The vesting schedule, either with the **Schedule generator** (**Total units**, **Frequency**
     — Monthly, Quarterly or Annual — **Over (years)** and **First vest**; click **Preview** to see
     the dates) or as **Manual tranches** (one row per vest: **Vest date**, **Units**, and
     **Kind** — Standard or Performance; **+ Add tranche row** adds another). **Edit manually**
     turns a generated schedule into rows you can adjust.
   - **Grant date**, and optionally **Price at grant (per unit)** — for reference only.
   - **Grant type** — RSU, ESOW or LTIP (performance).
   - **Tax event** — **Tax at vest** (the usual case in Singapore, the US, Germany) or **Tax
     deferred** (Vietnam and others). This only labels the vest in your reports; the app
     calculates no tax.
   - **Share delivery** — **Sellable at vest** (the usual case) or **Delivered later** if your
     plan hands the shares over some time after the vest date.
   - **Account** — the brokerage or custodian account the shares land in. **+ New account…**
     creates one.
4. Click **Save grant**.

The grant appears under **Grants** with its total, vested and unvested units and next vest date.
Click a grant's row to see every tranche, and to **Edit grant** or **Delete grant**.

## Record a vest

When a tranche vests:

1. Find it under **Upcoming vests** (or in the grant's tranche list) and click **Mark vested**.
   A vest date that has passed without being recorded is tagged **Overdue**.
2. Check the form:
   - **Units** — defaults to the whole tranche.
   - **FMV per unit** — the market value on the vest date, pre-filled from price history when
     available. Change it to match your broker statement.
   - **Price paid per unit** — `0` for RSUs; set it for ESPP or discounted purchases.
   - **Currency** and **Vest date**.
   - **Expected release date** — only for a grant with delayed delivery; optional, used to flag a
     late delivery.
3. Click **Confirm vest**.

The vested shares become a holding in the grant's account, with the FMV as their cost basis, so
their gain is measured from the vest. **No cash transaction is booked** — you were paid in shares,
not cash.

Recorded one by mistake? Open the grant and click **Undo** on that tranche.

## Record a share delivery (release)

Only for grants set to **Delivered later**. Until you record the release, the vested shares count
in your net worth but are marked as not yet sellable.

1. In **Vest history**, find the vest marked **Awaiting release**.
2. Click **Mark released**, enter the **Release date** (the day the shares arrived in your
   account — not before the vest date), and click **Confirm release**.

A vest recorded before release tracking existed shows **Not released** rather than a guessed
date. If those shares already arrived, record the release with the real date.

## What Lucius can do here

- Add a grant from your description or grant letter, or update one.
- Record a vest or release, undo a vest, or cancel a grant (for example when you leave a job).
- Tell you what vests next, what is still waiting for delivery, and summarise your vests by year.

Try: *"I have 400 MSFT RSUs vesting quarterly over 2 years starting next March — add the grant."*

## Things that trip people up

- **Unvested equity is left out of net worth on purpose.** It shows separately as unvested value.
- **Cancelling a grant forfeits its unvested units** — they drop to zero and leave your unvested
  value. Shares that already vested stay yours.
- **A holding that still carries a live grant can't be removed.** Cancel the grant, or move it to
  another account, first. A cancelled grant's leftover holding can be removed as usual.
- **Moving a grant to another custodian moves its unvested and awaiting-delivery units with it.**
  Shares already delivered stay where they were delivered — move or transfer those separately.
- **A grant's unit counts can't be edited on the holding.** They follow the vesting schedule and
  the vests you record: change the schedule on the grant, or record or undo a vest.
- **A vest and a release can be one event or two**, depending on your plan's **Share delivery**
  setting.
- **Vested-but-undelivered shares already have their cost basis** (the FMV at vest), so their
  gain reads correctly before delivery — they just can't be sold until you record the release.
- **Tax isn't calculated.** The **Tax event** setting and your tax residencies (in **Settings →
  Profile**) are information for reports and for Lucius; no tax is worked out or booked.

## Related pages

- [Investments](investments.md)
- [Net worth](../concepts/net-worth.md)
- [Positions, lots & cost basis](../concepts/positions-lots-and-cost-basis.md)
