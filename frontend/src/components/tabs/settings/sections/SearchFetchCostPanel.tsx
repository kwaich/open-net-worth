import { Panel } from '../ds'

type ServiceCostRow = [label: string, cost: number, calls: number]

export function SearchFetchCostPanel({
  rows,
  formatCost,
  periodLabel,
}: {
  rows: ServiceCostRow[]
  formatCost: (value: number) => string
  periodLabel: string
}) {
  const total = rows.reduce((sum, [, cost]) => sum + cost, 0)

  return (
    <Panel
      title="Search & fetch"
      sub="Estimated third-party API charges in the selected period"
      actions={
        <div className="rd-service-cost-total">
          <span>{`Estimated total · ${periodLabel}`}</span>
          <strong>{formatCost(total)}</strong>
        </div>
      }
    >
      {rows.length ? (
        <div className="rd-service-cost-table-wrap">
          <table className="rd-service-cost-table">
            <thead>
              <tr>
                <th scope="col">Provider</th>
                <th scope="col">Estimated cost</th>
                <th scope="col">Calls</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([label, cost, calls]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td>{formatCost(cost)}</td>
                  <td>
                    {calls.toLocaleString()} call{calls === 1 ? '' : 's'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rd-service-cost-empty">No search or fetch calls in this window.</div>
      )}
    </Panel>
  )
}
