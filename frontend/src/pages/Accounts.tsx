import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../lib/api";
import { connectBroker, disconnectBroker, toast, useSession } from "../lib/app";
import { emptyForm, type Account, type AccountForm, type AccountsResp } from "../lib/accounts";
import { usePoll } from "../lib/store";
import { Modal } from "../components/ui";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

/** Add or edit one account. While editing, empty secret boxes mean "keep what is saved". */
function AccountDialog({ edit, onClose, onDone }: { edit: Account | null; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<AccountForm>({ ...emptyForm(), label: edit?.label ?? "", static_ip: edit?.static_ip ?? "" });
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (p: Partial<AccountForm>) => setF({ ...f, ...p });
  const secretType = show ? "text" : "password";
  const kept = (saved: boolean) => (edit && saved ? "Saved. Leave empty to keep it." : "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await api(edit ? `/accounts/${edit.id}` : "/accounts", { method: edit ? "PUT" : "POST", body: f });
      toast(edit ? "Account updated" : "Account saved");
      onDone();
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={edit ? `Edit “${edit.label}”` : "Add a Kotak Neo account"} onClose={() => !busy && onClose()} footer={
      <>
        <button className="btn" type="button" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn primary" form="acct-form" disabled={busy}>{busy ? "Saving…" : edit ? "Save changes" : "Save account"}</button>
      </>
    }>
      <form id="acct-form" className="stack" onSubmit={submit} autoComplete="off">
        <Field id="a-label" label="Account name" hint="Only for you, to tell accounts apart. For example: My account, Dad, Student 1.">
          <input id="a-label" className="input" maxLength={40} value={f.label} onChange={(e) => set({ label: e.target.value })} autoFocus />
        </Field>
        <Field id="a-key" label="API token (consumer key)" hint={kept(true) || "The token Kotak gave you for API trading. Paste it as it is."}>
          <input id="a-key" className="input" type={secretType} autoComplete="off" spellCheck={false} value={f.consumer_key} onChange={(e) => set({ consumer_key: e.target.value })} />
        </Field>
        <div className="form-grid">
          <Field id="a-ucc" label="Client ID (UCC)" hint={edit ? "Cannot be changed. Add a new account for another client ID." : "Your Kotak client code."}>
            <input id="a-ucc" className="input" autoComplete="off" spellCheck={false} maxLength={20} value={edit ? edit.ucc : f.ucc} disabled={!!edit} onChange={(e) => set({ ucc: e.target.value })} />
          </Field>
          <Field id="a-mob" label="Mobile number" hint={edit ? `${edit.mobile}. Leave empty to keep it.` : "The number registered with Kotak, 10 digits."}>
            <input id="a-mob" className="input" inputMode="tel" autoComplete="off" maxLength={20} placeholder="+91" value={f.mobile} onChange={(e) => set({ mobile: e.target.value })} />
          </Field>
        </div>
        <Field id="a-mpin" label="MPIN" hint={kept(edit?.has_mpin ?? false) || "Your Kotak MPIN (usually 6 digits)."}>
          <input id="a-mpin" className="input num" type={secretType} inputMode="numeric" autoComplete="off" maxLength={8} value={f.mpin} onChange={(e) => set({ mpin: e.target.value.replace(/\D/g, "") })} />
        </Field>
        <Field id="a-totp" label="TOTP secret (optional)" hint={<>{kept(edit?.has_totp_secret ?? false)} {" "}
          With it, the terminal makes the daily 6-digit code itself and you do not type it. <b>Anyone who gets this file and your dashboard password can then log in as you.</b> Leave empty to type the code each morning (safer).</>}>
          <input id="a-totp" className="input" type={secretType} autoComplete="off" spellCheck={false} value={f.totp_secret} onChange={(e) => set({ totp_secret: e.target.value })} />
        </Field>
        {edit?.has_totp_secret && (
          <label className="check"><input type="checkbox" checked={f.clear_totp_secret} onChange={(e) => set({ clear_totp_secret: e.target.checked })} /><span>Remove the saved TOTP secret</span></label>
        )}
        <Field id="a-ip" label="Static IP for this account (optional)" hint="Leave empty to use the one set under Static IP below. Only fill this if this account has its own: IPv4, IPv6 or both separated by a comma. In live mode the terminal refuses to connect from any other address.">
          <input id="a-ip" className="input" autoComplete="off" spellCheck={false} maxLength={90} placeholder="49.36.10.20, 2401:4900:1c2a:77::17" value={f.static_ip} onChange={(e) => set({ static_ip: e.target.value })} />
        </Field>
        <label className="check"><input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /><span>Show the secret boxes while I type (do not use while screen-sharing or recording)</span></label>
        <p className="hint">Saved on this computer only, in an encrypted file protected by your dashboard password. It is never sent anywhere except to Kotak when you connect.</p>
        {err && <div className="err" role="alert">{err}</div>}
      </form>
    </Modal>
  );
}

function ConnectDialog({ a, mode, onClose, onDone }: { a: Account; mode: string; onClose: () => void; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const needCode = !a.has_totp_secret;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await connectBroker(code.trim(), a.id, sure);
      toast(`Connected as ${a.label}`);
      onDone();
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={`Connect “${a.label}”`} onClose={() => !busy && onClose()} footer={
      <>
        <button className="btn" type="button" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn primary" form="conn-form" disabled={busy || (needCode && code.length < 6) || (mode === "live" && !sure)}>{busy ? "Connecting…" : "Connect"}</button>
      </>
    }>
      <form id="conn-form" className="stack" onSubmit={submit}>
        <p>Client ID <b>{a.ucc}</b> · {a.mobile}</p>
        {needCode ? (
          <Field id="c-code" label="Authenticator code" hint="The 6-digit code from your authenticator app for this Kotak account.">
            <input id="c-code" className="input num" inputMode="numeric" maxLength={8} autoComplete="one-time-code" autoFocus placeholder="123456" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </Field>
        ) : (
          <p className="hint">The code is made from the saved TOTP secret. Nothing to type.</p>
        )}
        {mode === "live" ? (
          <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /><span><b>LIVE mode.</b> I understand orders placed in this terminal will be REAL orders on <b>{a.label}</b> (client {a.ucc}).</span></label>
        ) : (
          <p className="hint">{mode === "paper" ? "Paper mode: real prices, simulated orders." : ""}</p>
        )}
        {err && <div className="err" role="alert">{err}</div>}
      </form>
    </Modal>
  );
}

export function AccountsPanel() {
  const s = useSession();
  const { data, error, reload } = usePoll<AccountsResp>("/accounts", 5000);
  const [dlg, setDlg] = useState<null | { kind: "add" } | { kind: "edit" | "connect" | "delete" | "disconnect"; a: Account }>(null);
  const [busy, setBusy] = useState(false);
  const rows = data?.accounts ?? [];
  const connectedAny = data?.connected ?? false;
  const close = () => setDlg(null);
  const done = () => { setDlg(null); reload(); };

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await fn();
      toast(ok);
      done();
    } catch (e) {
      toast((e as Error).message, "err");
      setDlg(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" style={{ marginBottom: 12 }}>
      <div className="panel-h">Kotak Neo accounts</div>
      <div className="panel-b stack">
        {error && !data && <div className="notice bad">{error}</div>}
        {data && !data.readable && <div className="notice bad" role="alert">{data.problem}</div>}
        {data && !data.can_connect && <div className="notice">Sample mode uses invented data and never logs in to Kotak. You can still save accounts here; to connect one, choose <b>Paper trade</b> (real prices, simulated orders) in the <b>Trading mode</b> box above.</div>}
        {data && data.can_connect && !connectedAny && rows.length > 0 && <div className="notice warn">Not connected to any account. Press Connect on one below.</div>}
        {rows.map((a) => (
          <div key={a.id} className="acct">
            <div className="acct-main">
              <div className="acct-title">
                <b>{a.label}</b>
                {a.connected && <span className="chip up">Connected</span>}
                {a.default && <span className="chip flat">Default</span>}
                {a.source === "env" && <span className="chip flat">From .env file</span>}
              </div>
              <div className="acct-sub">
                Client ID {a.ucc} · {a.mobile} · token {a.token}
                {" · "}{a.has_totp_secret ? "TOTP secret saved" : "type the code each time"}
                {a.static_ip ? ` · IP ${a.static_ip}` : ""}
              </div>
            </div>
            <div className="row wrap">
              {a.connected
                ? <button className="btn sm" disabled={busy} onClick={() => setDlg({ kind: "disconnect", a })}>Disconnect</button>
                : <button className="btn primary sm" disabled={busy || !data?.can_connect || connectedAny} title={!data?.can_connect ? "Choose Paper trade under Trading mode first" : connectedAny ? "Disconnect the current account first" : undefined} onClick={() => setDlg({ kind: "connect", a })}>Connect</button>}
              {a.editable && <button className="btn sm" disabled={busy || a.connected} onClick={() => setDlg({ kind: "edit", a })}>Edit</button>}
              {!a.default && rows.length > 1 && <button className="btn ghost sm" disabled={busy} onClick={() => run(() => api(`/accounts/${a.id}/default`, { method: "POST", body: {} }), "Default account changed")}>Make default</button>}
              {a.editable && <button className="btn ghost sm" disabled={busy || a.connected} onClick={() => setDlg({ kind: "delete", a })}>Delete</button>}
            </div>
          </div>
        ))}
        {data && !rows.length && data.readable && <p className="hint">No account saved yet. Add your Kotak Neo account to connect to your own prices and orders.</p>}
        <div className="row wrap">
          <button className="btn" disabled={!data?.readable} onClick={() => setDlg({ kind: "add" })}>+ Add an account</button>
          <span className="hint">You can save several accounts and connect one at a time. Each needs its own Kotak API access and its own registered static IP.</span>
        </div>
        <p className="hint">Details are saved on this computer only, encrypted with your dashboard password, and are never shown again after saving. Each account keeps its own trade journal. Switching accounts is blocked while a strategy or the Algo is running. Positions stay open at Kotak when you disconnect.</p>
        {s?.mode === "live" && <div className="notice warn">LIVE mode: double-check which account you connect. Orders will be real.</div>}
      </div>

      {dlg?.kind === "add" && <AccountDialog edit={null} onClose={close} onDone={done} />}
      {dlg?.kind === "edit" && <AccountDialog edit={dlg.a} onClose={close} onDone={done} />}
      {dlg?.kind === "connect" && <ConnectDialog a={dlg.a} mode={data?.mode ?? "paper"} onClose={close} onDone={done} />}
      {dlg?.kind === "disconnect" && (
        <Modal title={`Disconnect “${dlg.a.label}”?`} onClose={close} footer={
          <>
            <button className="btn" onClick={close}>Stay connected</button>
            <button className="btn danger solid" disabled={busy} onClick={() => run(disconnectBroker, "Disconnected")}>Disconnect</button>
          </>
        }>
          <p>The terminal logs out of Kotak and stops its price feed. <b>Positions and orders stay as they are at Kotak</b>; this terminal just stops watching them. It is refused while a strategy or the Algo is running.</p>
        </Modal>
      )}
      {dlg?.kind === "delete" && (
        <Modal title={`Delete “${dlg.a.label}”?`} onClose={close} footer={
          <>
            <button className="btn" onClick={close}>Keep it</button>
            <button className="btn danger solid" disabled={busy} onClick={() => run(() => api(`/accounts/${dlg.a.id}`, { method: "DELETE" }), "Account deleted")}>Delete</button>
          </>
        }>
          <p>This removes the saved token, MPIN and other details from this computer. Trades already in the journal stay.</p>
        </Modal>
      )}
    </section>
  );
}
