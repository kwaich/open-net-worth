# Trades — brokerage and crypto confirmations

Read this before logging a trade confirmation or fill email. Work from whatever such mail
arrives — there is no fixed venue list.

Extract: venue, instrument or pair, side, quantity, unit price, total, fee if shown, and the
order/trade id (keep that in the description).

- **Dates.** Confirmations are stamped in the venue's local time. Convert to the profile's
  timezone before logging — an evening trade in one zone is the next day in another. When the
  body's time is ambiguous, convert the email's own Date header.
- **Accounts.** Each venue maps to its own account; look it up by name. If a venue's account
  doesn't exist yet, notify and ask for it — never auto-create one.
- **Payee.** Every trade row needs the venue's own payee entity. Reuse the existing one every
  time; a second one splits the venue's history in two.
- **Buying is unattended.** Resolve the instrument, add the position if this is a new holding,
  record the cost lot (with `idempotency_key: "email:<message_id>"`), and book the cash debit
  for the cost. Notify, and call out a newly created position explicitly.
- **Funding transfers.** A transfer from a bank account to an exchange is already logged on the
  bank side — don't book a matching credit on the exchange account.
