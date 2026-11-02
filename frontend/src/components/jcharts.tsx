import { useState } from "react";
import { useWidth } from "../lib/chain";
import { signedRupees } from "../lib/format";

const dayLabel = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });

function niceStep(span: number, n: number): number {
  const raw = span / n;
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}
function niceTicks(lo: number, hi: number, n = 4): number[] {
  const step = niceStep(hi - lo || 1, n);
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}
const axisRupee = (v: number) => (v === 0 ? "0" : (v < 0 ? "−" : "") + (Math.abs(v) >= 1000 ? `${+(Math.abs(v) / 1000).toFixed(1)}K` : String(Math.abs(v))));

/** Running total of closed-trade P&L, one point per trade. */
export function EquityChart({ points, height = 240 }: { points: { cum: number; pnl: number; day: string }[]; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const L = 54, R = 12, T = 12, B = 26;
  const data = [{ cum: 0, pnl: 0, day: "" }, ...points];
  const n = data.length;
  const lo0 = Math.min(0, ...data.map((d) => d.cum)), hi0 = Math.max(0, ...data.map((d) => d.cum));
  const pad = (hi0 - lo0 || 100) * 0.1;
  const lo = lo0 - pad, hi = hi0 + pad;
  const pw = Math.max(0, width - L - R), ph = height - T - B;
  const x = (i: number) => L + (n > 1 ? (i / (n - 1)) * pw : 0);
  const y = (v: number) => T + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const last = data[n - 1].cum;
  const labels = n > 1 ? [1, Math.ceil(n / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i) : [];
  return (
    <div className="chart" ref={ref} role="img" aria-label={`Cumulative profit and loss over ${n - 1} trades, ending at ${signedRupees(last)}`}>
      {width > 0 && (
        <svg width={width} height={height} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGElement).getBoundingClientRect();
            const i = Math.round(((e.clientX - r.left - L) / (pw || 1)) * (n - 1));
            setHover(i >= 1 && i < n ? i : null);
          }}>
          {niceTicks(lo, hi).map((t) => (
            <g key={t}><line className="grid" x1={L} x2={width - R} y1={y(t)} y2={y(t)} /><text x={L - 8} y={y(t) + 4} textAnchor="end">{axisRupee(t)}</text></g>
          ))}
          <line className="axis" x1={L} x2={width - R} y1={y(0)} y2={y(0)} />
          <polyline points={data.map((d, i) => `${x(i).toFixed(1)},${y(d.cum).toFixed(1)}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {labels.map((i) => <text key={i} x={x(i)} y={height - 7} textAnchor={i === labels[0] ? "start" : i === labels[labels.length - 1] ? "end" : "middle"}>{i === 0 ? "" : `Trade ${i}`}</text>)}
          {n > 1 && <circle cx={x(n - 1)} cy={y(last)} r={4} fill="var(--accent)" stroke="var(--panel)" strokeWidth={2} />}
          {hover != null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={T} y2={T + ph} stroke="var(--faint)" strokeDasharray="3 3" />
              <circle cx={x(hover)} cy={y(data[hover].cum)} r={4} fill="var(--accent)" stroke="var(--panel)" strokeWidth={2} />
            </g>
          )}
        </svg>
      )}
      {hover != null && (
        <div className="tip" style={{ left: Math.min(Math.max(8, x(hover) - 80), Math.max(8, width - 180)), top: 4 }}>
          <b>Trade {hover} · {dayLabel(data[hover].day)}</b>
          <div><span>This trade</span><span>{signedRupees(data[hover].pnl)}</span></div>
          <div><span>Running total</span><span>{signedRupees(data[hover].cum)}</span></div>
        </div>
      )}
    </div>
  );
}

/** One bar per day: green up for a profit, red down for a loss. */
export function DayBars({ rows, height = 240 }: { rows: { key: string; pnl: number; n: number }[]; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const L = 54, R = 8, T = 12, B = 26;
  const n = rows.length;
  const hi0 = Math.max(0, ...rows.map((r) => r.pnl)), lo0 = Math.min(0, ...rows.map((r) => r.pnl));
  const pad = (hi0 - lo0 || 100) * 0.08;
  const hi = hi0 + (hi0 > 0 ? pad : 0), lo = lo0 - (lo0 < 0 ? pad : 0);
  const pw = Math.max(0, width - L - R), ph = height - T - B;
  const band = n ? pw / n : 0;
  const bw = Math.max(3, Math.min(26, band * 0.66));
  const y = (v: number) => T + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const cx = (i: number) => L + (i + 0.5) * band;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / 56))));
  return (
    <div className="chart" ref={ref} role="img" aria-label={`Profit and loss for each of ${n} trading days`}>
      {width > 0 && (
        <svg width={width} height={height} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGElement).getBoundingClientRect();
            const i = Math.floor((e.clientX - r.left - L) / (band || 1));
            setHover(i >= 0 && i < n ? i : null);
          }}>
          {niceTicks(lo, hi).map((t) => (
            <g key={t}><line className="grid" x1={L} x2={width - R} y1={y(t)} y2={y(t)} /><text x={L - 8} y={y(t) + 4} textAnchor="end">{axisRupee(t)}</text></g>
          ))}
          <line className="axis" x1={L} x2={width - R} y1={y(0)} y2={y(0)} />
          {hover != null && <rect x={cx(hover) - band / 2} y={T} width={band} height={ph} fill="var(--text)" opacity={0.06} />}
          {rows.map((r, i) => {
            const top = Math.min(y(r.pnl), y(0)), h = Math.max(1, Math.abs(y(r.pnl) - y(0)));
            return (
              <g key={r.key}>
                <rect x={cx(i) - bw / 2} y={top} width={bw} height={h} rx={2} fill={r.pnl >= 0 ? "var(--up)" : "var(--down)"} />
                {i % every === 0 && <text x={cx(i)} y={height - 9} textAnchor="middle">{dayLabel(r.key)}</text>}
              </g>
            );
          })}
        </svg>
      )}
      {hover != null && (
        <div className="tip" style={{ left: Math.min(Math.max(8, cx(hover) - 75), Math.max(8, width - 170)), top: 4 }}>
          <b>{dayLabel(rows[hover].key)}</b>
          <div><span>P&amp;L</span><span>{signedRupees(rows[hover].pnl)}</span></div>
          <div><span>Trades</span><span>{rows[hover].n}</span></div>
        </div>
      )}
    </div>
  );
}

/** FII and DII net buying per day: two bars a day (up = net bought, down = net sold). Colours identify who, direction is the sign. */
export function FlowBars({ rows, height = 260 }: { rows: { day: string; fii: number; dii: number }[]; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const L = 58, R = 8, T = 12, B = 26;
  const n = rows.length;
  const all = rows.flatMap((r) => [r.fii, r.dii]);
  const hi0 = Math.max(0, ...all), lo0 = Math.min(0, ...all);
  const pad = (hi0 - lo0 || 100) * 0.08;
  const hi = hi0 + (hi0 > 0 ? pad : 0), lo = lo0 - (lo0 < 0 ? pad : 0);
  const pw = Math.max(0, width - L - R), ph = height - T - B;
  const band = n ? pw / n : 0;
  const bw = Math.max(2, Math.min(14, band * 0.36));
  const y = (v: number) => T + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const cx = (i: number) => L + (i + 0.5) * band;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / 56))));
  const bar = (i: number, v: number, off: number, color: string) => {
    const top = Math.min(y(v), y(0)), h = Math.max(1, Math.abs(y(v) - y(0)));
    return <rect x={cx(i) + off} y={top} width={bw} height={h} rx={2} fill={color} />;
  };
  return (
    <div className="chart" ref={ref} role="img" aria-label={`FII and DII net buying or selling for each of ${n} days, in rupees crore`}>
      {width > 0 && (
        <svg width={width} height={height} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGElement).getBoundingClientRect();
            const i = Math.floor((e.clientX - r.left - L) / (band || 1));
            setHover(i >= 0 && i < n ? i : null);
          }}>
          {niceTicks(lo, hi).map((t) => (
            <g key={t}><line className="grid" x1={L} x2={width - R} y1={y(t)} y2={y(t)} /><text x={L - 8} y={y(t) + 4} textAnchor="end">{axisRupee(t)}</text></g>
          ))}
          <line className="axis" x1={L} x2={width - R} y1={y(0)} y2={y(0)} />
          {hover != null && <rect x={cx(hover) - band / 2} y={T} width={band} height={ph} fill="var(--text)" opacity={0.06} />}
          {rows.map((r, i) => (
            <g key={r.day}>
              {bar(i, r.fii, -bw - 1, "var(--accent)")}
              {bar(i, r.dii, 1, "var(--buy)")}
              {i % every === 0 && <text x={cx(i)} y={height - 9} textAnchor="middle">{dayLabel(r.day)}</text>}
            </g>
          ))}
        </svg>
      )}
      {hover != null && (
        <div className="tip" style={{ left: Math.min(Math.max(8, cx(hover) - 80), Math.max(8, width - 190)), top: 4 }}>
          <b>{dayLabel(rows[hover].day)}</b>
          <div><span>FII net</span><span>{signedRupees(rows[hover].fii)} Cr</span></div>
          <div><span>DII net</span><span>{signedRupees(rows[hover].dii)} Cr</span></div>
        </div>
      )}
    </div>
  );
}
