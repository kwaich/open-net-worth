// retirement-app.jsx — Retirement tab redesign: verdict-led, minimal text, info icons, A/B compare.
const { useState } = React;
const { FIRE, READINESS, SCENARIOS } = window.RET;

/* ---------- format ---------- */
const fmt = (n) => 'S$' + Math.round(Math.abs(n)).toLocaleString('en-US');
function fk(n) {
  const a = Math.abs(n);
  if (a >= 1e6) return 'S$' + (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (a >= 1e3) return 'S$' + Math.round(n / 1e3) + 'k';
  return 'S$' + Math.round(n);
}

/* ---------- shared ui ---------- */
const card = { background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 16 };
const eyebrow = { fontSize: 10.5, letterSpacing: '0.13em', textTransform: 'uppercase', color: 'var(--fg-2)', fontWeight: 600 };

function Pill({ tone, children }) {
  const map = {
    pos: ['var(--teal)', 'var(--teal-soft)'], warn: ['var(--gold)', 'var(--gold-soft)'],
    bad: ['var(--red)', 'var(--red-soft)'], info: ['var(--blue)', 'var(--blue-soft)'],
  }[tone] || ['var(--fg-1)', 'var(--surface-2)'];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 99, fontSize: 12.5, fontWeight: 600, color: map[0], background: map[1], whiteSpace: 'nowrap' }}>
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
  fireNumber: 'Your FIRE number = the savings that can fund your lifestyle indefinitely (annual spending ÷ a safe withdrawal rate, here 4%).',
  yearsToFI: 'Years until your projected net worth reaches your FIRE number, at your assumed return and saving rate.',
  monteCarlo: 'A Monte Carlo simulation runs thousands of random market histories — including the retirement withdrawal phase — so you see how often your money survives, not one optimistic line.',
  moneyLasts: 'Share of simulations where your money lasted through retirement without hitting zero. Above ~80% is comfortable; this is well below.',
  p50: 'The middle path — half the simulations ended higher, half lower.',
  withdrawalRate: 'The share of your savings you withdraw each year in retirement. The classic "4% rule" is calibrated for ~30 years — retiring early stretches it over far longer, so a lower rate is safer.',
};

function SubTabs({ active }) {
  return (
    <div style={{ display: 'flex', gap: 26, borderBottom: '1px solid var(--line)', marginBottom: 24 }}>
      {['Budget', 'Projections', 'Retirement'].map(t => (
        <div key={t} style={{ paddingBottom: 12, fontSize: 14.5, fontWeight: active === t ? 600 : 500, color: active === t ? 'var(--fg-0)' : 'var(--fg-2)', borderBottom: active === t ? '2px solid var(--blue)' : '2px solid transparent', marginBottom: -1 }}>{t}</div>
      ))}
    </div>
  );
}

function ProgressBar({ pct, tone }) {
  return (
    <span style={{ display: 'block', height: 6, borderRadius: 99, background: 'var(--surface-2)', overflow: 'hidden' }}>
      <span style={{ display: 'block', height: '100%', width: `${Math.min(100, pct)}%`, background: tone }} />
    </span>
  );
}

/* ---------- FIRE trajectory chart (single line + target) ---------- */
function TrajectoryChart({ w = 1000, h = 200 }) {
  const { ages, nw, target } = FIRE;
  const padL = 4, padR = 4, padT = 14, padB = 24;
  const iW = w - padL - padR, iH = h - padT - padB;
  const yMax = Math.max(target, ...nw) * 1.08, yMin = 0;
  const X = (i) => padL + (i / (ages.length - 1)) * iW;
  const Y = (v) => padT + iH * (1 - (v - yMin) / (yMax - yMin));
  const line = nw.map((v, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ');
  const area = `${line} L ${X(nw.length - 1).toFixed(1)} ${Y(0)} L ${X(0)} ${Y(0)} Z`;
  // crossing point of target
  let crossI = nw.findIndex(v => v >= target);
  const ticks = [0, 0.5, 1].map(f => yMin + f * (yMax - yMin));
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: 'block' }}>
      {ticks.map((v, i) => (
        <g key={i}>
          <line x1={padL} x2={w - padR} y1={Y(v)} y2={Y(v)} stroke="var(--line)" strokeWidth="1" />
          <text x={padL + 2} y={Y(v) - 4} fill="var(--fg-2)" fontSize="10.5" fontFamily="inherit">{fk(v)}</text>
        </g>
      ))}
      {/* target line */}
      <line x1={padL} x2={w - padR} y1={Y(target)} y2={Y(target)} stroke="var(--gold)" strokeWidth="1.2" strokeDasharray="5 4" opacity="0.8" />
      <text x={w - padR} y={Y(target) - 5} fill="var(--gold)" fontSize="10.5" textAnchor="end" fontFamily="inherit">FIRE target {fk(target)}</text>
      <path d={area} fill="var(--teal)" opacity="0.08" />
      <path d={line} fill="none" stroke="var(--teal)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {crossI > 0 && <circle cx={X(crossI)} cy={Y(nw[crossI])} r="3.5" fill="var(--gold)" />}
      {ages.map((a, i) => (i % 4 === 0 ? <text key={i} x={X(i)} y={h - 5} fill="var(--fg-2)" fontSize="10.5" textAnchor={i === 0 ? 'start' : 'middle'} fontFamily="inherit">age {a}</text> : null))}
    </svg>
  );
}

/* ---------- readiness fan chart ---------- */
function FanChart({ w = 1000, h = 210 }) {
  const { ages, p10, p50, p90 } = READINESS;
  const padL = 4, padR = 4, padT = 14, padB = 24;
  const iW = w - padL - padR, iH = h - padT - padB;
  const yMax = Math.max(...p90) * 1.05, yMin = 0;
  const X = (i) => padL + (i / (ages.length - 1)) * iW;
  const Y = (v) => padT + iH * (1 - (v - yMin) / (yMax - yMin));
  const lineP = p50.map((v, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ');
  const band = `${p90.map((v, i) => `${i ? 'L' : 'M'} ${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')} ${p10.slice().reverse().map((v, i) => `L ${X(p10.length - 1 - i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')} Z`;
  const dryIdx = p50.findIndex((v, i) => i > 18 && v <= 0);
  const ticks = [0, 0.5, 1].map(f => yMin + f * (yMax - yMin));
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: 'block' }}>
      {ticks.map((v, i) => (
        <g key={i}>
          <line x1={padL} x2={w - padR} y1={Y(v)} y2={Y(v)} stroke="var(--line)" strokeWidth="1" />
          <text x={padL + 2} y={Y(v) - 4} fill="var(--fg-2)" fontSize="10.5" fontFamily="inherit">{fk(v)}</text>
        </g>
      ))}
      <path d={band} fill="var(--teal)" opacity="0.10" />
      <path d={lineP} fill="none" stroke="var(--teal)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
      {dryIdx > 0 && (
        <g>
          <line x1={X(dryIdx)} x2={X(dryIdx)} y1={padT} y2={padT + iH} stroke="var(--red)" strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
          <text x={X(dryIdx) + 5} y={padT + 12} fill="var(--red)" fontSize="10.5" fontFamily="inherit">money runs out ~age {READINESS.ages[dryIdx]}</text>
        </g>
      )}
      {ages.map((a, i) => (i % 8 === 0 ? <text key={i} x={X(i)} y={h - 5} fill="var(--fg-2)" fontSize="10.5" textAnchor={i === 0 ? 'start' : 'middle'} fontFamily="inherit">age {a}</text> : null))}
    </svg>
  );
}

/* ---------- FIRE settings (moved here from Profile) ---------- */
function FireSettings() {
  const wi = READINESS.whatIf;
  const fields = [
    ['Annual spend in retirement', fmt(wi.annualSpend)],
    ['Safe withdrawal rate', wi.withdrawalRate + '%', INFO.withdrawalRate],
    ['Expected return', wi.expectedReturn + '%'],
    ['Return volatility', wi.volatility + '%'],
    ['Target retirement age', wi.retirementAge],
    ['Monthly saving', fmt(wi.monthlySaving)],
  ];
  return (
    <div style={{ marginTop: 20, paddingTop: 18, borderTop: '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={eyebrow}>FIRE settings</div>
          <span style={{ fontSize: 11.5, color: 'var(--fg-3)' }}>now editable here — moved from Profile</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={{ padding: '7px 12px', borderRadius: 8, border: '1px solid var(--line-strong)', background: 'transparent', color: 'var(--fg-1)', cursor: 'pointer', font: 'inherit', fontSize: 12.5 }}>Reset</button>
          <button style={{ padding: '7px 14px', borderRadius: 8, border: 'none', background: 'var(--blue)', color: '#fff', cursor: 'pointer', font: 'inherit', fontSize: 12.5, fontWeight: 600 }}>Save settings</button>
        </div>
      </div>
      <p style={{ margin: '0 0 14px', fontSize: 12.5, color: 'var(--fg-2)' }}>
        FIRE number = annual spend ÷ withdrawal rate = <span className="num" style={{ color: 'var(--fg-1)' }}>{fmt(wi.annualSpend)} ÷ {wi.withdrawalRate}%</span> = <span className="num" style={{ color: 'var(--fg-0)', fontWeight: 600 }}>{fk(FIRE.number)}</span>
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
        {fields.map(([l, v, info]) => (
          <div key={l}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 5 }}>
              <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{l}</span>
              {info && <Info text={info} />}
            </div>
            <div className="num" style={{ padding: '9px 12px', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 9, fontSize: 14, color: 'var(--fg-0)' }}>{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- quick what-ifs (temporary; don't change saved settings) ---------- */
function QuickWhatIfs() {
  const [active, setActive] = useState(null);
  const chips = ['Retire 5 yrs earlier', 'Bad sequence of returns', 'Save +S$1k/mo', '2008-style crash'];
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      {chips.map(c => (
        <button key={c} onClick={() => setActive(a => a === c ? null : c)} style={{ padding: '6px 13px', borderRadius: 99, border: `1px solid ${active === c ? 'var(--blue)' : 'var(--line-strong)'}`, background: active === c ? 'var(--blue-soft)' : 'transparent', color: active === c ? 'var(--blue)' : 'var(--fg-1)', cursor: 'pointer', font: 'inherit', fontSize: 12.5 }}>{c}</button>
      ))}
      <span style={{ fontSize: 11.5, color: 'var(--fg-3)' }}>preview only — doesn't change your saved plan</span>
    </div>
  );
}

/* ---------- saved scenarios A/B compare ---------- */
function SavedCompare() {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 13.5, fontWeight: 600 }}>Compare saved plans</span>
        <button style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 11px', borderRadius: 8, border: '1px dashed var(--line-strong)', background: 'transparent', color: 'var(--fg-1)', cursor: 'pointer', font: 'inherit', fontSize: 12 }}>+ Save current as plan</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {SCENARIOS.map(s => (
          <div key={s.id} style={{ padding: '16px 18px', borderRadius: 14, background: 'var(--bg-2)', border: `1px solid ${s.tone === 'pos' ? 'var(--teal)' : 'var(--line)'}` }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ fontSize: 13.5, fontWeight: 600 }}>{s.name}</span>
              <Pill tone={s.tone}>{s.verdict}</Pill>
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 12 }}>
              <span className="num" style={{ fontSize: 26, fontWeight: 600, color: s.tone === 'pos' ? 'var(--teal)' : 'var(--red)' }}>{s.moneyLasts}%</span>
              <span style={{ fontSize: 12.5, color: 'var(--fg-2)' }}>money lasts</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-2)', marginTop: 6 }}>Retire at age <span className="num" style={{ color: 'var(--fg-1)' }}>{s.retireAge}</span></div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ===================================================================== */
function App() {
  const f = FIRE, r = READINESS;
  return (
    <div style={{ maxWidth: 1120, margin: '0 auto', padding: '30px 40px 70px' }}>
      <SubTabs active="Retirement" />
      <div style={eyebrow}>Retirement</div>

      {/* VERDICT HERO */}
      <div style={{ ...card, marginTop: 12, padding: '24px 28px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <Pill tone="bad">At risk</Pill>
          <span style={{ fontSize: 13.5, color: 'var(--fg-2)' }}>Can you retire at {f.targetAge}, and will the money last?</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 40, marginTop: 16, flexWrap: 'wrap' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span className="num" style={{ fontSize: 44, fontWeight: 700, color: 'var(--red)', letterSpacing: '-0.02em' }}>{r.moneyLasts}%</span>
              <span style={{ fontSize: 14, color: 'var(--fg-1)' }}>chance your money lasts</span>
              <Info text={INFO.moneyLasts} />
            </div>
          </div>
          <HeroStat label="Projected retirement" value={`age ${f.projectedAge}`} sub={`target ${f.targetAge}`} tone="var(--gold)" />
          <HeroStat label="To hit target age" value={`+${fmt(f.monthlyGap)}`} sub="more / month" tone="var(--gold)" />
        </div>
      </div>

      {/* FIRE TRAJECTORY */}
      <div style={{ ...card, marginTop: 18, padding: '22px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Your path to FIRE</h3>
          <Info text={INFO.fireNumber} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginBottom: 18 }}>
          <FireStat label="FIRE number" value={fk(f.number)} />
          <div style={{ padding: '14px 16px', ...card, borderRadius: 14, background: 'var(--bg-2)' }}>
            <div style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>Progress</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, margin: '6px 0 9px' }}>
              <span className="num" style={{ fontSize: 21, fontWeight: 600 }}>{f.progress}%</span>
            </div>
            <ProgressBar pct={f.progress} tone="var(--gold)" />
          </div>
          <FireStat label="Years to FI" value={f.yearsToFI} info={INFO.yearsToFI} />
        </div>
        <TrajectoryChart w={1000} h={196} />
        <FireSettings />
      </div>

      {/* READINESS — MONTE CARLO */}
      <div style={{ ...card, marginTop: 18, padding: '22px 24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Monte Carlo — will it survive a real market?</h3>
          <Info text={INFO.monteCarlo} />
          <span style={{ fontSize: 12.5, color: 'var(--fg-2)' }}><span className="num">{r.nPaths.toLocaleString()}</span> paths · withdrawal phase on</span>
        </div>

        {/* income streams */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
          {r.incomeStreams.map(s => (
            <span key={s.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderRadius: 9, background: 'var(--bg-2)', border: '1px solid var(--line)', fontSize: 12.5 }}>
              <span style={{ color: 'var(--fg-1)', fontWeight: 600 }}>{s.name}</span>
              <span className="num" style={{ color: 'var(--teal)' }}>+{fmt(s.monthly)}/mo</span>
              <span style={{ color: 'var(--fg-3)' }}>from {s.from}</span>
            </span>
          ))}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 9, border: '1px dashed var(--line-strong)', color: 'var(--fg-2)', fontSize: 12.5, cursor: 'pointer' }}>+ Add income</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 16 }}>
          <ReadStat label="Typical" value={fk(r.medianEnding)} tone="var(--fg-0)" info={INFO.p50} />
          <ReadStat label="If lucky" value={fk(r.p90Ending)} tone="var(--teal)" />
          <ReadStat label="Money lasts" value={r.moneyLasts + '%'} tone="var(--red)" info={INFO.moneyLasts} />
        </div>

        <FanChart w={1000} h={200} />
        <div style={{ display: 'flex', gap: 18, marginTop: 8, fontSize: 12, color: 'var(--fg-2)', flexWrap: 'wrap' }}>
          <span><span style={{ color: 'var(--teal)' }}>——</span> typical (p50)</span>
          <span><span style={{ color: 'var(--teal)', opacity: 0.4 }}>▩</span> p10–p90 range</span>
          <span style={{ color: 'var(--red)' }}>{r.pathsRunOut}% of paths run out</span>
        </div>

        {/* quick what-ifs */}
        <div style={{ marginTop: 22, paddingTop: 20, borderTop: '1px solid var(--line)' }}>
          <div style={{ ...eyebrow, marginBottom: 12 }}>Quick what-ifs</div>
          <QuickWhatIfs />
        </div>

        {/* A/B saved compare */}
        <div style={{ marginTop: 22, paddingTop: 20, borderTop: '1px solid var(--line)' }}>
          <SavedCompare />
        </div>
      </div>

      <div style={{ marginTop: 18, fontSize: 12.5, color: 'var(--fg-2)', fontStyle: 'italic' }}>
        Ask Lucius: "what if I retire at 45?", "how much more must I save to hit 80% confidence?"
      </div>
    </div>
  );
}

function HeroStat({ label, value, sub, tone }) {
  return (
    <div>
      <div style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 600, color: tone || 'var(--fg-0)', marginTop: 4 }}>{value}</div>
      <div style={{ fontSize: 11.5, color: 'var(--fg-3)', marginTop: 2 }}>{sub}</div>
    </div>
  );
}
function FireStat({ label, value, info }) {
  return (
    <div style={{ padding: '14px 16px', ...card, borderRadius: 14, background: 'var(--bg-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{label}</span>
        {info && <Info text={info} />}
      </div>
      <div className="num" style={{ fontSize: 21, fontWeight: 600, marginTop: 8 }}>{value}</div>
    </div>
  );
}
function ReadStat({ label, value, tone, info }) {
  return (
    <div style={{ padding: '14px 16px', ...card, borderRadius: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{label}</span>
        {info && <Info text={info} />}
      </div>
      <div className="num" style={{ fontSize: 21, fontWeight: 600, color: tone, marginTop: 6 }}>{value}</div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
