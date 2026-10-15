// Type declaration for tokens.source.mjs (consumed by build-tokens.mjs codegen and
// the theme test). A token tuple is [name, darkValue], [name, darkValue, warmValue],
// or [name, darkValue, warmValue, lightValue].
export declare const tokenGroups: ReadonlyArray<{
  readonly label: string
  readonly category: 'color' | 'space' | 'text' | 'radius' | 'shadow' | 'z'
  readonly tokens: ReadonlyArray<
    | readonly [string, string | number]
    | readonly [string, string, string]
    | readonly [string, string, string, string]
  >
}>
