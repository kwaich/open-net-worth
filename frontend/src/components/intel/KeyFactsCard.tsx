import { useState } from 'react'
import { Panel, Skeleton, Stack, Text } from '../ui'
import { entityRefProps } from '../../utils/mentionable'
import { buildCtxFields } from '../../lucius/ctxFields'
import { filterByDescriptor } from '../../lucius/entityRegistry'
import { countryFlag, fCompact, fDate, fMonth, fPrice, safeHref } from '../../utils/format'
import { displayTicker } from './assetDisplay'
import type { AssetFacts, AssetInfo, CryptoFacts } from '../../types/assetDetail'

interface Row {
  label: string
  value: string
  href?: string
}

function FactRow({ label, value, href }: Row) {
  return (
    <div className="asd-fact-row">
      <span className="asd-fact-label">{label}</span>
      {href ? (
        <a
          className="asd-fact-value asd-fact-link"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
        >
          {value}
        </a>
      ) : (
        <span className="asd-fact-value">{value}</span>
      )}
    </div>
  )
}

interface Props {
  asset: AssetInfo
  /** The instrument this card describes — the asset block itself carries no id. */
  instrumentId?: string
  facts: AssetFacts
  cryptoFacts?: CryptoFacts | null
  /** True while a real facts/peers/description synthesis pass is actually running
   *  (`refreshing` from useAssetDetail — the same signal `_do_refresh` drives). Owner-
   *  reproduced live bug, 2026-08-31 (KODK cold-open): with no loading state at all here,
   *  the card just showed whatever sparse baseline data existed (often only Symbol/
   *  Exchange, pulled straight off `asset` rather than the AI-synthesized `facts`) with no
   *  indication more was coming. */
  generating?: boolean
}

/** "Network" id from CoinGecko is a slug ("binance-smart-chain") — humanize it. */
function prettyNetwork(id: string): string {
  return id
    .split('-')
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ')
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** Crypto Key Facts from real CoinGecko data (market cap / supply / ATH / launch). */
function cryptoRows(asset: AssetInfo, cf: CryptoFacts): Row[] {
  const sym = asset.symbol || ''
  const link = cf.homepage ? safeHref(cf.homepage) : null
  return [
    { label: 'Symbol', value: displayTicker(asset) || asset.name },
    cf.category && { label: 'Category', value: cf.category },
    cf.network && { label: 'Network', value: prettyNetwork(cf.network) },
    cf.market_cap != null && { label: 'Market cap', value: `$${fCompact(cf.market_cap)}` },
    cf.market_cap_rank != null && { label: 'Rank', value: `#${cf.market_cap_rank}` },
    cf.circulating_supply != null && {
      label: 'Circulating',
      value: `${fCompact(cf.circulating_supply)}${sym ? ` ${sym}` : ''}`,
    },
    cf.max_supply != null && {
      label: 'Max supply',
      value: `${fCompact(cf.max_supply)}${sym ? ` ${sym}` : ''}`,
    },
    cf.ath != null && {
      label: 'All-time high',
      value: `$${fPrice(cf.ath)}${cf.ath_date ? ` (${fMonth(cf.ath_date, 'short', 'always')})` : ''}`,
    },
    cf.genesis_date && { label: 'Launch', value: fMonth(cf.genesis_date, 'short', 'always') },
    link && { label: 'Website', value: hostname(cf.homepage as string), href: link },
  ].filter(Boolean) as Row[]
}

/** Equity Key Facts from the (AI-synthesized) brief. */
function equityRows(asset: AssetInfo, facts: AssetFacts): Row[] {
  const countryCode = facts.country || asset.country
  return [
    { label: 'Symbol', value: displayTicker(asset) || asset.name },
    facts.sector && { label: 'Sector', value: facts.sector },
    facts.industry && { label: 'Industry', value: facts.industry },
    (facts.exchange || asset.exchange) && {
      label: 'Exchange',
      value: facts.exchange || asset.exchange || '',
    },
    countryCode && { label: 'Country', value: `${countryFlag(countryCode)} ${countryCode}` },
    facts.ceo && { label: 'CEO', value: facts.ceo },
    facts.employees && { label: 'Employees', value: facts.employees.toLocaleString() },
    facts.ipo_date && {
      label: 'IPO Date',
      value: fDate(facts.ipo_date, 'short', 'always'),
    },
  ].filter(Boolean) as Row[]
}

export function KeyFactsCard({ asset, instrumentId, facts, cryptoFacts, generating }: Props) {
  const [expanded, setExpanded] = useState(false)

  const isCrypto = !!cryptoFacts
  const rows = isCrypto ? cryptoRows(asset, cryptoFacts) : equityRows(asset, facts)

  const desc = asset.description
  const descPreview = desc && !expanded ? desc.slice(0, 200) + (desc.length > 200 ? '…' : '') : desc

  // Crypto facts are real-time CoinGecko data, never LLM-synthesized — no generating state
  // applies there. `sparse` means only the always-available baseline (Symbol/Exchange, read
  // straight off `asset`) has landed, nothing from the AI-synthesized `facts` object yet.
  const showGenerating = !isCrypto && generating && rows.length <= 2 && !desc

  return (
    <Panel
      title="Key Facts"
      className="asd-rail-panel"
      actions={
        showGenerating ? (
          <span className="asd-pi-updating" role="status" aria-label="Key facts are generating">
            <span className="ui-btn__spinner" aria-hidden="true" />
            Generating
          </span>
        ) : undefined
      }
      {...(instrumentId
        ? {
            ...entityRefProps(
              'asset_brief',
              instrumentId,
              asset.ticker ?? asset.name,
              `${asset.ticker ?? ''} ${asset.name ?? ''} research brief`,
              asset.name ?? 'Research brief',
            ),
            'data-ctx': '',
            'data-ctx-type': 'asset_brief',
            'data-ctx-fields': buildCtxFields(
              filterByDescriptor('asset_brief', {
                symbol: asset.ticker,
                name: asset.name,
              }),
            ),
            draggable: true,
          }
        : {})}
    >
      <Stack gap="--space-2">
        {rows.map((r) => (
          <FactRow key={r.label} label={r.label} value={r.value} href={r.href} />
        ))}

        {showGenerating && (
          <div className="asd-skel-stack" role="status" aria-label="Generating">
            <Skeleton height={13} width="85%" />
            <Skeleton height={13} width="70%" />
            <Skeleton height={13} width="60%" />
          </div>
        )}

        {desc && (
          <div className="asd-fact-desc">
            <Text size="sm" tone="faint">
              {descPreview}
            </Text>
            {desc.length > 200 && (
              <button
                type="button"
                className="asd-fact-desc-toggle"
                onClick={() => setExpanded((e) => !e)}
              >
                {expanded ? 'Show less' : 'View more'}
              </button>
            )}
          </div>
        )}

        {/* Crypto facts are real CoinGecko data; equity facts are AI-synthesized. */}
        {isCrypto ? (
          <Text size="xs" tone="faint" className="asd-fact-disclaimer">
            Data via CoinGecko
          </Text>
        ) : (
          facts.unverified && (
            <Text size="xs" tone="faint" className="asd-fact-disclaimer">
              AI-generated facts, may be inaccurate.
            </Text>
          )
        )}
      </Stack>
    </Panel>
  )
}
