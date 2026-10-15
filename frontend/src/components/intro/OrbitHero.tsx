// The intro's picture: what a screen holds, as round icons orbiting its two-line title. The
// ring turns once a minute; each icon turns back the other way so it stays upright.
import type { CSSProperties } from 'react'
import type { LucideIcon } from 'lucide-react'
import './intro.css'

export interface OrbitItem {
  label: string
  Icon: LucideIcon
  /** Tint; omitted draws a dashed "add your own" icon. */
  color?: string
}

export function OrbitHero({ title, items }: { title: [string, string]; items: OrbitItem[] }) {
  return (
    <div className="fi-orbit">
      <ul className="fi-orbit-ring" aria-label="What this screen holds">
        {items.map(({ label, Icon, color }, n) => (
          <li
            key={label}
            className={'fi-orbit-item' + (color ? '' : ' fi-orbit-item--own')}
            title={label}
            style={
              {
                '--a': `${-90 + (360 * n) / items.length}deg`,
                ...(color ? { '--c': color } : {}),
              } as CSSProperties
            }
          >
            <span className="fi-orbit-dot">
              <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
              <span className="ui-visually-hidden">{label}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="fi-orbit-title" aria-hidden="true">
        <span>{title[0]}</span> <span>{title[1]}</span>
      </p>
    </div>
  )
}
