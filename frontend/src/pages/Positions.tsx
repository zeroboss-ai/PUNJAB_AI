import { useState } from "react";
import { api } from "../lib/api";
import { openTicket, toast, useTitle } from "../lib/app";
import { dash, lotsLabel, px, rupees } from "../lib/format";
import { openChart } from "../lib/chart";
import { usePoll } from "../lib/store";
import type { Funds, Listing, PositionRow } from "../lib/types";
import { IconChart } from "../components/Icons";
import { Empty, Modal, Pnl } from "../components/ui";

interface Preview {
  key: string;
  label: string;
  side: "B" | "S" | null;
  qty: number;
  price: number | null;
  product: string;
  error: string | null;
}

export function Positions() {
  useTitle("Positions");
  const pos = usePoll<Listing<PositionRow>>("/positions", 1000);
  const funds = usePoll<{ ok: boolean; funds: Funds | null }>("/funds", 8000);
  const [preview, setPreview] = useState<Preview[] | null>(null);
  const [scope, setScope] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);

  const rows = pos.data?.rows ?? [];
  const t = pos.data?.totals;
  const f = funds.data?.funds;
  const open = rows.filter((r) => r.open);

  async function askClose(keys: string[] | null) {
    try {
      const r = await api<{ orders: Preview[] }>("/squareoff/preview", { method: "POST", body: { keys } });
      if (r.orders.length === 0) return toast("Nothing to close", "info");
      setScope(keys);
      setPreview(r.orders);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  async function confirmClose() {
    setBusy(true);
    try {
      const keys = scope ?? preview?.filter((p) => !p.error).map((p) => p.key) ?? null;
      const r = await api<{ results: { key: string; ok: boolean; error?: string }[] }>("/squareoff", { method: "POST", body: { keys } });
      const bad = r.results.filter((x) => !x.ok);
      toast(bad.length ? `${r.results.length - bad.length} closing, ${bad.length} failed: ${bad[0].error ?? ""}` : `Closing ${r.results.length} position${r.results.length === 1 ? "" : "s"}`, bad.length ? "err" : "ok");
      setPreview(null);
      pos.reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const open_ = (p: PositionRow, side: "B" | "S") => openTicket({ key: p.key, label: p.label, side, lot: p.lot, tick: 0.05, product: p.product });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Positions</h1>
          <p>Profit and loss update with every price tick.</p>
        </div>
        <button className="btn danger" disabled={open.length === 0} onClick={() => askClose(null)}>Close all positions</button>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-b stats">
          <div className="stat"><span className="k">Day P&amp;L</span><span className="v"><Pnl v={t?.pnl} /></span></div>
          <div className="stat"><span className="k">Booked</span><span className="v" style={{ fontSize: 17 }}><Pnl v={t?.realised} /></span></div>
          <div className="stat"><span className="k">Open</span><span className="v" style={{ fontSize: 17 }}><Pnl v={t?.unrealised} /></span></div>
          <div className="stat"><span className="k">{f?.simulated ? "Practice cash left" : "Available cash"}</span><span className="v" style={{ fontSize: 17 }}>{rupees(f?.available)}</span></div>
          <div className="stat"><span className="k">Margin in use</span><span className="v" style={{ fontSize: 17 }}>{rupees(f?.used)}</span></div>
        </div>
        {f?.note && <div className="hint" style={{ padding: "0 14px 12px" }}>{f.note}</div>}
      </section>

      {pos.data && !pos.data.ok && (
        <div className="notice bad" style={{ marginBottom: 12 }}>
          {pos.data.reason === "not_connected" ? "Not connected to Kotak yet." : "Kotak’s positions could not be read just now. Do not rely on the numbers below until this message goes away."}
        </div>
      )}

      <section className="panel">
        {rows.length === 0 ? (
          <Empty title="No positions today">Trades you take today, manual or from the algo, are listed here.</Empty>
        ) : (
          <div className="tscroll">
            <table className="t">
              <thead>
                <tr><th>Instrument</th><th className="num">Net qty</th><th className="num">Avg price</th><th className="num">LTP</th><th className="num">Bought / Sold</th><th className="num">P&amp;L</th><th /></tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.key + p.product} className={p.open ? "" : "dim"}>
                    <td><div className="cell-name"><b>{p.label}</b><span>{p.product}{!p.open ? " · closed" : ""}</span></div></td>
                    <td className="num" style={{ fontWeight: 600 }}>{p.net_qty === 0 ? "0" : lotsLabel(p.net_qty, p.lot)}</td>
                    <td className="num">{p.open ? px(p.avg_price) : dash}</td>
                    <td className="num">{px(p.ltp)}</td>
                    <td className="num muted">{p.buy_qty ? `${p.buy_qty} @ ${px(p.buy_avg)}` : dash} / {p.sell_qty ? `${p.sell_qty} @ ${px(p.sell_avg)}` : dash}</td>
                    <td className="num" style={{ fontWeight: 600 }}><Pnl v={p.pnl} /></td>
                    <td>
                      <div className="acts hover-acts">
                        <button className="btn ghost icon sm" onClick={() => openChart(p.key, p.label)} aria-label={`Chart ${p.label}`} title="Chart"><IconChart /></button>
                        <button className="bs b" onClick={() => open_(p, "B")} aria-label={`Buy more ${p.label}`}>B</button>
                        <button className="bs s" onClick={() => open_(p, "S")} aria-label={`Sell ${p.label}`}>S</button>
                        {p.open && <button className="btn sm danger" onClick={() => askClose([p.key])}>Close</button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {preview && (
        <Modal
          title={scope ? "Close this position?" : `Close ${preview.length} position${preview.length === 1 ? "" : "s"}?`}
          onClose={() => !busy && setPreview(null)}
          footer={
            <>
              <button className="btn" onClick={() => setPreview(null)} disabled={busy}>Cancel</button>
              <button className="btn danger solid" onClick={confirmClose} disabled={busy || preview.every((p) => p.error)}>{busy ? "Sending…" : "Send closing orders"}</button>
            </>
          }
        >
          <p className="hint" style={{ marginBottom: 10 }}>Each position is closed with a limit order set slightly beyond the last price so it fills right away.</p>
          <table className="t">
            <thead><tr><th>Instrument</th><th>Action</th><th className="num">Qty</th><th className="num">Limit</th></tr></thead>
            <tbody>
              {preview.map((p) => (
                <tr key={p.key}>
                  <td>{p.label}</td>
                  <td>{p.error ? <span className="err">{p.error}</span> : <span className={`chip ${p.side === "B" ? "buy" : "sell"}`}>{p.side === "B" ? "Buy" : "Sell"}</span>}</td>
                  <td className="num">{p.qty}</td>
                  <td className="num">{px(p.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
    </>
  );
}
