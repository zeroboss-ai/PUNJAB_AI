import { useMemo, useState } from "react";
import { ChainControls } from "../components/ChainControls";
import { Legend, LineChart, PairedBars, type BarRow } from "../components/charts";
import { Empty } from "../components/ui";
import { openTicket, useTitle } from "../lib/app";
import { useChainSel, type ChainData, type ChainLeg, type OiPoint } from "../lib/chain";
import { compact, dash, int, px } from "../lib/format";
import { useQuote } from "../lib/quotes";
import { usePoll } from "../lib/store";
import { useChain } from "./Chain";

const CE = "var(--ce)", PE = "var(--pe)";

function Tile({ k, v, sub, onClick }: { k: string; v: React.ReactNode; sub?: React.ReactNode; onClick?: () => void }) {
  return (
    <div className={`stat ${onClick ? "clickable" : ""}`} onClick={onClick} style={onClick ? { cursor: "pointer" } : undefined}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
      {sub && <span className="hint">{sub}</span>}
    </div>
  );
}

function Card({
  title,
  sub,
  legend,
  legendItems,
  children,
  table,
  footer,
  id,
}: {
  title: string;
  sub?: string;
  legend?: boolean;
  legendItems?: { name: string; color: string }[];
  children: React.ReactNode;
  table?: React.ReactNode;
  footer?: React.ReactNode;
  id?: string;
}) {
  const [asTable, setAsTable] = useState(false);
  const items = legendItems ?? [{ name: "Puts", color: PE }, { name: "Calls", color: CE }];
  return (
    <section className="panel" id={id}>
      <div className="panel-h">
        <span>{title}</span>
        {sub && <span className="sub">{sub}</span>}
        <span className="grow" />
        {table && (
          <button className="btn ghost sm" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>
            {asTable ? "Show chart" : "Show as table"}
          </button>
        )}
      </div>
      <div className="panel-b">
        {(legend || legendItems) && (
          <div style={{ marginBottom: 8 }}>
            <Legend items={items} />
          </div>
        )}
        {asTable && table ? table : children}
        {footer && <div className="chart-foot">{footer}</div>}
      </div>
    </section>
  );
}

function StraddleTable({ points }: { points: OiPoint[] }) {
  const hm = (t: number) =>
    new Date(t * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  return (
    <div className="tscroll" style={{ maxHeight: 290, overflowY: "auto" }}>
      <table className="t">
        <thead>
          <tr>
            <th>Time</th>
            <th className="num">Spot</th>
            <th className="num">ATM Call</th>
            <th className="num">ATM Put</th>
            <th className="num">Straddle (CE+PE)</th>
          </tr>
        </thead>
        <tbody>
          {points.slice().reverse().map((p) => (
            <tr key={p.ts}>
              <td>{hm(p.ts)}</td>
              <td className="num">{p.spot != null ? px(p.spot) : dash}</td>
              <td className="num" style={{ color: CE }}>{p.atm_ce != null ? px(p.atm_ce) : dash}</td>
              <td className="num" style={{ color: PE }}>{p.atm_pe != null ? px(p.atm_pe) : dash}</td>
              <td className="num">
                <b style={{ fontWeight: 650, color: "var(--accent)" }}>{p.straddle != null ? px(p.straddle) : dash}</b>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrikeTable({ rows, signedVals }: { rows: BarRow[]; signedVals?: boolean }) {
  const f = (v: number | null) => (v == null ? dash : (signedVals && v > 0 ? "+" : "") + int(v));
  return (
    <div className="tscroll" style={{ maxHeight: 290, overflowY: "auto" }}>
      <table className="t">
        <thead><tr><th className="num">Strike</th><th className="num">Puts</th><th className="num">Calls</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.strike}><td className="num">{int(r.strike)}</td><td className="num">{f(r.pe)}</td><td className="num">{f(r.ce)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function AddTable({ title, rows, data, kind }: { title: string; rows: { strike: number; leg: ChainLeg }[]; data: ChainData; kind: "CE" | "PE" }) {
  return (
    <section className="panel">
      <div className="panel-h"><span>{title}</span></div>
      {rows.length === 0 ? <Empty title="No additions yet" /> : (
        <div className="tscroll">
          <table className="t">
            <thead><tr><th className="num">Strike</th><th className="num">OI added</th><th className="num">Price</th><th>Reading</th><th /></tr></thead>
            <tbody>
              {rows.map(({ strike, leg }) => (
                <tr key={strike}>
                  <td className="num"><b style={{ fontWeight: 600 }}>{int(strike)}</b></td>
                  <td className="num">+{compact(leg.oi_chg)} <span className="muted">({leg.oi_chg_pct}%)</span></td>
                  <td className="num">{px(leg.ltp)}</td>
                  <td className="muted">{leg.buildup ?? dash}</td>
                  <td><div className="acts hover-acts">
                    <button className="bs b" aria-label={`Buy ${strike} ${kind}`} onClick={() => openTicket({ key: leg.key, label: `${data.underlying} ${strike} ${kind}`, side: "B", lot: data.lot, tick: 0.05 })}>B</button>
                    <button className="bs s" aria-label={`Sell ${strike} ${kind}`} onClick={() => openTicket({ key: leg.key, label: `${data.underlying} ${strike} ${kind}`, side: "S", lot: data.lot, tick: 0.05 })}>S</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function OIAnalysis() {
  useTitle("OI analysis");
  const sel = useChainSel();
  const { data, raw, error } = useChain(0);
  const spotQ = useQuote(data?.spot_key ?? undefined);
  const spot = spotQ?.l ?? data?.spot ?? null;
  const series = usePoll<{ ok: boolean; points: OiPoint[] }>(data ? `/chain/series?underlying=${data.underlying}&expiry=${data.expiry}` : null, 30000);

  const view = useMemo(() => {
    if (!data) return null;
    let rows = data.rows;
    if (sel.window && data.atm != null) {
      const i = rows.findIndex((r) => r.strike === data.atm);
      if (i >= 0) rows = rows.slice(Math.max(0, i - sel.window), i + sel.window + 1);
    }
    const oi: BarRow[] = rows.map((r) => ({ strike: r.strike, ce: r.ce?.oi ?? null, pe: r.pe?.oi ?? null }));
    const chg: BarRow[] = rows.map((r) => ({ strike: r.strike, ce: r.ce?.oi_chg ?? null, pe: r.pe?.oi_chg ?? null }));
    const adds = (kind: "ce" | "pe") =>
      data.rows.filter((r) => (r[kind]?.oi_chg ?? 0) > 0).sort((a, b) => b[kind]!.oi_chg! - a[kind]!.oi_chg!).slice(0, 5).map((r) => ({ strike: r.strike, leg: r[kind]! }));
    return { oi, chg, ceAdds: adds("ce"), peAdds: adds("pe") };
  }, [data, sel.window]);

  const s = data?.summary;
  const problem = raw && !raw.ok ? raw : null;
  const rawPts = (series.data?.points ?? []).filter((p) => p.pcr != null);
  const pts = useMemo(() => {
    const list = [...rawPts];
    if (data && s && s.pcr != null) {
      const nowS = Math.floor(Date.now() / 1000);
      const last = list[list.length - 1];
      if (!last || nowS - last.ts >= 30) {
        list.push({
          ts: nowS,
          spot: spot,
          pcr: s.pcr,
          ce_oi: s.ce_oi,
          pe_oi: s.pe_oi,
          max_pain: s.max_pain,
          straddle: s.straddle,
          atm_ce: s.atm_ce,
          atm_pe: s.atm_pe,
        });
      }
    }
    return list;
  }, [rawPts, data, s, spot]);

  const straddlePts = useMemo(() => {
    return pts.filter((p) => p.straddle != null && p.straddle > 0);
  }, [pts]);
  const hasLegs = straddlePts.some((p) => p.atm_ce != null || p.atm_pe != null);
  const dist = spot != null && s?.max_pain != null ? spot - s.max_pain : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>OI analysis</h1>
          <p>Where open interest sits across strikes and how it changed since yesterday.</p>
        </div>
        <ChainControls data={data} allowAll />
      </div>

      {problem && <div className="notice bad" style={{ marginBottom: 12 }} role="alert">{problem.reason === "not_connected" ? "Connect to Kotak first. Open interest is read from your broker." : problem.error ?? "Open interest could not be loaded."}</div>}
      {error && !raw && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}

      {!data || !s || !view ? (
        <section className="panel"><Empty title={problem ? "No data to show" : "Loading…"} /></section>
      ) : (
        <>
          <section className="panel chain-sum" style={{ marginBottom: 12 }}>
            <Tile k="PCR (put OI ÷ call OI)" v={s.pcr == null ? dash : s.pcr.toFixed(2)} sub={s.pcr_prev != null ? `Yesterday ${s.pcr_prev.toFixed(2)}` : undefined} />
            <Tile k="Max pain" v={s.max_pain == null ? dash : int(s.max_pain)} sub={dist != null ? `Spot is ${int(Math.abs(Math.round(dist)))} points ${dist >= 0 ? "above" : "below"}` : undefined} />
            <Tile k="Total put OI" v={compact(s.pe_oi)} sub={`${s.pe_oi_chg >= 0 ? "+" : "−"}${compact(Math.abs(s.pe_oi_chg))} since yesterday`} />
            <Tile k="Total call OI" v={compact(s.ce_oi)} sub={`${s.ce_oi_chg >= 0 ? "+" : "−"}${compact(Math.abs(s.ce_oi_chg))} since yesterday`} />
            <Tile k="Largest put OI" v={s.pe_top[0] ? int(s.pe_top[0].strike) : dash} sub={s.pe_top[1] ? `then ${s.pe_top.slice(1).map((t) => int(t.strike)).join(", ")}` : undefined} />
            <Tile k="Largest call OI" v={s.ce_top[0] ? int(s.ce_top[0].strike) : dash} sub={s.ce_top[1] ? `then ${s.ce_top.slice(1).map((t) => int(t.strike)).join(", ")}` : undefined} />
            <Tile
              k="ATM straddle"
              v={s.straddle == null ? dash : px(s.straddle)}
              sub={s.straddle_pct != null ? `${s.straddle_pct}% of spot · Tap for chart` : "Tap for chart"}
              onClick={() => document.getElementById("straddle-chart")?.scrollIntoView({ behavior: "smooth" })}
            />
          </section>

          <div className="charts">
            <Card
              title="Open interest by strike"
              legend
              table={<StrikeTable rows={view.oi} />}
              footer={
                <>
                  <div className="chart-foot-stat">
                    <span className="dot" style={{ background: PE }} />
                    <span className="lbl">Total Put OI:</span>
                    <span className="val">{compact(s.pe_oi)}</span>
                    <span className="sub">({int(s.pe_oi)})</span>
                  </div>
                  <div className="chart-foot-stat">
                    <span className="dot" style={{ background: CE }} />
                    <span className="lbl">Total Call OI:</span>
                    <span className="val">{compact(s.ce_oi)}</span>
                    <span className="sub">({int(s.ce_oi)})</span>
                  </div>
                  <div className="chart-foot-stat">
                    <span className="lbl">PCR:</span>
                    <span className="val">{s.pcr == null ? dash : s.pcr.toFixed(2)}</span>
                    <span className="sub">({s.pe_oi >= s.ce_oi ? `+${compact(s.pe_oi - s.ce_oi)} Puts lead` : `+${compact(s.ce_oi - s.pe_oi)} Calls lead`})</span>
                  </div>
                </>
              }
            >
              <PairedBars rows={view.oi} spot={spot} maxPain={s.max_pain} label="Put and call open interest at each strike" putFirst />
            </Card>
            <Card
              title="Change in OI since yesterday"
              legend
              table={<StrikeTable rows={view.chg} signedVals />}
              footer={
                <>
                  <div className="chart-foot-stat">
                    <span className="dot" style={{ background: PE }} />
                    <span className="lbl">Put OI Day Change:</span>
                    <span className={`val ${s.pe_oi_chg >= 0 ? "up" : "down"}`}>
                      {s.pe_oi_chg >= 0 ? "+" : "−"}{compact(Math.abs(s.pe_oi_chg))}
                    </span>
                    <span className="sub">({s.pe_oi_chg >= 0 ? "+" : ""}{int(s.pe_oi_chg)})</span>
                  </div>
                  <div className="chart-foot-stat">
                    <span className="dot" style={{ background: CE }} />
                    <span className="lbl">Call OI Day Change:</span>
                    <span className={`val ${s.ce_oi_chg >= 0 ? "up" : "down"}`}>
                      {s.ce_oi_chg >= 0 ? "+" : "−"}{compact(Math.abs(s.ce_oi_chg))}
                    </span>
                    <span className="sub">({s.ce_oi_chg >= 0 ? "+" : ""}{int(s.ce_oi_chg)})</span>
                  </div>
                  <div className="chart-foot-stat">
                    <span className="lbl">Day Shift:</span>
                    <span className={`val ${s.pe_oi_chg >= s.ce_oi_chg ? "up" : "down"}`}>
                      {s.pe_oi_chg >= s.ce_oi_chg ? `+${compact(s.pe_oi_chg - s.ce_oi_chg)} Puts` : `+${compact(s.ce_oi_chg - s.pe_oi_chg)} Calls`}
                    </span>
                    <span className="sub">({s.pe_oi_chg >= s.ce_oi_chg ? "Put addition lead" : "Call addition lead"})</span>
                  </div>
                </>
              }
            >
              <PairedBars rows={view.chg} spot={spot} maxPain={null} diverging label="Change in put and call open interest at each strike since yesterday" putFirst />
            </Card>
          </div>

          <div className="charts" style={{ gridTemplateColumns: "1fr" }}>
            <Card
              id="straddle-chart"
              title="ATM straddle through the day"
              sub={data.atm != null ? `ATM strike ${int(data.atm)} · combined Call + Put premium` : "recorded while the terminal runs"}
              legendItems={
                hasLegs
                  ? [
                      { name: "Straddle (CE+PE)", color: "var(--accent)" },
                      { name: "ATM Call", color: CE },
                      { name: "ATM Put", color: PE },
                    ]
                  : [{ name: "Straddle (CE+PE)", color: "var(--accent)" }]
              }
              table={<StraddleTable points={straddlePts} />}
              footer={
                s.straddle != null ? (
                  <>
                    <div className="chart-foot-stat">
                      <span className="dot" style={{ background: "var(--accent)" }} />
                      <span className="lbl">ATM Straddle:</span>
                      <span className="val">{px(s.straddle)}</span>
                      {s.straddle_pct != null && <span className="sub">({s.straddle_pct}% of spot)</span>}
                    </div>
                    {s.atm_ce != null && (
                      <div className="chart-foot-stat">
                        <span className="dot" style={{ background: CE }} />
                        <span className="lbl">ATM Call LTP:</span>
                        <span className="val">{px(s.atm_ce)}</span>
                      </div>
                    )}
                    {s.atm_pe != null && (
                      <div className="chart-foot-stat">
                        <span className="dot" style={{ background: PE }} />
                        <span className="lbl">ATM Put LTP:</span>
                        <span className="val">{px(s.atm_pe)}</span>
                      </div>
                    )}
                    {data.atm != null && (
                      <div className="chart-foot-stat">
                        <span className="lbl">Strike:</span>
                        <span className="val">{int(data.atm)}</span>
                      </div>
                    )}
                  </>
                ) : undefined
              }
            >
              {straddlePts.length >= 2 ? (
                <LineChart
                  times={straddlePts.map((p) => p.ts)}
                  yFmt={(v: number) => px(v)}
                  label="ATM straddle premium through the day"
                  series={[
                    { name: "Straddle (CE+PE)", color: "var(--accent)", values: straddlePts.map((p) => p.straddle ?? null), fmt: (v: number) => px(v) },
                    ...(hasLegs
                      ? [
                          { name: "ATM Call", color: CE, values: straddlePts.map((p) => p.atm_ce ?? null), fmt: (v: number) => px(v) },
                          { name: "ATM Put", color: PE, values: straddlePts.map((p) => p.atm_pe ?? null), fmt: (v: number) => px(v) },
                        ]
                      : []),
                  ]}
                />
              ) : (
                <Empty title="Building today’s trend">
                  The terminal records ATM straddle prices once a minute while an option chain or OI page is open. Leave this page open to see the line.
                </Empty>
              )}
            </Card>
          </div>

          <div className="charts">
            <Card title="PCR through the day" sub="recorded while the terminal runs">
              {pts.length >= 2 ? (
                <LineChart times={pts.map((p) => p.ts)} yFmt={(v) => v.toFixed(2)} label="Put-call ratio through the day"
                  series={[{ name: "PCR", color: "var(--accent)", values: pts.map((p) => p.pcr), fmt: (v) => v.toFixed(3) }]} />
              ) : (
                <Empty title="Building today’s trend">The terminal records a point about once a minute while an option chain or OI page is open. Leave this page open to see the line.</Empty>
              )}
            </Card>
            <Card title="Total put and call OI through the day" sub="recorded while the terminal runs" legend>
              {pts.length >= 2 ? (
                <LineChart times={pts.map((p) => p.ts)} yFmt={(v) => compact(v)} label="Total put and call open interest through the day"
                  series={[
                    { name: "Puts", color: PE, values: pts.map((p) => p.pe_oi), fmt: (v) => int(v) },
                    { name: "Calls", color: CE, values: pts.map((p) => p.ce_oi), fmt: (v) => int(v) },
                  ]} />
              ) : (
                <Empty title="Building today’s trend" />
              )}
            </Card>
          </div>

          <div className="charts">
            <AddTable title="Biggest put OI additions" rows={view.peAdds} data={data} kind="PE" />
            <AddTable title="Biggest call OI additions" rows={view.ceAdds} data={data} kind="CE" />
          </div>
          <p className="hint">
            “Reading” compares price and OI against yesterday: price up with OI up is a long build-up, price down with OI up a short build-up, price up with OI down short covering, price down with OI down long unwinding. These are descriptions of what happened, not forecasts or advice.
          </p>
        </>
      )}
    </>
  );
}
