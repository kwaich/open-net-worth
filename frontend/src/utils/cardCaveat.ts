/** The note a spending-based figure (savings rate, burn, runway) carries while an open credit
 *  card has no purchases in its period: that card's spending is not in the figure yet, so the
 *  figure reads better than the book is. `null` when every card has activity (or there are no
 *  cards), so callers render nothing. */
export function cardCaveat(cards: string[] | undefined): string | null {
  if (!cards?.length) return null
  return `Card spending not included yet (${cards.join(', ')})`
}
