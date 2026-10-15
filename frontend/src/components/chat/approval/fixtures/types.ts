import type { ProposedChange } from '../../../../types/chat'

/** One realistic card for the approval-card gallery and the anatomy contract test: a
 * synthetic payload in the exact shape the backend emits for that change type. */
export interface CardFixture {
  /** Short, unique, human name — e.g. "lot_dispose · sale, no cash account". */
  name: string
  change: ProposedChange
}
