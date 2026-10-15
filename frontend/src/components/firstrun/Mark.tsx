export interface FirstRunMarkProps {
  size?: 'sm' | 'lg'
}

export function FirstRunMark({ size = 'sm' }: FirstRunMarkProps) {
  const px = size === 'lg' ? 28 : 22
  return (
    <div className={`firstrun-mark${size === 'lg' ? ' firstrun-mark--lg' : ''}`}>
      <img
        className="firstrun-mark__icon"
        src="/logo-mark.svg?v=facet-solid"
        width={px}
        height={px}
        data-builtin-logo
        alt=""
        aria-hidden="true"
      />
      <span className="firstrun-mark__word">Open Net Worth</span>
    </div>
  )
}
