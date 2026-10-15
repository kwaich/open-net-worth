import { useMemo, useState } from 'react'
import { diffLines } from 'diff'
import type { ProposedChange, TextEditDisplay } from '../../../../types/chat'
import {
  ApprovalDetails,
  ApprovalDiff,
  ApprovalDiffLine,
  ApprovalFact,
  ApprovalFacts,
  ApprovalMeta,
  ApprovalSection,
  ApprovalToggle,
} from '../ApprovalCard'
import { fmt } from '../cardTypes'

type Display = NonNullable<ProposedChange['display']>

const fieldName = (field: string) => field.replace(/_/g, ' ')
// A prompt with and without a final newline would otherwise show its last line as changed.
const endLine = (text: string) => (text.endsWith('\n') ? text : `${text}\n`)
const toolsText = (tools: string[]) => (tools.length > 0 ? tools.join(', ') : 'none')
const clip = (text: string, max = 60) => {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat
}

// The auto-apply grant (P3-AG4) means the agent may APPLY, not just propose — so it is a
// full-weight fact, never folded away. Absent/empty ⇒ propose-only.
function AutoApplyGrant({ grant }: { grant: Display['auto_apply'] }) {
  if (!grant || grant.capabilities.length === 0) {
    return (
      <ApprovalFact label="Auto-apply" tone="muted">
        Propose only
      </ApprovalFact>
    )
  }
  const unattended = grant.unattended_capabilities ?? []
  return (
    <>
      <ApprovalFact
        label="Auto-apply"
        tone="bad"
        title="The agent may apply these changes itself, not only propose them."
      >
        May apply: {grant.capabilities.join(', ')}
      </ApprovalFact>
      {unattended.length > 0 && (
        <ApprovalFact
          label="Unattended"
          tone="warn"
          title="Capabilities the agent may use on runs with no one watching."
        >
          {unattended.join(', ')}
        </ApprovalFact>
      )}
      {grant.max_writes_per_run != null && (
        <ApprovalFact label="Max writes per run">{grant.max_writes_per_run}</ApprovalFact>
      )}
      {grant.max_amount_base != null && (
        <ApprovalFact label="Max amount">{fmt(grant.max_amount_base)}</ApprovalFact>
      )}
    </>
  )
}

/** An exact {old,new} text edit (RFC 2026-07-20 §14.2): the model supplied the delta, so it
 * renders as asked — nothing derived, no part of the surrounding body collapsed. */
export function TextEditCell({ edit }: { edit: TextEditDisplay }) {
  return (
    <ApprovalDiff>
      <ApprovalDiffLine kind="removed">
        {edit.old}
        {edit.old_truncated ? ' …' : ''}
      </ApprovalDiffLine>
      <ApprovalDiffLine kind="added">
        {edit.new}
        {edit.new_truncated ? ' …' : ''}
      </ApprovalDiffLine>
    </ApprovalDiff>
  )
}

function PromptText({ preview, full }: { preview?: string; full?: string }) {
  const text = full || preview || ''
  if (!text) return null
  return (
    <ApprovalDetails summary={`Prompt · ${clip(text)}`}>
      <ApprovalDiff>
        {text.split('\n').map((line, i) => (
          <ApprovalDiffLine key={i} kind="added">
            {line}
          </ApprovalDiffLine>
        ))}
      </ApprovalDiff>
    </ApprovalDetails>
  )
}

function PromptDiffCell({
  oldFull,
  newPreview,
  newFull,
}: {
  oldFull?: string
  newPreview?: string
  newFull?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const hasDiff = oldFull != null && newFull != null
  const rows = useMemo(() => {
    if (!hasDiff) return []
    return diffLines(endLine(oldFull), endLine(newFull)).flatMap((hunk, i) => {
      const kind = hunk.added ? 'added' : hunk.removed ? 'removed' : 'context'
      const lines = hunk.value.split('\n')
      if (lines[lines.length - 1] === '') lines.pop()
      return lines.map((text, j) => ({ key: `${i}-${j}`, kind, text }) as const)
    })
  }, [hasDiff, oldFull, newFull])

  if (!hasDiff) return <PromptText preview={newPreview} full={newFull} />
  const count = (kind: 'added' | 'removed') => rows.filter((r) => r.kind === kind).length
  return (
    <ApprovalSection title="Prompt rewritten">
      <ApprovalToggle onClick={() => setExpanded((e) => !e)}>
        {expanded ? 'Hide diff' : `Show diff · +${count('added')} −${count('removed')} lines`}
      </ApprovalToggle>
      {expanded && (
        <ApprovalDiff>
          {rows.map((r) => (
            <ApprovalDiffLine key={r.key} kind={r.kind}>
              {r.text}
            </ApprovalDiffLine>
          ))}
        </ApprovalDiff>
      )}
    </ApprovalSection>
  )
}

export function AgentCreatePreview({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  const prompt = change.agent?.prompt
  return (
    <>
      <ApprovalMeta items={[d.description]} />
      <ApprovalFacts>
        <ApprovalFact label="Tools">{toolsText(d.allowed_tools || [])}</ApprovalFact>
        {d.model && <ApprovalFact label="Model">{d.model}</ApprovalFact>}
        {d.channels && d.channels.length > 0 && (
          <ApprovalFact label="Channels">{d.channels.join(', ')}</ApprovalFact>
        )}
        <AutoApplyGrant grant={d.auto_apply} />
      </ApprovalFacts>
      <PromptText
        preview={d.prompt_preview}
        full={typeof prompt === 'string' ? prompt : undefined}
      />
    </>
  )
}

// Renders only the fields present in the change's display — the backend includes a field's
// new value only when it is being changed.
const AGENT_FIELDS_SHOWN = [
  'description',
  'prompt',
  'allowed_tools',
  'model',
  'channels',
  'enabled',
  'auto_apply',
]

function AgentUpdateFacts({ d, fields }: { d: Display; fields: string[] }) {
  const empty = (v: unknown) => (v == null || v === '' ? '—' : String(v))
  return (
    <ApprovalFacts>
      {d.description != null && (
        <ApprovalFact label="Description">{empty(d.description)}</ApprovalFact>
      )}
      {d.allowed_tools != null && (
        <ApprovalFact label="Tools">{toolsText(d.allowed_tools)}</ApprovalFact>
      )}
      {d.model !== undefined && <ApprovalFact label="Model">{empty(d.model)}</ApprovalFact>}
      {d.channels !== undefined && (
        <ApprovalFact label="Channels">{empty(d.channels?.join(', '))}</ApprovalFact>
      )}
      {d.enabled !== undefined && (
        <ApprovalFact label="Enabled">{d.enabled ? 'Yes' : 'No'}</ApprovalFact>
      )}
      {fields.includes('auto_apply') && <AutoApplyGrant grant={d.auto_apply} />}
    </ApprovalFacts>
  )
}

export function AgentUpdatePreview({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  const fields = d.changed_fields || []
  const others = fields.filter((f) => !AGENT_FIELDS_SHOWN.includes(f)).map(fieldName)
  const newFullPrompt = change.changes?.prompt
  return (
    <>
      <ApprovalMeta items={[others.length > 0 && `Also changes ${others.join(', ')}`]} />
      <AgentUpdateFacts d={d} fields={fields} />
      {d.prompt_edit ? (
        <ApprovalSection title="Prompt edit">
          <TextEditCell edit={d.prompt_edit} />
        </ApprovalSection>
      ) : d.prompt_preview != null ? (
        <PromptDiffCell
          oldFull={d.prompt_old}
          newPreview={d.prompt_preview}
          newFull={typeof newFullPrompt === 'string' ? newFullPrompt : undefined}
        />
      ) : null}
    </>
  )
}

export function SkillUpdateBody({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  const fields = d.changed_fields || []
  return (
    <>
      {fields.length > 0 && (
        <ApprovalFacts>
          <ApprovalFact label="Changes">{fields.map(fieldName).join(', ')}</ApprovalFact>
        </ApprovalFacts>
      )}
      {d.body_edit && (
        <ApprovalSection title="Body edit">
          <TextEditCell edit={d.body_edit} />
        </ApprovalSection>
      )}
    </>
  )
}
