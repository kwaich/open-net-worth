// ISO-8601 stamps reach the client in two spellings — the browser's own `…123Z` and the
// server's Python `…123456+00:00` (which also drops the fraction when it is exactly zero).
// Compared as strings they order wrongly around those differences, so compare as instants.

function ms(value: string | undefined | null): number {
  return value ? Date.parse(value) : NaN
}

/** The later of two stamps; a missing or unparsable one never wins. */
export function laterIso(a?: string | null, b?: string | null): string | undefined {
  const ma = ms(a)
  const mb = ms(b)
  if (Number.isNaN(ma)) return Number.isNaN(mb) ? undefined : (b as string)
  if (Number.isNaN(mb)) return a as string
  return mb > ma ? (b as string) : (a as string)
}

/** Is `a` strictly after `b`? A missing `b` is the beginning of time; a missing `a` is never
 *  after anything. */
export function isAfterIso(a?: string | null, b?: string | null): boolean {
  const ma = ms(a)
  if (Number.isNaN(ma)) return false
  const mb = ms(b)
  return Number.isNaN(mb) ? true : ma > mb
}
