// Investments — entry point forwarded to the redesigned InvestmentsTab module.
// The redesign lives in investments/InvestmentsTab.tsx; this file keeps the
// existing import path intact for all callers (App.tsx, etc.).

export { InvestmentsTab as Investments } from './investments/InvestmentsTab'
