import { ArrowUpDown, ChevronDown } from '../../ui/icons'
import { PULSE_SORTS, isPulseSort, type PulseSort } from './pulseHelp'

// The pulse list's sort, shaped like the toolbar buttons beside it (Mark all read, Archive) with
// a sort icon and a chevron, so it reads as a menu. The control itself stays a native <select>:
// keyboard, screen readers and the phone's own picker all keep working.
export function PulseSortMenu({
  value,
  onChange,
}: {
  value: PulseSort
  onChange: (next: PulseSort) => void
}) {
  return (
    <span className="intel-sort">
      <ArrowUpDown size={13} className="intel-sort-icon" aria-hidden="true" />
      <select
        className="intel-sort-select"
        aria-label="Sort the list"
        value={value}
        onChange={(e) => {
          if (isPulseSort(e.target.value)) onChange(e.target.value)
        }}
      >
        {PULSE_SORTS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown size={13} className="intel-sort-chev" aria-hidden="true" />
    </span>
  )
}
