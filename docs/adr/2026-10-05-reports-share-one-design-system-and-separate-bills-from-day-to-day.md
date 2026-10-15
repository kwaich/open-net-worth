---
title: Reports share one design system, and separate bills from day-to-day spending
status: Accepted
date: 2026-10-05
deciders: Repo owner, Claude
---

# Reports share one design system, and separate bills from day-to-day spending

## Context

The weekly report's headline said rent was most of the week's spending, next to a large jump
"on the week before" — a fixed monthly bill landing in the week dominated every figure. Its "Came in" line added a friend's loan repayment and two reimbursements to a salary.
And it looked unbalanced: about 14 font sizes (9–28px) between a template patched one complaint
at a time and a chart drawer that baked its own sizes into fixed 340px pictures, which a laptop
then scaled up (12px labels became ~20px, and chart values stopped short of the page's other
amounts). The owner: "our font size is pretty unbalanced, not consistent".

## Decision

1. **`get_spending_breakdown`** (`services/spending_breakdown.py` + `spending_bills.py`,
   `spending_income.py`, `spending_periods.py`) computes, server-side, what a report may only
   quote:
   - **bills vs day-to-day.** A bill is a row of a recurring OUT stream — or any row in a
     category that HOSTS one (≥3 claimed rows carrying ≥25% of its money), so rent to a new
     landlord is a bill from its first payment, not after three;
   - **comparisons**: the previous period, the usual period (mean of the ones before), the same
     week last month for a week (four weeks back, weekdays aligned — owner's choice), the same
     month last year on request; per category too;
   - **earned vs paid back**: an income row under an expense-kind category is money paid back,
     never income;
   - bills expected but not seen, ignoring ones too small to matter (<1% of a usual period).
   Totals equal `get_transactions`' summary for the same window.
2. **One design system** (`services/artifact_design.py`): a page puts `<!-- onw:design -->` in
   its `<head>` and the server swaps it for the stylesheet on save. Five type sizes only
   (11/12/14/20/28 — body at the app's own 14px — enforced by `tests/test_artifact_design.py`), one spacing scale, the app's
   colour tokens with dark mode, table layout (PyMuPDF lays out blocks and tables only), no
   requests. Report templates carry markup and slots, no CSS.
3. **Charts render twice**: the fixed SVG stays for the PDF (`class="onw-print"`, stamped as a
   vector graphic), and a screen form (`services/artifact_charts_screen.py`, inside
   `<onw-screen>`, which the PDF path removes) is plain HTML for bars and a three-part SVG for a
   line (fixed-size labels either side of a horizontally stretching plot), so chart text is
   never scaled and lines up with the page.
4. **The skills say what is news**: day-to-day spending against the comparison and what drove
   it, a missing or changed bill, a flagged charge, the portfolio against its benchmark. Never a
   bill that behaved as expected, never the largest payment. Short file names
   (`Weekly report 28 Sep-4 Oct.html`, `Monthly report Sep.html`).

## Consequences

- Installed, unedited report skills are replaced on boot (superseded body hashes) and their
  template synced by `starter_skill_files`.
- The PDF keeps PyMuPDF; a headless-browser PDF (free layout, ~150 MB) was considered and
  deferred. Screen-only decoration (a bar's comparison mark) does not appear in a PDF.
- A page Lucius writes freely can use the same components; the stylesheet is opt-in.
