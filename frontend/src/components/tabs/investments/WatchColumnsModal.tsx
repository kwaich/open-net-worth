// WatchColumnsModal — Manage Columns for the Watchlist table: tick a column on (it joins the end)
// or off, grouped the way a broker groups them; the shown columns can be nudged left/right.

import { Button, Checkbox, Modal } from '../../ui'
import { ChevronLeft, ChevronRight } from '../../../ui/icons'
import {
  DEFAULT_WATCH_COLUMNS,
  WATCH_COLUMN_GROUPS,
  WATCH_COLUMNS,
  columnsFor,
} from './watchColumns'

interface Props {
  open: boolean
  value: string[]
  onChange: (keys: string[]) => void
  onClose: () => void
}

export function WatchColumnsModal({ open, value, onChange, onClose }: Props) {
  const toggle = (key: string) =>
    onChange(value.includes(key) ? value.filter((k) => k !== key) : [...value, key])
  const move = (i: number, by: -1 | 1) => {
    const next = [...value]
    const j = i + by
    if (j < 0 || j >= next.length) return
    ;[next[i], next[j]] = [next[j]!, next[i]!]
    onChange(next)
  }
  const shown = columnsFor(value)

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Manage columns"
      subtitle="Instrument is always shown first."
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={() => onChange(DEFAULT_WATCH_COLUMNS)}>
            Reset to default
          </Button>
          <Button variant="primary" size="sm" onClick={onClose}>
            Done
          </Button>
        </>
      }
    >
      <div className="mk-wcols">
        <section>
          <h4 className="mk-wcols-h">Shown, in order</h4>
          {shown.length === 0 ? (
            <p className="mk-wcols-empty">No columns — tick some below.</p>
          ) : (
            <ol className="mk-wcols-order">
              {shown.map((c, i) => (
                <li key={c.key} className="mk-wcols-chip">
                  <button
                    type="button"
                    aria-label={`Move ${c.label} left`}
                    disabled={i === 0}
                    onClick={() => move(i, -1)}
                  >
                    <ChevronLeft size={13} aria-hidden="true" />
                  </button>
                  <span>{c.label}</span>
                  <button
                    type="button"
                    aria-label={`Move ${c.label} right`}
                    disabled={i === shown.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <ChevronRight size={13} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>
        {WATCH_COLUMN_GROUPS.map((g) => (
          <section key={g}>
            <h4 className="mk-wcols-h">{g}</h4>
            <div className="mk-wcols-grid">
              {WATCH_COLUMNS.filter((c) => c.group === g).map((c) => (
                <Checkbox
                  key={c.key}
                  id={`wcol-${c.key}`}
                  label={c.label}
                  checked={value.includes(c.key)}
                  onChange={() => toggle(c.key)}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </Modal>
  )
}
