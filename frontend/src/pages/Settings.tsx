import { useState } from "react";
import { api } from "../lib/api";
import { applyTheme, logout, toast, useSession, useTheme, useTitle } from "../lib/app";
import { dash, rupees } from "../lib/format";
import { usePoll } from "../lib/store";
import { AccountsPanel } from "./Accounts";
import { ModePanel } from "./ModePanel";
import { StaticIpPanel } from "./StaticIp";

interface AlertState {
  configured: boolean;
  enabled: boolean;
  sent: number;
  failed: number;
  suppressed: number;
  dropped: number;
  queued: number;
  last_ok: number | null;
  last_error: string;
  events: { strategy_events: boolean; warnings: boolean; orders: boolean; daily_summary: boolean };
}

function Alerts() {
  const { data: a, reload } = usePoll<AlertState>("/alerts", 15000);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const test = async () => {
    setBusy(true);
    setResult(null);
    try {
      const r = await api<{ ok: boolean }>("/alerts/test", { method: "POST", body: {} });
      setResult({ ok: r.ok, text: "Sent. Check Telegram: the message should arrive within a few seconds." });
      toast("Test message sent");
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
      reload();
    }
  };
  const on = (v: boolean) => (v ? "On" : "Off");
  const last = a?.last_ok ? new Date(a.last_ok * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false }) : dash;
  return (
    <section className="panel">
      <div className="panel-h"><span>Telegram alerts</span></div>
      <div className="panel-b">
        {!a ? <span className="hint">Loading…</span> : !a.configured ? (
          <>
            <p style={{ marginBottom: 8 }}><b>Not set up.</b> Alerts send strategy events, warnings and errors to your own Telegram bot, so you notice a problem without watching the screen.</p>
            <ol className="plain">
              <li>In Telegram, message <b>@BotFather</b>, send <code>/newbot</code>, and copy the token it gives you.</li>
              <li>Put it in your <code>.env</code> file as <code>TELEGRAM_BOT_TOKEN=…</code>. The token stays on this computer.</li>
              <li>Open your new bot in Telegram, press <b>Start</b>, and send it any message.</li>
              <li>Run <code>python tools/telegram_chat_id.py</code>, and put the number it prints in <code>.env</code> as <code>TELEGRAM_CHAT_ID=…</code>.</li>
              <li>Restart the terminal, then come back here and press <b>Send a test message</b>.</li>
            </ol>
          </>
        ) : (
          <dl className="kv">
            <dt>Status</dt><dd>{a.enabled ? "On: errors and warnings are sent to your Telegram chat" : "Switched off in config.yaml (alerts: enabled: false)"}</dd>
            <dt>Strategy and algo events</dt><dd>{on(a.events.strategy_events)}</dd>
            <dt>Warnings</dt><dd>{on(a.events.warnings)} <span className="hint">(errors and ACTION REQUIRED are always sent)</span></dd>
            <dt>Orders you place by hand</dt><dd>{on(a.events.orders)}</dd>
            <dt>Daily summary after 15:35</dt><dd>{on(a.events.daily_summary)}</dd>
            <dt>Sent since start</dt><dd>{a.sent}{a.failed ? `, ${a.failed} failed` : ""}{a.suppressed ? `, ${a.suppressed} repeats skipped` : ""} <span className="hint">(last delivered {last})</span></dd>
            {a.last_error && (<><dt>Last problem</dt><dd className="down">{a.last_error}</dd></>)}
          </dl>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" disabled={busy || !a?.configured} onClick={test}>{busy ? "Sending…" : "Send a test message"}</button>
          {result && <span className={result.ok ? "up" : "down"} role="status">{result.text}</span>}
        </div>
        <p className="hint" style={{ marginTop: 10 }}>Switches are in config.yaml under <code>alerts:</code> and apply after a restart. Messages contain your trades and symbols, so only use a bot and chat that are yours. Alerts are a convenience: if Telegram or your internet is down, trading is not affected, but you will not be told.</p>
      </div>
    </section>
  );
}

export function Settings() {
  useTitle("Settings");
  const s = useSession();
  const theme = useTheme();
  const modeText = s?.mode === "live" ? "Live: orders are real" : s?.mode === "paper" ? "Paper: real prices, simulated orders" : "Sample: invented prices, simulated orders";
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Paper or real trading, your Kotak accounts, static IP, risk limits and connection details. Accounts are managed here; limits are changed in config.yaml and take effect when you restart the terminal.</p>
        </div>
      </div>
      <ModePanel />
      <AccountsPanel />
      <StaticIpPanel />
      <div className="grid2">
        <section className="panel">
          <div className="panel-h">Connection</div>
          <div className="panel-b">
            <dl className="kv">
              <dt>Mode</dt><dd>{modeText}</dd>
              <dt>Broker</dt><dd>{s?.mode === "sample" ? "None (sample data)" : s?.connected ? "Kotak Neo, connected" : "Kotak Neo, not connected"}</dd>
              {s?.account && <><dt>Account</dt><dd>{s.account.label} <span className="muted">(client {s.account.ucc})</span></dd></>}
              <dt>Your IP at Kotak</dt><dd>{s?.my_ip ?? dash}</dd>
              <dt>Prices</dt><dd>{s ? `${s.feed.subscribed} instruments streaming, ${s.feed.ticks.toLocaleString("en-IN")} ticks received` : dash}</dd>
              <dt>Instrument list</dt><dd>{s?.catalog_loading ? "Loading…" : "Ready"}</dd>
            </dl>
          </div>
        </section>
        <section className="panel">
          <div className="panel-h">Risk limits</div>
          <div className="panel-b">
            <dl className="kv">
              <dt>Max loss per day</dt><dd>{s?.limits.max_daily_loss ? rupees(s.limits.max_daily_loss) : "Not set (your own rules apply)"}</dd>
              <dt>After the limit is hit</dt><dd>{!s?.limits.max_daily_loss ? "Nothing is blocked by a daily loss limit. Set risk.max_daily_loss in config.yaml to add one" : s?.limits.block_after_loss ? "New positions are blocked; closing orders still work" : "Orders are not blocked"}</dd>
              <dt>Max lots per order</dt><dd>{s?.limits.max_lots_per_order ?? dash}</dd>
              <dt>Max value per order</dt><dd>{rupees(s?.limits.max_order_value)}</dd>
              <dt>Close-out limit buffer</dt><dd>{s ? `${s.limits.squareoff_buffer_pct}% beyond the last price` : dash}</dd>
            </dl>
          </div>
        </section>
      </div>
      <Alerts />
      <section className="panel" style={{ marginTop: 12 }}>
        <div className="panel-h">Appearance and session</div>
        <div className="panel-b row" style={{ flexWrap: "wrap" }}>
          <div className="seg" role="group" aria-label="Theme">
            <button aria-pressed={theme === "dark"} onClick={() => applyTheme("dark")}>Dark</button>
            <button aria-pressed={theme === "light"} onClick={() => applyTheme("light")}>Light</button>
          </div>
          <span className="grow" />
          <button className="btn" onClick={logout}>Log out</button>
        </div>
      </section>
    </>
  );
}
