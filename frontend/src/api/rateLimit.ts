// The one wording for the server's brute-force guard (429 + Retry-After), shared by every screen
// that can trip it: the login form and the password panel draw on the same attempt counter.

/** Retry-After is seconds; the server's window is 15 minutes, which is the fallback. */
export function tooManyAttemptsMessage(res: Pick<Response, 'headers'>): string {
  const seconds = Number(res.headers?.get('Retry-After'))
  const minutes = seconds > 0 ? Math.ceil(seconds / 60) : 15
  return `Too many attempts. Try again in ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}.`
}
