import { useState, type FormEvent } from "react";
import { Logo } from "../components/Icons";
import { connectBroker, login, navigate, toast, useAuth, useSession, useTitle } from "../lib/app";
import type { AccountsResp } from "../lib/accounts";
import { usePoll } from "../lib/store";

export function LoginPage() {
  useTitle("Log in");
  const { mode } = useAuth();
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !pw) return;
    setBusy(true);
    setErr(null);
    try {
      await login(pw);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="panel gate-card" onSubmit={submit}>
        <div className="brand">
          <Logo size={44} />
          <span>PUNJAB AI</span>
        </div>
        <div>
          <h1>Log in</h1>
          <p className="hint" style={{ marginTop: 4 }}>
            {mode === "sample"
              ? "Sample mode. The password for this run is printed in the window where you started the terminal."
              : "Use the DASHBOARD_PASSWORD from your .env file."}
          </p>
        </div>
        <div className="field">
          <label htmlFor="pw">Password</label>
          <input id="pw" className="input" type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
        </div>
        {err && <div className="err" role="alert">{err}</div>}
        <button className="btn primary" style={{ height: 38 }} disabled={busy || !pw}>{busy ? "Checking…" : "Log in"}</button>
      </form>
    </div>
  );
}

/** Shown in place of every page until the Kotak session is established (paper and live modes). */
export function ConnectGate() {
  useTitle("Connect to Kotak");
  const s = useSession();
  const { data, reload } = usePoll<AccountsResp>("/accounts", 4000);
  const [pick, setPick] = useState("");
  const [code, setCode] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rows = data?.accounts ?? [];
  const chosen = rows.find((a) => a.id === (pick || data?.default || "")) ?? (rows.length === 1 ? rows[0] : undefined);
  const live = s?.mode === "live";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !chosen) return;
    setBusy(true);
    setErr(null);
    try {
      await connectBroker(code.trim(), chosen.id, sure);
      toast(`Connected as ${chosen.label}`);
    } catch (e) {
      setErr((e as Error).message);
      reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate" style={{ minHeight: "60vh", background: "none" }}>
      <form className="panel gate-card" onSubmit={submit}>
        <div>
          <h1>Connect to Kotak Neo</h1>
          <p className="hint" style={{ marginTop: 6 }}>
            Choose the account to trade with. Its saved details are used; you only type the 6-digit code from your authenticator app, once a day.
          </p>
        </div>
        {data && !data.readable && <div className="notice bad" role="alert">{data.problem}</div>}
        {data && data.readable && !rows.length && (
          <div className="notice">
            No account is saved yet. <a href="/settings" onClick={(e) => { e.preventDefault(); navigate("/settings"); }}>Add your Kotak Neo account in Settings</a> (token, client ID, mobile, MPIN), then come back here.
          </div>
        )}
        {rows.length > 0 && (
          <div className="field">
            <label htmlFor="acct">Account</label>
            <select id="acct" className="select" value={chosen?.id ?? ""} onChange={(e) => { setPick(e.target.value); setSure(false); }}>
              {rows.length > 1 && !chosen && <option value="">Choose an account…</option>}
              {rows.map((a) => <option key={a.id} value={a.id}>{a.label} · client {a.ucc}</option>)}
            </select>
            <span className="hint"><a href="/settings" onClick={(e) => { e.preventDefault(); navigate("/settings"); }}>Add or edit accounts</a></span>
          </div>
        )}
        {chosen && !chosen.has_totp_secret && (
          <div className="field">
            <label htmlFor="totp">Authenticator code</label>
            <input id="totp" className="input num" inputMode="numeric" maxLength={8} autoComplete="one-time-code" autoFocus placeholder="123456" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </div>
        )}
        {chosen?.has_totp_secret && <span className="hint">The code is made from the TOTP secret saved for this account.</span>}
        {live && chosen && (
          <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /><span><b>LIVE mode.</b> Orders placed in this terminal will be REAL orders on <b>{chosen.label}</b> (client {chosen.ucc}).</span></label>
        )}
        {err && <div className="err" role="alert">{err}</div>}
        <button className="btn primary" style={{ height: 38 }} disabled={busy || !chosen || (!chosen.has_totp_secret && code.length < 6) || (live && !sure)}>{busy ? "Connecting…" : "Connect"}</button>
      </form>
    </div>
  );
}
