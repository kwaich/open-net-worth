import type { Automation } from '../../../api/automations'

/** The draft is not saved, so Lucius cannot look it up — screen awareness only knows which tab
 *  is open. The prompt carries what the user typed so Lucius can create (or update) it. */
export function cadencePrompt(name: string, instructions: string, automation?: Automation) {
  if (automation)
    return `Help me set a custom cadence for my automation “${automation.name}” (${automation.id}), then update its trigger.`
  const what = instructions.trim() || 'not written yet'
  return [
    'Help me set a custom cadence for a new automation I’m drafting. It isn’t saved yet — create it once we agree on the schedule.',
    `Name: ${name.trim() || 'untitled'}`,
    `What it should do: ${what}`,
  ].join('\n')
}
