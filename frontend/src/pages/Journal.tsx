import { useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { toast, useSession, useTitle } from "../lib/app";
import { px, signedRupees } from "../lib/format";
import { usePoll } from "../lib/store";
import type { Group, JournalData, Trip } from "../lib/strategy";
import { DayBars, EquityChart } from "../components/jcharts";
import { Empty } from "../components/ui";

const PERIODS = [
  { id: "today", label: "Today", days: 0 },
  { id: "7", label: "7 days", days: 6 },
  { id: "30", label: "30 days", days: 29 },
  { id: "all", label: "All time", days: -1 },
] as const;

const ymd = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 0.5 ? "flat" : v > 0 ? "up" : "down");
const when = (ts: number) => new Date(ts * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const held = (s: number) => (s < 90 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`);
const tagName = (t: string) => (t === "manual" ? "Manual" : t === "squareoff" ? "Square-off" : t === "other" ? "Outside this terminal" : t.replace(/_/g, " "));

function Tile({ k, v, sub, cls }: { k: string; v: ReactNode; sub?: string; cls?: string }) {
  return (
    <div className="stat">
      <span className="k">{k}</span>
      <span className={`v ${cls ?? ""}`}>{v}</span>
      {sub && <span className="hint">{sub}</span>}
    </div>
  );
}

function Card({ title, children, table }: { title: string; children: ReactNode; table?: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="panel">
      <div className="panel-h">
        <span>{title}</span>
        <span className="grow" />
        {table && <button className="btn ghost sm" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>{asTable ? "Show chart" : "Show as table"}</button>}
      </div>
      <div className="panel-b">{asTable && table ? table : children}</div>
    </section>
  );
}

function GroupTable({ rows, label, labelFn }: { rows: Group[]; label: string; labelFn?: (k: string) => string }) {
  if (!rows.length) return <span className="hint">Nothing yet.</span>;
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.pnl)));
  return (
    <div className="tscroll">
      <table className="t">
        <thead><tr><th>{label}</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">P&amp;L</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{labelFn ? labelFn(r.key) : r.key}</td>
              <td className="num">{r.n}</td>
              <td className="num">{r.win_rate.toFixed(0)}%</td>
              <td className={`num ${tone(r.pnl)}`}>
                <span className="mbar" aria-hidden="true"><i style={{ width: `${(Math.abs(r.pnl) / max) * 100}%`, background: r.pnl >= 0 ? "var(--up)" : "var(--down)" }} /></span>
                {signedRupees(r.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NoteEditor({ t, done }: { t: Trip; done: () => void }) {
  const [body, setBody] = useState(t.note);
  const [tags, setTags] = useState(t.tags.join(", "));
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await api(`/journal/notes/trip:${t.id}`, { method: "PUT", body: { body, tags: tags.split(",").map((x) => x.trim()).filter(Boolean) } });
      toast("Note saved");
      done();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="note-ed">
      <div className="field"><label htmlFor={`n${t.id}`}>What happened? What would you do differently?</label>
        <textarea id={`n${t.id}`} className="input" rows={3} maxLength={2000} value={body} onChange={(e) => setBody(e.target.value)} />
      </div>
      <div className="field"><label htmlFor={`t${t.id}`}>Tags (comma separated, e.g. fomo, followed plan)</label>
        <input id={`t${t.id}`} className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>
      <div className="row"><button className="btn primary sm" disabled={busy} onClick={save}>Save note</button></div>
    </div>
  );
}

export function Journal() {
  useTitle("Trade journal");
  const s = useSession();
  const [period, setPeriod] = useState<(typeof PERIODS)[number]["id"]>("30");
  const [tag, setTag] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const p = PERIODS.find((x) => x.id === period)!;
  const frm = p.days < 0 ? "" : ymd(new Date(Date.now() - p.days * 86400000));
  const q = new URLSearchParams();
  if (frm) q.set("frm", frm);
  if (tag) q.set("tag", tag);
  const { data: j, error, reload } = usePoll<JournalData>(`/journal?${q}`, 8000);
  const st = j?.stats;

  async function demo(add: boolean) {
    try {
      await api("/journal/demo", { method: add ? "POST" : "DELETE" });
      toast(add ? "Demo trades added. They are invented." : "Demo trades removed");
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  const notConnected = j && !j.ok && j.reason === "not_connected";
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Trade journal</h1>
          <p>Every trade placed here, paired into round trips, with the numbers that show how you are really doing. Before brokerage and taxes.</p>
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <div className="seg" role="group" aria-label="Period">
            {PERIODS.map((x) => <button key={x.id} aria-pressed={period === x.id} onClick={() => setPeriod(x.id)}>{x.label}</button>)}
          </div>
          <select className="select" style={{ width: "auto" }} aria-label="Filter by strategy or tag" value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All strategies</option>
            {(j?.tags ?? []).map((t) => <option key={t} value={t}>{tagName(t)}</option>)}
          </select>
        </div>
      </div>

      {error && !j && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {notConnected && <div className="notice" style={{ marginBottom: 12 }}>Connect to Kotak first. The journal reads your fills from the broker.</div>}
      {j?.demo && (
        <div className="notice warn" style={{ marginBottom: 12 }}>
          <span>This journal includes <b>invented demo trades</b> so you can explore. They are not real.</span><span className="grow" />
          <button className="btn sm" onClick={() => demo(false)}>Remove demo trades</button>
        </div>
      )}

      {j?.ok && st && !st.n && (
        <section className="panel" style={{ marginBottom: 12 }}>
          <Empty title="No closed trades in this period">
            <span>Trades appear here once a buy and a sell are matched. Orders placed in the Kotak app also show up, tagged “Outside this terminal”.</span>
            {s?.mode === "sample" && !j.demo && <div style={{ marginTop: 12 }}><button className="btn primary" onClick={() => demo(true)}>Add demo trades to explore</button></div>}
          </Empty>
        </section>
      )}

      {j?.ok && st && st.n > 0 && (
        <>
          <section className="panel panel-b" style={{ marginBottom: 12 }}>
            <div className="stats">
              <Tile k="Net P&L" v={signedRupees(st.net)} cls={tone(st.net)} sub={`${st.days} trading day${st.days === 1 ? "" : "s"}`} />
              <Tile k="Trades" v={st.n} sub={`${st.wins} won · ${st.losses} lost`} />
              <Tile k="Win rate" v={st.win_rate == null ? "–" : `${st.win_rate.toFixed(0)}%`} />
              <Tile k="Profit factor" v={st.profit_factor == null ? "–" : st.profit_factor.toFixed(2)} sub="gross profit ÷ gross loss" />
              <Tile k="Average win" v={signedRupees(st.avg_win)} cls="up" />
              <Tile k="Average loss" v={signedRupees(st.avg_loss)} cls="down" />
              <Tile k="Per trade" v={signedRupees(st.expectancy)} cls={tone(st.expectancy)} sub="average result" />
              <Tile k="Worst drawdown" v={signedRupees(-st.max_drawdown)} cls={st.max_drawdown > 0 ? "down" : "flat"} sub={`${st.max_losing_streak} losses in a row, at worst`} />
            </div>
          </section>

          <div className="charts">
            <Card title="Running total" table={
              <div className="tscroll"><table className="t"><thead><tr><th>Trade</th><th>Day</th><th className="num">P&amp;L</th><th className="num">Total</th></tr></thead>
                <tbody>{j.equity.map((e, i) => <tr key={i}><td>{i + 1}</td><td>{e.day}</td><td className={`num ${tone(e.pnl)}`}>{signedRupees(e.pnl)}</td><td className={`num ${tone(e.cum)}`}>{signedRupees(e.cum)}</td></tr>)}</tbody></table></div>
            }><EquityChart points={j.equity} /></Card>
            <Card title="Profit and loss by day" table={<GroupTable rows={j.by_day} label="Day" />}><DayBars rows={j.by_day} /></Card>
          </div>

          <div className="grid3">
            <Card title="By strategy"><GroupTable rows={j.by_tag} label="Strategy" labelFn={tagName} /></Card>
            <Card title="By instrument"><GroupTable rows={j.by_name} label="Underlying" /></Card>
            <Card title="By time of entry"><GroupTable rows={j.by_hour} label="Hour" /></Card>
          </div>

          <section className="panel" style={{ marginTop: 12 }}>
            <div className="panel-h">Trades <span className="sub">click a row to add a note</span></div>
            <div className="tscroll">
              <table className="t">
                <thead><tr><th>Entered</th><th>Instrument</th><th>Side</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Exit</th><th className="num">P&amp;L</th><th className="num">Held</th><th>Strategy</th><th>Note</th></tr></thead>
                <tbody>
                  {j.trips.map((t) => (
                    <>
                      <tr key={t.id} className="clickable" onClick={() => setOpenId(openId === t.id ? null : t.id)} tabIndex={0} aria-expanded={openId === t.id}
                        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setOpenId(openId === t.id ? null : t.id))}>
                        <td>{when(t.entry_ts)}</td>
                        <td className="cell-name">{t.label}</td>
                        <td><span className={`chip ${t.dir === "Short" ? "sell" : "buy"}`}>{t.dir}</span></td>
                        <td className="num">{t.qty}{t.lots !== t.qty ? <small className="muted"> ({t.lots} lot{t.lots === 1 ? "" : "s"})</small> : null}</td>
                        <td className="num">{px(t.entry)}</td>
                        <td className="num">{px(t.exit)}</td>
                        <td className={`num ${tone(t.pnl)}`} style={{ fontWeight: 600 }}>{signedRupees(t.pnl)}</td>
                        <td className="num">{held(t.hold_s)}</td>
                        <td>{tagName(t.tag)}</td>
                        <td className="muted">{t.note ? "✎ " + t.note.slice(0, 28) + (t.note.length > 28 ? "…" : "") : ""}{t.tags.length ? ` · ${t.tags.join(", ")}` : ""}</td>
                      </tr>
                      {openId === t.id && (
                        <tr key={t.id + "n"} className="note-row"><td colSpan={10}><NoteEditor t={t} done={() => { setOpenId(null); reload(); }} /></td></tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {j?.ok && j.open.length > 0 && (
        <div className="notice" style={{ marginTop: 12 }}>
          <span>Still open (not counted until closed): {j.open.map((o) => `${o.side} ${o.qty} ${o.label}`).join(" · ")}</span>
        </div>
      )}
      <p className="hint" style={{ marginTop: 14 }}>
        {j?.disclaimer ?? "Before brokerage, taxes and charges."} Trades are paired first-in, first-out per instrument. Fills are copied from the broker about every 20 seconds while the terminal runs, so history is kept even though Kotak only shows today's trade book.
      </p>
    </>
  );
}
