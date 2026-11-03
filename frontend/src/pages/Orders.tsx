import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { openTicket, toast, useSession, useTitle } from "../lib/app";
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

function getExchange(key: string, label?: string): "NSE" | "BSE" | "MCX" {
  const k = (key || "").toLowerCase();
  if (k.startsWith("mcx")) return "MCX";
  if (k.startsWith("bse")) return "BSE";
  if (k.startsWith("nse")) return "NSE";
  const lbl = (label || "").toUpperCase();
  if (lbl.includes("GOLD") || lbl.includes("SILVER") || lbl.includes("CRUDE") || lbl.includes("NATURAL")) return "MCX";
  if (lbl.includes("SENSEX") || lbl.includes("BANKEX")) return "BSE";
  return "NSE";
}

export function Orders() {
  useTitle("Orders");
  const sess = useSession();
  const [tab, setTab] = useState<"orders" | "trades">("orders");
  const [selectedAccount, setSelectedAccount] = useState<string>("all");
  const [selectedExchange, setSelectedExchange] = useState<"ALL" | "NSE" | "BSE" | "MCX">("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const ord = usePoll<Listing<OrderRow>>("/orders", 1500);
  const trd = usePoll<Listing<TradeRow>>("/trades", 3000);
  const [busyId, setBusyId] = useState<string | null>(null);

  const rows = [...(ord.data?.rows ?? [])].sort((a, b) => Number(b.open) - Number(a.open));
  const trades = trd.data?.rows ?? [];

  const availableAccounts = useMemo(() => {
    const map = new Map<string, string>();
    if (sess?.accounts) {
      for (const a of sess.accounts) {
        map.set(a.id, a.label);
      }
    }
    for (const r of rows) {
      if (r.account_id) map.set(r.account_id, r.account_label || r.account_id);
    }
    for (const t of trades) {
      if (t.account_id) map.set(t.account_id, t.account_label || t.account_id);
    }
    return Array.from(map.entries()).map(([id, label]) => ({ id, label }));
  }, [rows, trades, sess?.accounts]);

  const filteredRows = useMemo(() => {
    return rows.filter((o) => {
      if (selectedAccount !== "all") {
        if (o.account_id && o.account_id !== selectedAccount) return false;
        if (!o.account_id && selectedAccount !== "env") return false;
      }
      if (selectedExchange !== "ALL") {
        if (getExchange(o.key, o.label || o.symbol) !== selectedExchange) return false;
      }
      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase();
        const match =
          (o.label && o.label.toLowerCase().includes(q)) ||
          (o.symbol && o.symbol.toLowerCase().includes(q)) ||
          (o.product && o.product.toLowerCase().includes(q)) ||
          (o.account_label && o.account_label.toLowerCase().includes(q));
        if (!match) return false;
      }
      return true;
    });
  }, [rows, selectedAccount, selectedExchange, searchQuery]);

  const filteredTrades = useMemo(() => {
    return trades.filter((t) => {
      if (selectedAccount !== "all") {
        if (t.account_id && t.account_id !== selectedAccount) return false;
        if (!t.account_id && selectedAccount !== "env") return false;
      }
      if (selectedExchange !== "ALL") {
        if (getExchange(t.key, t.label || t.symbol) !== selectedExchange) return false;
      }
      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase();
        const match =
          (t.label && t.label.toLowerCase().includes(q)) ||
          (t.symbol && t.symbol.toLowerCase().includes(q)) ||
          (t.product && t.product.toLowerCase().includes(q)) ||
          (t.account_label && t.account_label.toLowerCase().includes(q));
        if (!match) return false;
      }
      return true;
    });
  }, [trades, selectedAccount, selectedExchange, searchQuery]);

  const openCount = filteredRows.filter((r) => r.open).length;
  const isFiltered = selectedAccount !== "all" || selectedExchange !== "ALL" || searchQuery.trim() !== "";

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
          <button role="tab" className="tab" aria-selected={tab === "orders"} onClick={() => setTab("orders")}>Orders<span className="count">{filteredRows.length}{openCount ? ` · ${openCount} open` : ""}</span></button>
          <button role="tab" className="tab" aria-selected={tab === "trades"} onClick={() => setTab("trades")}>Trades<span className="count">{filteredTrades.length}</span></button>
        </div>

        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", justifyContent: "space-between" }}>
          <div className="row wrap" style={{ gap: 12, alignItems: "center" }}>
            {availableAccounts.length > 1 && (
              <div className="row" style={{ alignItems: "center", gap: 6 }}>
                <span className="lbl" style={{ fontSize: 12, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase" }}>Account:</span>
                <select
                  className="select sm"
                  value={selectedAccount}
                  onChange={(e) => setSelectedAccount(e.target.value)}
                  style={{ height: 32, padding: "2px 8px", fontSize: 13, borderRadius: 6, fontWeight: 550 }}
                >
                  <option value="all">All Accounts ({availableAccounts.length})</option>
                  {availableAccounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.label}</option>
                  ))}
                </select>
              </div>
            )}

            <div className="row" style={{ alignItems: "center", gap: 6 }}>
              <span className="lbl" style={{ fontSize: 12, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase" }}>Exchange:</span>
              <div className="seg" role="group" aria-label="Exchange filter" style={{ height: 32 }}>
                {(["ALL", "NSE", "BSE", "MCX"] as const).map((ex) => (
                  <button
                    key={ex}
                    className="sm"
                    aria-pressed={selectedExchange === ex}
                    onClick={() => setSelectedExchange(ex)}
                    style={{ padding: "0 10px", fontSize: 12, fontWeight: 550 }}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="row" style={{ alignItems: "center", gap: 8, flexGrow: 1, maxWidth: 300, minWidth: 190, justifyContent: "flex-end" }}>
            <div style={{ position: "relative", width: "100%", maxWidth: 220 }}>
              <input
                type="text"
                className="input sm"
                placeholder="Search symbol..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{ width: "100%", height: 32, paddingLeft: 28, paddingRight: searchQuery ? 26 : 8, fontSize: 13, borderRadius: 6 }}
              />
              <span style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)", color: "var(--muted)", pointerEvents: "none", fontSize: 12 }}>
                🔍
              </span>
              {searchQuery && (
                <button
                  className="btn ghost icon sm"
                  onClick={() => setSearchQuery("")}
                  style={{ position: "absolute", right: 2, top: "50%", transform: "translateY(-50%)", height: 24, width: 24, padding: 0 }}
                  title="Clear search"
                >
                  ✕
                </button>
              )}
            </div>
            {isFiltered && (
              <button
                className="btn ghost sm"
                onClick={() => { setSelectedAccount("all"); setSelectedExchange("ALL"); setSearchQuery(""); }}
                title="Reset filters"
                style={{ fontSize: 11, height: 32, whiteSpace: "nowrap" }}
              >
                Reset
              </button>
            )}
          </div>
        </div>

        {unreadable && (
          <div className="notice bad" style={{ margin: 12 }}>
            {ord.data?.reason === "not_connected" ? "Not connected to Kotak yet." : "Kotak’s order book could not be read just now. What you see below may be out of date; it refreshes every few seconds."}
          </div>
        )}

        {tab === "orders" ? (
          filteredRows.length === 0 ? (
            <Empty title="No orders found">{rows.length === 0 ? "Orders you place from the Watchlist, Positions or the algo show up here." : "No orders match the selected filters."}</Empty>
          ) : (
            <div className="tscroll">
              <table className="t">
                <thead>
                  <tr><th>Time</th><th>Instrument</th><th>Side</th><th className="num">Filled / Qty</th><th className="num">Price</th><th className="num">Avg fill</th><th>Status</th><th /></tr>
                </thead>
                <tbody>
                  {filteredRows.map((o) => (
                    <tr key={o.id} className={o.status === "CANCELLED" || o.status === "REJECTED" ? "dim" : ""}>
                      <td className="muted">{shortTime(o.time)}</td>
                      <td>
                        <div className="cell-name">
                          <b>{o.label || o.symbol}</b>
                          <span>{o.account_label && <span className="chip flat" style={{ marginRight: 4 }}>{o.account_label}</span>}{o.product} · {OT[o.order_type] ?? o.order_type}{o.tag && o.tag !== "manual" ? ` · ${o.tag}` : ""}</span>
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
                            <button className="btn sm" onClick={() => openTicket({ key: o.key, label: o.label || o.symbol, side: o.side, lot: o.lot, tick: 0.05, modify: o, account_id: o.account_id })}><IconEdit />Modify</button>
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
        ) : filteredTrades.length === 0 ? (
          <Empty title="No trades found">{trades.length === 0 ? "Fills appear here as orders complete." : "No trades match the selected filters."}</Empty>
        ) : (
          <div className="tscroll">
            <table className="t">
              <thead>
                <tr><th>Time</th><th>Instrument</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th><th>Product</th><th>Order</th></tr>
              </thead>
              <tbody>
                {filteredTrades.map((t, i) => (
                  <tr key={t.trade_id || t.order_id + i}>
                    <td className="muted">{shortTime(t.time)}</td>
                    <td><b style={{ fontWeight: 600 }}>{t.label || t.symbol}</b>{t.account_label && <span className="chip flat" style={{ marginLeft: 6 }}>{t.account_label}</span>}</td>
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
