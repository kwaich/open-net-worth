// First run, restore branch — for someone who already has a book.
//
// Preview-then-commit over endpoints that already ship: `/api/data/import/validate` writes NOTHING
// and returns an ImportReport ({ok, schema_version, base_currency, files[], warnings, errors});
// `/api/data/import` with replace=true does the write, wrapped in a pre-import checkpoint returned
// as undo_checkpoint_id.
//
// Why it lives in first run at all: restore used to be reachable only from Settings › Data, so
// a returning user had to pick a data folder, connect a model, set a password and land on an
// empty dashboard before importing their data. Currency comes from the bundle; AI and
// machine security setup follow the restore.

import { useState } from 'react'
import { authFetch } from '../../api/client'
import { Button, Cluster, Stack, Text } from '../ui'
import { ReportProblemButton } from '../ReportProblemButton'

interface BundleFile {
  name: string
  row_count: number
  sha_ok: boolean
}

interface ImportReport {
  ok: boolean
  schema_version: number | null
  base_currency: string
  files: BundleFile[]
  warnings: string[]
  errors: string[]
}

export interface StepRestoreProps {
  /** Restore succeeded — the instance now holds the bundle's data. */
  onDone: () => void
  /** Back out to ordinary setup. */
  onBack: () => void
}

/** Strip the store suffix so a row reads "transactions", not "transactions.json". */
function label(name: string): string {
  return name.replace(/\.json$/i, '').replace(/_/g, ' ')
}

export function StepRestore({ onDone, onBack }: StepRestoreProps) {
  const [file, setFile] = useState<File | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function validate(picked: File) {
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      const body = new FormData()
      body.append('file', picked)
      const res = await authFetch('/api/data/import/validate', { method: 'POST', body })
      if (!res.ok) {
        const detail = await res.json().catch(() => null)
        setError(detail?.detail || `Could not read that file (${res.status}).`)
        return
      }
      setReport(await res.json())
    } catch {
      setError('Could not reach the server. Check it is running and try again.')
    } finally {
      setBusy(false)
    }
  }

  async function commit() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      body.append('replace', 'true')
      const res = await authFetch('/api/data/import', { method: 'POST', body })
      if (!res.ok) {
        const detail = await res.json().catch(() => null)
        setError(detail?.detail || `The import did not complete (${res.status}).`)
        return
      }
      onDone()
    } catch {
      setError('Could not reach the server. Check it is running and try again.')
    } finally {
      setBusy(false)
    }
  }

  const rows = (report?.files || []).filter((f) => f.row_count > 0).slice(0, 6)
  const total = (report?.files || []).reduce((n, f) => n + (f.row_count || 0), 0)

  return (
    <Stack gap="--space-6" className="firstrun-restore">
      <Text as="p" tone="muted" className="firstrun-lead">
        Bring back a portable export from another machine. Nothing is written until you have seen
        what is inside it.
      </Text>

      <label className="firstrun-drop">
        <input
          type="file"
          accept=".zip,application/zip"
          className="firstrun-drop__input"
          disabled={busy}
          onChange={(e) => {
            if (busy) return
            const picked = e.target.files?.[0] || null
            setFile(picked)
            setReport(null)
            setError(null)
            if (picked) void validate(picked)
          }}
        />
        <Stack gap="--space-2">
          <Text size="sm" weight={600}>
            {file ? file.name : 'Choose a backup file'}
          </Text>
          <Text size="xs" tone="faint">
            An Open Net Worth export .zip from Settings › Data
          </Text>
        </Stack>
      </label>

      {busy && !report && (
        <Text as="p" size="sm" tone="muted">
          Reading the bundle…
        </Text>
      )}

      {report && !report.ok && (
        <Stack gap="--space-2">
          {(report.errors.length ? report.errors : ['This file is not a valid bundle.']).map(
            (e) => (
              <Text key={e} as="p" size="sm" tone="bad">
                {e}
              </Text>
            ),
          )}
          <Cluster>
            <ReportProblemButton
              size="sm"
              error={(report.errors.length
                ? report.errors
                : ['This file is not a valid bundle.']
              ).join('\n')}
            />
          </Cluster>
        </Stack>
      )}

      {report && report.ok && (
        <Stack gap="--space-4">
          <div className="firstrun-rows">
            {report.base_currency && (
              <div className="firstrun-row firstrun-row--kv">
                <span className="firstrun-row__label">App currency</span>
                <span className="firstrun-row__value">{report.base_currency}</span>
              </div>
            )}
            <div className="firstrun-row firstrun-row--kv">
              <span className="firstrun-row__label">Records</span>
              <span className="firstrun-row__value">{total.toLocaleString()}</span>
            </div>
            {rows.map((f) => (
              <div key={f.name} className="firstrun-row firstrun-row--kv">
                <span
                  className="firstrun-row__label"
                  style={{ textTransform: 'none', letterSpacing: 'normal', fontWeight: 400 }}
                >
                  {label(f.name)}
                </span>
                <span className="firstrun-row__value" style={{ color: 'var(--faint)' }}>
                  {f.row_count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>

          {report.warnings.map((w) => (
            <Text key={w} as="p" size="xs" tone="warn">
              {w}
            </Text>
          ))}

          <Text as="p" size="xs" tone="faint">
            This replaces everything on this instance. A checkpoint is taken first, so it can be
            undone.
          </Text>
        </Stack>
      )}

      {error && (
        <Stack gap="--space-3">
          <Text as="p" size="sm" tone="bad">
            {error}
          </Text>
          <Cluster>
            <ReportProblemButton size="sm" error={error} />
          </Cluster>
        </Stack>
      )}

      <Cluster justify="between">
        <Button variant="secondary" onClick={onBack} disabled={busy}>
          Set up instead
        </Button>
        {report?.ok && (
          <Button variant="primary" loading={busy} onClick={() => void commit()}>
            Restore this backup
          </Button>
        )}
      </Cluster>
    </Stack>
  )
}
