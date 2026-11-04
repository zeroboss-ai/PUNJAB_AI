import { useState } from "react";
import { api } from "../lib/api";
import { navigate, toast, useSession, useTitle } from "../lib/app";
import { px, signedRupees } from "../lib/format";
import { usePoll } from "../lib/store";
import { STATUS_TEXT, type StrategyRow, type Template } from "../lib/strategy";
import { Empty, Modal } from "../components/ui";
import { Live, pnlClass } from "../components/LiveStrategyView";

interface ListResp {
  ok: boolean;
  mode: string;
  connected: boolean;
  strategies: StrategyRow[];
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
  const [showTemplates, setShowTemplates] = useState(false);
  const rows = data?.strategies ?? [];
  const runningRows = rows.filter((r) => r.running);
  const anyRunning = runningRows.length > 0;
  const totalPnl = runningRows.reduce((sum, r) => sum + (r.state?.pnl?.total ?? 0), 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>My strategies</h1>
          <p>Algos you build leg by leg (straddles, strangles, delta strategies) or from rule blocks. Each runs on its own with its own stop-loss and limits. {anyRunning ? "Keep this terminal running; strategies stop if it closes." : ""}</p>
        </div>
        <div className="row">
          <button className="btn" onClick={() => setShowTemplates(true)}>
            + Template library
          </button>
          <button className="btn" onClick={() => navigate("/builder")}>+ Rule-block strategy</button>
          <button className="btn primary" onClick={() => navigate("/leg-builder")}>+ New strategy</button>
        </div>
      </div>
      {error && !data && <div className="notice bad" style={{ marginBottom: 12 }}>{error}</div>}
      {s?.mode === "live" && <div className="notice warn" style={{ marginBottom: 12 }}>Live mode: a started strategy places real orders. Paper-trade a new strategy for a few weeks first.</div>}

      {anyRunning && (
        <div className="notice" style={{ marginBottom: 14, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div>
            ⚡ <b>{runningRows.length} {runningRows.length === 1 ? "Algo" : "Algos"} running live</b> · Net P&amp;L today:{" "}
            <span className={pnlClass(totalPnl)} style={{ fontWeight: 700 }}>
              {signedRupees(totalPnl)}
            </span>
          </div>
          <button className="btn sm primary" onClick={() => navigate("/algo")}>
            View in Algo Dashboard →
          </button>
        </div>
      )}

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

      {showTemplates && (
        <Modal
          title="Pre-built Strategy Templates"
          onClose={() => setShowTemplates(false)}
          footer={
            <button className="btn" onClick={() => setShowTemplates(false)}>
              Close
            </button>
          }
        >
          <p style={{ marginBottom: 14 }}>
            Select any pre-built template to customize its legs, rules, underlyings, or targets in the strategy builder:
          </p>
          <div className="tpl-grid">
            {(tpl?.templates ?? []).map((t) => (
              <section className="panel" key={t.id}>
                <div className="panel-h">
                  {t.title} <span className="sub">{t.engine === "legs" ? "leg builder" : "rule blocks"}</span>
                </div>
                <div className="panel-b stack">
                  <p>{t.blurb}</p>
                  {t.caution && <p className="hint">{t.caution}</p>}
                  <div>
                    <button
                      className="btn primary sm"
                      onClick={() => {
                        setShowTemplates(false);
                        navigate(`${t.engine === "legs" ? "/leg-builder" : "/builder"}?template=${t.id}`);
                      }}
                    >
                      Use this template
                    </button>
                  </div>
                </div>
              </section>
            ))}
          </div>
        </Modal>
      )}

      <p className="hint" style={{ marginTop: 14 }}>
        Honest limits: candles are built from the moment this terminal starts watching (nothing is back-filled), so start strategies before the first candle they use.
        Strategies do not survive a restart. Paper and sample orders ignore brokerage, taxes and queue priority.
      </p>
    </>
  );
}
