import { useMemo, useState, type ReactNode } from "react";
import { Legend, PairedBars, type BarRow } from "../components/charts";
import { FlowBars } from "../components/jcharts";
import { Empty } from "../components/ui";
import { useTitle } from "../lib/app";
import { compact, dash, int, px, signedRupees } from "../lib/format";
import { usePoll } from "../lib/store";

interface Side { buy: number; sell: number; net: number }
interface FiiRow { day: string; fii: Side; dii: Side }
interface FiiData { ok: boolean; source: string; rows: FiiRow[]; error: string; note: string }
interface Stock { symbol: string; close: number; prev: number; chg_pct: number; volume: number; turnover_cr: number; deliv_pct: number | null }
interface DelData { ok: boolean; source: string; day: string; count: number; liquid: number; min_turnover_cr: number; gainers: Stock[]; losers: Stock[]; turnover: Stock[]; delivery: Stock[]; lookup?: Stock[] }
interface Fut { symbol: string; expiry: string; close: number | null; chg_pct: number | null; oi: number; oi_chg: number; oi_chg_pct: number | null; buildup: string | null }
interface Opt { expiry: string; ref: number | null; total_ce_oi: number; total_pe_oi: number; pcr: number | null; max_pain: number | null; top_ce: { strike: number; oi: number }[]; top_pe: { strike: number; oi: number }[]; strikes: { strike: number; ce_oi: number | null; ce_chg: number | null; pe_oi: number | null; pe_chg: number | null }[] }
interface FoData { ok: boolean; source: string; day: string; index_futures: Fut[]; buildup: Record<string, Fut[]>; options: Record<string, Opt>; stock_futures: number }

const TABS = [{ id: "fii", label: "FII / DII" }, { id: "del", label: "Delivery and movers" }, { id: "fo", label: "F&O positioning" }] as const;
type Tab = (typeof TABS)[number]["id"];

const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 0.005 ? "flat" : v > 0 ? "up" : "down");
const cr = (v: number) => signedRupees(v, 0);
const dayText = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const ymd = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

function Tile({ k, v, sub, cls }: { k: string; v: ReactNode; sub?: string; cls?: string }) {
  return (
    <div className="stat">
      <span className="k">{k}</span>
      <span className={`v ${cls ?? ""}`}>{v}</span>
      {sub && <span className="hint">{sub}</span>}
    </div>
  );
}

function Panel({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-h"><span>{title}</span>{sub && <span className="sub">{sub}</span>}</div>
      <div className="panel-b">{children}</div>
    </section>
  );
}

function SampleNotice({ source }: { source: string }) {
  if (source !== "sample") return null;
  return <div className="notice warn" style={{ marginBottom: 12 }}>These figures are invented, so you can explore the screen. In paper and live mode they come from NSE's public files.</div>;
}

function DayPicker({ day, setDay, latest }: { day: string; setDay: (d: string) => void; latest?: string }) {
  const today = new Date();
  const min = new Date(today.getTime() - 90 * 86400000);
  return (
    <label className="mini">
      Day
      <input className="input" type="date" value={day} min={ymd(min)} max={ymd(today)} onChange={(e) => setDay(e.target.value)} aria-label="Day to show" style={{ width: 150 }} />
      {day ? <button className="btn ghost sm" onClick={() => setDay("")}>Latest</button> : latest ? <span className="hint">showing {dayText(latest)}</span> : null}
    </label>
  );
}

// ------------------------------------------------------------------------------------------------ FII / DII
function FiiDii() {
  const { data, error, loading } = usePoll<FiiData>("/nse/fii-dii", 120000);
  const rows = data?.rows ?? [];
  const chron = useMemo(() => rows.slice(0, 20).reverse().map((r) => ({ day: r.day, fii: r.fii.net, dii: r.dii.net })), [rows]);
  const [asTable, setAsTable] = useState(false);
  if (!data) return loading ? <p className="hint">Asking NSE…</p> : <div className="notice bad">{error ?? "Not available."}</div>;
  const last = rows[0];
  const sum5 = (k: "fii" | "dii") => rows.slice(0, 5).reduce((a, r) => a + r[k].net, 0);
  return (
    <>
      <SampleNotice source={data.source} />
      {data.error && <div className="notice warn" style={{ marginBottom: 12 }}>Could not refresh: {data.error} The figures below are the ones fetched earlier.</div>}
      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-b">
          <div className="stats">
            <Tile k="Latest day" v={dayText(last.day).replace(/, \d{4}$/, "")} sub={`${rows.length} day(s) kept`} />
            <Tile k="FII net" v={`${cr(last.fii.net)} Cr`} cls={tone(last.fii.net)} sub={last.fii.net >= 0 ? "net bought" : "net sold"} />
            <Tile k="DII net" v={`${cr(last.dii.net)} Cr`} cls={tone(last.dii.net)} sub={last.dii.net >= 0 ? "net bought" : "net sold"} />
            <Tile k={`FII, last ${Math.min(5, rows.length)} days`} v={`${cr(sum5("fii"))} Cr`} cls={tone(sum5("fii"))} />
            <Tile k={`DII, last ${Math.min(5, rows.length)} days`} v={`${cr(sum5("dii"))} Cr`} cls={tone(sum5("dii"))} />
          </div>
        </div>
      </section>
      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-h">
          <span>Net buying (+) or selling (−), rupees crore</span>
          <span className="grow" />
          <button className="btn ghost sm" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>{asTable ? "Show chart" : "Show as table"}</button>
        </div>
        <div className="panel-b">
          <div style={{ marginBottom: 8 }}><Legend items={[{ name: "FII / FPI", color: "var(--accent)" }, { name: "DII", color: "var(--buy)" }]} /></div>
          {asTable ? <FiiTable rows={rows} /> : <FlowBars rows={chron} />}
        </div>
      </section>
      {!asTable && <Panel title="Day by day"><FiiTable rows={rows} /></Panel>}
      <p className="hint" style={{ marginTop: 12 }}>{data.note} Foreign institutional (FII/FPI) and domestic institutional (DII) cash-market flows are context, not a signal to trade.</p>
    </>
  );
}

function FiiTable({ rows }: { rows: FiiRow[] }) {
  return (
    <div className="tscroll" style={{ maxHeight: 360, overflowY: "auto" }}>
      <table className="t">
        <thead>
          <tr><th>Day</th><th className="num">FII buy</th><th className="num">FII sell</th><th className="num">FII net</th><th className="num">DII buy</th><th className="num">DII sell</th><th className="num">DII net</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.day}>
              <td>{dayText(r.day)}</td>
              <td className="num">{int(r.fii.buy)}</td><td className="num">{int(r.fii.sell)}</td><td className={`num ${tone(r.fii.net)}`}>{cr(r.fii.net)}</td>
              <td className="num">{int(r.dii.buy)}</td><td className="num">{int(r.dii.sell)}</td><td className={`num ${tone(r.dii.net)}`}>{cr(r.dii.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------------------------------ delivery and movers
function StockTable({ rows, empty = "Nothing to show." }: { rows: Stock[]; empty?: string }) {
  if (!rows.length) return <span className="hint">{empty}</span>;
  return (
    <div className="tscroll">
      <table className="t">
        <thead><tr><th>Stock</th><th className="num">Close</th><th className="num">Change</th><th className="num">Traded (₹ Cr)</th><th className="num">Delivery</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>{r.symbol}</td>
              <td className="num">{px(r.close)}</td>
              <td className={`num ${tone(r.chg_pct)}`}>{r.chg_pct > 0 ? "+" : ""}{r.chg_pct.toFixed(2)}%</td>
              <td className="num">{int(r.turnover_cr)}</td>
              <td className="num">{r.deliv_pct == null ? dash : `${r.deliv_pct.toFixed(1)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Delivery() {
  const [day, setDay] = useState("");
  const [q, setQ] = useState("");
  const [qq, setQQ] = useState("");
  const path = `/nse/delivery?day=${day}&q=${encodeURIComponent(qq)}`;
  const { data: raw, error } = usePoll<DelData>(path, 600000);
  const data = raw && (!day || raw.day === day) && (!!qq === (raw.lookup !== undefined)) ? raw : null;     // never show the previous day's table under a new date
  return (
    <>
      <div className="row" style={{ marginBottom: 12, gap: 12 }}>
        <DayPicker day={day} setDay={setDay} latest={data?.day} />
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQQ(q.trim()); }}>
          <input className="input" style={{ width: 170 }} placeholder="Find a stock, e.g. TCS" maxLength={20} value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} aria-label="Find a stock" />
          <button className="btn" type="submit">Find</button>
          {qq && <button className="btn ghost" type="button" onClick={() => { setQ(""); setQQ(""); }}>Clear</button>}
        </form>
      </div>
      {error && !data && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {!data && !error && <p className="hint">Asking NSE…</p>}
      {data && (
        <>
          <SampleNotice source={data.source} />
          <p className="hint" style={{ marginBottom: 12 }}>
            End of day {dayText(data.day)}. {data.count.toLocaleString("en-IN")} stocks in the file; lists below only include the {data.liquid.toLocaleString("en-IN")} that traded at least ₹{data.min_turnover_cr} crore, because tiny volumes mislead.
            "Delivery" is the share of the day's traded quantity that was taken for delivery rather than squared off the same day.
          </p>
          {data.lookup && (
            <div style={{ marginBottom: 12 }}>
              <Panel title={`Search: ${qq}`}><StockTable rows={data.lookup} empty="No stock with that name in the file." /></Panel>
            </div>
          )}
          <div className="charts">
            <Panel title="Top gainers"><StockTable rows={data.gainers} /></Panel>
            <Panel title="Top losers"><StockTable rows={data.losers} /></Panel>
            <Panel title="Most traded (by value)"><StockTable rows={data.turnover} /></Panel>
            <Panel title="Highest delivery share"><StockTable rows={data.delivery} /></Panel>
          </div>
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ F&O
const UNDS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];

function FutTable({ rows }: { rows: Fut[] }) {
  if (!rows.length) return <span className="hint">None today.</span>;
  return (
    <div className="tscroll">
      <table className="t">
        <thead><tr><th>Future</th><th className="num">Price change</th><th className="num">OI</th><th className="num">OI change</th><th>Reading</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>{r.symbol}</td>
              <td className={`num ${tone(r.chg_pct)}`}>{r.chg_pct == null ? dash : `${r.chg_pct > 0 ? "+" : ""}${r.chg_pct.toFixed(2)}%`}</td>
              <td className="num">{compact(r.oi)}</td>
              <td className={`num ${tone(r.oi_chg_pct)}`}>{r.oi_chg_pct == null ? dash : `${r.oi_chg_pct > 0 ? "+" : ""}${r.oi_chg_pct.toFixed(1)}%`}</td>
              <td>{r.buildup ?? dash}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FnO() {
  const [day, setDay] = useState("");
  const [und, setUnd] = useState("NIFTY");
  const { data: raw, error } = usePoll<FoData>(`/nse/fo?day=${day}`, 600000);
  const data = raw && (!day || raw.day === day) ? raw : null;
  const o = data?.options[und] ?? null;
  const oi: BarRow[] = useMemo(() => (o?.strikes ?? []).map((s) => ({ strike: s.strike, ce: s.ce_oi, pe: s.pe_oi })), [o]);
  const chg: BarRow[] = useMemo(() => (o?.strikes ?? []).map((s) => ({ strike: s.strike, ce: s.ce_chg, pe: s.pe_chg })), [o]);
  return (
    <>
      <div className="row" style={{ marginBottom: 12, gap: 12 }}>
        <DayPicker day={day} setDay={setDay} latest={data?.day} />
        <div className="seg" role="group" aria-label="Index">
          {UNDS.map((u) => <button key={u} aria-pressed={und === u} onClick={() => setUnd(u)}>{u}</button>)}
        </div>
      </div>
      {error && !data && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {!data && !error && <p className="hint">Asking NSE…</p>}
      {data && (
        <>
          <SampleNotice source={data.source} />
          <p className="hint" style={{ marginBottom: 12 }}>
            NSE's end-of-day F&O file for {dayText(data.day)}. Open interest (OI) is the number of contracts still open. "Reading" is the usual four-way description of price direction against OI direction; it describes what happened and does not say what comes next.
          </p>
          {!o ? <Empty title={`No ${und} options in this file`} /> : (
            <>
              <section className="panel" style={{ marginBottom: 12 }}>
                <div className="panel-b">
                  <div className="stats">
                    <Tile k="Expiry" v={dayText(o.expiry).replace(/, \d{4}$/, "")} sub="nearest" />
                    <Tile k="Put-call ratio" v={o.pcr == null ? dash : o.pcr.toFixed(2)} sub="put OI ÷ call OI" />
                    <Tile k="Max pain" v={o.max_pain == null ? dash : int(o.max_pain)} />
                    <Tile k="Call OI" v={compact(o.total_ce_oi)} sub={o.top_ce[0] ? `largest at ${int(o.top_ce[0].strike)}` : undefined} />
                    <Tile k="Put OI" v={compact(o.total_pe_oi)} sub={o.top_pe[0] ? `largest at ${int(o.top_pe[0].strike)}` : undefined} />
                  </div>
                </div>
              </section>
              <div className="charts">
                <Panel title="Open interest by strike">
                  <div style={{ marginBottom: 8 }}><Legend items={[{ name: "Calls", color: "var(--ce)" }, { name: "Puts", color: "var(--pe)" }]} /></div>
                  <PairedBars rows={oi} spot={null} maxPain={null} label={`${und} call and put open interest at each strike`} />
                </Panel>
                <Panel title="Change in OI on the day">
                  <div style={{ marginBottom: 8 }}><Legend items={[{ name: "Calls", color: "var(--ce)" }, { name: "Puts", color: "var(--pe)" }]} /></div>
                  <PairedBars rows={chg} spot={null} maxPain={null} diverging label={`${und} change in call and put open interest at each strike`} />
                </Panel>
              </div>
            </>
          )}
          <div style={{ marginBottom: 12 }}><Panel title="Index futures"><FutTable rows={data.index_futures} /></Panel></div>
          <p className="hint" style={{ margin: "4px 0 10px" }}>Stock futures: the {data.stock_futures} contracts with a clear reading, biggest OI change first.</p>
          <div className="charts">
            {Object.entries(data.buildup).map(([name, rows]) => <Panel key={name} title={name}><FutTable rows={rows} /></Panel>)}
          </div>
        </>
      )}
    </>
  );
}

// ----------------------------------------------------------------------------------------------------- page
export function MarketData() {
  useTitle("Market data");
  const [tab, setTab] = useState<Tab>("fii");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Market data</h1>
          <p>End-of-day figures from NSE's free public data. They are not live, and NSE can change or block them, so anything that cannot be checked is shown as unavailable instead of guessed.</p>
        </div>
      </div>
      <div className="seg" role="tablist" aria-label="Market data" style={{ marginBottom: 14 }}>
        {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} aria-pressed={tab === t.id} onClick={() => setTab(t.id)}>{t.label}</button>)}
      </div>
      {tab === "fii" && <FiiDii />}
      {tab === "del" && <Delivery />}
      {tab === "fo" && <FnO />}
    </>
  );
}
