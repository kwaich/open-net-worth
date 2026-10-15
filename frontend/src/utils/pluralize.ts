/**
 * English plural for a counted noun. "2 categorys added" shipped because the chat's receipt
 * head appended "s" (or "es") by suffix; the rules a reader notices are few, so they live here
 * once.
 *
 *   category -> categories     envelope -> envelopes     match -> matches
 *   person -> people           news -> news              day -> days (vowel + y keeps "s")
 *
 * Only the LAST word of a phrase inflects ("funding source" -> "funding sources"), and the
 * casing of the input is kept ("Category" -> "Categories"). A count of exactly 1 is singular.
 */

const IRREGULAR: Record<string, string> = {
  person: 'people',
  child: 'children',
  man: 'men',
  woman: 'women',
  half: 'halves',
  life: 'lives',
}

// Already plural in form, or uncountable: appending anything would be wrong.
const INVARIANT = new Set(['news', 'returns', 'series', 'species', 'equipment', 'information'])

function inflect(word: string): string {
  const lower = word.toLowerCase()
  if (INVARIANT.has(lower)) return word
  const irregular = IRREGULAR[lower]
  if (irregular) return word[0] === word[0]!.toUpperCase() ? capitalise(irregular) : irregular
  if (/[^aeiou]y$/i.test(word)) return `${word.slice(0, -1)}ies`
  if (/(s|x|z|ch|sh)$/i.test(word)) return `${word}es`
  return `${word}s`
}

function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1)
}

export function pluralize(noun: string, count: number): string {
  if (count === 1 || !noun) return noun
  const cut = Math.max(noun.lastIndexOf(' '), noun.lastIndexOf('_'))
  return noun.slice(0, cut + 1) + inflect(noun.slice(cut + 1))
}
