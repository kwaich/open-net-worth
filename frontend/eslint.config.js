import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// Inline `fontSize` in a JSX style object bypasses the type scale (the same
// debt the stylelint raw-`font-size` rule guards in CSS). Flag a raw numeric
// `fontSize` value (e.g. `style={{ fontSize: 13 }}`); a token reference
// (`fontSize: 'var(--text-base)'` / a template literal) is a string and is
// allowed, so the design-system Gallery stays clean.
//
// Severity is WARNING for now: ~162 pre-existing raw-numeric usages live in
// not-yet-migrated tabs (tracked debt, migrated in FE1). Warning surfaces the
// count without blocking. FLIP TO 'error' once FE1 lands and the count is 0 —
// the migrated codebase then can't regress. `src/components/ui/` is already
// clean (zero raw-numeric fontSize), so new design-system code is held to the
// rule today.
const NO_INLINE_FONT_SIZE = {
  // Flag a raw literal fontSize value (`13`, `'13px'`) and the negative-number
  // case (a UnaryExpression). A `var(--text-*)` string or a template literal is
  // NOT a flagged Literal, so token references pass.
  selector:
    "Property[key.name='fontSize'][value.type='Literal'][value.value!=/^var\\(--text-/]" +
    ':not([value.value=/^(inherit|initial|unset|revert)$/]), ' +
    "Property[key.name='fontSize'] > UnaryExpression",
  message:
    'Inline fontSize uses a raw value. Use a --text-* token via a className or ' +
    "style={{ fontSize: 'var(--text-base)' }}. See docs/FRONTEND_CONVENTIONS.md.",
}

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      'no-restricted-syntax': ['warn', NO_INLINE_FONT_SIZE],
    },
  },
])
