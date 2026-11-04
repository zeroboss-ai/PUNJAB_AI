import { useState } from "react";
import { api } from "../lib/api";
import { navigate, toast, useSession, useTitle } from "../lib/app";
import { dash, px, rupees, signedRupees, shortTime } from "../lib/format";
import { usePoll } from "../lib/store";
import type { AlgoSnap } from "../lib/types";
import { type StrategyRow, STATUS_TEXT } from "../lib/strategy";
import { Empty, Modal, Pnl } from "../components/ui";
import { Live, pnlClass } from "../components/LiveStrategyView";

const LEGACY_STATE: Record<string, string> = {
  WAIT_LOCK: "Waiting for the 9:15 open",
  BUILDING_RANGE: "Building the opening range",
  SCANNING: "Watching for a breakout",
  ENTRY_PENDING: "Sending the entry order",
  IN_TRADE: "In a trade",
  EXIT_PENDING: "Sending the exit order",
  HALTED: "Halted for the day",
  DONE: "Done for the day",
};

const LEGACY_PHASE: Record<string, string> = {
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
  const [confirmKillAll, setConfirmKillAll] = useState(false);
  const [askStopId, setAskStopId] = useState<number | null>(null);
  const [selectedLaunchStrat, setSelectedLaunchStrat] = useState<StrategyRow | null>(null);
  const [showLaunchModal, setShowLaunchModal] = useState(false);
  const [confirmLive, setConfirmLive] = useState(false);

  const runningStrats = (a?.running ?? []).filter((r) => r.running);
  const availableStrats = a?.available ?? [];
  const legacyRunning = !!a && a.phase !== "IDLE" && !a.finished && runningStrats.length === 0;
  const anyRunning = runningStrats.length > 0 || legacyRunning;
  const sample = s?.mode === "sample";
  const isLive = s?.mode === "live";

  const totalPnl = a?.total_pnl ?? 0;
  const realisedPnl = a?.realised_pnl ?? 0;
  const unrealisedPnl = a?.unrealised_pnl ?? 0;

  async function startStrategy(sid: number) {
    if (isLive && !confirmLive) {
      toast("Please confirm live orders before launching", "err");
      return;
    }
    setBusy(true);
    try {
      await api(`/strategies/${sid}/start`, {
        method: "POST",
        body: { confirm_live: confirmLive },
      });
      toast("Algo started successfully");
      setSelectedLaunchStrat(null);
      setShowLaunchModal(false);
      setConfirmLive(false);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function pauseStrategy(sid: number, paused: boolean) {
    setBusy(true);
    try {
      await api(`/strategies/${sid}/pause`, {
        method: "POST",
        body: { paused },
      });
      toast(paused ? "Entries paused" : "Entries resumed");
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function stopStrategy(sid: number) {
    setBusy(true);
    try {
      await api(`/strategies/${sid}/stop`, { method: "POST" });
      toast("Algo stopped");
      setAskStopId(null);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function killAll() {
    setBusy(true);
    try {
      await api("/algo/kill", { method: "POST" });
      toast("All algos stopped and exited");
      setConfirmKillAll(false);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Algo execution</h1>
          <p>
            Live automated execution dashboard. When you run strategies (pre-built or custom), they appear here numbered as Algo 1, 2, 3… with real-time triggers, live position tracking, and execution controls.
          </p>
        </div>
        <div className="row">
          <button
            className="btn primary"
            disabled={busy || (a?.needs_login && !a.logged_in)}
            onClick={() => {
              setConfirmLive(false);
              setShowLaunchModal(true);
            }}
          >
            + Launch Strategy
          </button>
          {anyRunning && (
            <button
              className="btn danger"
              disabled={busy}
              onClick={() => setConfirmKillAll(true)}
            >
              Emergency stop all
            </button>
          )}
        </div>
      </div>

      {error && !a && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {a?.manual_action_required && (
        <div className="notice bad" style={{ marginBottom: 12 }} role="alert">
          <b>Action required.</b> The algo could not confirm what is open on your account. Check your positions in the Kotak app and close anything you do not want.
        </div>
      )}
      {a?.needs_login && !a.logged_in && (
        <div className="notice" style={{ marginBottom: 12 }}>
          Connect to Kotak first. The algo engine needs broker access to stream live ticks and punch orders.
        </div>
      )}
      {sample && !anyRunning && (
        <div className="notice" style={{ marginBottom: 12 }}>
          Sample mode: started algos run on simulated market scenarios without affecting real capital.
        </div>
      )}

      {/* KPI stats bar when running */}
      {anyRunning && (
        <div className="grid4" style={{ marginBottom: 16 }}>
          <section className="panel" style={{ padding: "12px 14px" }}>
            <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Active Algos</div>
            <div style={{ fontSize: "22px", fontWeight: 700, marginTop: 4 }}>
              {runningStrats.length || (legacyRunning ? 1 : 0)} Running
            </div>
          </section>
          <section className="panel" style={{ padding: "12px 14px" }}>
            <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Total P&amp;L Today</div>
            <div className={pnlClass(totalPnl)} style={{ fontSize: "22px", fontWeight: 700, marginTop: 4 }}>
              {signedRupees(totalPnl)}
            </div>
          </section>
          <section className="panel" style={{ padding: "12px 14px" }}>
            <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Booked / Open</div>
            <div style={{ fontSize: "17px", fontWeight: 600, marginTop: 7 }}>
              <span className={pnlClass(realisedPnl)}>{signedRupees(realisedPnl)}</span>
              {" / "}
              <span className={pnlClass(unrealisedPnl)}>{signedRupees(unrealisedPnl)}</span>
            </div>
          </section>
          <section className="panel" style={{ padding: "12px 14px" }}>
            <div className="muted" style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Trading Mode</div>
            <div style={{ fontSize: "18px", fontWeight: 700, marginTop: 6 }}>
              <span className={`chip ${isLive ? "down" : "flat"}`}>
                {s?.mode?.toUpperCase() ?? "PAPER"}
              </span>
            </div>
          </section>
        </div>
      )}

      {/* Running Algos list: Algo 1, Algo 2, Algo 3... */}
      {runningStrats.length > 0 && (
        <div className="stack" style={{ gap: 16, marginBottom: 20 }}>
          {runningStrats.map((strat, idx) => {
            const st = strat.state;
            const statusLabel = STATUS_TEXT[st?.status ?? ""] ?? st?.status ?? "Running";
            return (
              <section className="panel strat" key={strat.id}>
                <div className="strat-h" style={{ borderBottom: "1px solid var(--line)", background: "var(--raised)" }}>
                  <div className="strat-title">
                    <span
                      className="chip warn"
                      style={{ fontWeight: 800, fontSize: "13px", padding: "3px 10px", borderRadius: 4 }}
                    >
                      Algo #{idx + 1}
                    </span>
                    <b style={{ fontSize: "15px" }}>{strat.name}</b>
                    <span className={`chip ${st?.status === "halted" ? "down" : strat.running ? "up" : "flat"}`}>
                      {statusLabel}
                    </span>
                    {st?.pnl && (
                      <span className={pnlClass(st.pnl.total)} style={{ fontWeight: 700, fontSize: "15px", marginLeft: 6 }}>
                        {signedRupees(st.pnl.total)}
                      </span>
                    )}
                  </div>
                  <div className="row" style={{ gap: 8 }}>
                    <button
                      className="btn sm"
                      disabled={busy}
                      onClick={() => pauseStrategy(strat.id, !st?.paused)}
                    >
                      {st?.paused ? "Resume entries" : "Pause entries"}
                    </button>
                    <button
                      className="btn danger sm"
                      disabled={busy}
                      onClick={() => setAskStopId(strat.id)}
                    >
                      Stop Algo
                    </button>
                  </div>
                </div>
                <div className="panel-b">
                  {st ? (
                    <Live st={st} />
                  ) : (
                    <div className="hint" style={{ padding: 12 }}>Waiting for initial strategy telemetry…</div>
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {/* Legacy Controller fallback view if active */}
      {legacyRunning && a && a.strategy && (
        <section className="panel strat" style={{ marginBottom: 20 }}>
          <div className="strat-h" style={{ borderBottom: "1px solid var(--line)", background: "var(--raised)" }}>
            <div className="strat-title">
              <span className="chip warn" style={{ fontWeight: 800, fontSize: "13px", padding: "3px 10px" }}>
                Algo #1
              </span>
              <b style={{ fontSize: "15px" }}>Opening-Range Breakout (Legacy)</b>
              <span className="chip up">{LEGACY_STATE[a.strategy.state] ?? a.strategy.state}</span>
              {a.risk && (
                <span className={pnlClass(a.risk.total)} style={{ fontWeight: 700, fontSize: "15px", marginLeft: 6 }}>
                  {signedRupees(a.risk.total)}
                </span>
              )}
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button
                className="btn sm"
                disabled={busy}
                onClick={() => api("/algo/pause", { method: "POST", body: { paused: !a.strategy?.entries_paused } }).then(reload)}
              >
                {a.strategy.entries_paused ? "Allow entries" : "Pause entries"}
              </button>
              <button className="btn danger sm" disabled={busy} onClick={() => setConfirmKillAll(true)}>
                Stop Algo
              </button>
            </div>
          </div>
          <div className="panel-b stack">
            <dl className="kv">
              <dt>State</dt><dd style={{ fontWeight: 600 }}>{LEGACY_STATE[a.strategy.state] ?? a.strategy.state}</dd>
              <dt>Clock</dt><dd>{a.time ?? dash}</dd>
              <dt>ATM Strike</dt><dd>{a.atm ?? dash}</dd>
              <dt>Index Spot</dt><dd>{px(a.prices?.INDEX)}</dd>
              <dt>ATM Call / Put</dt><dd>{px(a.prices?.CE)} / {px(a.prices?.PE)}</dd>
              <dt>Trades Today</dt><dd>{`${a.strategy.trades_taken} of ${a.strategy.max_trades} allowed`}</dd>
            </dl>
            {a.strategy.position ? (
              <div className="panel-b" style={{ background: "var(--raised)", borderRadius: 6 }}>
                <b>Current Position:</b> Sold {a.strategy.position.symbol} × {a.strategy.position.qty} @ {px(a.strategy.position.entry)} | SL {px(a.strategy.position.sl)} | LTP {px(a.strategy.position.ltp)} | P&amp;L <Pnl v={a.strategy.position.open_pnl} />
              </div>
            ) : null}
          </div>
        </section>
      )}

      {/* Empty State / Available Algos to Launch */}
      {!anyRunning && (
        <div className="stack" style={{ gap: 16 }}>
          <section className="panel" style={{ padding: 24, textAlign: "center" }}>
            <h2 style={{ marginBottom: 8, fontSize: "19px" }}>No active algos running right now</h2>
            <p className="muted" style={{ maxWidth: 640, margin: "0 auto 18px auto" }}>
              All pre-built strategies (Opening-Range Breakout, Short Straddle, Short Strangle, Iron Condor) and any strategies you create in My Strategies can be launched here. Each running strategy will be numbered as Algo 1, Algo 2, etc.
            </p>
            <div className="row" style={{ justifyContent: "center", gap: 12 }}>
              <button
                className="btn primary"
                onClick={() => {
                  setConfirmLive(false);
                  setShowLaunchModal(true);
                }}
              >
                + Launch a Strategy
              </button>
              <button className="btn" onClick={() => navigate("/strategies")}>
                Go to My Strategies
              </button>
            </div>
          </section>

          {availableStrats.length > 0 && (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <h3 style={{ fontSize: "16px", fontWeight: 600 }}>Ready to Launch</h3>
                <span className="muted" style={{ fontSize: "13px" }}>{availableStrats.length} strategies available</span>
              </div>
              <div className="cards">
                {availableStrats.map((strat) => (
                  <div className="card" key={strat.id} style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                        <b style={{ fontSize: "15px" }}>{strat.name}</b>
                      </div>
                      <div className="muted" style={{ fontSize: "12.5px", marginBottom: 12 }}>
                        {strat.summary?.slice(0, 2).join(" · ") || "Ready to run"}
                      </div>
                    </div>
                    <div>
                      <button
                        className="btn primary sm"
                        style={{ width: "100%" }}
                        onClick={() => {
                          setSelectedLaunchStrat(strat);
                          setConfirmLive(false);
                        }}
                      >
                        ▶ Launch as Algo #{runningStrats.length + 1}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Launch Strategy Modal */}
      {(showLaunchModal || selectedLaunchStrat) && (
        <Modal
          title={selectedLaunchStrat ? `Launch ${selectedLaunchStrat.name}` : "Launch an Algo"}
          onClose={() => {
            setSelectedLaunchStrat(null);
            setShowLaunchModal(false);
            setConfirmLive(false);
          }}
          footer={
            selectedLaunchStrat ? (
              <>
                <button
                  className="btn"
                  onClick={() => {
                    setSelectedLaunchStrat(null);
                    setConfirmLive(false);
                  }}
                >
                  Back
                </button>
                <button
                  className="btn primary"
                  disabled={busy || (isLive && !confirmLive)}
                  onClick={() => startStrategy(selectedLaunchStrat.id)}
                >
                  Start Algo
                </button>
              </>
            ) : (
              <button
                className="btn"
                onClick={() => {
                  setShowLaunchModal(false);
                  setConfirmLive(false);
                }}
              >
                Close
              </button>
            )
          }
        >
          {selectedLaunchStrat ? (
            <div className="stack">
              <p>
                You are about to launch <b>{selectedLaunchStrat.name}</b> as <b>Algo #{runningStrats.length + 1}</b>.
              </p>
              {selectedLaunchStrat.summary && (
                <div className="panel-b" style={{ background: "var(--raised)", borderRadius: 6 }}>
                  <ul style={{ paddingLeft: 18, margin: 0, fontSize: "13.5px" }}>
                    {selectedLaunchStrat.summary.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
              {isLive ? (
                <label className="checkbox" style={{ marginTop: 12, padding: "8px 10px", background: "var(--down-bg)", border: "1px solid var(--down)", borderRadius: 6, display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={confirmLive}
                    onChange={(e) => setConfirmLive(e.target.checked)}
                  />
                  <span style={{ fontSize: "13px", fontWeight: 600 }}>
                    LIVE mode: I confirm this strategy will place REAL orders on my account.
                  </span>
                </label>
              ) : (
                <p className="hint">
                  Trading mode: <b>{s?.mode?.toUpperCase() ?? "PAPER"}</b>. Orders will be simulated safely.
                </p>
              )}
            </div>
          ) : (
            <div className="stack">
              <p style={{ marginBottom: 10 }}>Select which strategy to start running:</p>
              {availableStrats.length === 0 ? (
                <Empty title="No available strategies">
                  All saved strategies are already running or none are created yet. Go to <a onClick={() => navigate("/strategies")} style={{ cursor: "pointer", textDecoration: "underline" }}>My Strategies</a> to add more.
                </Empty>
              ) : (
                <div className="stack" style={{ gap: 8, maxHeight: "50vh", overflowY: "auto" }}>
                  {availableStrats.map((strat) => (
                    <div
                      key={strat.id}
                      style={{
                        padding: "10px 14px",
                        border: "1px solid var(--line)",
                        borderRadius: 8,
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        background: "var(--panel)",
                      }}
                    >
                      <div>
                        <b>{strat.name}</b>
                        <div className="muted" style={{ fontSize: "12px" }}>
                          {strat.summary?.slice(0, 2).join(" · ")}
                        </div>
                      </div>
                      <button
                        className="btn primary sm"
                        onClick={() => setSelectedLaunchStrat(strat)}
                      >
                        Select
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Modal>
      )}

      {/* Stop Single Strategy Confirmation */}
      {askStopId !== null && (
        <Modal
          title="Stop this algo?"
          onClose={() => setAskStopId(null)}
          footer={
            <>
              <button className="btn" onClick={() => setAskStopId(null)}>
                Keep running
              </button>
              <button
                className="btn danger solid"
                disabled={busy}
                onClick={() => stopStrategy(askStopId)}
              >
                Stop Algo
              </button>
            </>
          }
        >
          <p>This will stop this specific algorithm and cancel any pending orders or exit open positions for this strategy.</p>
        </Modal>
      )}

      {/* Emergency Stop All Modal */}
      {confirmKillAll && (
        <Modal
          title="Emergency Stop All Algos?"
          onClose={() => setConfirmKillAll(false)}
          footer={
            <>
              <button className="btn" onClick={() => setConfirmKillAll(false)}>
                Keep running
              </button>
              <button
                className="btn danger solid"
                disabled={busy}
                onClick={killAll}
              >
                Emergency Stop All
              </button>
            </>
          }
        >
          <p>
            This halts <b>all active running algorithms immediately</b> and requests market exit for all open positions.
          </p>
        </Modal>
      )}
    </>
  );
}
