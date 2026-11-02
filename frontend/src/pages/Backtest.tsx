import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DayBars, EquityChart } from "../components/jcharts";
import { Empty } from "../components/ui";
import { api } from "../lib/api";
import { navigate, toast, useSession, useTitle } from "../lib/app";
import { px, signedRupees } from "../lib/format";
import { usePoll } from "../lib/store";
import type { Group, JStats } from "../lib/strategy";

interface UndDay { und: string; spot_candles: number; from: number | null; to: number | null; coverage_pct: number; contracts: number; strikes: number; usable: boolean }
interface RecDay { day: string; unds: UndDay[] }
interface StratRow { id: number; name: string; valid: boolean; underlying: string | null }
interface DaysData {
  ok: boolean; src: string; sample: boolean; running: boolean; days: RecDay[]; strategies: StratRow[];
  recorder: { on: boolean; saved: number; last_flush: number | null; error: string; keys: number } | null;
}
interface BtTrade { day: string; leg: string; label: string; dir: string; lots: number; qty: number; entry: number; exit: number; entry_time: string; exit_time: string; exit_reason: string; gross: number; pnl: number; forced: boolean }
interface LogLine { level: string; msg: string; t?: string; ts?: number }
interface DayResult { day: string; status: "ok" | "attention" | "no_data" | "error" | "cancelled"; note: string; trades: BtTrade[]; pnl: number; log: LogLine[]; outcome: string }
interface Summary { stats: JStats; equity: { ts: number; cum: number; pnl: number; day: string }[]; by_day: { key: string; pnl: number; n: number }[]; by_dir: Group[]; by_reason: Group[]; by_leg: Group[]; days_run: number; days_skipped: number; days_traded: number }
interface Job {
  id: number; state: "running" | "done" | "stopped" | "error"; strategy: string; src: string; total: number; done: number; results: DayResult[];
  summary: Summary | null; assumptions: string[]; cost_per_trade: number; start_time: string; invented: boolean; underlying: string; error?: string;
}

const tone = (v: number | null | undefined) => (v == null || Math.abs(v) < 0.5 ? "flat" : v > 0 ? "up" : "down");
const hm = (m: number | null) => (m == null ? "–" : `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`);
const dayText = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
const STATUS: Record<string, { label: string; cls: string }> = {
  ok: { label: "Done", cls: "" }, attention: { label: "Needs a look", cls: "warn" }, no_data: { label: "No data", cls: "warn" },
  error: { label: "Error", cls: "down" }, cancelled: { label: "Stopped", cls: "warn" },
};

function Tile({ k, v, sub, cls }: { k: string; v: ReactNode; sub?: string; cls?: string }) {
  return (
    <div className="stat">
      <span className="k">{k}</span>
      <span className={`v ${cls ?? ""}`}>{v}</span>
      {sub && <span className="hint">{sub}</span>}
    </div>
  );
}

function Card({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-h"><span>{title}</span>{sub && <span className="sub">{sub}</span>}</div>
      <div className="panel-b">{children}</div>
    </section>
  );
}

function GroupTable({ rows, label }: { rows: Group[]; label: string }) {
  if (!rows.length) return <span className="hint">Nothing yet.</span>;
  return (
    <div className="tscroll">
      <table className="t">
        <thead><tr><th>{label}</th><th className="num">Trades</th><th className="num">Win rate</th><th className="num">P&amp;L</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}><td>{r.key}</td><td className="num">{r.n}</td><td className="num">{r.win_rate.toFixed(0)}%</td><td className={`num ${tone(r.pnl)}`}>{signedRupees(r.pnl)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Results({ job }: { job: Job }) {
  const [open, setOpen] = useState<string | null>(null);
  const s = job.summary;
  const st = s?.stats;
  const results = [...job.results].sort((a, b) => (a.day < b.day ? 1 : -1));
  return (
    <>
      {job.invented && <div className="notice warn" style={{ marginBottom: 12 }}><span>These results come from <b>invented days</b>, made by the terminal so you can try this screen. They say nothing about how a strategy would really do.</span></div>}
      {s && st && st.n > 0 && (
        <>
          <section className="panel panel-b" style={{ marginBottom: 12 }}>
            <div className="stats">
              <Tile k="Net P&L" v={signedRupees(st.net)} cls={tone(st.net)} sub={`${s.days_run} day${s.days_run === 1 ? "" : "s"} replayed, ${s.days_traded} with a trade${job.cost_per_trade ? ` · after ₹${job.cost_per_trade} per trade` : ""}`} />
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
            <Card title="Running total"><EquityChart points={s.equity} /></Card>
            <Card title="Profit and loss by day"><DayBars rows={s.by_day} /></Card>
          </div>
          <div className="grid3">
            <Card title="By direction"><GroupTable rows={s.by_dir} label="Direction" /></Card>
            <Card title="By leg"><GroupTable rows={s.by_leg} label="Leg" /></Card>
            <Card title="How trades ended"><GroupTable rows={s.by_reason} label="Reason" /></Card>
          </div>
        </>
      )}
      {s && st && !st.n && job.state !== "running" && (
        <section className="panel" style={{ marginBottom: 12 }}>
          <Empty title="The strategy did not trade on these days">Look at the day list below to see why: no entry signal, or missing data.</Empty>
        </section>
      )}

      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-h">Day by day <span className="sub">click a day for its trades and what the strategy said</span></div>
        <div className="tscroll">
          <table className="t">
            <thead><tr><th>Day</th><th>Result</th><th className="num">Trades</th><th className="num">P&amp;L</th><th>Note</th></tr></thead>
            <tbody>
              {results.map((r) => {
                const stt = STATUS[r.status] ?? STATUS.error;
                const isOpen = open === r.day;
                const toggle = () => setOpen(isOpen ? null : r.day);
                return (
                  <Fragment key={r.day}>
                    <tr className="clickable" onClick={toggle} tabIndex={0} aria-expanded={isOpen} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), toggle())}>
                      <td>{dayText(r.day)}</td>
                      <td><span className={`chip ${stt.cls}`}>{stt.label}</span></td>
                      <td className="num">{r.trades.length}</td>
                      <td className={`num ${tone(r.pnl)}`}>{r.trades.length ? signedRupees(r.pnl) : "–"}</td>
                      <td className="muted">{r.note || "–"}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={5}>
                          {r.trades.length > 0 && (
                            <div className="tscroll">
                              <table className="t">
                                <thead><tr><th>Instrument</th><th>Side</th><th className="num">Lots</th><th>In</th><th className="num">Entry</th><th>Out</th><th className="num">Exit</th><th className="num">P&amp;L</th><th>Ended by</th></tr></thead>
                                <tbody>
                                  {r.trades.map((t, i) => (
                                    <tr key={i}>
                                      <td>{t.label}</td><td>{t.dir}</td><td className="num">{t.lots}</td>
                                      <td>{t.entry_time.slice(0, 8)}</td><td className="num">{px(t.entry)}</td>
                                      <td>{t.exit_time.slice(0, 8)}</td><td className="num">{px(t.exit)}</td>
                                      <td className={`num ${tone(t.pnl)}`}>{signedRupees(t.pnl)}</td>
                                      <td>{t.exit_reason || "–"}{t.forced ? " (data ended)" : ""}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                          {r.log.length > 0 && (
                            <ul className="bt-log" aria-label="What the strategy said">
                              {r.log.map((l, i) => <li key={i} className={l.level === "error" || l.level === "warn" ? "down" : ""}>{l.msg}</li>)}
                            </ul>
                          )}
                          {!r.trades.length && !r.log.length && <span className="hint">Nothing more to show for this day.</span>}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {!results.length && <tr><td colSpan={5} className="muted">Waiting for the first day…</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-h">Read these before you trust any number</div>
        <ul className="panel-b bt-assume">
          {job.assumptions.map((a, i) => <li key={i}>{a}</li>)}
        </ul>
      </section>
    </>
  );
}

export function Backtest() {
  useTitle("Back-test");
  const sess = useSession();
  const { data: d, error, reload } = usePoll<DaysData>("/backtest/days", 10000);
  const [stratId, setStratId] = useState<number | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [start, setStart] = useState("09:10");
  const [cost, setCost] = useState("0");
  const [busy, setBusy] = useState(false);
  const [watching, setWatching] = useState(false);
  const { data: jr, reload: reloadJob } = usePoll<{ job: Job | null }>("/backtest/job", watching ? 800 : 5000);
  const job = jr?.job ?? null;
  const running = job?.state === "running";
  const seeded = useRef<string>("");

  const strat = d?.strategies.find((s) => s.id === stratId) ?? null;
  const validStrats = (d?.strategies ?? []).filter((s) => s.valid);
  useEffect(() => {
    if (stratId == null && validStrats.length) setStratId(validStrats[0].id);
  }, [validStrats, stratId]);
  useEffect(() => {
    if (running) setWatching(true);
    else if (watching) setWatching(false);
  }, [running, watching]);

  const rows = useMemo(
    () => (d?.days ?? []).map((r) => ({ day: r.day, u: r.unds.find((x) => x.und === strat?.underlying) ?? null, all: r.unds })),
    [d, strat?.underlying],
  );
  const usable = rows.filter((r) => r.u?.usable).map((r) => r.day);
  // pre-select every usable day the first time a strategy's days are known
  useEffect(() => {
    const key = `${stratId}:${rows.length}`;
    if (!strat || seeded.current === key) return;
    seeded.current = key;
    setSel(new Set(usable.slice(0, 60)));
  }, [strat, rows.length, stratId]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (day: string) => setSel((s) => { const n = new Set(s); n.has(day) ? n.delete(day) : n.add(day); return n; });
  const pick = (n: number) => setSel(new Set(usable.slice(0, n)));

  async function run() {
    if (!strat) return;
    const c = Number(cost);
    if (!isFinite(c) || c < 0) return toast("Cost per trade must be a number, zero or more.", "err");
    setBusy(true);
    try {
      await api("/backtest/run", { method: "POST", body: { strategy_id: strat.id, days: [...sel].sort(), start_time: start, cost: c } });
      setWatching(true);
      reloadJob();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }
  async function stop() {
    try { await api("/backtest/stop", { method: "POST" }); } catch (e) { toast((e as Error).message, "err"); }
    reloadJob();
  }
  async function sample(add: boolean) {
    setBusy(true);
    try {
      if (add) await api("/backtest/sample-days", { method: "POST", body: { days: 20 } });
      else await api("/backtest/sample-days", { method: "DELETE" });
      toast(add ? "Invented days created. They are not real." : "Invented days removed");
      seeded.current = "";
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const rec = d?.recorder;
  const canRun = !!strat && sel.size > 0 && !running && !busy;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Back-test</h1>
          <p>Replay one of your saved strategies over days this terminal has recorded, using the same rules engine that runs it live. Nothing is bought or sold.</p>
        </div>
      </div>

      {error && !d && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {d?.sample && (
        <div className="notice warn wrap" style={{ marginBottom: 12 }}>
          <span>Sample mode records nothing. Create <b>invented days</b> to try this screen. They are not real prices.</span><span className="grow" />
          <button className="btn sm" disabled={busy} onClick={() => sample(true)}>Create invented days</button>
          {d.days.length > 0 && <button className="btn sm" disabled={busy} onClick={() => sample(false)}>Remove them</button>}
        </div>
      )}
      {d && !d.sample && (
        <div className={`notice ${rec?.error ? "bad" : ""}`} style={{ marginBottom: 12 }}>
          {rec?.on
            ? <span>Recording 1-minute candles of the index and its options while the terminal runs between 9:15 and 15:30. Saved so far: <b>{rec.saved.toLocaleString("en-IN")}</b> candles.{rec.error ? ` Problem: ${rec.error}` : ""} History starts today and grows every market day the terminal is left running.</span>
            : <span>The recorder is not running.</span>}
        </div>
      )}

      <div className="grid2">
        <Card title="1. What to test">
          {!validStrats.length ? (
            <Empty title="No saved strategy yet">Build and save one first, then come back. <div style={{ marginTop: 10 }}><button className="btn primary sm" onClick={() => navigate("/builder")}>Open the builder</button></div></Empty>
          ) : (
            <div className="col" style={{ gap: 12 }}>
              <div className="field">
                <label htmlFor="bt-s">Strategy</label>
                <select id="bt-s" className="select" value={stratId ?? ""} onChange={(e) => setStratId(Number(e.target.value))} disabled={running}>
                  {validStrats.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.underlying}</option>)}
                </select>
              </div>
              <div className="row" style={{ flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}>
                <div className="field">
                  <label htmlFor="bt-t">Watching starts at</label>
                  <input id="bt-t" className="input" type="time" value={start} onChange={(e) => setStart(e.target.value)} style={{ width: 124 }} disabled={running} />
                </div>
                <div className="field">
                  <label htmlFor="bt-c">Cost per trade (₹)</label>
                  <input id="bt-c" className="input" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} style={{ width: 124 }} disabled={running} />
                  <span className="hint">brokerage and charges for one trade, in and out</span>
                </div>
              </div>
              <div className="row" style={{ flexWrap: "wrap" }}>
                {!running ? <button className="btn primary" disabled={!canRun} onClick={run}>Run back-test ({sel.size} day{sel.size === 1 ? "" : "s"})</button>
                  : <button className="btn" onClick={stop}>Stop</button>}
                {running && job && <span className="hint" role="status">Day {Math.min(job.done + 1, job.total)} of {job.total}…</span>}
              </div>
              {running && job && <progress max={job.total} value={job.done} style={{ width: "100%" }} aria-label="Back-test progress" />}
            </div>
          )}
        </Card>

        <Card title="2. Which days" sub={strat ? `${strat.underlying} · ${sel.size} chosen` : undefined}>
          {!rows.length ? (
            <Empty title="No recorded days yet">
              {d?.sample ? "Create invented days above." : "Run the terminal in paper or live mode during market hours. Each day it records the index and option prices, and they show up here the next time you open this page."}
            </Empty>
          ) : (
            <>
              <div className="row" style={{ flexWrap: "wrap", marginBottom: 8, gap: 6 }}>
                <button className="btn ghost sm" onClick={() => pick(5)} disabled={running}>Latest 5</button>
                <button className="btn ghost sm" onClick={() => pick(10)} disabled={running}>Latest 10</button>
                <button className="btn ghost sm" onClick={() => pick(60)} disabled={running}>All usable</button>
                <button className="btn ghost sm" onClick={() => setSel(new Set())} disabled={running}>None</button>
              </div>
              <div className="tscroll" style={{ maxHeight: 300, overflowY: "auto" }}>
                <table className="t">
                  <thead><tr><th aria-label="Use"></th><th>Day</th><th>Recorded</th><th className="num">Coverage</th><th className="num">Options</th></tr></thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.day}>
                        <td><input type="checkbox" checked={sel.has(r.day)} onChange={() => toggle(r.day)} disabled={running || !r.u} aria-label={`Use ${dayText(r.day)}`} /></td>
                        <td>{dayText(r.day)}</td>
                        <td>{r.u ? <>{hm(r.u.from)}–{hm(r.u.to)} {r.u.usable ? <span className="chip up">Usable</span> : <span className="chip warn" title="Recording started after 9:15 or too few option prices">Partial</span>}</> : <span className="muted">no {strat?.underlying ?? "data"}</span>}</td>
                        <td className="num">{r.u ? `${r.u.coverage_pct}%` : "–"}</td>
                        <td className="num">{r.u ? r.u.contracts : "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      </div>

      {job && (
        <div style={{ marginTop: 12 }}>
          <h2 className="h2">Results · {job.strategy}{job.state === "stopped" ? " (stopped early)" : job.state === "running" ? " (running)" : ""}</h2>
          {job.state === "error" && <div className="notice bad" style={{ marginBottom: 12 }}>The back-test failed: {job.error || "unknown error"}</div>}
          <Results job={job} />
        </div>
      )}
      {!job && sess && <p className="hint" style={{ marginTop: 12 }}>Results appear here after you run a back-test.</p>}
    </>
  );
}
