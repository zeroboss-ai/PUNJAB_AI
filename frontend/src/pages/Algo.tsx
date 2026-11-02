import { useState } from "react";
import { api } from "../lib/api";
import { toast, useSession, useTitle } from "../lib/app";
import { dash, px, rupees, shortTime } from "../lib/format";
import { usePoll } from "../lib/store";
import type { AlgoSnap } from "../lib/types";
import { Empty, Modal, Pnl } from "../components/ui";

const STATE: Record<string, string> = {
  WAIT_LOCK: "Waiting for the 9:15 open",
  BUILDING_RANGE: "Building the opening range",
  SCANNING: "Watching for a breakout",
  ENTRY_PENDING: "Sending the entry order",
  IN_TRADE: "In a trade",
  EXIT_PENDING: "Sending the exit order",
  HALTED: "Halted for the day",
  DONE: "Done for the day",
};
const PHASE: Record<string, string> = {
  IDLE: "Not started",
  INIT: "Starting",
  WAIT_REF: "Waiting for reference prices",
  WAIT_OPEN: "Waiting for the open",
  RUNNING: "Running",
  NO_TRADE: "No trade today",
  BLOCKED: "Blocked. See the log below",
};

const WHY: Record<string, string> = {
  TIME: "Exit time reached",
  SL: "Stop-loss hit",
  STOP: "Stop-loss hit",
  KILL: "Stopped by you",
  DAILY_LOSS: "Daily loss limit",
};

export function Algo() {
  useTitle("Algo");
  const s = useSession();
  const { data: a, reload, error } = usePoll<AlgoSnap>("/algo", 1000);
  const [busy, setBusy] = useState(false);
  const [confirmKill, setConfirmKill] = useState(false);

  const running = !!a && a.phase !== "IDLE" && !a.finished;
  const sample = s?.mode === "sample";
  const st = a?.strategy;

  async function act(path: string, body?: object, done?: string) {
    setBusy(true);
    try {
      await api(path, { method: "POST", body });
      if (done) toast(done);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const limit = a?.risk?.max_daily_loss ?? a?.max_daily_loss ?? 0;
  const loss = a?.risk && limit > 0 ? Math.min(1, Math.max(0, -a.risk.total / limit)) : 0;
  const status = !a ? "Loading…" : a.finished ? (st ? STATE[st.state] ?? st.state : "Finished") : st && running && a.phase === "RUNNING" ? STATE[st.state] ?? st.state : PHASE[a.phase] ?? a.phase;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Opening-range algo</h1>
          <p>
            Sells the ATM option when the index and that option both break their first-four-minute range. {a?.instrument ? `Trading ${a.instrument}.` : ""}
          </p>
        </div>
        <div className="row">
          {!running && (
            <button className="btn primary" disabled={busy || (a?.needs_login && !a.logged_in)} onClick={() => act("/algo/start", undefined, sample ? "Demo day started" : "Algo started")}>
              {sample ? "Run a demo day" : a?.finished ? "Start a new session" : "Start algo"}
            </button>
          )}
          {running && st && (
            <button className="btn" disabled={busy} onClick={() => act("/algo/pause", { paused: !st.entries_paused })}>
              {st.entries_paused ? "Allow new entries" : "Pause new entries"}
            </button>
          )}
          {running && <button className="btn danger" disabled={busy} onClick={() => setConfirmKill(true)}>Stop and exit</button>}
        </div>
      </div>

      {error && !a && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {a?.manual_action_required && (
        <div className="notice bad" style={{ marginBottom: 12 }} role="alert">
          <b>Action required.</b> The algo could not confirm what is open on your account. Check your positions in the Kotak app and close anything you do not want. See the log below.
        </div>
      )}
      {a?.needs_login && !a.logged_in && <div className="notice" style={{ marginBottom: 12 }}>Connect to Kotak first. The algo needs a login to read prices and place orders.</div>}
      {sample && !running && (
        <div className="notice" style={{ marginBottom: 12 }}>
          Sample mode replays an invented trading day through the real algo logic, so you can watch how it behaves. No prices here are real.
        </div>
      )}

      <div className="grid2">
        <section className="panel">
          <div className="panel-h">Status</div>
          <div className="panel-b">
            <dl className="kv">
              <dt>State</dt><dd style={{ fontWeight: 600 }}>{status}</dd>
              <dt>Clock</dt><dd>{a?.time ?? dash}</dd>
              <dt>ATM strike</dt><dd>{a?.atm ?? dash}</dd>
              <dt>Index</dt><dd>{px(a?.prices?.INDEX)}</dd>
              <dt>ATM call / put</dt><dd>{px(a?.prices?.CE)} / {px(a?.prices?.PE)}</dd>
              <dt>Trades today</dt><dd>{st ? `${st.trades_taken} of ${st.max_trades} allowed` : dash}</dd>
              {a?.rules && (<><dt>Rules</dt><dd className="muted">Range {a.rules.range}; stop-loss from the {a.rules.sl_candle} candle high; entries until {a.rules.entry_cutoff}; exit by {a.rules.exit_time}</dd></>)}
            </dl>
          </div>
        </section>

        <section className="panel">
          <div className="panel-h">Day risk</div>
          <div className="panel-b">
            <div className="stats" style={{ marginBottom: 14 }}>
              <div className="stat"><span className="k">Booked</span><span className="v" style={{ fontSize: 17 }}><Pnl v={a?.risk?.realised} /></span></div>
              <div className="stat"><span className="k">Open</span><span className="v" style={{ fontSize: 17 }}><Pnl v={a?.risk?.open_pnl} /></span></div>
              <div className="stat"><span className="k">Total</span><span className="v" style={{ fontSize: 17 }}><Pnl v={a?.risk?.total} /></span></div>
            </div>
            <div className="range-bar" style={{ background: "var(--line-2)", overflow: "hidden" }} aria-hidden="true">
              <div style={{ width: `${loss * 100}%`, height: "100%", background: loss > 0.8 ? "var(--down)" : "var(--accent)", borderRadius: 3 }} />
            </div>
            <div className="range-lbl"><span>Loss used</span><span>{limit > 0 ? `Limit ${rupees(limit)}` : "No daily limit set"}</span></div>
          </div>
        </section>
      </div>

      <div className="grid2">
        <section className="panel">
          <div className="panel-h">Opening ranges</div>
          {st && Object.keys(st.ranges).length ? (
            <table className="t">
              <thead><tr><th>Instrument</th><th className="num">High</th><th className="num">Low</th></tr></thead>
              <tbody>
                {Object.entries(st.ranges).map(([k, r]) => (
                  <tr key={k}><td>{k}</td><td className="num">{px(r.high)}</td><td className="num">{px(r.low)}</td></tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty title="No range yet">The range is set from the first four one-minute candles after 9:15.</Empty>
          )}
        </section>

        <section className="panel">
          <div className="panel-h">Current trade</div>
          {st?.position ? (
            <div className="panel-b">
              <dl className="kv">
                <dt>Sold</dt><dd style={{ fontWeight: 600 }}>{st.position.symbol} × {st.position.qty}</dd>
                <dt>Entry</dt><dd>{px(st.position.entry)}</dd>
                <dt>Stop-loss</dt><dd>{px(st.position.sl)}</dd>
                <dt>Price now</dt><dd>{px(st.position.ltp)}</dd>
                <dt>Open P&amp;L</dt><dd><Pnl v={st.position.open_pnl} /></dd>
              </dl>
            </div>
          ) : (
            <Empty title="No open trade" />
          )}
        </section>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-h">Trades today</div>
        {st && st.trades.length ? (
          <div className="tscroll">
            <table className="t">
              <thead><tr><th>Sold</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Exit</th><th>Why it closed</th><th className="num">P&amp;L</th></tr></thead>
              <tbody>
                {st.trades.map((t, i) => (
                  <tr key={i}>
                    <td>{String(t.symbol ?? t.side)}</td>
                    <td className="num">{String(t.qty ?? "")}</td>
                    <td className="num">{px(t.entry as number)}</td>
                    <td className="num">{px(t.exit as number)}</td>
                    <td className="muted">{WHY[String(t.reason)] ?? String(t.reason ?? "")}</td>
                    <td className="num"><Pnl v={t.pnl as number} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No completed trades" />
        )}
      </section>

      <section className="panel">
        <div className="panel-h">Log</div>
        {a?.events?.length ? (
          <div className="log" role="log">
            {[...a.events].reverse().map((e, i) => (
              <div key={i} className={`lv-${e.level}`}>
                <span className="faint">{shortTime(e.ts)}</span>
                <span>{e.level}</span>
                <span>{e.msg}</span>
              </div>
            ))}
          </div>
        ) : (
          <Empty title="Nothing logged yet" />
        )}
      </section>

      {confirmKill && (
        <Modal
          title="Stop the algo and exit?"
          onClose={() => setConfirmKill(false)}
          footer={
            <>
              <button className="btn" onClick={() => setConfirmKill(false)}>Keep running</button>
              <button className="btn danger solid" disabled={busy} onClick={async () => { await act("/algo/kill", undefined, "Algo stopped"); setConfirmKill(false); }}>Stop and exit</button>
            </>
          }
        >
          <p>The algo closes any trade it has open and takes no more entries today.</p>
        </Modal>
      )}
    </>
  );
}
