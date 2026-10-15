### AT-1 · Cash on hand is one number
- GIVEN the demo book with a bank account and an investment account carrying negative settlement cash
- WHEN I read "Cash on hand" on Accounts, "Cash & Savings" on Assets, and "Cash on hand · today" on Plan → Cash Flow
- THEN all three show the same figure, equal to Σ cash accounts
### AT-2 · Settlement cash is visible on the Investments card
- GIVEN an investment account with settlement cash ≠ 0
- WHEN I open Assets
- THEN the Investments card shows rows "Positions" and "Settlement cash" and its headline equals their sum
### AT-3 · Single-kind card shows no rows
- GIVEN no fixed-deposit account
- WHEN I open Assets
- THEN the Cash & Savings card shows only the headline
### AT-4 · Runway toggles
- GIVEN Plan → Cash Flow
- WHEN I toggle "Investments"
- THEN the starting balance increases by positions net of settlement cash, and the runway months update
### AT-5 · Net worth excl. retirement
- GIVEN a retirement account
- WHEN I open Overview
- THEN a second line "excl. retirement" appears under net worth
