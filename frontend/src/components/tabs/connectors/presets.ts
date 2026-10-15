// Connector presets (P3-CONN-S3 catalog expansion). Thin, declarative adapters —
// NO bespoke per-provider feature code: a preset is just a set of field values the
// generic Email / Webhook config forms prefill. The user still enters their own
// credentials; the preset only spares them the host/port/URL-shape lookup.

export interface EmailPreset {
  key: string
  label: string
  imap_host: string
  imap_port: number
  smtp_host: string
  smtp_port: number
  /** Provider-specific gotcha shown under the form (e.g. "needs an app password"). */
  note?: string
}

// IMAP is uniformly port 993 (implicit TLS) across all four. SMTP submission is either
// 587 (STARTTLS) or 465 (implicit TLS) depending on the provider — both are genuinely
// secure (backend/connectors/email_out/channel.py handles either correctly), so this
// picks whichever each provider's own docs list as its primary/first-listed option;
// Gmail supports both equally but 587 keeps this list's story consistent (3 of 4 on
// 587) with the port field's own help text ("587 is the standard... almost every
// server"). All four require an APP-SPECIFIC password when 2FA is on — surfaced as
// the note.
export const EMAIL_PRESETS: EmailPreset[] = [
  {
    key: 'gmail',
    label: 'Gmail',
    imap_host: 'imap.gmail.com',
    imap_port: 993,
    smtp_host: 'smtp.gmail.com',
    smtp_port: 587,
    note: 'Enable IMAP in Gmail settings and use an App Password (Google account → Security).',
  },
  {
    key: 'outlook',
    label: 'Outlook',
    imap_host: 'outlook.office365.com',
    imap_port: 993,
    smtp_host: 'smtp-mail.outlook.com',
    smtp_port: 587,
    note: 'Use an app password if your account has 2-step verification enabled.',
  },
  {
    key: 'icloud',
    label: 'iCloud',
    imap_host: 'imap.mail.me.com',
    imap_port: 993,
    smtp_host: 'smtp.mail.me.com',
    smtp_port: 587,
    note: 'iCloud requires an app-specific password (appleid.apple.com → Sign-In and Security).',
  },
  {
    key: 'fastmail',
    label: 'Fastmail',
    imap_host: 'imap.fastmail.com',
    imap_port: 993,
    smtp_host: 'smtp.fastmail.com',
    smtp_port: 465,
    note: 'Create an app password in Fastmail → Settings → Privacy & Security.',
  },
]

export function emailPreset(key: string | undefined): EmailPreset | undefined {
  return EMAIL_PRESETS.find((p) => p.key === key)
}

export interface WebhookPreset {
  key: string
  label: string
  /** Placeholder URL shape shown in the URL field. */
  url_placeholder: string
  /** Short hint on where to get the URL. */
  note: string
}

// Slack + Discord both accept a plain incoming-webhook POST with a JSON body, which
// is exactly what the webhook channel emits — so they are pure presets (URL shape +
// where-to-find-it), no custom transport.
export const WEBHOOK_PRESETS: WebhookPreset[] = [
  {
    key: 'slack',
    label: 'Slack',
    url_placeholder: 'https://hooks.slack.com/services/T…/B…/…',
    note: 'Create an Incoming Webhook in your Slack app → Incoming Webhooks, then paste its URL.',
  },
  {
    key: 'discord',
    label: 'Discord',
    url_placeholder: 'https://discord.com/api/webhooks/…/…',
    note: 'Channel → Edit → Integrations → Webhooks → New Webhook → Copy Webhook URL.',
  },
]

export function webhookPreset(key: string | undefined): WebhookPreset | undefined {
  return WEBHOOK_PRESETS.find((p) => p.key === key)
}
