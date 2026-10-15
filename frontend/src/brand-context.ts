import { createContext, useContext } from 'react'

// Instance branding / white-label (P3-ON / ON3). The context + hook live in a
// non-component module so the ThemeProvider component file stays
// component-only (react-refresh/only-export-components).

export interface BrandConfig {
  app_name: string
  accent_color: string
  logo_url: string
}

export const DEFAULT_BRAND: BrandConfig = {
  app_name: 'Open Net Worth',
  accent_color: '',
  logo_url: '',
}

export const BrandContext = createContext<BrandConfig>(DEFAULT_BRAND)

/** Read the current instance branding (name / accent / logo). */
export function useBrand(): BrandConfig {
  return useContext(BrandContext)
}
