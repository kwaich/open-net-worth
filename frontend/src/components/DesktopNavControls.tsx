import { ArrowLeft, ArrowRight, PanelLeft } from 'lucide-react'
import { Tooltip } from './ui/Tooltip'

interface Props {
  sidebarExpanded: boolean
  onToggleSidebar: () => void
  /** Pointer entered / left the toggle: drives the collapsed rail's hover peek. */
  onToggleHover?: () => void
  onToggleLeave?: () => void
  canGoBack: boolean
  canGoForward: boolean
  onBack: () => void
  onForward: () => void
}

// The macOS desktop window has no title bar: this cluster sits just right of the traffic lights
// (src/index.css, "macOS desktop shell"), the way native apps put sidebar + history controls.
export function DesktopNavControls({
  sidebarExpanded,
  onToggleSidebar,
  onToggleHover,
  onToggleLeave,
  canGoBack,
  canGoForward,
  onBack,
  onForward,
}: Props) {
  return (
    <div className="desktop-nav" role="toolbar" aria-label="Navigation">
      <Tooltip label={sidebarExpanded ? 'Hide sidebar' : 'Show sidebar'} shortcut={['Mod', 'B']}>
        <button
          className="desktop-nav-btn"
          onClick={onToggleSidebar}
          onMouseEnter={onToggleHover}
          onMouseLeave={onToggleLeave}
          aria-label={sidebarExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
          aria-pressed={sidebarExpanded}
        >
          <PanelLeft size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
      </Tooltip>
      <Tooltip label="Back" shortcut={['Mod', '[']}>
        <button
          className="desktop-nav-btn"
          onClick={onBack}
          disabled={!canGoBack}
          aria-label="Back"
        >
          <ArrowLeft size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
      </Tooltip>
      <Tooltip label="Forward" shortcut={['Mod', ']']}>
        <button
          className="desktop-nav-btn"
          onClick={onForward}
          disabled={!canGoForward}
          aria-label="Forward"
        >
          <ArrowRight size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
      </Tooltip>
    </div>
  )
}
