import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { refreshSession, toast } from "../lib/app";
import type { NetworkStatus } from "../lib/network";
import { usePoll } from "../lib/store";

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied: " + text);
  } catch {
    toast("Could not copy automatically. Select the address and copy it.", "err");
  }
}

const VERDICT: Record<NetworkStatus["verdict"], { cls: string; label: string }> = {
  ok: { cls: "up", label: "Matches" },
  partial: { cls: "warn", label: "Check IPv6/IPv4" },
  changed: { cls: "down", label: "IP changed" },
  mismatch: { cls: "down", label: "Does not match" },
  unset: { cls: "warn", label: "Not set up" },
  unchecked: { cls: "flat", label: "Not checked" },
};

function when(ts: number | null | undefined) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

function AddrRow({ label, ip, err, checked, onCheck, registered }: { label: string; ip: string; err: string; checked: boolean; onCheck: (v: boolean) => void; registered: string }) {
  return (
    <div className="ip-row">
      <span className="ip-fam">{label}</span>
      {ip ? (
        <>
          <code className="ip-val">{ip}</code>
          <button className="btn sm" onClick={() => copy(ip)} aria-label={`Copy ${label} address`}>Copy</button>
          <label className="check ip-reg"><input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} /><span>I registered this in Kotak</span></label>
          {registered && registered === ip && <span className="chip up">Registered</span>}
          {registered && registered !== ip && <span className="chip down" title={`You registered ${registered}`}>Was {registered}</span>}
        </>
      ) : (
        <span className="muted ip-none">Not found{err ? `: ${err}` : ""}</span>
      )}
    </div>
  );
}

export function StaticIpPanel() {
  const { data: st, reload } = usePoll<NetworkStatus>("/network", 20000);
  const [view, setView] = useState<"auto" | "own" | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [own, setOwn] = useState("");
  const [tick, setTick] = useState({ v4: true, v6: true });
  const asked = useRef(false);
  const seeded = useRef(false);

  const mode = view ?? st?.mode ?? "auto";

  async function call(what: string, fn: () => Promise<unknown>, okText?: string) {
    setBusy(what);
    setErr(null);
    try {
      await fn();
      if (okText) toast(okText);
      reload();
      refreshSession();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const detect = () => call("detect", () => api("/network/detect", { method: "POST", body: {} }));

  useEffect(() => {
    if (st && !asked.current && !st.detected.at) {
      asked.current = true;
      detect();
    }
    if (st && !seeded.current) {
      seeded.current = true;
      setOwn([st.own.v4, st.own.v6].filter(Boolean).join(", "));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st]);

  const v = st ? VERDICT[st.verdict] : null;
  const reg = st?.registered ?? null;
  const live = st?.app_mode === "live";

  return (
    <section className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-h">Static IP</div>
      <div className="panel-b stack">
        <p>
          SEBI's rules for API trading say the <b>same static IP</b> must be registered with Kotak and used by this terminal. Kotak rejects orders from any other address.
          Kotak accepts both <b>IPv4</b> and <b>IPv6</b>. Choose how you get your IP:
        </p>

        {st && v && (
          <div className={`notice ${st.bad || st.verdict === "unset" ? "warn" : ""}`} role="status">
            <span className={`chip ${v.cls}`} style={{ marginRight: 8, whiteSpace: "nowrap" }}>{v.label}</span>
            {st.message}
            {st.kotak_sees && <div className="hint" style={{ marginTop: 4 }}>Kotak sees this computer as <code>{st.kotak_sees}</code>. This is the exact address Kotak compares.</div>}
            {st.source === "account" && <div className="hint" style={{ marginTop: 4 }}>The account “{st.account_label}” has its own static IP saved ({st.expected.join(", ")}); that one is used while it is connected.</div>}
            {st.source === "config" && <div className="hint" style={{ marginTop: 4 }}>Using <code>static_ip</code> from config.yaml. Settings below override it.</div>}
          </div>
        )}

        <div className="seg full" role="group" aria-label="How do you get your static IP">
          <button aria-pressed={mode === "auto"} onClick={() => setView("auto")}>Option 1: Detect my IP automatically</button>
          <button aria-pressed={mode === "own"} onClick={() => setView("own")}>Option 2: I have my own static IP</button>
        </div>
        {st && <p className="hint">In use now: <b>{st.mode === "own" ? "Option 2, your own static IP" : "Option 1, the detected IP you registered"}</b>. {mode !== st.mode ? "Finish the steps below to switch to this option." : ""}</p>}

        {mode === "auto" && (
          <div className="stack">
            <ol className="plain">
              <li>This is the address the internet sees for this computer right now. Copy it.</li>
              <li>In the Kotak Neo app or website open <b>Trade API, API Dashboard</b> and add it as your static IP. Add the IPv6 one too if you want to use IPv6.</li>
              <li>Come back, tick what you registered and press <b>I registered it</b>. The terminal remembers it and checks it every 5 minutes.</li>
            </ol>
            <div className="ip-box">
              <AddrRow label="IPv4" ip={st?.detected.v4 ?? ""} err={st?.detected.errors.v4 ?? ""} checked={tick.v4} onCheck={(c) => setTick((t) => ({ ...t, v4: c }))} registered={reg?.v4 ?? ""} />
              <AddrRow label="IPv6" ip={st?.detected.v6 ?? ""} err={st?.detected.errors.v6 ?? ""} checked={tick.v6} onCheck={(c) => setTick((t) => ({ ...t, v6: c }))} registered={reg?.v6 ?? ""} />
            </div>
            <div className="row wrap">
              <button className="btn" disabled={!!busy} onClick={detect}>{busy === "detect" ? "Looking…" : "Detect again"}</button>
              <button className="btn primary" disabled={!!busy || !st || (!(tick.v4 && st.detected.v4) && !(tick.v6 && st.detected.v6))}
                onClick={() => call("confirm", () => api("/network/confirm", { method: "POST", body: { v4: tick.v4 && !!st?.detected.v4, v6: tick.v6 && !!st?.detected.v6 } }), "Saved. The terminal will check this address.")}>
                {busy === "confirm" ? "Saving…" : "I registered it in Kotak"}
              </button>
              {st?.detected.at && <span className="hint">Looked up {when(st.detected.at)}</span>}
            </div>
            {reg && <p className="hint">You confirmed {[reg.v4, reg.v6].filter(Boolean).join(" and ")} on {when(reg.at)}.</p>}
            <div className="notice warn">
              <b>Read this before relying on it.</b> Home internet addresses usually change when the router restarts or the provider decides. When that happens Kotak rejects your orders,
              including stop-losses, until you register the new address. This terminal checks every 5 minutes, shows a red warning at the top, and sends a Telegram message in live mode.
              An IPv6 address can also change on its own every day. If your provider can give you a fixed address, or you buy one, use Option 2.
            </div>
          </div>
        )}

        {mode === "own" && (
          <div className="stack">
            <ol className="plain">
              <li>Get a static IP from your internet provider, or run the terminal on a cloud server that has one.</li>
              <li>Register the <b>same address</b> in the Kotak Neo app or website: <b>Trade API, API Dashboard</b>.</li>
              <li>Type it here. IPv4, IPv6 or both, separated by a comma. Press Save.</li>
            </ol>
            <div className="field">
              <label htmlFor="own-ip">Your static IP</label>
              <input id="own-ip" className="input" autoComplete="off" spellCheck={false} maxLength={90} placeholder="49.36.10.20, 2401:4900:1c2a:77::17" value={own} onChange={(e) => setOwn(e.target.value)} />
              <span className="hint">Use the public address from your provider, not a home-network one like 192.168.x.x. The terminal must run on the computer that goes out through this address.</span>
            </div>
            <div className="row wrap">
              <button className="btn primary" disabled={!!busy || !own.trim()} onClick={() => call("save", () => api("/network", { method: "PUT", body: { mode: "own", own_ips: own } }), "Static IP saved")}>{busy === "save" ? "Saving…" : "Save"}</button>
              <button className="btn" disabled={!!busy} onClick={detect}>{busy === "detect" ? "Looking…" : "Check what this connection uses"}</button>
            </div>
            {st && (st.detected.v4 || st.detected.v6) && (
              <p className="hint">This connection is using {[st.detected.v4, st.detected.v6].filter(Boolean).map((x) => x).join(" and ")}. {st.mode === "own" && st.expected.length ? "It should match what you saved above." : ""}</p>
            )}
          </div>
        )}

        {err && <div className="err" role="alert">{err}</div>}
        <p className="hint">
          How the check works: when you connect in live mode, Kotak tells the terminal which address it sees. If that is not the address you set here the terminal logs out again and does not start, so no order can be sent from a wrong address.
          The addresses above are found with a free “what is my IP” service (ipify and similar), which sees your public IP like any website does.{live ? "" : " The strict check applies in live mode; paper and sample mode only show the status."}{" "}
          Keep in mind that a VPN changes the address you appear to have.
        </p>
      </div>
    </section>
  );
}
