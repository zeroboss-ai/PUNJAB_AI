import { useState } from "react";
import { api } from "../lib/api";
import { openTicket, toast, useTitle } from "../lib/app";
import { dash, int, lotsLabel, px, shortTime } from "../lib/format";
import { usePoll } from "../lib/store";
import type { Listing, OrderRow, TradeRow } from "../lib/types";
import { Empty } from "../components/ui";
import { IconEdit, IconX } from "../components/Icons";
import { StatusChip } from "./Dashboard";

const OT: Record<string, string> = { L: "Limit", MKT: "Market", SL: "SL", "SL-M": "SL-M" };

function priceText(o: OrderRow) {
  if (o.order_type === "MKT") return "Market";
  if (o.order_type === "SL-M") return `trigger ${px(o.trigger)}`;
  if (o.order_type === "SL") return `${px(o.price)} · trigger ${px(o.trigger)}`;
  return px(o.price);
}

export function Orders() {
  useTitle("Orders");
  const [tab, setTab] = useState<"orders" | "trades">("orders");
  const ord = usePoll<Listing<OrderRow>>("/orders", 1500);
  const trd = usePoll<Listing<TradeRow>>("/trades", 3000);
  const [busyId, setBusyId] = useState<string | null>(null);

  const rows = [...(ord.data?.rows ?? [])].sort((a, b) => Number(b.open) - Number(a.open));
  const trades = trd.data?.rows ?? [];
  const openCount = rows.filter((r) => r.open).length;

  async function cancel(o: OrderRow) {
    setBusyId(o.id);
    try {
      await api(`/orders/${encodeURIComponent(o.id)}`, { method: "DELETE" });
      toast(`Cancelled ${o.label}`);
      ord.reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusyId(null);
    }
  }

  const unreadable = ord.data && !ord.data.ok;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Orders</h1>
          <p>Today’s orders and fills. Open orders can be changed or cancelled.</p>
        </div>
      </div>
      <section className="panel">
        <div className="tabs" role="tablist">
          <button role="tab" className="tab" aria-selected={tab === "orders"} onClick={() => setTab("orders")}>Orders<span className="count">{rows.length}{openCount ? ` · ${openCount} open` : ""}</span></button>
          <button role="tab" className="tab" aria-selected={tab === "trades"} onClick={() => setTab("trades")}>Trades<span className="count">{trades.length}</span></button>
        </div>

        {unreadable && (
          <div className="notice bad" style={{ margin: 12 }}>
            {ord.data?.reason === "not_connected" ? "Not connected to Kotak yet." : "Kotak’s order book could not be read just now. What you see below may be out of date; it refreshes every few seconds."}
          </div>
        )}

        {tab === "orders" ? (
          rows.length === 0 ? (
            <Empty title="No orders today">Orders you place from the Watchlist, Positions or the algo show up here.</Empty>
          ) : (
            <div className="tscroll">
              <table className="t">
                <thead>
                  <tr><th>Time</th><th>Instrument</th><th>Side</th><th className="num">Filled / Qty</th><th className="num">Price</th><th className="num">Avg fill</th><th>Status</th><th /></tr>
                </thead>
                <tbody>
                  {rows.map((o) => (
                    <tr key={o.id} className={o.status === "CANCELLED" || o.status === "REJECTED" ? "dim" : ""}>
                      <td className="muted">{shortTime(o.time)}</td>
                      <td>
                        <div className="cell-name">
                          <b>{o.label || o.symbol}</b>
                          <span>{o.product} · {OT[o.order_type] ?? o.order_type}{o.tag && o.tag !== "manual" ? ` · ${o.tag}` : ""}</span>
                        </div>
                      </td>
                      <td><span className={`chip ${o.side === "B" ? "buy" : "sell"}`}>{o.side === "B" ? "Buy" : "Sell"}</span></td>
                      <td className="num">{int(o.filled)} / {lotsLabel(o.qty, o.lot)}</td>
                      <td className="num">{priceText(o)}</td>
                      <td className="num">{o.avg_price ? px(o.avg_price) : dash}</td>
                      <td>
                        <StatusChip s={o.status} />
                        {o.reason && <div className="hint" style={{ maxWidth: 260, marginTop: 3 }}>{o.reason}</div>}
                      </td>
                      <td>
                        {o.open && (
                          <div className="acts">
                            <button className="btn sm" onClick={() => openTicket({ key: o.key, label: o.label || o.symbol, side: o.side, lot: o.lot, tick: 0.05, modify: o })}><IconEdit />Modify</button>
                            <button className="btn sm danger" disabled={busyId === o.id} onClick={() => cancel(o)}><IconX />Cancel</button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : trades.length === 0 ? (
          <Empty title="No trades yet">Fills appear here as orders complete.</Empty>
        ) : (
          <div className="tscroll">
            <table className="t">
              <thead>
                <tr><th>Time</th><th>Instrument</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th><th>Product</th><th>Order</th></tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={t.trade_id || t.order_id + i}>
                    <td className="muted">{shortTime(t.time)}</td>
                    <td><b style={{ fontWeight: 600 }}>{t.label || t.symbol}</b></td>
                    <td><span className={`chip ${t.side === "B" ? "buy" : "sell"}`}>{t.side === "B" ? "Buy" : "Sell"}</span></td>
                    <td className="num">{int(t.qty)}</td>
                    <td className="num">{px(t.price)}</td>
                    <td>{t.product}</td>
                    <td className="muted">{t.order_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
