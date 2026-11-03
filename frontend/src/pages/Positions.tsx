import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { openTicket, toast, useSession, useTitle } from "../lib/app";
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

function getExchange(key: string, segment?: string, label?: string): "NSE" | "BSE" | "MCX" {
  const s = (segment || "").toLowerCase();
  const k = (key || "").toLowerCase();
  if (s.startsWith("mcx") || k.startsWith("mcx")) return "MCX";
  if (s.startsWith("bse") || k.startsWith("bse")) return "BSE";
  if (s.startsWith("nse") || k.startsWith("nse")) return "NSE";
  const lbl = (label || "").toUpperCase();
  if (lbl.includes("GOLD") || lbl.includes("SILVER") || lbl.includes("CRUDE") || lbl.includes("NATURAL")) return "MCX";
  if (lbl.includes("SENSEX") || lbl.includes("BANKEX")) return "BSE";
  return "NSE";
}

export function Positions() {
  useTitle("Positions");
  const sess = useSession();
  const [selectedAccount, setSelectedAccount] = useState<string>("all");
  const [selectedExchange, setSelectedExchange] = useState<"ALL" | "NSE" | "BSE" | "MCX">("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const pos = usePoll<Listing<PositionRow>>("/positions", 1000);
  const fundsUrl = selectedAccount !== "all" ? `/funds?account_id=${encodeURIComponent(selectedAccount)}` : "/funds";
  const funds = usePoll<{ ok: boolean; funds: Funds | null }>(fundsUrl, 8000);
  const [preview, setPreview] = useState<Preview[] | null>(null);
  const [scope, setScope] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);

  const rows = pos.data?.rows ?? [];
  const t = pos.data?.totals;
  const f = funds.data?.funds;

  const availableAccounts = useMemo(() => {
    const map = new Map<string, string>();
    if (sess?.accounts) {
      for (const a of sess.accounts) {
        map.set(a.id, a.label);
      }
    }
    for (const r of rows) {
      if (r.account_id) {
        map.set(r.account_id, r.account_label || r.account_id);
      }
    }
    return Array.from(map.entries()).map(([id, label]) => ({ id, label }));
  }, [rows, sess?.accounts]);

  const filtered = useMemo(() => {
    return rows.filter((p) => {
      // 1. Account filter
      if (selectedAccount !== "all") {
        if (p.account_id && p.account_id !== selectedAccount) return false;
        if (!p.account_id && selectedAccount !== "env") return false;
      }
      // 2. Exchange filter
      if (selectedExchange !== "ALL") {
        const ex = getExchange(p.key, p.segment, p.label || p.symbol);
        if (ex !== selectedExchange) return false;
      }
      // 3. Search query
      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase();
        const match =
          (p.label && p.label.toLowerCase().includes(q)) ||
          (p.symbol && p.symbol.toLowerCase().includes(q)) ||
          (p.product && p.product.toLowerCase().includes(q)) ||
          (p.account_label && p.account_label.toLowerCase().includes(q));
        if (!match) return false;
      }
      return true;
    });
  }, [rows, selectedAccount, selectedExchange, searchQuery]);

  const isFiltered = selectedAccount !== "all" || selectedExchange !== "ALL" || searchQuery.trim() !== "";
  const open = rows.filter((r) => r.open);
  const openFiltered = filtered.filter((r) => r.open);

  const displayPnl = isFiltered ? filtered.reduce((s, r) => s + r.pnl, 0) : t?.pnl;
  const displayRealised = isFiltered ? filtered.reduce((s, r) => s + r.realised, 0) : t?.realised;
  const displayUnrealised = isFiltered ? filtered.reduce((s, r) => s + r.unrealised, 0) : t?.unrealised;

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

  const open_ = (p: PositionRow, side: "B" | "S") => openTicket({ key: p.key, label: p.label, side, lot: p.lot, tick: 0.05, product: p.product, account_id: p.account_id });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Positions</h1>
          <p>Profit and loss update with every price tick.</p>
        </div>
        <button
          className="btn danger"
          disabled={openFiltered.length === 0}
          onClick={() => askClose(isFiltered ? openFiltered.map((p) => p.key) : null)}
          title={isFiltered ? `Close only the ${openFiltered.length} filtered open positions` : "Close all open positions"}
        >
          {isFiltered ? `Close ${openFiltered.length} filtered position${openFiltered.length === 1 ? "" : "s"}` : "Close all positions"}
        </button>
      </div>

      <section className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-b stats">
          <div className="stat"><span className="k">{isFiltered ? "Filtered Day P&L" : "Day P&L"}</span><span className="v"><Pnl v={displayPnl} /></span></div>
          <div className="stat"><span className="k">{isFiltered ? "Filtered Booked" : "Booked"}</span><span className="v" style={{ fontSize: 17 }}><Pnl v={displayRealised} /></span></div>
          <div className="stat"><span className="k">{isFiltered ? "Filtered Open" : "Open"}</span><span className="v" style={{ fontSize: 17 }}><Pnl v={displayUnrealised} /></span></div>
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
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", justifyContent: "space-between" }}>
          {/* Left: Account selector & Exchange filter */}
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
                {(["ALL", "NSE", "BSE", "MCX"] as const).map((ex) => {
                  const count = ex === "ALL"
                    ? (selectedAccount === "all" ? rows.length : rows.filter(r => r.account_id === selectedAccount || (!r.account_id && selectedAccount === "env")).length)
                    : (selectedAccount === "all" ? rows : rows.filter(r => r.account_id === selectedAccount || (!r.account_id && selectedAccount === "env"))).filter(r => getExchange(r.key, r.segment, r.label || r.symbol) === ex).length;
                  return (
                    <button
                      key={ex}
                      className="sm"
                      aria-pressed={selectedExchange === ex}
                      onClick={() => setSelectedExchange(ex)}
                      style={{ padding: "0 10px", fontSize: 12, fontWeight: 550 }}
                    >
                      {ex} <span style={{ opacity: 0.7, fontSize: 11, marginLeft: 2 }}>({count})</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Right: Search box & Reset */}
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

        {rows.length === 0 ? (
          <Empty title="No positions today">Trades you take today, manual or from the algo, are listed here.</Empty>
        ) : filtered.length === 0 ? (
          <Empty title="No matching positions">
            No positions match your selected account, exchange or search query.
            <div style={{ marginTop: 10 }}>
              <button className="btn sm" onClick={() => { setSelectedAccount("all"); setSelectedExchange("ALL"); setSearchQuery(""); }}>
                Reset filters
              </button>
            </div>
          </Empty>
        ) : (
          <div className="tscroll">
            <table className="t">
              <thead>
                <tr><th>Instrument</th><th className="num">Net qty</th><th className="num">Avg price</th><th className="num">LTP</th><th className="num">Bought / Sold</th><th className="num">P&amp;L</th><th /></tr>
              </thead>
              <tbody>
                {filtered.map((p) => (
                  <tr key={p.key + p.product + (p.account_id || "")} className={p.open ? "" : "dim"}>
                    <td><div className="cell-name"><b>{p.label}</b><span>{p.account_label && <span className="chip flat" style={{ marginRight: 4 }}>{p.account_label}</span>}{p.product}{!p.open ? " · closed" : ""}</span></div></td>
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
