import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { authFetch } from './api/client'
import { BrandContext, DEFAULT_BRAND, type BrandConfig } from './brand-context'

// Instance branding / white-label (P3-ON / ON3). A tiny provider that fetches
// GET /api/config once at boot, then:
//   - sets document.title to app_name,
//   - overrides the `--accent` CSS token at runtime (the existing :root alias
//     already cascades app-wide — buttons / focus rings / chips), and
//   - exposes {app_name, logo_url} to the sidebar / login / auth-splash brand.
// GET /api/config is auth-exempt, so this works on the login screen before the
// user authenticates. The context + useBrand hook live in ./brand-context.

function applyBrand(brand: BrandConfig) {
  if (brand.app_name) document.title = brand.app_name
  const root = document.documentElement
  if (brand.accent_color) {
    root.style.setProperty('--accent', brand.accent_color)
  } else {
    // Clear any runtime override → fall back to the built-in :root --accent token.
    root.style.removeProperty('--accent')
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [brand, setBrand] = useState<BrandConfig>(DEFAULT_BRAND)

  useEffect(() => {
    authFetch('/api/config')
      .then((r) => r.json())
      .then((c: Partial<BrandConfig>) => {
        const next: BrandConfig = {
          app_name: c.app_name || DEFAULT_BRAND.app_name,
          accent_color: c.accent_color || '',
          logo_url: c.logo_url || '',
        }
        setBrand(next)
        applyBrand(next)
      })
      // Branding is best-effort: a failed fetch leaves the built-in defaults.
      .catch(() => {})
  }, [])

  return <BrandContext.Provider value={brand}>{children}</BrandContext.Provider>
}
