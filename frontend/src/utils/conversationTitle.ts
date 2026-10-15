import { makeMentionTokenRE } from '../types/mentions'
import { isOnboardingInit } from './onboardingSentinel'

function humanizeSkillName(value: string): string {
  const cleaned = value.trim()
  if (!cleaned) return 'Skill'
  if (!/[-_]/.test(cleaned) && cleaned !== cleaned.toLowerCase()) return cleaned
  const words = cleaned.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Derive a readable title without exposing serialized composer mention tokens.
 *
 *  The onboarding harness note (`__ONBOARDING_INIT__`) is what starts a first-run
 *  conversation — there is no human message yet, so deriving a title from it literally put
 *  the raw sentinel string into the conversation list. Named explicitly instead, and — like
 *  a connector's stable name (`connectors/session_title.py`) — the caller's own guard
 *  ("only while still 'New conversation'") means this is set once and never overwritten by
 *  whatever the person says next. */
export function conversationTitleFromFirstMessage(message: string): string {
  if (isOnboardingInit(message)) return 'Onboarding to Open Net Worth'
  const skillNames: string[] = []
  // Older clients sliced the serialized first message at 60 characters before storing the
  // title, sometimes cutting a skill token before its closing bracket. Strip and humanize
  // that dangling prefix too so legacy rows can never expose raw `[skill:` plumbing.
  const danglingSkill = message.match(/\[skill:\s*([^|\]\s]+)(?:\|[^\]]*)?$/i)
  const source = danglingSkill ? `${message.slice(0, danglingSkill.index)} ` : message
  if (danglingSkill?.[1]) skillNames.push(humanizeSkillName(danglingSkill[1]))
  let visible = source
    .replace(makeMentionTokenRE(), (token, type: string, ref: string, label?: string) => {
      if (type !== 'skill') return token
      skillNames.push(humanizeSkillName(label || ref))
      return ' '
    })
    .replace(/\s+/g, ' ')
    .trim()
  // Skills-tab runs are persisted as direct slash commands. Strip every leading skill
  // selector from the title while preserving a trailing task; a selector-only message gets
  // a readable title from the selected skill names.
  while (true) {
    const slash = visible.match(/^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+|$)/i)
    if (!slash?.[1]) break
    skillNames.push(humanizeSkillName(slash[1]))
    visible = visible.slice(slash[0].length).trim()
  }
  const title = visible || skillNames.join(' + ') || 'New conversation'
  // Truncation can land exactly on a separator. Trim once more afterwards so every
  // generated or repaired title has the same clean boundary.
  return title.slice(0, 60).trim()
}
