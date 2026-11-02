import { useState } from "react";
import { api } from "../lib/api";
import { navigate, toast, useSession, useTitle } from "../lib/app";
import { px, signedRupees } from "../lib/format";
import { usePoll } from "../lib/store";
import { STATUS_TEXT, type CondState, type RunState, type StrategyRow, type Template } from "../lib/strategy";
import type { LegView } from "../lib/legs";
import { Empty, Modal } from "../components/ui";

interface ListResp {
  ok: boolean;
  mode: string;
  connected: boolean;
  strategies: StrategyRow[];
}

const pnlClass = (v: number | undefined) => (v == null || Math.abs(v) < 0.5 ? "flat" : v > 0 ? "up" : "down");

function Cond({ c }: { c: CondState }) {
  return (
    <li className={c.ok ? "ok" : ""}>
      <span className={`dot ${c.ok ? "ok" : ""}`} aria-label={c.ok ? "true" : "false"} />
      <span>
        {c.text}
        <small className="muted">
          {c.l != null && c.r != null ? ` (${px(c.l)} vs ${px(c.r)})` : ""}
          {c.why.length ? ` · ${c.why[0]}` : ""}
        </small>
      </span>
    </li>
  );
}

const LEG_STATUS: Record<string, string> = {
  selected: "Strike chosen, waiting for its trigger",
  waiting: "Choosing the strike",
  open: "In position",
  closed: "Finished",
  skipped: "Not traded",
  attention: "Needs attention",
};

function LegLine({ l }: { l: LegView }) {
  const reSl = l.re_sl[1] ? ` · stop re-entries ${l.re_sl[0]}/${l.re_sl[1]}` : "";
  const reTg = l.re_tgt[1] ? ` · target re-entries ${l.re_tgt[0]}/${l.re_tgt[1]}` : "";
  return (
    <div className="leg-live">
      <div><b>{l.title}</b><br /><small className="muted">{l.strike_rule}</small></div>
      <div>
        {l.label ? <>{l.label}{l.ltp != null ? ` @ ${px(l.ltp)}` : ""}<br /></> : null}
        <small className="muted">{LEG_STATUS[l.status] ?? l.status}{l.progress ? ` · ${l.progress}` : ""}{l.pending ? ` · re-entry (${l.pending.mode}) waiting` : ""}{reSl}{reTg}{l.note ? ` · ${l.note}` : ""}</small>
        {l.pick && <><br /><small className="faint">{l.pick}</small></>}
      </div>
      <span className="chip flat">{l.side === "S" ? "Sell" : "Buy"}</span>
    </div>
  );
}

function Live({ st }: { st: RunState }) {
  const legsLive = st.kind === "legs" && Array.isArray(st.legs) ? (st.legs as unknown as LegView[]) : null;
  return (
    <div className="live stack">
      {st.halt && <div className="notice bad" role="alert"><b>Action required.</b> {st.halt}</div>}
      <dl className="kv">
        <dt>Status</dt><dd style={{ fontWeight: 600 }}>{STATUS_TEXT[st.status] ?? st.status}{st.paused ? " · new entries paused" : ""}</dd>
        {st.message && <><dt>Now</dt><dd>{st.message}</dd></>}
        {!legsLive && st.legs && Object.keys(st.legs).length > 0 && <><dt>Strikes</dt><dd>{Object.entries(st.legs).map(([k, l]) => `${k === "CE" ? "Call" : "Put"} ${l.strike} @ ${px(l.ltp)}`).join(" · ")}{st.expiry ? ` · expiry ${st.expiry}` : ""}</dd></>}
        {legsLive && st.overall && <><dt>Whole strategy</dt><dd>{st.overall.reason ? `Ended: ${st.overall.reason}. ` : ""}{st.overall.floor != null ? `Profit locked: ${signedRupees(st.overall.floor)}. ` : ""}{st.overall.monitoring ? "Watching targets, trailing and re-entries." : "Monitoring of targets and re-entries is switched off (stop-loss orders stay)."}</dd></>}
        {st.spot && <><dt>Index</dt><dd>{px(st.spot.ltp)}</dd></>}
        {st.pnl && <><dt>P&amp;L today</dt><dd className={pnlClass(st.pnl.total)} style={{ fontWeight: 600 }}>{signedRupees(st.pnl.total)} <small className="muted">(booked {signedRupees(st.pnl.realised)}, open {signedRupees(st.pnl.unrealised)})</small></dd></>}
        {st.entries_done != null && <><dt>{legsLive ? "Legs entered" : "Entries"}</dt><dd>{st.entries_done}{st.max_entries ? ` of ${st.max_entries}` : " (no limit set)"}</dd></>}
      </dl>
      {legsLive && <div className="legs-live">{legsLive.map((l) => <LegLine key={l.id} l={l} />)}</div>}
      {st.rules?.map((r) => (
        <div key={r.leg}>
          <div className="lbl">{r.side === "S" ? "Sell" : "Buy"} the {r.leg === "CE" ? "Call" : "Put"}: {r.ok ? "signal true now" : "no signal"}{!r.in_window ? " (outside the entry window)" : ""}{r.ok ? "" : r.armed ? " · ready" : " · waiting to re-arm"}</div>
          {r.conds.length ? <ul className="conds">{r.conds.map((c, i) => <Cond key={i} c={c} />)}</ul> : <span className="hint">No conditions: enters when the window opens.</span>}
        </div>
      ))}
      {st.exit_rule && st.exit_rule.conds.length > 0 && (
        <div><div className="lbl">Extra exit rule</div><ul className="conds">{st.exit_rule.conds.map((c, i) => <Cond key={i} c={c} />)}</ul></div>
      )}
      {st.positions && st.positions.length > 0 && (
        <div className="tscroll">
          <table className="t">
            <thead><tr><th>Position</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Stop</th><th className="num">Now / exit</th><th className="num">P&amp;L</th><th>Status</th></tr></thead>
            <tbody>
              {st.positions.map((p, i) => (
                <tr key={i}>
                  <td>{p.side === "S" ? "Sold" : "Bought"} {p.symbol}</td>
                  <td className="num">{p.qty}</td>
                  <td className="num">{px(p.entry_price)}</td>
                  <td className="num">{px(p.sl)}{p.exchange_sl ? " ⚑" : ""}</td>
                  <td className="num">{px(p.status === "closed" ? p.exit_price : p.ltp)}</td>
                  <td className={`num ${pnlClass(p.pnl)}`}>{signedRupees(p.pnl)}</td>
                  <td>{p.status === "closed" ? p.exit_reason + (p.estimated ? " (price estimated)" : "") : p.status === "attention" ? "Needs attention" : "Open"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {st.positions.some((p) => p.exchange_sl) && <p className="hint" style={{ padding: "6px 2px" }}>⚑ = a stop-loss order is resting at the exchange.</p>}
        </div>
      )}
      <div className="log" role="log" aria-label="Strategy log">
        {[...st.log].reverse().map((e, i) => (
          <div key={i} className={e.level === "error" ? "lv-ERROR" : e.level === "warn" ? "lv-WARN" : ""}>
            <span className="faint">{e.t}</span><span>{e.level}</span><span>{e.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Card({ row, mode, connected, reload }: { row: StrategyRow; mode: string; connected: boolean; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState<null | "start" | "stop" | "delete">(null);
  const [sure, setSure] = useState(false);
  const st = row.state;
  const active = row.running;

  async function call(path: string, method: "POST" | "DELETE" = "POST", body: object = {}, ok?: string) {
    setBusy(true);
    try {
      await api(path, { method, body: method === "POST" ? body : undefined });
      if (ok) toast(ok);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
      setAsk(null);
      setSure(false);
    }
  }
  async function duplicate() {
    try {
      await api("/strategies", { method: "POST", body: { spec: { ...row.spec, name: `${row.spec.name} copy`.slice(0, 60) } } });
      toast("Duplicated");
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  const tone = st?.status === "halted" ? "down" : active ? "up" : "flat";
  return (
    <section className="panel strat">
      <div className="strat-h">
        <div className="strat-title">
          <b>{row.name}</b>
          <span className={`chip ${tone}`}>{st ? STATUS_TEXT[st.status] ?? st.status : "Not running"}</span>
          {st?.pnl && st.positions?.length ? <span className={pnlClass(st.pnl.total)} style={{ fontWeight: 600 }}>{signedRupees(st.pnl.total)}</span> : null}
        </div>
        <div className="row">
          {!active && <button className="btn primary sm" disabled={busy || !row.valid || !connected} onClick={() => setAsk("start")}>Start</button>}
          {active && <button className="btn sm" disabled={busy} onClick={() => call(`/strategies/${row.id}/pause`, "POST", { paused: !st?.paused }, st?.paused ? "Resumed" : "Paused")}>{st?.paused ? "Allow entries" : "Pause entries"}</button>}
          {active && <button className="btn danger sm" disabled={busy} onClick={() => setAsk("stop")}>Stop and exit</button>}
          <button className="btn sm" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide details" : "Details"}</button>
          {!active && <button className="btn sm" onClick={() => navigate(`${"legs" in row.spec ? "/leg-builder" : "/builder"}?id=${row.id}`)}>Edit</button>}
          {!active && <button className="btn ghost sm" onClick={duplicate}>Duplicate</button>}
          {!active && <button className="btn ghost sm" onClick={() => setAsk("delete")}>Delete</button>}
        </div>
      </div>
      {!row.valid && <div className="notice bad" style={{ margin: "0 14px 10px" }}>This strategy is not valid: {row.errors[0]}</div>}
      {!connected && row.valid && <div className="hint" style={{ padding: "0 14px 10px" }}>Connect to Kotak (Connect screen) before starting.</div>}
      {st && !open && st.message && active && <div className="hint" style={{ padding: "0 14px 12px" }}>{st.message}</div>}
      {st?.halt && !open && <div className="notice bad" style={{ margin: "0 14px 12px" }} role="alert"><b>Action required.</b> {st.halt}</div>}
      {open && (
        <div className="panel-b stack" style={{ borderTop: "1px solid var(--line)" }}>
          <div>
            <div className="lbl">What it does</div>
            <ol className="plain">{row.summary.map((l, i) => <li key={i}>{l}</li>)}</ol>
            {row.warnings.map((w, i) => <div key={i} className="notice warn" style={{ marginTop: 6 }}>{w}</div>)}
          </div>
          {st ? <Live st={st} /> : <span className="hint">Not run in this session. Its log and live values appear here once started.</span>}
        </div>
      )}

      {ask === "start" && (
        <Modal title={`Start “${row.name}”?`} onClose={() => !busy && setAsk(null)} footer={
          <>
            <button className="btn" onClick={() => setAsk(null)} disabled={busy}>Cancel</button>
            <button className="btn primary" disabled={busy || (mode === "live" && !sure)} onClick={() => call(`/strategies/${row.id}/start`, "POST", { confirm_live: sure }, "Strategy started")}>Start</button>
          </>
        }>
          <ol className="plain">{row.summary.map((l, i) => <li key={i}>{l}</li>)}</ol>
          <ul style={{ margin: "10px 0 0 18px", padding: 0, lineHeight: 1.65 }}>
            <li>It trades by itself from now on, until it finishes, you stop it, or you press Stop all.</li>
            <li>A signal that is already true now is ignored. It waits for a fresh one.</li>
            <li>If you restart the terminal, the strategy stops. Anything it left open must be closed by hand.</li>
          </ul>
          {mode === "live" ? (
            <label className="check" style={{ marginTop: 12 }}><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /><span><b>LIVE mode.</b> I understand this strategy places REAL orders on my Kotak account.</span></label>
          ) : (
            <p className="hint" style={{ marginTop: 10 }}>{mode === "paper" ? "Paper mode: real prices, simulated orders." : "Sample mode: invented prices, simulated orders."}</p>
          )}
        </Modal>
      )}
      {ask === "stop" && (
        <Modal title="Stop this strategy?" onClose={() => !busy && setAsk(null)} footer={
          <>
            <button className="btn" onClick={() => setAsk(null)} disabled={busy}>Keep running</button>
            <button className="btn danger solid" disabled={busy} onClick={() => call(`/strategies/${row.id}/stop`, "POST", {}, "Stopping: closing its positions")}>Stop and exit</button>
          </>
        }>
          <p>It takes no more entries and closes the positions it opened (only its own quantity), then stops.</p>
        </Modal>
      )}
      {ask === "delete" && (
        <Modal title={`Delete “${row.name}”?`} onClose={() => setAsk(null)} footer={
          <>
            <button className="btn" onClick={() => setAsk(null)}>Keep it</button>
            <button className="btn danger solid" disabled={busy} onClick={() => call(`/strategies/${row.id}`, "DELETE", {}, "Deleted")}>Delete</button>
          </>
        }>
          <p>This removes the saved rules. Trades it already made stay in the journal.</p>
        </Modal>
      )}
    </section>
  );
}

export function Strategies() {
  useTitle("My strategies");
  const s = useSession();
  const { data, error, reload } = usePoll<ListResp>("/strategies", 1500);
  const { data: tpl } = usePoll<{ templates: Template[] }>("/strategy-templates", 3_600_000);
  const rows = data?.strategies ?? [];
  const anyRunning = rows.some((r) => r.running);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My strategies</h1>
          <p>Algos you build leg by leg (straddles, strangles, delta strategies) or from rule blocks. Each runs on its own with its own stop-loss and limits. {anyRunning ? "Keep this terminal running; strategies stop if it closes." : ""}</p>
        </div>
        <div className="row"><button className="btn" onClick={() => navigate("/builder")}>+ Rule-block strategy</button><button className="btn primary" onClick={() => navigate("/leg-builder")}>+ New strategy</button></div>
      </div>
      {error && !data && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {s?.mode === "live" && <div className="notice warn" style={{ marginBottom: 12 }}>Live mode: a started strategy places real orders. Paper-trade a new strategy for a few weeks first.</div>}

      {rows.map((r) => <Card key={r.id} row={r} mode={data?.mode ?? "sample"} connected={data?.connected ?? false} reload={reload} />)}

      {data && !rows.length && (
        <>
          <Empty title="No strategies yet">Start from a template, then change anything you like.</Empty>
          <div className="tpl-grid">
            {(tpl?.templates ?? []).map((t) => (
              <section className="panel" key={t.id}>
                <div className="panel-h">{t.title} <span className="sub">{t.engine === "legs" ? "leg builder" : "rule blocks"}</span></div>
                <div className="panel-b stack">
                  <p>{t.blurb}</p>
                  {t.caution && <p className="hint">{t.caution}</p>}
                  <div><button className="btn" onClick={() => navigate(`${t.engine === "legs" ? "/leg-builder" : "/builder"}?template=${t.id}`)}>Use this</button></div>
                </div>
              </section>
            ))}
          </div>
        </>
      )}
      <p className="hint" style={{ marginTop: 14 }}>
        Honest limits: candles are built from the moment this terminal starts watching (nothing is back-filled), so start strategies before the first candle they use.
        Strategies do not survive a restart. Paper and sample orders ignore brokerage, taxes and queue priority.
      </p>
    </>
  );
}
