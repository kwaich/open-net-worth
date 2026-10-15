export interface DriverChip {
  ticker: string
  label: string
  instrument_id: string
  day_change_base: number
  day_change_pct: number
}

export interface PortfolioDrivers {
  top_gainers: DriverChip[]
  top_losers: DriverChip[]
  base_currency: string
}
