import { BP } from '../components/ui/breakpoints'

export function stickyTopbarOffset(topbar: HTMLElement, viewportWidth: number): number {
  return viewportWidth >= BP.sm ? topbar.offsetHeight : 0
}
