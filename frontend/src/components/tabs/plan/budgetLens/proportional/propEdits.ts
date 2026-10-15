// budgetLens/proportional/propEdits.ts — turns the Edit envelope draft into the writes it implies:
// structure through the shared envelope routes (envelopeOps.ts), the cap through 50/30/20's own
// verb. Sequential on purpose — each step is its own hardened choke-point op.
import type { EnvelopeDraft } from './EnvelopeEditor'
import * as ops from './envelopeOps'
import type { BudgetGroupRef } from './envelopeOps'
import { setCap, setEnvelopeExcluded } from './propApi'
import type { PropEnvelope } from './propApi'

export async function applyEnvelopeEdit(
  env: PropEnvelope,
  d: EnvelopeDraft,
  ctx: { ym: string; capsLocked: boolean; groups: BudgetGroupRef[]; envs: PropEnvelope[] },
): Promise<void> {
  const patch: { name?: string; color?: string } = {}
  if (d.label.trim() !== env.label) patch.name = d.label.trim()
  if (d.color !== env.color) patch.color = d.color
  if (patch.name !== undefined || patch.color !== undefined) await ops.updateEnvelope(env.id, patch)
  // Removals BEFORE the move, additions after: the server refuses to move an envelope holding
  // more than one category, so this order is the one where a "drop a category, change group,
  // add another" edit lands whole instead of failing half-saved.
  for (const k of env.category_ids) if (!d.cats.includes(k)) await ops.removeCategory(env.id, k)
  if (d.group.trim() && d.group.trim() !== env.group)
    await ops.moveToGroup(env.id, d.group, ctx.groups)
  for (const k of d.cats) {
    if (env.category_ids.includes(k)) continue
    if (k.startsWith('__new:')) {
      const id = await ops.createCategory(k.slice(6))
      if (id) await ops.addCategory(env.id, id)
    } else {
      const owner = ctx.envs.find((e) => e.id !== env.id && e.category_ids.includes(k))
      await ops.addCategory(env.id, k, owner?.id)
    }
  }
  if (!ctx.capsLocked && d.cap !== (env.cap ?? 0)) await setCap(env.id, ctx.ym, d.cap)
  if (d.counted === !!env.excluded) await setEnvelopeExcluded(env.id, !d.counted)
}

/** Merge keeps the money view whole: the source's cap is added to the target's (this month on). */
export async function mergeEnvelopes(
  env: PropEnvelope,
  to: PropEnvelope,
  ctx: { ym: string; capsLocked: boolean },
): Promise<void> {
  await ops.mergeEnvelope(env.id, to.id)
  if (!ctx.capsLocked && (env.cap ?? 0) > 0)
    await setCap(to.id, ctx.ym, (to.cap ?? 0) + (env.cap ?? 0))
}
