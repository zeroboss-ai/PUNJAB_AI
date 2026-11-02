import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { toast } from "../lib/app";
import { usePoll } from "../lib/store";

interface ModeInfo {
  mode: "sample" | "paper" | "live";
  started_as: string;
  connected: boolean;
  can: { paper: boolean; live: boolean };
  why: { paper?: string; live?: string };
  account: string | null;
}

/** Paper (real prices, simulated orders) or Real (orders go to Kotak). Switches the whole terminal. */
export function ModePanel() {
  const { data, reload } = usePoll<ModeInfo>("/mode", 5000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!data) return null;

  async function change(mode: "paper" | "live") {
    if (data && data.mode === mode) return;
    setBusy(true);
    setErr(null);
    try {
      await api("/mode", { method: "POST", body: { mode, confirm: "REAL" } });
      toast(mode === "live" ? "Real trading is ON. Orders go to your Kotak account." : "Paper trading is ON. Orders are simulated.", mode === "live" ? "err" : "ok");
      window.location.reload();                                    // every screen starts fresh in the new mode
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not change the mode");
      setBusy(false);
      reload();
    }
  }

  const sample = data.mode === "sample";
  const live = data.mode === "live";
  return (
    <section className="panel" style={{ marginBottom: 12 }} aria-label="Trading mode">
      <div className="panel-h"><span>Trading mode</span></div>
      <div className="panel-b">
        <div className="seg" role="group" aria-label="Trading mode">
          {sample && <button aria-pressed={true} disabled>Demo (invented prices)</button>}
          <button aria-pressed={data.mode === "paper"} disabled={busy} onClick={() => change("paper")} title={data.why.paper}>Paper trade</button>
          <button aria-pressed={live} disabled={busy} onClick={() => change("live")} title={data.why.live}>Real trade</button>
        </div>
        <p className="hint" style={{ marginTop: 10 }}>
          {live
            ? "REAL: every order you or a strategy places goes to your Kotak account with real money."
            : sample
              ? "DEMO: prices and orders are invented, nothing is connected. Choose Paper trade to use real Kotak prices with simulated orders: you then add your Kotak account below and press Connect."
              : "PAPER: real Kotak prices, but every order is simulated. Nothing reaches your broker."}
        </p>
        {!live && data.mode !== "paper" && !data.can.paper && data.why.paper && <div className="notice warn" style={{ marginTop: 8 }}>{data.why.paper}</div>}
        {!live && !data.can.live && data.why.live && <div className="notice warn" style={{ marginTop: 8 }}>{data.why.live}</div>}
        {live && !data.can.paper && data.why.paper && <div className="notice warn" style={{ marginTop: 8 }}>{data.why.paper}</div>}
        {err && <div className="notice bad" role="alert" style={{ marginTop: 8 }}>{err}</div>}
        <p className="hint" style={{ marginTop: 8 }}>
          Positions, orders and the journal are kept separately for demo, paper and real. The choice lasts until you restart; the terminal then starts in the mode set in config.yaml (or <code>--mode</code>), so real trading is never switched on by itself.
        </p>
      </div>
    </section>
  );
}
