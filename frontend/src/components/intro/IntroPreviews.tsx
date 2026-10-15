// What each screen's intro shows above its title: a small, simplified copy of the screen
// itself, built from the design system's own pieces with sample figures, so a user sees what
// the screen will look like before it holds anything of theirs. Decorative: hidden from
// screen readers and never interactive.
import type { CSSProperties, ReactNode } from 'react'
import {
  Bell,
  CalendarDays,
  Coins,
  CreditCard,
  Crosshair,
  Globe,
  Landmark,
  Mail,
  Plug,
  Send,
  TrendingDown,
} from 'lucide-react'
import { Badge, Meter, Money, Panel, Tag, Text } from '../ui'
import { Sparkline } from '../ui/Sparkline'
import './intro.css'

function Preview({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  // Labelled: on a first visit these sample figures sat beside the user's real ones and
  // were read as theirs ("NET WORTH S$412,800 — whose money is that?", persona QA 2026-10-13).
  return (
    <>
      <span className="fi-pv-example">
        <Tag tone="muted">Example</Tag>
      </span>
      <div className={'fi-pv' + (wide ? ' fi-pv--wide' : '')} aria-hidden="true">
        {children}
      </div>
    </>
  )
}

function Row({ children }: { children: ReactNode }) {
  return <div className="fi-pv-row">{children}</div>
}

type SourceKind = 'bank' | 'card' | 'mail'
const SOURCE_ICON: Record<SourceKind, typeof Landmark> = {
  bank: Landmark,
  card: CreditCard,
  mail: Mail,
}

function SourceIcon({ kind }: { kind: SourceKind }) {
  const Icon = SOURCE_ICON[kind]
  return (
    <span className={`fi-pv-icon fi-pv-icon--${kind}`}>
      <Icon {...ICON} />
    </span>
  )
}

const ICON = { size: 13, strokeWidth: 2 } as const

// Each row carries the icon of the source it came from, so the picture says what the screen
// does: bank, card and email all land in one ledger, already categorised.
// Each category wears its own colour, as categories do across the app.
const LEDGER = [
  ['bank', 'Salary', 'Income', 'var(--good)', '+6,200.00'],
  ['card', 'Grab', 'Transport', 'var(--primary)', '−12.40'],
  ['mail', 'Netflix', 'Subscriptions', 'var(--cat-watch)', '−19.98'],
  ['card', 'NTUC', 'Groceries', 'var(--cat-metal)', '−84.15'],
] as const

export function AccountsPreview() {
  return (
    <Preview>
      <div className="fi-pv-sources">
        {(
          [
            ['bank', 'Bank'],
            ['card', 'Card'],
            ['mail', 'Email'],
          ] as const
        ).map(([kind, label]) => (
          <div key={kind} className="fi-pv-source">
            <SourceIcon kind={kind} />
            <Text size="xs">{label}</Text>
          </div>
        ))}
      </div>
      <svg className="fi-pv-flow" viewBox="0 0 300 24" preserveAspectRatio="none">
        <path d="M50 0 C50 14 150 10 150 24" />
        <path d="M150 0 V24" />
        <path d="M250 0 C250 14 150 10 150 24" />
      </svg>
      <Panel flush>
        {LEDGER.map(([kind, payee, cat, color, amt]) => (
          <Row key={payee}>
            <SourceIcon kind={kind} />
            <Text size="xs" weight={500}>
              {payee}
            </Text>
            <span className="fi-pv-cat" style={{ '--cc': color } as CSSProperties}>
              {cat}
            </span>
            <Money className={'fi-pv-amt' + (amt.startsWith('+') ? ' fi-pv-pos' : '')}>{amt}</Money>
          </Row>
        ))}
      </Panel>
    </Preview>
  )
}

const SPLIT = [
  { id: 'needs', label: 'Needs', amount: 'S$3,100', pct: 50, color: 'var(--primary)' },
  { id: 'wants', label: 'Wants', amount: 'S$1,860', pct: 30, color: 'var(--cat-watch)' },
  { id: 'save', label: 'Save', amount: 'S$1,240', pct: 20, color: 'var(--good)' },
] as const

export function BudgetPreview() {
  return (
    <Preview wide>
      <div className="fi-pv-pair">
        <Panel title="50/30/20">
          <Meter
            ariaLabel="Needs, wants and savings"
            size="lg"
            segments={SPLIT.map(({ id, pct, color }) => ({ id, pct, color }))}
          />
          {SPLIT.map((p) => (
            <Row key={p.id}>
              <span className="fi-pv-swatch" style={{ background: p.color }} />
              <Text size="xs">{p.label}</Text>
              <Money>{p.amount}</Money>
            </Row>
          ))}
        </Panel>
        <Panel title="Envelopes">
          {(
            [
              ['Groceries', '320 of 400', 80, 'good'],
              ['Dining', '45 over', 100, 'bad'],
              ['Rent', 'Funded', 100, 'accent'],
            ] as const
          ).map(([name, note, value, tone]) => (
            <div key={name} className="fi-pv-env">
              <Row>
                <Text size="xs">{name}</Text>
                <Text size="xs" tone={tone === 'bad' ? 'bad' : 'muted'}>
                  {note}
                </Text>
              </Row>
              <Meter ariaLabel={name} value={value} tone={tone} size="sm" />
            </div>
          ))}
        </Panel>
      </div>
    </Preview>
  )
}

// Six weeks of balance: paydays lift it, rent and the card bill pull it down.
const CASH_PATH = 'M0 62 H30 V22 H82 V60 H132 V86 H170 V40 H222 V74 H272 V78 H300'

export function CashFlowPreview() {
  return (
    <Preview>
      <Panel title="Next 6 weeks" badge={<Badge tone="warn">Low point Oct 24</Badge>}>
        <svg className="fi-pv-chart" viewBox="0 0 300 100" preserveAspectRatio="none">
          <path d={CASH_PATH} className="fi-pv-line" />
          <circle cx="30" cy="22" r="3.5" className="fi-pv-dot fi-pv-dot--good" />
          <circle cx="82" cy="60" r="3.5" className="fi-pv-dot fi-pv-dot--bad" />
          <circle cx="132" cy="86" r="3.5" className="fi-pv-dot fi-pv-dot--bad" />
          <circle cx="170" cy="40" r="3.5" className="fi-pv-dot fi-pv-dot--good" />
        </svg>
        <div className="fi-pv-legend">
          <Text size="2xs" tone="good">
            Payday
          </Text>
          <Text size="2xs" tone="bad">
            Rent, card bill
          </Text>
        </div>
      </Panel>
    </Preview>
  )
}

export function OutlookPreview() {
  return (
    <Preview>
      <Panel title="Your financial future">
        <div className="fi-pv-stat">
          <Text size="2xl" weight={700} tone="good">
            92%
          </Text>
          <Text size="xs" tone="muted">
            chance your money lasts to 90, retiring at 60
          </Text>
        </div>
        <svg className="fi-pv-chart" viewBox="0 0 300 90" preserveAspectRatio="none">
          <path
            d="M0 86 C80 78 140 50 200 18 C240 2 270 8 300 34 L300 86 C270 80 240 72 200 58 C140 72 80 84 0 88Z"
            className="fi-pv-band"
          />
          <path d="M0 87 C80 81 140 62 200 38 C240 22 270 26 300 56" className="fi-pv-line" />
          <line x1="200" y1="0" x2="200" y2="90" className="fi-pv-mark" />
        </svg>
      </Panel>
    </Preview>
  )
}

// The three things the screen does at a glance: what you hold, how it has done against the
// market, and what you are watching.
const HOLDINGS = [
  ['VWRA', 'World ETF', 'S$48,200', '+0.6%', 'accent'],
  ['NVDA', 'Nvidia', 'S$21,940', '−2.1%', 'watch'],
  ['BTC', 'Bitcoin', 'S$9,310', '+1.4%', 'warn'],
] as const

const PERF = [100, 101, 100.5, 104, 103.6, 107, 106.8, 110, 112.4]
const BENCH = [100, 100.6, 101.4, 102, 103.1, 104.2, 105.6, 107.3, 109.1]

export function InvestmentsPreview() {
  return (
    <Preview>
      <Panel title="Holdings" actions={<Money className="fi-pv-amt">S$86,500</Money>} flush>
        {HOLDINGS.map(([sym, name, value, move, hue]) => (
          <Row key={sym}>
            <span className={`fi-pv-tk fi-pv-tk--${hue}`}>{sym}</span>
            <Text size="xs" weight={500}>
              {name}
            </Text>
            <Money className="fi-pv-amt">{value}</Money>
            <Text size="2xs" tone={move.startsWith('+') ? 'good' : 'bad'} className="fi-pv-move">
              {move}
            </Text>
          </Row>
        ))}
      </Panel>
      <div className="fi-pv-pair">
        <Panel>
          <Text size="2xs" tone="muted">
            This year
          </Text>
          <Text size="lg" weight={700} tone="good">
            +12.4%
          </Text>
          <Sparkline values={PERF} compare={BENCH} up fluid className="fi-pv-spark" />
          <Text size="2xs" tone="muted">
            S&amp;P 500 +9.1%
          </Text>
        </Panel>
        <Panel>
          <Text size="2xs" tone="muted">
            Watchlist
          </Text>
          <Row>
            <Text size="xs">TSLA</Text>
            <Text size="xs" tone="bad">
              −4.8%
            </Text>
          </Row>
          <Row>
            <Text size="xs">AAPL</Text>
            <Text size="xs" tone="good">
              +0.9%
            </Text>
          </Row>
          <div className="fi-pv-alert">
            <Bell {...ICON} />
            <Text size="2xs" tone="warn">
              TSLA hit your alert
            </Text>
          </div>
        </Panel>
      </div>
    </Preview>
  )
}

export function IntelPreview() {
  return (
    <Preview>
      <div className="fi-pv-alerts">
        <Panel>
          <Row>
            <span className="fi-pv-icon fi-pv-icon--bad">
              <TrendingDown {...ICON} />
            </span>
            <Text size="xs" weight={600}>
              NVDA down 6% today
            </Text>
            <Badge tone="neg">−S$1,480</Badge>
          </Row>
        </Panel>
        <Panel>
          <Row>
            <span className="fi-pv-icon">
              <CalendarDays {...ICON} />
            </span>
            <Text size="xs" weight={600}>
              Rate decision on Wednesday
            </Text>
            <Tag tone="muted">Your bonds</Tag>
          </Row>
        </Panel>
        <Panel>
          <Row>
            <span className="fi-pv-icon fi-pv-icon--good">
              <Coins {...ICON} />
            </span>
            <Text size="xs" weight={600}>
              Dividend paid
            </Text>
            <Badge tone="pos">+S$212</Badge>
          </Row>
        </Panel>
      </div>
    </Preview>
  )
}

export function CommitmentsPreview() {
  return (
    <Preview>
      <Panel title="Coming up" flush>
        {(
          [
            ['New car', 'Mar 2027', 'S$18,000', 60, 'good'],
            ['Wedding', 'Nov 2027', 'S$40,000', 25, 'warn'],
          ] as const
        ).map(([name, when, amount, funded, tone]) => (
          <div key={name} className="fi-pv-goal">
            <Row>
              <Text size="xs" weight={600}>
                {name}
              </Text>
              <Text size="xs" tone="muted">
                {when}
              </Text>
              <Money>{amount}</Money>
            </Row>
            <Meter ariaLabel={name} value={funded} tone={tone} size="sm" />
            <Text size="2xs" tone="muted">
              {funded}% funded
            </Text>
          </div>
        ))}
      </Panel>
    </Preview>
  )
}

export function DashboardPreview() {
  return (
    <Preview wide>
      <div className="fi-pv-widgets">
        <Panel title="Net worth">
          <Money className="fi-pv-big">S$412,800</Money>
          <Sparkline values={[310, 318, 316, 335, 348, 362, 371, 389, 402, 413]} up fluid />
        </Panel>
        <Panel title="Spent this month">
          <Money className="fi-pv-big">S$2,940</Money>
        </Panel>
        <Panel title="Next bill">
          <Text size="sm" weight={600}>
            Card, Friday
          </Text>
        </Panel>
      </div>
    </Preview>
  )
}

// An automation is Lucius acting on a trigger: a price, a date, an email. The first one acts on
// a market move, so the picture shows more than a reminder.
const AUTOMATIONS = [
  [Crosshair, '', 'Buy AAPL under $180', 'When price hits'],
  [CalendarDays, 'fi-pv-icon--good', 'Monthly portfolio report', '1st, 9:00'],
  [Mail, 'fi-pv-icon--mail', 'Log transactions from email', 'On new mail'],
] as const

export function AutomationsPreview() {
  return (
    <Preview>
      <Panel flush>
        {AUTOMATIONS.map(([Icon, tone, name, when]) => (
          <Row key={name}>
            <span className={'fi-pv-icon ' + tone}>
              <Icon {...ICON} />
            </span>
            <Text size="xs" weight={500}>
              {name}
            </Text>
            <Text size="2xs" tone="muted">
              {when}
            </Text>
          </Row>
        ))}
      </Panel>
    </Preview>
  )
}

const SKILLS = [
  ['monthly-report', 'Your report, laid out your way'],
  ['import-bank-pdf', 'Reads your bank’s statement'],
  ['trip-budget', 'Plans a trip from your envelopes'],
] as const

export function SkillsPreview() {
  return (
    <Preview>
      <Panel flush>
        {SKILLS.map(([name, what]) => (
          <Row key={name}>
            <span className="fi-pv-icon fi-pv-icon--mail fi-pv-slash">/</span>
            <Text size="xs" weight={500}>
              {name}
            </Text>
            <Text size="2xs" tone="muted">
              {what}
            </Text>
          </Row>
        ))}
      </Panel>
    </Preview>
  )
}

// A connector gives Lucius something new: data to read, a way to reach you, a tool to use.
const CONNECTORS = [
  [Landmark, 'fi-pv-icon--good', 'Bank feed', 'Reads your transactions'],
  [Send, '', 'Telegram', 'Messages you anywhere'],
  [Globe, 'fi-pv-icon--mail', 'Web search', 'Looks things up'],
  [Plug, 'fi-pv-icon--card', 'Any MCP server', 'Adds its tools'],
] as const

export function ConnectorsPreview() {
  return (
    <Preview>
      <Panel flush>
        {CONNECTORS.map(([Icon, tone, name, gives]) => (
          <Row key={name}>
            <span className={'fi-pv-icon ' + tone}>
              <Icon {...ICON} />
            </span>
            <Text size="xs" weight={500}>
              {name}
            </Text>
            <Text size="2xs" tone="muted">
              {gives}
            </Text>
          </Row>
        ))}
      </Panel>
    </Preview>
  )
}
