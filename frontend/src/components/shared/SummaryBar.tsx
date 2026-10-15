interface Card {
  label: string
  value: string
  cls?: string
}

export function SummaryBar({ cards }: { cards: Card[] }) {
  return (
    <div className="sumbar">
      {cards.map(({ label, value, cls }) => (
        <div key={label} className="sumcard">
          <div className="sk">{label}</div>
          <div className={`sv ${cls || ''}`}>{value}</div>
        </div>
      ))}
    </div>
  )
}
