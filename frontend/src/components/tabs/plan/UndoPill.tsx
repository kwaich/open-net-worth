// plan/UndoPill.tsx — the floating "… · Undo" pill both budget methods render at their root
// (useUndoPill.ts holds its state).
export function UndoPill({ undo, onUndo }: { undo: { label: string } | null; onUndo: () => void }) {
  if (!undo) return null
  return (
    <div className="pl-undo mb-anchored" role="status">
      <span>{undo.label}</span>
      <button onClick={onUndo}>Undo</button>
    </div>
  )
}
