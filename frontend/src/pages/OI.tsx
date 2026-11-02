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

function Tile({ k, v, sub }: { k: string; v: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="stat">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
      {sub && <span className="hint">{sub}</span>}
    </div>
  );
}

function Card({ title, sub, legend, children, table }: { title: string; sub?: string; legend?: boolean; children: React.ReactNode; table?: React.ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="panel">
      <div className="panel-h">
        <span>{title}</span>
        {sub && <span className="sub">{sub}</span>}
        <span className="grow" />
        {table && <button className="btn ghost sm" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>{asTable ? "Show chart" : "Show as table"}</button>}
      </div>
      <div className="panel-b">
        {legend && <div style={{ marginBottom: 8 }}><Legend items={[{ name: "Calls", color: CE }, { name: "Puts", color: PE }]} /></div>}
        {asTable && table ? table : children}
      </div>
    </section>
  );
}

function StrikeTable({ rows, signedVals }: { rows: BarRow[]; signedVals?: boolean }) {
  const f = (v: number | null) => (v == null ? dash : (signedVals && v > 0 ? "+" : "") + int(v));
  return (
    <div className="tscroll" style={{ maxHeight: 290, overflowY: "auto" }}>
      <table className="t">
        <thead><tr><th className="num">Strike</th><th className="num">Calls</th><th className="num">Puts</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.strike}><td className="num">{int(r.strike)}</td><td className="num">{f(r.ce)}</td><td className="num">{f(r.pe)}</td></tr>)}</tbody>
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
  const pts = (series.data?.points ?? []).filter((p) => p.pcr != null);
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
            <Tile k="Total call OI" v={compact(s.ce_oi)} sub={`${s.ce_oi_chg >= 0 ? "+" : "−"}${compact(Math.abs(s.ce_oi_chg))} since yesterday`} />
            <Tile k="Total put OI" v={compact(s.pe_oi)} sub={`${s.pe_oi_chg >= 0 ? "+" : "−"}${compact(Math.abs(s.pe_oi_chg))} since yesterday`} />
            <Tile k="Largest call OI" v={s.ce_top[0] ? int(s.ce_top[0].strike) : dash} sub={s.ce_top[1] ? `then ${s.ce_top.slice(1).map((t) => int(t.strike)).join(", ")}` : undefined} />
            <Tile k="Largest put OI" v={s.pe_top[0] ? int(s.pe_top[0].strike) : dash} sub={s.pe_top[1] ? `then ${s.pe_top.slice(1).map((t) => int(t.strike)).join(", ")}` : undefined} />
            <Tile k="ATM straddle" v={s.straddle == null ? dash : px(s.straddle)} sub={s.straddle_pct != null ? `${s.straddle_pct}% of spot` : undefined} />
          </section>

          <div className="charts">
            <Card title="Open interest by strike" legend table={<StrikeTable rows={view.oi} />}>
              <PairedBars rows={view.oi} spot={spot} maxPain={s.max_pain} label="Call and put open interest at each strike" />
            </Card>
            <Card title="Change in OI since yesterday" legend table={<StrikeTable rows={view.chg} signedVals />}>
              <PairedBars rows={view.chg} spot={spot} maxPain={null} diverging label="Change in call and put open interest at each strike since yesterday" />
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
            <Card title="Total call and put OI through the day" sub="recorded while the terminal runs" legend>
              {pts.length >= 2 ? (
                <LineChart times={pts.map((p) => p.ts)} yFmt={(v) => compact(v)} label="Total call and put open interest through the day"
                  series={[
                    { name: "Calls", color: CE, values: pts.map((p) => p.ce_oi), fmt: (v) => int(v) },
                    { name: "Puts", color: PE, values: pts.map((p) => p.pe_oi), fmt: (v) => int(v) },
                  ]} />
              ) : (
                <Empty title="Building today’s trend" />
              )}
            </Card>
          </div>

          <div className="charts">
            <AddTable title="Biggest call OI additions" rows={view.ceAdds} data={data} kind="CE" />
            <AddTable title="Biggest put OI additions" rows={view.peAdds} data={data} kind="PE" />
          </div>
          <p className="hint">
            “Reading” compares price and OI against yesterday: price up with OI up is a long build-up, price down with OI up a short build-up, price up with OI down short covering, price down with OI down long unwinding. These are descriptions of what happened, not forecasts or advice.
          </p>
        </>
      )}
    </>
  );
}
