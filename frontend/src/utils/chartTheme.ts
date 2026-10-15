// Canvas charts (Chart.js) can't resolve `var(--token)` — they need concrete
// color strings at draw time. Read the CURRENT computed value of a design token
// off <html> so chart chrome (ticks, grids, tooltips, segment borders) follows
// the active named theme. Callers re-run their chart effect on theme flips
// by depending on useTheme().resolved (src/theme.ts).
export function cssToken(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || '#888'
}
