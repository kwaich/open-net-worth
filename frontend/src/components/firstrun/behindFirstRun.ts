// First run is rendered INSIDE .app-shell, over a live app (see FirstRun.tsx). Covering the shell
// is not hiding it: its nav, topbar and Lucius panel stay in the tab order, so Tab from step 02
// walked into controls nobody can see. Every shell region BEHIND first run spreads these props;
// FirstRun itself never does.
export function behindFirstRunProps(firstRunShowing: boolean) {
  return { inert: firstRunShowing, 'aria-hidden': firstRunShowing || undefined } as const
}
