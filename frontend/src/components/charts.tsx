import { useState } from "react";
import { useWidth } from "../lib/chain";
import { compact as compact0, int } from "../lib/format";

/** "75.00 L" -> "75 L", "1.00 Cr" -> "1 Cr" for axis ticks. */
const compact = (n: number) => compact0(n).replace(/\.0+(?= |$)/, "");

export interface BarRow {
  strike: number;
  ce: number | null;
  pe: number | null;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/** Top corners rounded, bottom flat (or the reverse for a bar that points down). */
function barPath(x: number, y0: number, w: number, h: number, up: boolean, r = 3): string {
  const rr = Math.min(r, w / 2, Math.abs(h));
  if (h === 0) return "";
  if (up) return `M${x},${y0} v${-(h - rr)} a${rr},${rr} 0 0 1 ${rr},${-rr} h${w - 2 * rr} a${rr},${rr} 0 0 1 ${rr},${rr} v${h - rr} z`;
  return `M${x},${y0} v${h - rr} a${rr},${rr} 0 0 0 ${rr},${rr} h${w - 2 * rr} a${rr},${rr} 0 0 0 ${rr},${-rr} v${-(h - rr)} z`;
}

export function Legend({ items }: { items: { name: string; color: string }[] }) {
  return (
    <div className="legend" aria-hidden="false">
      {items.map((i) => (
        <span key={i.name}><i style={{ background: i.color }} />{i.name}</span>
      ))}
    </div>
  );
}

/** Calls and puts side by side for every strike. `diverging` lets bars go below zero (for OI change). */
export function PairedBars({ rows, spot, maxPain, diverging = false, height = 290, label }: { rows: BarRow[]; spot: number | null; maxPain: number | null; diverging?: boolean; height?: number; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = rows.length;
  const L = 50, R = 8, T = 10, B = 28;
  const pw = Math.max(0, width - L - R), ph = height - T - B;
  const vals = rows.flatMap((r) => [r.ce ?? 0, r.pe ?? 0]);
  const hi = niceMax(Math.max(0, ...vals.map((v) => (diverging ? Math.abs(v) : v))));
  const lo = diverging ? -niceMax(Math.max(0, ...vals.map((v) => Math.abs(v)))) : 0;
  const y = (v: number) => T + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const band = n ? pw / n : 0;
  const bw = Math.max(2, Math.min(18, band * 0.38));
  const cx = (i: number) => L + (i + 0.5) * band;
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);
  const step = n > 1 ? rows[1].strike - rows[0].strike : 1;
  const xOf = (strike: number) => cx((strike - (rows[0]?.strike ?? 0)) / step);
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / 54))));
  const zero = y(0);
  const inRange = (v: number | null) => v != null && n > 0 && v >= rows[0].strike - step / 2 && v <= rows[n - 1].strike + step / 2;
  const h = hover != null ? rows[hover] : null;

  return (
    <div className="chart" ref={ref} role="img" aria-label={label}>
      {width > 0 && (
        <svg width={width} height={height} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGElement).getBoundingClientRect();
            const i = Math.floor((e.clientX - r.left - L) / (band || 1));
            setHover(i >= 0 && i < n ? i : null);
          }}>
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={L} x2={width - R} y1={y(t)} y2={y(t)} />
              <text x={L - 8} y={y(t) + 4} textAnchor="end">{t < 0 ? "−" : ""}{compact(Math.abs(t))}</text>
            </g>
          ))}
          <line className="axis" x1={L} x2={width - R} y1={zero} y2={zero} />
          {h && <rect x={cx(hover!) - band / 2} y={T} width={band} height={ph} fill="var(--text)" opacity={0.06} />}
          {rows.map((r, i) => {
            const draw = (v: number | null, off: number, color: string) => {
              if (!v) return null;
              const top = y(v), h0 = Math.abs(top - zero);
              return <path d={barPath(cx(i) + off, zero, bw, h0, v > 0)} transform={v > 0 ? undefined : undefined} fill={color} />;
            };
            return (
              <g key={r.strike}>
                {draw(r.ce, -bw - 1, "var(--ce)")}
                {draw(r.pe, 1, "var(--pe)")}
                {i % every === 0 && <text x={cx(i)} y={height - 9} textAnchor="middle">{int(r.strike)}</text>}
              </g>
            );
          })}
          {spot != null && inRange(spot) && (
            <g>
              <line x1={xOf(spot)} x2={xOf(spot)} y1={T} y2={T + ph} stroke="var(--text)" strokeWidth={1.2} strokeDasharray="4 3" opacity={0.75} />
              <text x={xOf(spot)} y={T + 9} textAnchor={xOf(spot) > width - 90 ? "end" : "start"} dx={xOf(spot) > width - 90 ? -5 : 5} style={{ fill: "var(--text)" }}>Spot {int(Math.round(spot))}</text>
            </g>
          )}
          {maxPain != null && inRange(maxPain) && (
            <g>
              <path d={`M${xOf(maxPain) - 5},${T + ph + 1} l5,-8 l5,8 z`} fill="var(--accent)" />
              <line x1={xOf(maxPain)} x2={xOf(maxPain)} y1={T + 16} y2={T + ph} stroke="var(--accent)" strokeWidth={1} strokeDasharray="2 4" opacity={0.7} />
              <text x={xOf(maxPain)} y={T + 24} textAnchor={xOf(maxPain) > width - 90 ? "end" : "start"} dx={xOf(maxPain) > width - 90 ? -5 : 5} style={{ fill: "var(--accent)" }}>Max pain {int(maxPain)}</text>
            </g>
          )}
        </svg>
      )}
      {h && (
        <div className="tip" style={{ left: Math.min(Math.max(8, cx(hover!) - 75), Math.max(8, width - 170)), top: 6 }}>
          <b>{int(h.strike)}</b>
          <div><span><i style={{ background: "var(--ce)" }} />Calls</span><span>{h.ce == null ? "–" : (diverging && h.ce > 0 ? "+" : "") + int(h.ce)}</span></div>
          <div><span><i style={{ background: "var(--pe)" }} />Puts</span><span>{h.pe == null ? "–" : (diverging && h.pe > 0 ? "+" : "") + int(h.pe)}</span></div>
        </div>
      )}
    </div>
  );
}

export interface Series {
  name: string;
  color: string;
  values: (number | null)[];
  fmt: (v: number) => string;
}

export function LineChart({ times, series, height = 230, label, yFmt }: { times: number[]; series: Series[]; height?: number; label: string; yFmt: (v: number) => string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const L = 54, R = 12, T = 12, B = 26;
  const pw = Math.max(0, width - L - R), ph = height - T - B;
  const all = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!isFinite(lo)) return null;
  const pad = (hi - lo || Math.abs(hi) * 0.02 || 1) * 0.15;
  lo -= pad;
  hi += pad;
  const t0 = times[0], t1 = times[times.length - 1] || t0 + 1;
  const x = (t: number) => L + ((t - t0) / (t1 - t0 || 1)) * pw;
  const y = (v: number) => T + ph - ((v - lo) / (hi - lo || 1)) * ph;
  const ticks = Array.from({ length: 4 }, (_, i) => lo + ((hi - lo) * i) / 3);
  const hm = (t: number) => new Date(t * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const xt = Array.from({ length: Math.min(5, times.length) }, (_, i) => times[Math.round((i * (times.length - 1)) / Math.max(1, Math.min(5, times.length) - 1))]);

  return (
    <div className="chart" ref={ref} role="img" aria-label={label}>
      {width > 0 && (
        <svg width={width} height={height} onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGElement).getBoundingClientRect();
            const px_ = e.clientX - r.left;
            let best = 0, d = Infinity;
            times.forEach((t, i) => { const dd = Math.abs(x(t) - px_); if (dd < d) { d = dd; best = i; } });
            setHover(best);
          }}>
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={L} x2={width - R} y1={y(t)} y2={y(t)} />
              <text x={L - 8} y={y(t) + 4} textAnchor="end">{yFmt(t)}</text>
            </g>
          ))}
          {xt.map((t, i) => <text key={i} x={x(t)} y={height - 7} textAnchor={i === 0 ? "start" : i === xt.length - 1 ? "end" : "middle"}>{hm(t)}</text>)}
          {series.map((s) => {
            const pts = times.map((t, i) => (s.values[i] == null ? null : `${x(t).toFixed(1)},${y(s.values[i]!).toFixed(1)}`)).filter(Boolean);
            return (
              <g key={s.name}>
                <polyline points={pts.join(" ")} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                {times.length === 1 && s.values[0] != null && <circle cx={x(times[0])} cy={y(s.values[0]!)} r={4} fill={s.color} stroke="var(--panel)" strokeWidth={2} />}
              </g>
            );
          })}
          {hover != null && (
            <g>
              <line x1={x(times[hover])} x2={x(times[hover])} y1={T} y2={T + ph} stroke="var(--faint)" strokeDasharray="3 3" />
              {series.map((s) => s.values[hover] != null && <circle key={s.name} cx={x(times[hover])} cy={y(s.values[hover]!)} r={4} fill={s.color} stroke="var(--panel)" strokeWidth={2} />)}
            </g>
          )}
        </svg>
      )}
      {hover != null && (
        <div className="tip" style={{ left: Math.min(Math.max(8, x(times[hover]) - 75), Math.max(8, width - 190)), top: 4 }}>
          <b>{hm(times[hover])}</b>
          {series.map((s) => <div key={s.name}><span><i style={{ background: s.color }} />{s.name}</span><span>{s.values[hover] == null ? "–" : s.fmt(s.values[hover]!)}</span></div>)}
        </div>
      )}
    </div>
  );
}
