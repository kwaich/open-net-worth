// How the Details section reads an asset's facts: each of its category's guided prompts
// (CATEGORY_FACTS) is matched to a saved fact by label — case, spacing and apostrophe style
// aside — and every other fact stays an "other detail". Nothing is ever dropped: a category
// change only moves facts between the two. Pure; the Details card and its editor share it.
import { guidedFactsFor, sanitizeFacts, type FactPrompt } from './assetCategoryRules'

export type Fact = [label: string, value: string]

/** A guided prompt and the value saved under its label — '' while unset. */
export interface GuidedFact {
  prompt: FactPrompt
  value: string
}

export interface SplitFacts {
  guided: GuidedFact[]
  other: Fact[]
}

function labelKey(label: string): string {
  return label.trim().toLowerCase().replace(/[‘’]/g, "'").replace(/\s+/g, ' ')
}

export function splitFacts(category: string, facts: Fact[]): SplitFacts {
  const used = new Set<number>()
  const guided = guidedFactsFor(category).map((prompt) => {
    const i = facts.findIndex(([k], at) => !used.has(at) && labelKey(k) === labelKey(prompt[0]))
    if (i < 0) return { prompt, value: '' }
    used.add(i)
    return { prompt, value: facts[i]![1] }
  })
  return { guided, other: facts.filter((_, at) => !used.has(at)) }
}

/** What to save: guided values under their prompts' own labels, in the category's order,
 *  then the other details — blank rows dropped. */
export function joinFacts(guided: GuidedFact[], other: Fact[]): Fact[] {
  return sanitizeFacts([...guided.map((g): Fact => [g.prompt[0], g.value]), ...other])
}

/** "plate number, COE expires and 1 more" — at most two labels, each lowered for a sentence
 *  (an acronym kept), then how many more there are. */
export function labelList(labels: string[], max = 2): string {
  const names = labels.map((l) =>
    /^[A-Z]{2}/.test(l) ? l : l.charAt(0).toLowerCase() + l.slice(1),
  )
  if (names.length <= max) return names.join(', ')
  return `${names.slice(0, max).join(', ')} and ${names.length - max} more`
}
