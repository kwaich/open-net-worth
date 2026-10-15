// projections-app.jsx — Projections redesign, two directions on a canvas
const { useState, useMemo } = React;
const { ACCURACY, SCENARIO, RETIRE_POINTER } = window.PROJ;

/* ---------- format ---------- */
const fmt = (n) => 'S$' + Math.round(Math.abs(n)).toLocaleString('en-US');
const sgn = (n) => (n < 0 ? '−' : '+') + 'S$' + Math.abs(Math.round(n)).toLocaleString('en-US');
function fk(n) {
  const a = Math.abs(n);
  if (a >= 1e6) return 'S$' + (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (a >= 1e3) return 'S$' + Math.round(n / 1e3) + 'k';
  return 'S$' + Math.round(n);
}

/* ---------- shared ui ---------- */
function Pill({ tone, children }) {
  const map = {
    pos: ['var(--teal)', 'var(--teal-soft)'], warn: ['var(--gold)', 'var(--gold-soft)'],
    bad: ['var(--red)', 'var(--red-soft)'], info: ['var(--blue)', 'var(--blue-soft)'],
  }[tone] || ['var(--fg-1)', 'var(--surface-2)'];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 11px', borderRadius: 99, fontSize: 12, fontWeight: 600, color: map[0], background: map[1], whiteSpace: 'nowrap' }}>
      <span style={{ width: 6, height: 6, borderRadius: 99, background: map[0] }} />{children}
    </span>
  );
}
function Info({ text, w = 250 }) {
  const [open, setOpen] = useState(false);
  return (
    <span style={{ position: 'relative', display: 'inline-flex', verticalAlign: 'middle' }}>
      <button onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }} aria-label="Explain"
        style={{ width: 16, height: 16, borderRadius: 99, border: '1px solid var(--line-strong)', background: open ? 'var(--blue-soft)' : 'transparent', color: open ? 'var(--blue)' : 'var(--fg-2)', cursor: 'pointer', font: 'inherit', fontSize: 10, fontWeight: 700, lineHeight: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0 }}>i</button>
      {open && (
        <>
          <span onClick={(e) => { e.stopPropagation(); setOpen(false); }} style={{ position: 'fixed', inset: 0, zIndex: 50 }} />
          <span style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 51, width: w, background: 'var(--surface-2)', border: '1px solid var(--line-strong)', borderRadius: 10, padding: '10px 12px', fontSize: 12, lineHeight: 1.5, color: 'var(--fg-1)', boxShadow: '0 14px 40px rgba(0,0,0,0.5)', fontWeight: 400, whiteSpace: 'normal', textWrap: 'pretty' }}>{text}</span>
        </>
      )}
    </span>
  );
}
const INFO = {
  monteCarlo: 'A Monte Carlo simulation runs thousands of random market histories instead of assuming one fixed return — so you see a realistic range of outcomes, not a single optimistic line.',
  p50: 'The middle outcome — half the simulations ended higher, half lower.',
  p10: 'A bad-luck case — only 1 in 10 simulations ended this low or lower.',
  p90: 'A good-luck case — only 1 in 10 simulations ended this high or higher.',
  moneyLasts: 'Share of simulations where your money survived the whole period without hitting zero.',
};
const eyebrow = { fontSize: 10.5, letterSpacing: '0.13em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 };
const card = { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 16 };

function SubTabs({ active }) {
  return (
    <div style={{ display: 'flex', gap: 26, borderBottom: '1px solid var(--line)', marginBottom: 24 }}>
      {['Budget', 'Projections', 'Retirement'].map(t => (
        <div key={t} style={{ paddingBottom: 12, fontSize: 14.5, fontWeight: active === t ? 600 : 500, color: active === t ? 'var(--fg-0)' : 'var(--fg-2)', borderBottom: active === t ? '2px solid var(--blue)' : '2px solid transparent', marginBottom: -1 }}>{t}</div>
      ))}
    </div>
  );
}

function WindowChips() {
  const [w, setW] = useState('3M');
  return (
    <div style={{ display: 'flex', gap: 4, padding: 3, background: 'var(--surface-2)', borderRadius: 9 }}>
      {['1M', '3M', '6M', '1Y'].map(o => (
        <button key={o} onClick={() => setW(o)} style={{ padding: '4px 11px', borderRadius: 7, border: 'none', cursor: 'pointer', font: 'inherit', fontSize: 12.5, background: w === o ? 'var(--bg-2)' : 'transparent', color: w === o ? 'var(--fg-0)' : 'var(--fg-2)', boxShadow: w === o ? 'inset 0 0 0 1px var(--line-strong)' : 'none' }}>{o}</button>
      ))}
    </div>
  );
}

/* ---------- accuracy pieces ---------- */
function AccuracyReads() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
      <Read tone="pos" head="Cash flow — reliable" body={ACCURACY.cashflow.note} foot={`Typically within ~${ACCURACY.cashflow.errorPct}% of plan`} />
      <Read tone="warn" head="Net worth — market-driven" body={ACCURACY.networth.note} foot="Treat the projected line loosely" />
    </div>
  );
}
function Read({ tone, head, body, foot }) {
  const c = tone === 'pos' ? 'var(--teal)' : 'var(--gold)';
  return (
    <div style={{ padding: '14px 16px', borderRadius: 12, background: 'var(--bg-2)', border: '1px solid var(--line)', borderLeft: `2px solid ${c}` }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: c }}>{head}</div>
      <p style={{ margin: '6px 0 8px', fontSize: 12.5, color: 'var(--fg-1)', lineHeight: 1.5, textWrap: 'pretty' }}>{body}</p>
      <div style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{foot}</div>
    </div>
  );
}
function MonthTable() {
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', fontSize: 10.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600, padding: '0 2px 8px' }}>
        <span>Month</span><span style={{ textAlign: 'right' }}>Cash-flow miss</span><span style={{ textAlign: 'right' }}>Net-worth miss</span>
      </div>
      {ACCURACY.months.map(r => (
        <div key={r.m} style={{ display: 'grid', gridTemplateColumns: '1fr 150px 150px', alignItems: 'center', padding: '9px 2px', borderTop: '1px solid var(--line)', fontSize: 13 }}>
          <span style={{ color: 'var(--fg-1)' }}>{r.m}</span>
          <span style={{ textAlign: 'right' }}><MissBadge n={r.cfMiss} /></span>
          <span style={{ textAlign: 'right' }}><MissBadge n={r.nwMiss} /></span>
        </div>
      ))}
    </div>
  );
}
function MissBadge({ n }) {
  const pos = n >= 0;
  return <span className="num" style={{ fontSize: 12.5, padding: '3px 9px', borderRadius: 99, color: pos ? 'var(--teal)' : 'var(--red)', background: pos ? 'var(--teal-soft)' : 'var(--red-soft)' }}>{sgn(n)}</span>;
}
function TechToggle() {
  const [open, setOpen] = useState(false);
  const t = ACCURACY.tech;
  return (
    <div>
      <button onClick={() => setOpen(o => !o)} style={{ background: 'none', border: 'none', color: 'var(--blue)', cursor: 'pointer', font: 'inherit', fontSize: 12.5, padding: 0 }}>
        {open ? 'Hide the technical numbers' : 'Show the technical numbers'} {open ? '▾' : '▸'}
      </button>
      {open && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginTop: 12 }}>
          <Tech label="Cash-flow error (MAPE)" v={t.cashflowMape + '%'} />
          <Tech label="Avg monthly miss" v={fmt(t.avgMonthlyMiss)} />
          <Tech label="Cash-flow bias" v={sgn(t.cashflowBias)} tone="bad" />
          <Tech label="Net-worth drift" v={sgn(t.netWorthDrift)} tone="bad" />
        </div>
      )}
    </div>
  );
}
function Tech({ label, v, tone }) {
  return (
    <div style={{ padding: '10px 12px', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 10 }}>
      <div style={{ fontSize: 10.5, color: 'var(--fg-2)' }}>{label}</div>
      <div className="num" style={{ fontSize: 15, fontWeight: 600, marginTop: 4, color: tone === 'bad' ? 'var(--red)' : 'var(--fg-0)' }}>{v}</div>
    </div>
  );
}

/* ---------- scenario pieces ---------- */
function Presets() {
  const [active, setActive] = useState('Base case');
  const items = [{ n: 'Base case' }, { n: 'Stress test' }, { n: 'Big purchase' }, { n: 'Retire @ 45', saved: true }];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', gap: 4, padding: 3, background: 'var(--surface-2)', borderRadius: 9, flexWrap: 'wrap' }}>
        {items.map(it => (
          <button key={it.n} onClick={() => setActive(it.n)} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '6px 12px', borderRadius: 7, border: 'none', cursor: 'pointer', font: 'inherit', fontSize: 13, background: active === it.n ? 'var(--bg-2)' : 'transparent', color: active === it.n ? 'var(--fg-0)' : 'var(--fg-2)', boxShadow: active === it.n ? 'inset 0 0 0 1px var(--line-strong)' : 'none' }}>
            {it.saved && <span style={{ color: 'var(--blue)', fontSize: 11 }}>✦</span>}{it.n}
          </button>
        ))}
      </div>
      <button style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8, border: '1px dashed var(--line-strong)', background: 'transparent', color: 'var(--fg-1)', cursor: 'pointer', font: 'inherit', fontSize: 12.5 }}>+ Save current</button>
    </div>
  );
}
function Inputs() {
  const i = SCENARIO.inputs;
  const f = [['Expected return', i.expectedReturn + '%'], ['Market swings (volatility)', i.volatility + '%'], ['Monthly saving', fmt(i.monthlySaving)]];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
      {f.map(([l, v]) => (
        <div key={l}>
          <div style={{ fontSize: 11.5, color: 'var(--fg-2)', marginBottom: 5 }}>{l}</div>
          <div className="num" style={{ padding: '9px 12px', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 9, fontSize: 14, color: 'var(--fg-0)' }}>{v}</div>
        </div>
      ))}
    </div>
  );
}
function ScenarioStats({ cols = 4 }) {
  const s = SCENARIO;
  const cards = [
    ['Typical outcome', fk(s.medianEnding), 'var(--fg-0)', INFO.p50],
    ['If unlucky', fk(s.p10Ending), 'var(--red)', INFO.p10],
    ['If lucky', fk(s.p90Ending), 'var(--teal)', INFO.p90],
    ['Money lasts', s.moneyLasts + '%', s.moneyLasts >= 80 ? 'var(--teal)' : s.moneyLasts >= 50 ? 'var(--gold)' : 'var(--red)', INFO.moneyLasts],
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols},1fr)`, gap: 12 }}>
      {cards.map(([l, v, c, info]) => (
        <div key={l} style={{ padding: '14px 16px', ...card, borderRadius: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{l}</span>
            <Info text={info} />
          </div>
          <div className="num" style={{ fontSize: 21, fontWeight: 600, color: c, marginTop: 6 }}>{v}</div>
        </div>
      ))}
    </div>
  );
}

/* ---------- fan chart (40y) ---------- */
function FanChart({ w = 1040, h = 230 }) {
  const { ages, p10, p50, p90 } = SCENARIO;
  const padL = 4, padR = 4, padT = 14, padB = 26;
  const iW = w - padL - padR, iH = h - padT - padB;
  const yMax = Math.max(...p90) * 1.05, yMin = 0;
  const X = (i) => padL + (i / (ages.length - 1)) * iW;
  const Y = (v) => padT + iH * (1 - (v - yMin) / (yMax - yMin));
  const line = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ');
  const band = `${p90.map((v, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')} ${p10.slice().reverse().map((v, i) => `L ${X(p10.length - 1 - i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')} Z`;
  const dryIdx = p50.findIndex((v, i) => i > 18 && v <= 0);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => yMin + f * (yMax - yMin));
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: 'block' }}>
      {ticks.map((v, i) => (
        <g key={i}>
          <line x1={padL} x2={w - padR} y1={Y(v)} y2={Y(v)} stroke="var(--line)" strokeWidth="1" />
          <text x={padL + 2} y={Y(v) - 4} fill="var(--fg-2)" fontSize="10.5" fontFamily="inherit">{fk(v)}</text>
        </g>
      ))}
      <path d={band} fill="var(--teal)" opacity="0.10" />
      <path d={line(p50)} fill="none" stroke="var(--teal)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {dryIdx > 0 && (
        <g>
          <line x1={X(dryIdx)} x2={X(dryIdx)} y1={padT} y2={padT + iH} stroke="var(--red)" strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
          <text x={X(dryIdx) + 5} y={padT + 12} fill="var(--red)" fontSize="10.5" fontFamily="inherit">typical path runs dry ~age {ages[dryIdx]}</text>
        </g>
      )}
      {ages.map((a, i) => (i % 8 === 0 ? <text key={i} x={X(i)} y={h - 6} fill="var(--fg-2)" fontSize="10.5" textAnchor={i === 0 ? 'start' : 'middle'} fontFamily="inherit">age {a}</text> : null))}
    </svg>
  );
}

function RetirementPointer() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 16px', borderRadius: 12, background: 'var(--bg-2)', border: '1px solid var(--line)' }}>
      <span style={{ fontSize: 13, color: 'var(--fg-1)', flex: 1 }}>Free-form sandbox — for your actual FIRE plan &amp; verdict</span>
      <a style={{ fontSize: 13, color: 'var(--blue)', cursor: 'pointer', fontWeight: 500, whiteSpace: 'nowrap' }}>Open Retirement →</a>
    </div>
  );
}
function AiHint() {
  return (
    <div style={{ fontSize: 12.5, color: 'var(--fg-2)', fontStyle: 'italic' }}>
      Ask Lucius: "stress-test a 2008-style crash", "can I afford an S$80k car?", "what if I save S$1k more a month?"
    </div>
  );
}

/* ---------- hero stat ---------- */
function HeroStat({ label, value, sub, tone, info }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{label}</span>
        {info && <Info text={info} />}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, color: tone || 'var(--fg-0)', marginTop: 4 }}>{value}</div>
      <div style={{ fontSize: 11.5, color: 'var(--fg-3)', marginTop: 2 }}>{sub}</div>
    </div>
  );
}

/* ===================================================================== */
/* Single Projections screen — matches the Retirement visual system      */
/* ===================================================================== */
function App() {
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '30px 40px 70px' }}>
      <SubTabs active="Projections" />
      <div style={eyebrow}>Projections</div>

      {/* HERO — at-a-glance, flat card (no gradient), mirrors Retirement */}
      <div style={{ ...card, marginTop: 12, padding: '24px 28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <Pill tone="pos">Forecasts on point</Pill>
          <span style={{ fontSize: 13.5, color: 'var(--fg-2)' }}>How reliable are our forecasts — and what could markets do to your money?</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 40, marginTop: 18, flexWrap: 'wrap' }}>
          <HeroStat label="Cash-flow forecast" value="Reliable" sub={`within ~${ACCURACY.cashflow.errorPct}% of plan`} tone="var(--teal)" />
          <HeroStat label="Net-worth forecast" value="Noisy" sub="swings with markets" tone="var(--gold)" />
          <HeroStat label="Markets · 40-yr Monte Carlo" value={`${SCENARIO.moneyLasts}% lasts`} sub="money survival" tone="var(--red)" info={INFO.moneyLasts} />
        </div>
      </div>

      {/* Panel 1 — forecast accuracy */}
      <div style={{ ...card, marginTop: 18, padding: '22px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>How close have our forecasts been?</h3>
          <WindowChips />
        </div>
        <AccuracyReads />
        <div style={{ marginTop: 18 }}><MonthTable /></div>
        <div style={{ marginTop: 16 }}><TechToggle /></div>
      </div>

      {/* Panel 2 — Monte Carlo sandbox */}
      <div style={{ ...card, marginTop: 18, padding: '22px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Monte Carlo — what could markets do to your money?</h3>
          <Info text={INFO.monteCarlo} />
          <span style={{ fontSize: 12.5, color: 'var(--fg-2)' }}><span className="num">{SCENARIO.nPaths.toLocaleString()}</span> paths · {SCENARIO.horizon} yrs</span>
        </div>
        <Presets />
        <div style={{ marginTop: 16 }}><Inputs /></div>
        <div style={{ marginTop: 18 }}><ScenarioStats cols={4} /></div>
        <div style={{ marginTop: 18, padding: '6px 4px 0' }}><FanChart w={1000} h={220} /></div>
        <div style={{ display: 'flex', gap: 18, marginTop: 8, fontSize: 12, color: 'var(--fg-2)', flexWrap: 'wrap' }}>
          <span><span style={{ color: 'var(--teal)' }}>——</span> typical (p50)</span>
          <span><span style={{ color: 'var(--teal)', opacity: 0.4 }}>▩</span> p10–p90 range</span>
          <span style={{ color: 'var(--red)' }}>{SCENARIO.pathsRunOut}% of paths run out</span>
        </div>
        <div style={{ marginTop: 18 }}><RetirementPointer /></div>
        <div style={{ marginTop: 14 }}><AiHint /></div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
