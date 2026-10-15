import { describe, expect, it } from 'vitest'
import { ALL_TEMPLATES, RECOMMENDED_TEMPLATES, TEMPLATE_ICONS } from './automationTemplates'

const mail = RECOMMENDED_TEMPLATES.find((t) => t.key === 'mail_ledger')!
const byKey = (key: string) => ALL_TEMPLATES.find((t) => t.key === key)!

describe('automation templates', () => {
  it('recommends email logging, then the three reports', () => {
    expect(RECOMMENDED_TEMPLATES.map((t) => t.key)).toEqual([
      'mail_ledger',
      'daily-briefing',
      'weekly-report',
      'monthly-report',
    ])
  })

  it('reads like something a non-technical person would write', () => {
    for (const template of RECOMMENDED_TEMPLATES) {
      expect(template.instructions).not.toMatch(
        /read-only|calendar day|idempot|\bskill for\b|run the/i,
      )
    }
  })

  it('each report runs its built-in skill', () => {
    expect(byKey('daily-briefing').trigger).toEqual({
      kind: 'schedule',
      type: 'daily',
      time: '09:00',
    })
    expect(byKey('daily-briefing').instructions).toContain('using the Daily briefing skill')
    expect(byKey('weekly-report').instructions).toContain('using the Weekly report skill')
    expect(byKey('monthly-report').instructions).toContain('using the Monthly report skill')
  })

  // The trigger is the only "when" (ADR 2026-10-11): timing written into the text goes stale
  // the moment someone changes the trigger — "over 500" in the words, 1,000 in the trigger.
  it('says what to do, never when — that is the trigger', () => {
    const timing =
      /\b(every (hour|day|morning|week|month|\d)|each (morning|day|week|month)|hourly|on the \d|(mon|tues|wednes|thurs|fri|satur|sun)day|\d{1,2}\s?(am|pm)|when an? (charge|transaction))/i
    for (const template of ALL_TEMPLATES) {
      expect(template.instructions, template.key).not.toMatch(timing)
      expect(template.description ?? '', template.key).not.toMatch(timing)
    }
  })

  it('only uses the triggers automations still have', () => {
    for (const template of ALL_TEMPLATES) {
      if (template.trigger.kind === 'event') {
        expect(['txn', 'balance', 'price']).toContain(template.trigger.event)
      }
    }
  })

  it('has unique keys and an icon keyed to every template', () => {
    expect(new Set(ALL_TEMPLATES.map((t) => t.key)).size).toBe(ALL_TEMPLATES.length)
    for (const template of ALL_TEMPLATES) expect(TEMPLATE_ICONS[template.key]).toBeTruthy()
  })
})

describe('the email-logging template', () => {
  it('runs every 12 hours through the Log transactions from email skill', () => {
    expect(mail.name).toBe('Auto-log transactions from email')
    expect(mail.trigger).toEqual({ kind: 'schedule', type: 'interval', seconds: 43200 })
    expect(mail.execution).toEqual({ kind: 'lucius' })
    expect(mail.permission_mode).toBe('auto')
    expect(mail.instructions).toContain('using the Log transactions from email skill')
    expect(mail.instructions).not.toMatch(/message_id|idempot/i)
  })
})
