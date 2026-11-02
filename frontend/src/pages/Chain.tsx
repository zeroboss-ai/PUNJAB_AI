import { useEffect, useMemo, useRef } from "react";
import { ChainControls } from "../components/ChainControls";
import { Empty } from "../components/ui";
import { openTicket, useTitle } from "../lib/app";
import { openChart } from "../lib/chart";
import { chainSel, useChainSel, type ChainData, type ChainLeg, type ChainRow } from "../lib/chain";
import { compact, dash, int, pct, px, signed, tone } from "../lib/format";
import { lastMove, useQuote, useWant } from "../lib/quotes";
import { usePoll } from "../lib/store";

export function useChain(window: number) {
  const sel = useChainSel();
  const path = `/chain?underlying=${encodeURIComponent(sel.underlying)}&expiry=${encodeURIComponent(sel.expiry)}&window=${window}`;
  const r = usePoll<ChainData>(path, 3000);
  const fresh = r.data && r.data.underlying === sel.underlying ? r.data : null;
  return { ...r, data: fresh, raw: r.data };
}

function expShort(iso: string) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function Bar({ v, max, side }: { v: number | null; max: number; side: "ce" | "pe" }) {
  const w = v && max ? Math.max(1.5, (v / max) * 100) : 0;
  return (
    <div className={`oibar ${side}`} aria-hidden="true">
      <i style={{ width: `${w}%` }} />
    </div>
  );
}

function Side({ leg, side, row, maxOi, itm, onTrade, onChart }: { leg: ChainLeg | null; side: "ce" | "pe"; row: ChainRow; maxOi: number; itm: boolean; onTrade: (s: "B" | "S") => void; onChart: () => void }) {
  const q = useQuote(leg?.key);
  if (!leg) return <><td className={itm ? "itm" : ""} colSpan={4} /></>;
  const ltp = q?.l ?? leg.ltp;
  const oi = q?.oi || leg.oi;
  const chg = oi != null && leg.oi_prev != null ? oi - leg.oi_prev : leg.oi_chg;
  const pc = q?.pc || leg.pc;
  const chgPct = ltp != null && pc ? ((ltp - pc) / pc) * 100 : leg.chg_pct;
  const vol = q?.v || leg.vol;
  const dir = q ? lastMove(leg.key) : 0;
  const cells = {
    oi: (
      <td className={`oi ${itm ? "itm" : ""}`} title={leg.buildup ?? undefined}>
        <Bar v={oi} max={maxOi} side={side} />
        <span>{oi == null ? dash : compact(oi)}</span>
      </td>
    ),
    chg: <td className={`num ${tone(chg)} ${itm ? "itm" : ""}`}>{chg == null ? dash : (chg > 0 ? "+" : chg < 0 ? "−" : "") + compact(Math.abs(chg))}</td>,
    vol: <td className={`num muted hide-m ${itm ? "itm" : ""}`}>{vol ? compact(vol) : dash}</td>,
    ltp: (
      <td className={`num ltp ${itm ? "itm" : ""} ${dir > 0 ? "flash-up" : dir < 0 ? "flash-down" : ""}`} key={ltp ?? "x"}>
        <div className="ltp-wrap">
          <span className="hover-acts bs-pair">
            <button className="bs b" onClick={() => onTrade("B")} aria-label={`Buy ${row.strike} ${side === "ce" ? "call" : "put"}`}>B</button>
            <button className="bs s" onClick={() => onTrade("S")} aria-label={`Sell ${row.strike} ${side === "ce" ? "call" : "put"}`}>S</button>
            <button className="bs c" onClick={onChart} aria-label={`Chart ${row.strike} ${side === "ce" ? "call" : "put"}`} title="Chart">C</button>
          </span>
          <span><b>{ltp == null ? dash : px(ltp)}</b><small className={tone(chgPct)}>{pct(chgPct)}</small></span>
        </div>
      </td>
    ),
  };
  return side === "ce" ? <>{cells.oi}{cells.chg}{cells.vol}{cells.ltp}</> : <>{cells.ltp}{cells.vol}{cells.chg}{cells.oi}</>;
}

export function Chain() {
  useTitle("Option chain");
  const sel = useChainSel();
  const { data, raw, error } = useChain(sel.window);                    // 0 = every strike
  // Live prices for the strikes near the money; the others show the chain snapshot (refreshed every few seconds).
  const keys = useMemo(() => {
    if (!data) return [];
    let rows = data.rows;
    const i = rows.findIndex((r) => r.strike === data.atm);
    if (rows.length > 61 && i >= 0) rows = rows.slice(Math.max(0, i - 30), i + 31);
    return rows.flatMap((r) => [r.ce?.key, r.pe?.key].filter(Boolean) as string[]);
  }, [data?.underlying, data?.expiry, data?.rows.length, data?.atm]); // eslint-disable-line react-hooks/exhaustive-deps
  const box = useRef<HTMLDivElement>(null);
  const shownFor = useRef("");
  const jump = (smooth = false) => {
    const el = box.current, row = el?.querySelector<HTMLElement>("tr.atm");
    if (!el || !row) return;
    const delta = row.getBoundingClientRect().top - el.getBoundingClientRect().top - el.clientHeight / 2 + row.clientHeight / 2;
    el.scrollTo({ top: el.scrollTop + delta, behavior: smooth ? "smooth" : "auto" });
  };
  useEffect(() => {                                                       // centre the ATM strike once per chain/selection
    if (!data) return;
    const id = `${data.underlying}|${data.expiry}|${sel.window}`;
    if (shownFor.current === id) return;
    shownFor.current = id;
    window.setTimeout(() => jump(), 0);
  }, [data?.underlying, data?.expiry, sel.window, data?.rows.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useWant(data?.spot_key ? [data.spot_key, ...keys] : keys);
  const spotQ = useQuote(data?.spot_key ?? undefined);
  const spot = spotQ?.l ?? data?.spot ?? null;

  const maxOi = useMemo(() => Math.max(1, ...(data?.rows ?? []).flatMap((r) => [r.ce?.oi ?? 0, r.pe?.oi ?? 0])), [data]);
  const s = data?.summary;
  const callWall = s?.ce_top[0]?.strike, putWall = s?.pe_top[0]?.strike;

  const trade = (r: ChainRow, kind: "CE" | "PE", side: "B" | "S") => {
    const leg = kind === "CE" ? r.ce : r.pe;
    if (!leg || !data) return;
    openTicket({ key: leg.key, label: `${data.underlying} ${expShort(data.expiry)} ${r.strike} ${kind}`, side, lot: data.lot, tick: 0.05 });
  };

  const chartLeg = (r: ChainRow, kind: "CE" | "PE") => {
    const leg = kind === "CE" ? r.ce : r.pe;
    if (leg && data) openChart(leg.key, `${data.underlying} ${expShort(data.expiry)} ${r.strike} ${kind}`);
  };

  const problem = raw && !raw.ok ? raw : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Option chain</h1>
          <p>Live call and put prices with open interest. Hover a price to buy or sell.</p>
        </div>
        <ChainControls data={data} extra={data ? <button className="btn sm" onClick={() => jump(true)} title="Scroll to the at-the-money strike">Jump to ATM</button> : null} />
      </div>

      {problem && (
        <div className="notice bad" style={{ marginBottom: 12 }} role="alert">
          {problem.reason === "not_connected" ? "Connect to Kotak first. The chain is read from your broker." : problem.error ?? "The option chain could not be loaded."}
        </div>
      )}
      {error && !raw && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {data?.stale && <div className="notice" style={{ marginBottom: 12 }}>The broker has not returned a fresh chain for a while. Prices still update live; open-interest changes may lag.</div>}

      {data && s && (
        <section className="panel chain-sum" aria-label="Chain summary">
          <div className="stat"><span className="k">{data.underlying} {data.spot_key?.startsWith("mcx_fo|") ? "future" : "spot"}</span><span className="v">{px(spot)}{data.spot_key && <button className="btn ghost sm" style={{ marginLeft: 8 }} onClick={() => openChart(data.spot_key as string, `${data.underlying} ${data.spot_key?.startsWith("mcx_fo|") ? "future" : "spot"}`)}>Chart</button>}</span></div>
          <div className="stat"><span className="k">PCR (put OI ÷ call OI)</span><span className="v">{s.pcr == null ? dash : s.pcr.toFixed(2)}</span></div>
          <div className="stat"><span className="k">Max pain</span><span className="v">{s.max_pain == null ? dash : int(s.max_pain)}</span></div>
          <div className="stat"><span className="k">Largest call OI</span><span className="v">{callWall == null ? dash : int(callWall)}</span></div>
          <div className="stat"><span className="k">Largest put OI</span><span className="v">{putWall == null ? dash : int(putWall)}</span></div>
          <div className="stat"><span className="k">ATM straddle</span><span className="v">{s.straddle == null ? dash : px(s.straddle)}{s.straddle_pct != null && <small className="muted"> {s.straddle_pct}%</small>}</span></div>
        </section>
      )}

      <section className="panel" style={{ marginTop: 12 }}>
        {!data ? (
          <Empty title={problem ? "No chain to show" : "Loading the chain…"}>
            {problem ? (problem.reason === "not_connected" ? "Connect to Kotak first." : (problem.error ?? "The option chain could not be loaded.")) : "The first load can take a few seconds."}
          </Empty>
        ) : (
          <div className="tscroll chain-scroll" ref={box}>
            <table className="t chain">
              <thead>
                <tr className="grp">
                  <th colSpan={4} className="ce-h">Calls</th>
                  <th className="num">Strike</th>
                  <th colSpan={4} className="pe-h">Puts</th>
                </tr>
                <tr>
                  <th>OI</th><th className="num">OI chg</th><th className="num hide-m">Volume</th><th className="num">Price</th>
                  <th className="num">{data.lot > 1 ? `Lot ${data.lot}` : ""}</th>
                  <th className="num">Price</th><th className="num hide-m">Volume</th><th className="num">OI chg</th><th>OI</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => {
                  const atm = r.strike === data.atm;
                  const ceItm = spot != null && r.strike < spot, peItm = spot != null && r.strike > spot;
                  return (
                    <tr key={r.strike} className={atm ? "atm" : ""}>
                      <Side leg={r.ce} side="ce" row={r} maxOi={maxOi} itm={ceItm} onTrade={(sd) => trade(r, "CE", sd)} onChart={() => chartLeg(r, "CE")} />
                      <td className="strike">
                        <b>{int(r.strike)}</b>
                        <span className="tags">
                          {atm && <i className="tag-atm">ATM</i>}
                          {r.strike === s?.max_pain && <i title="Strike where option buyers together would lose the most at expiry">Max pain</i>}
                          {r.strike === callWall && <i className="c" title="Strike with the largest call open interest">Top call OI</i>}
                          {r.strike === putWall && <i className="p" title="Strike with the largest put open interest">Top put OI</i>}
                        </span>
                      </td>
                      <Side leg={r.pe} side="pe" row={r} maxOi={maxOi} itm={peItm} onTrade={(sd) => trade(r, "PE", sd)} onChart={() => chartLeg(r, "PE")} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="hint" style={{ marginTop: 10 }}>
        OI chg is today’s open interest minus the previous day’s. Shaded cells are in the money. Max pain and PCR are arithmetic on the numbers shown, not a forecast.
      </p>
    </>
  );
}
export { chainSel, signed };
