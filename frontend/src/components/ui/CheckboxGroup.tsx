import { useId } from 'react'
import type { ReactNode } from 'react'

export interface CheckboxGroupOption {
  value: string
  label: ReactNode
  description?: ReactNode
}

export interface CheckboxGroupProps {
  options: CheckboxGroupOption[]
  value: string[]
  onChange: (next: string[]) => void
  columns?: number | 'auto'
  maxHeight?: number
  selectAll?: boolean
  selectAllControls?: boolean
  emptyText?: string
  ariaLabel?: string
}

/**
 * CheckboxGroup — the fix for the cramped Targets linked-accounts grid. Lays
 * options out in an even auto-fill column grid, truncates long labels with an
 * ellipsis (+ `title`), gives a scroll region with a sticky "N selected"
 * header. No clipped mid-row content.
 *
 * Two header affordances (both off by default, additive — existing callers are
 * unaffected):
 *  - `selectAll` — a single text button that flips between "Select all" / "Clear all".
 *  - `selectAllControls` — an explicit "Select all · Clear all" pair, each button
 *    disabled when it would be a no-op (all already selected / nothing selected).
 *    Reads clearer than a flipping toggle on long lists (e.g. a 55-tool picker).
 */
export function CheckboxGroup({
  options,
  value,
  onChange,
  columns = 'auto',
  maxHeight = 220,
  selectAll = false,
  selectAllControls = false,
  emptyText = 'Nothing to select',
  ariaLabel,
}: CheckboxGroupProps) {
  const groupId = useId()
  const selectedSet = new Set(value)
  const allSelected = options.length > 0 && options.every((o) => selectedSet.has(o.value))
  const noneSelected = value.length === 0

  const toggle = (optValue: string) => {
    if (selectedSet.has(optValue)) onChange(value.filter((v) => v !== optValue))
    else onChange([...value, optValue])
  }
  const toggleAll = () => {
    onChange(allSelected ? [] : options.map((o) => o.value))
  }
  const selectAllOptions = () => onChange(options.map((o) => o.value))
  const clearAllOptions = () => onChange([])

  const gridStyle =
    columns === 'auto'
      ? { gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }
      : { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }

  if (options.length === 0) {
    return (
      <div className="ui-checkgroup" role="group" aria-label={ariaLabel}>
        <div className="ui-checkgroup__empty">{emptyText}</div>
      </div>
    )
  }

  return (
    <div className="ui-checkgroup" role="group" aria-label={ariaLabel}>
      <div className="ui-checkgroup__head">
        <span className="ui-checkgroup__count">
          {value.length} of {options.length} selected
        </span>
        {selectAllControls ? (
          <span className="ui-checkgroup__actions">
            <button
              type="button"
              className="ui-checkgroup__selectall"
              onClick={selectAllOptions}
              disabled={allSelected}
            >
              Select all
            </button>
            <span className="ui-checkgroup__actions-sep" aria-hidden="true">
              •
            </span>
            <button
              type="button"
              className="ui-checkgroup__selectall"
              onClick={clearAllOptions}
              disabled={noneSelected}
            >
              Clear all
            </button>
          </span>
        ) : (
          selectAll && (
            <button type="button" className="ui-checkgroup__selectall" onClick={toggleAll}>
              {allSelected ? 'Clear all' : 'Select all'}
            </button>
          )
        )}
      </div>
      <div className="ui-checkgroup__list" style={{ ...gridStyle, maxHeight }}>
        {options.map((o) => {
          const id = `${groupId}-${o.value}`
          const title = typeof o.label === 'string' ? o.label : undefined
          // Full description in a native tooltip — the desc wraps to ~2 lines (CSS
          // line-clamp), and a longer one still reads in full on hover.
          const descTitle = typeof o.description === 'string' ? o.description : undefined
          return (
            <label key={o.value} className="ui-checkgroup__opt" htmlFor={id}>
              <input
                type="checkbox"
                id={id}
                checked={selectedSet.has(o.value)}
                onChange={() => toggle(o.value)}
              />
              <span className="ui-checkgroup__opt-text">
                <span className="ui-checkgroup__opt-label" title={title}>
                  {o.label}
                </span>
                {o.description && (
                  <span className="ui-checkgroup__opt-desc" title={descTitle}>
                    {o.description}
                  </span>
                )}
              </span>
            </label>
          )
        })}
      </div>
    </div>
  )
}
