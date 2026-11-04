import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { applyTheme, logout, navigate, toast, useSession, useTheme } from "../lib/app";
import { clock, pct, px, signed, tone } from "../lib/format";
import { useQuote, useWant, wsState } from "../lib/quotes";
import { useStore } from "../lib/store";
import { IconAlert, IconLogout, IconMoon, IconSun, Logo } from "./Icons";
import { InstallPwaButton } from "./InstallPwa";
import { MobileNav, useIsPhone } from "./MobileNav";
import { isActive, NAV } from "./nav";
import { Modal } from "./ui";

const DEFAULT_INDICES = [
  { label: "NIFTY 50", key: "nse_cm|Nifty 50" },
  { label: "NIFTY BANK", key: "nse_cm|Nifty Bank" },
  { label: "SENSEX", key: "bse_cm|SENSEX" },
  { label: "NIFTY FIN SERVICE", key: "nse_cm|Nifty Fin Service" },
];

const DEFAULT_COMMODITIES = [
  { label: "Gold", key: "mcx_fo|104765" },
  { label: "Silver", key: "mcx_fo|105506" },
  { label: "Crude Oil", key: "mcx_fo|106247" },
  { label: "Natural Gas", key: "mcx_fo|106985" },
];

function TapeItem({ k, label }: { k: string; label: string }) {
  const q = useQuote(k);
  const t = tone(q?.c);
  return (
    <div className="tape-item" title={label}>
      <span className="tape-name">{label}</span>
      <span className="tape-val">
        <span>{q ? px(q.l) : "–"}</span>
        <small className={t}>{q ? `${signed(q.c)} ${pct(q.p)}` : ""}</small>
      </span>
    </div>
  );
}

function Clock() {
  const [t, setT] = useState(clock());
  useEffect(() => {
    const id = window.setInterval(() => setT(clock()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="muted hide-s" title="India time">{t} IST</span>;
}

function StatusPills() {
  const s = useSession();
  const ws = useStore(wsState);
  if (!s) return null;
  const age = s.feed.age_s;
  const fresh = age != null && age < 5;
  const feed = !ws.open ? { cls: "bad", text: "Reconnecting" } : fresh ? { cls: "ok", text: "Prices live" } : s.feed.connected || s.mode === "sample" ? { cls: "warn", text: age == null ? "Waiting for prices" : "Prices quiet" } : { cls: "bad", text: "No prices" };
  const mkt = s.market.state === "open" ? "ok" : s.market.state === "preopen" ? "warn" : "";
  return (
    <>
      <span className="pill hide-s" title="Based on the clock only; NSE holidays are not known to the terminal."><span className={`dot ${mkt}`} />{s.market.label}</span>
      {s.market_mcx && (
        <span className="pill hide-s" title="Commodity (MCX) hours by the clock only. MCX holidays and special sessions are not known to the terminal. Evening close is 23:30 in US summer time and 23:55 in winter.">
          <span className={`dot ${s.market_mcx.state === "open" ? "ok" : ""}`} />{s.market_mcx.label}
        </span>
      )}
      <span className="pill" title={age != null ? `Last price ${age}s ago` : ""}><span className={`dot ${feed.cls}`} />{feed.text}</span>
      <span className={`pill ${s.mode === "live" ? "live" : s.mode === "sample" ? "sample" : ""}`}>{s.mode === "live" ? "LIVE" : s.mode === "paper" ? "Paper" : "Sample"}</span>
      {s.accounts && s.accounts.length > 0 ? (
        s.accounts.map((a) => (
          <span key={a.id} className="pill" title={`Connected as ${a.label} (${a.ucc})`}>
            {a.label}
          </span>
        ))
      ) : s.account ? (
        <span className="pill" title={`Connected as ${s.account.label}`}>
          {s.account.label}
        </span>
      ) : null}
    </>
  );
}

export function PanicButton() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const s = useSession();
  async function go() {
    setBusy(true);
    try {
      const r = await api<{ cancelled: number; squareoff: { ok: boolean }[]; errors: string[] }>("/panic", { method: "POST" });
      const closed = r.squareoff.filter((x) => x.ok).length;
      const failed = r.squareoff.length - closed + r.errors.length;
      toast(`Stopped. ${r.cancelled} order${r.cancelled === 1 ? "" : "s"} cancelled, ${closed} position${closed === 1 ? "" : "s"} closing.${failed ? ` ${failed} step${failed === 1 ? "" : "s"} failed - check Orders and Positions.` : ""}`, failed ? "err" : "ok");
      setOpen(false);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button className="btn danger sm" onClick={() => setOpen(true)} title="Cancel all orders, stop the algo and close all positions">
        <IconAlert />
        <span className="hide-s">Stop all</span>
      </button>
      {open && (
        <Modal
          title="Stop everything?"
          onClose={() => !busy && setOpen(false)}
          footer={
            <>
              <button className="btn" onClick={() => setOpen(false)} disabled={busy}>Keep trading</button>
              <button className="btn danger solid" onClick={go} disabled={busy}>{busy ? "Stopping…" : "Stop all"}</button>
            </>
          }
        >
          <p style={{ marginBottom: 10 }}>This will, in order:</p>
          <ol style={{ margin: "0 0 10px 18px", padding: 0, lineHeight: 1.7 }}>
            <li>Cancel every open order</li>
            <li>Stop the algo (it will not enter again today)</li>
            <li>Close every open position with a marketable limit order{s ? ` (${s.limits.squareoff_buffer_pct}% beyond the last price)` : ""}</li>
          </ol>
          <p className="hint">{s?.mode === "live" ? "These are real orders on your Kotak account." : "Nothing here touches a real account in this mode."}</p>
        </Modal>
      )}
    </>
  );
}

export function Shell({ path, children }: { path: string; children: ReactNode }) {
  const s = useSession();
  const theme = useTheme();
  const phone = useIsPhone();

  const indices = s?.indices && s.indices.length > 0 ? s.indices : DEFAULT_INDICES;
  const commodities = s?.commodities && s.commodities.length > 0 ? s.commodities : DEFAULT_COMMODITIES;

  useWant([
    ...indices.map((i) => i.key),
    ...commodities.map((c) => c.key),
  ]);

  const mcxOpen = s?.market_mcx?.state === "open";
  const nseOpen = s?.market?.state === "open";
  // During evening session when equity is closed but MCX is open, show actively trading commodities on topbar
  const tapeItems = (!nseOpen && mcxOpen) ? commodities.slice(0, 4) : indices.slice(0, 4);
  return (
    <div className="app">
      {!phone && <nav className="rail" aria-label="Main">
        <a className="brand" href="/" onClick={(e) => { e.preventDefault(); navigate("/"); }}>
          <Logo />
          <span>PUNJAB AI</span>
        </a>
        {NAV.map((g) => (
          <div key={g.group} style={{ display: "contents" }}>
            <div className="nav-group">{g.group}</div>
            {g.items.map((it) =>
              it.soon ? (
                <div key={it.path} className="nav-item soon" title={`${it.label} - coming in the next release`}>
                  {it.icon}
                  <span>{it.label}</span>
                  <span className="tag">Soon</span>
                </div>
              ) : (
                <a key={it.path} href={it.path} className="nav-item" aria-current={isActive(it, path) ? "page" : undefined} title={it.label}
                  onClick={(e) => { e.preventDefault(); navigate(it.path); }}>
                  {it.icon}
                  <span>{it.label}</span>
                </a>
              ),
            )}
          </div>
        ))}
        <div className="rail-foot">Your keys stay on this computer.</div>
      </nav>}
      <div className="main">
        {s?.mode === "sample" && <div className="banner sample">Sample data: prices are invented and orders are simulated. Nothing here touches your broker.</div>}
        {s?.mode === "paper" && <div className="banner paper">Paper mode: real Kotak prices, simulated orders.</div>}
        {s?.mode === "live" && <div className="banner live">LIVE: orders go to your real Kotak account.</div>}
        {s?.ip_alert && (
          <div className="banner ipwarn" role="alert">
            <b>Static IP:</b> <span>{s.ip_alert}</span>
            <a href="/settings" onClick={(e) => { e.preventDefault(); navigate("/settings"); }}>Fix in Settings</a>
          </div>
        )}
        <header className="topbar">
          {phone ? (
            <div className="mobile-brand" onClick={() => navigate("/")} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", flex: "none" }}>
              <Logo />
              <b style={{ fontSize: "14px", letterSpacing: "0.02em" }}>PUNJAB AI</b>
            </div>
          ) : (
            <div className="tape" aria-label="Index prices">
              {tapeItems.map((i) => <TapeItem key={i.key} k={i.key} label={i.label} />)}
            </div>
          )}
          <div className="topbar-right">
            <Clock />
            <StatusPills />
            <InstallPwaButton />
            <PanicButton />
            <button className="btn ghost icon sm" onClick={() => applyTheme(theme === "dark" ? "light" : "dark")} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Switch theme">
              {theme === "dark" ? <IconSun /> : <IconMoon />}
            </button>
            <button className="btn ghost icon sm hide-s" onClick={logout} aria-label="Log out" title="Log out"><IconLogout /></button>
          </div>
        </header>
        <main className="page">{children}</main>
      </div>
      {phone && <MobileNav path={path} />}
    </div>
  );
}
