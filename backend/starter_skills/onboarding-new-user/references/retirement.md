# Retirement: fill in the Outlook for them

Read this on the retirement branch, after their money is in. The Outlook screen asks for a birth date, what they save and what they would spend in retirement. They have just told you most of it in chat, so you enter it — never send them to the screen to fill it in, and never make them answer twice.

Load the tools in one call: update_profile, simulate_retirement.

1. **Use what they already said.** Check the FINANCIAL PROFILE in your context and their messages; ask only for what is still missing, and no more than two or three items in one short message so they can answer in a line. The inputs are: date of birth, what they put aside each month, what they would spend a month in retirement, the age they'd like to stop working.
2. **Birth date: only the one they give.** Ask for their date of birth and save exactly what they give. If they give only a month and year, ask once whether to save the 1st of that month, and save it only on a clear yes. If they would rather not, store nothing and say the full Outlook needs a date; meanwhile give a rough answer from the age they stated, labelled as rough. A bare age ("I'm 44") is not a date of birth: ask.
3. **Save only what they stated**, with update_profile: `date_of_birth`, `monthly_saving` (what they put aside — never their income), `fire_monthly_retirement_spending`, `retirement_target_age`. Never fill a field with a guess or an estimate of your own, and never offer to save one.
4. **Run it.** simulate_retirement with no arguments is exactly what the Outlook screen shows. Give the answer in plain words — the odds, the age that works, what would move it — in one reply, with the figures the tool returned.
5. **Then** point them to [Plan › Outlook](/plan?sub=outlook) to explore the levers themselves — as somewhere to look, not something left for them to complete.
