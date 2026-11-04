import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { openTicket, navigate, toast, useTitle } from "../lib/app";
import { compact, dash, int, pct, px, signed, tone } from "../lib/format";
import { lastMove, useQuote, useWant } from "../lib/quotes";
import { usePoll } from "../lib/store";
import type { Inst, WatchItem, Watchlist as WL } from "../lib/types";
import { openChart } from "../lib/chart";
import { IconChart, IconEdit, IconPlus, IconTrash } from "../components/Icons";
import { SearchBox } from "../components/SearchBox";
import { Drawer, Empty, Modal } from "../components/ui";

const KIND: Record<string, string> = { EQ: "Stock", INDEX: "Index", CE: "Call", PE: "Put", FUT: "Future" };

function MobileWatchItem({ it, onSelect }: { it: WatchItem; onSelect: () => void }) {
  const q = useQuote(it.key);
  const t = tone(q?.c);
  const dir = q ? lastMove(it.key) : 0;
  const label = it.label ?? it.name ?? it.key;
  const sub = [KIND[it.kind ?? ""] ?? "", it.segment === "mcx_fo" ? "MCX" : it.desc, (it.lot ?? 1) > 1 ? `lot ${it.lot}` : ""].filter(Boolean).join(" · ");

  return (
    <div className="m-wl-item" onClick={onSelect}>
      <div className="m-wl-left">
        <div className="m-wl-name">{label}</div>
        <div className="m-wl-sub">{sub}</div>
      </div>
      <div className="m-wl-right">
        <div className={`m-wl-px ${dir > 0 ? "flash-up" : dir < 0 ? "flash-down" : ""}`} key={q?.l ?? "x"}>
          {q ? px(q.l) : dash}
        </div>
        <div className={`m-wl-chg ${t}`}>
          {q ? `${signed(q.c)} (${pct(q.p)})` : dash}
        </div>
      </div>
    </div>
  );
}

function Row({ it, onRemove }: { it: WatchItem; onRemove: () => void }) {
  const q = useQuote(it.key);
  const t = tone(q?.c);
  const dir = q ? lastMove(it.key) : 0;
  const label = it.label ?? it.name ?? it.key;
  const tradable = it.kind !== "INDEX";
  const open = (side: "B" | "S") => openTicket({ key: it.key, label, side, lot: it.lot ?? 1, tick: it.tick ?? 0.05 });
  return (
    <tr>
      <td>
        <div className="cell-name">
          <b>{label}</b>
          <span>{[KIND[it.kind ?? ""] ?? "", it.segment === "mcx_fo" ? "MCX" : it.desc, (it.lot ?? 1) > 1 ? `lot ${it.lot}` : ""].filter(Boolean).join(" · ")}</span>
        </div>
      </td>
      <td className={`num ${dir > 0 ? "flash-up" : dir < 0 ? "flash-down" : ""}`} key={q?.l ?? "x"} style={{ fontWeight: 600 }}>{q ? px(q.l) : dash}</td>
      <td className={`num hide-m ${t}`}>{signed(q?.c)}</td>
      <td className={`num ${t}`}>{pct(q?.p)}</td>
      <td className="num muted hide-m">{px(q?.h)}</td>
      <td className="num muted hide-m">{px(q?.lo)}</td>
      <td className="num muted hide-m">{q?.v ? compact(q.v) : dash}</td>
      <td className="num muted hide-m">{q?.oi ? int(q.oi) : dash}</td>
      <td>
        <div className="acts hover-acts">
          <button className="btn ghost icon sm" onClick={() => openChart(it.key, label)} aria-label={`Chart ${label}`} title="Chart"><IconChart /></button>
          {tradable && (
            <>
              <button className="bs b" onClick={() => open("B")} aria-label={`Buy ${label}`}>B</button>
              <button className="bs s" onClick={() => open("S")} aria-label={`Sell ${label}`}>S</button>
            </>
          )}
          <button className="btn ghost icon sm" onClick={onRemove} aria-label={`Remove ${label}`} title="Remove"><IconTrash /></button>
        </div>
      </td>
    </tr>
  );
}

export function Watchlist() {
  useTitle("Watchlist");
  const { data, reload, error } = usePoll<{ lists: WL[] }>("/watchlists", 60000);
  const [active, setActive] = useState<number | null>(null);
  const [dlg, setDlg] = useState<null | "new" | "rename" | "delete">(null);
  const [selectedMobileItem, setSelectedMobileItem] = useState<WatchItem | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const lists = data?.lists ?? [];
  const cur = lists.find((l) => l.id === active) ?? lists[0];
  useEffect(() => {
    if (cur && active !== cur.id) setActive(cur.id);
  }, [cur, active]);
  useWant(cur?.items.map((i) => i.key) ?? []);

  async function run(fn: () => Promise<unknown>, done?: string) {
    setBusy(true);
    try {
      await fn();
      if (done) toast(done);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const add = (i: Inst) => {
    if (!cur) return;
    if (cur.items.some((x) => x.key === i.key)) return toast(`${i.label} is already in ${cur.name}`, "info");
    run(() => api(`/watchlists/${cur.id}/items`, { method: "POST", body: { key: i.key } }), `Added ${i.label}`);
  };

  async function dialogOk() {
    if (dlg === "new") {
      setBusy(true);
      try {
        const r = await api<{ id: number }>("/watchlists", { method: "POST", body: { name: name || "Watchlist" } });
        setActive(r.id);
        reload();
        setDlg(null);
      } catch (e) {
        toast((e as Error).message, "err");
      } finally {
        setBusy(false);
      }
    } else if (dlg === "rename" && cur) {
      await run(() => api(`/watchlists/${cur.id}`, { method: "PATCH", body: { name } }));
      setDlg(null);
    } else if (dlg === "delete" && cur) {
      await run(() => api(`/watchlists/${cur.id}`, { method: "DELETE" }), `Deleted ${cur.name}`);
      setActive(null);
      setDlg(null);
    }
  }

  const selectedQ = useQuote(selectedMobileItem?.key ?? "");
  const selectedT = tone(selectedQ?.c);
  const tradable = selectedMobileItem && selectedMobileItem.kind !== "INDEX";
  const mobileLabel = selectedMobileItem?.label ?? selectedMobileItem?.name ?? selectedMobileItem?.key ?? "";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Watchlist</h1>
          <p>Prices stream live. Tap an instrument to buy, sell, or view chart.</p>
        </div>
        <SearchBox onPick={add} />
      </div>

      <section className="panel">
        <div className="tabs" role="tablist" style={{ overflowX: "auto", whiteSpace: "nowrap" }}>
          {lists.map((l) => (
            <button key={l.id} role="tab" className="tab" aria-selected={cur?.id === l.id} onClick={() => setActive(l.id)}>
              {l.name}<span className="count">{l.items.length}</span>
            </button>
          ))}
          <button className="tab" onClick={() => { setName(""); setDlg("new"); }} aria-label="New watchlist" title="New watchlist"><IconPlus /></button>
          <span className="grow" />
          {cur && (
            <div className="row" style={{ paddingRight: 4 }}>
              <button className="btn ghost sm" onClick={() => { setName(cur.name); setDlg("rename"); }}><IconEdit />Rename</button>
              {lists.length > 1 && <button className="btn ghost sm" onClick={() => setDlg("delete")}><IconTrash />Delete list</button>}
            </div>
          )}
        </div>
        {error && !data ? (
          <Empty title="Could not load your watchlists">
            <p style={{ marginBottom: 12 }}>{error}</p>
            <button className="btn primary sm" onClick={reload}>Retry</button>
          </Empty>
        ) : !cur ? (
          <Empty title="Loading…" />
        ) : cur.items.length === 0 ? (
          <Empty title="This watchlist is empty">Search above to add stocks, indices, futures or options.</Empty>
        ) : (
          <>
            {/* Desktop Table View */}
            <div className="tscroll hide-on-mobile">
              <table className="t">
                <thead>
                  <tr>
                    <th>Instrument</th><th className="num">LTP</th><th className="num hide-m">Change</th><th className="num">Chg %</th>
                    <th className="num hide-m">High</th><th className="num hide-m">Low</th><th className="num hide-m">Volume</th><th className="num hide-m">OI</th><th />
                  </tr>
                </thead>
                <tbody>
                  {cur.items.map((it) => (
                    <Row key={it.key} it={it} onRemove={() => run(() => api(`/watchlists/${cur.id}/items?key=${encodeURIComponent(it.key)}`, { method: "DELETE" }))} />
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile Touch-Friendly List View */}
            <div className="m-watchlist-list show-on-mobile">
              {cur.items.map((it) => (
                <MobileWatchItem
                  key={it.key}
                  it={it}
                  onSelect={() => setSelectedMobileItem(it)}
                />
              ))}
            </div>
          </>
        )}
      </section>

      {/* Mobile Action Sheet Drawer for selected instrument */}
      {selectedMobileItem && (
        <Drawer side="B" label={`Trade ${mobileLabel}`} onClose={() => setSelectedMobileItem(null)}>
          <div className="drawer-h">
            <div>
              <h2 style={{ fontSize: "17px", margin: 0 }}>{mobileLabel}</h2>
              <span className="muted" style={{ fontSize: "12px" }}>
                {[KIND[selectedMobileItem.kind ?? ""] ?? "", selectedMobileItem.segment === "mcx_fo" ? "MCX" : selectedMobileItem.desc, (selectedMobileItem.lot ?? 1) > 1 ? `Lot size ${selectedMobileItem.lot}` : ""].filter(Boolean).join(" · ")}
              </span>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: "18px", fontWeight: 700 }}>{selectedQ ? px(selectedQ.l) : dash}</div>
              <div className={selectedT} style={{ fontSize: "12px", fontWeight: 600 }}>
                {selectedQ ? `${signed(selectedQ.c)} (${pct(selectedQ.p)})` : ""}
              </div>
            </div>
          </div>

          <div className="drawer-b stack" style={{ padding: "16px" }}>
            {/* High/Low/Volume stats */}
            {selectedQ && (
              <div className="stats" style={{ gridTemplateColumns: "repeat(3, 1fr)", marginBottom: 12 }}>
                <div className="stat"><span className="k">Day High</span><span className="v" style={{ fontSize: "14px" }}>{px(selectedQ.h)}</span></div>
                <div className="stat"><span className="k">Day Low</span><span className="v" style={{ fontSize: "14px" }}>{px(selectedQ.lo)}</span></div>
                <div className="stat"><span className="k">Volume</span><span className="v" style={{ fontSize: "14px" }}>{selectedQ.v ? compact(selectedQ.v) : dash}</span></div>
              </div>
            )}

            {/* Big Action Buttons */}
            {tradable ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, margin: "8px 0" }}>
                <button
                  className="btn primary"
                  style={{ background: "var(--up)", color: "#fff", borderColor: "var(--up)", padding: "12px", fontSize: "15px", fontWeight: 700 }}
                  onClick={() => {
                    openTicket({ key: selectedMobileItem.key, label: mobileLabel, side: "B", lot: selectedMobileItem.lot ?? 1, tick: selectedMobileItem.tick ?? 0.05 });
                    setSelectedMobileItem(null);
                  }}
                >
                  BUY
                </button>
                <button
                  className="btn danger solid"
                  style={{ background: "var(--down)", color: "#fff", borderColor: "var(--down)", padding: "12px", fontSize: "15px", fontWeight: 700 }}
                  onClick={() => {
                    openTicket({ key: selectedMobileItem.key, label: mobileLabel, side: "S", lot: selectedMobileItem.lot ?? 1, tick: selectedMobileItem.tick ?? 0.05 });
                    setSelectedMobileItem(null);
                  }}
                >
                  SELL
                </button>
              </div>
            ) : null}

            {/* Secondary Actions */}
            <div className="stack" style={{ gap: 8, marginTop: 8 }}>
              <button
                className="btn"
                style={{ justifyContent: "center" }}
                onClick={() => {
                  openChart(selectedMobileItem.key, mobileLabel);
                  setSelectedMobileItem(null);
                }}
              >
                <IconChart /> View Full Chart
              </button>

              <button
                className="btn"
                style={{ justifyContent: "center" }}
                onClick={() => {
                  navigate("/chain");
                  setSelectedMobileItem(null);
                }}
              >
                View Option Chain
              </button>

              <button
                className="btn danger"
                style={{ justifyContent: "center", marginTop: 4 }}
                onClick={() => {
                  if (cur) {
                    run(() => api(`/watchlists/${cur.id}/items?key=${encodeURIComponent(selectedMobileItem.key)}`, { method: "DELETE" }));
                  }
                  setSelectedMobileItem(null);
                }}
              >
                <IconTrash /> Remove from Watchlist
              </button>
            </div>
          </div>
        </Drawer>
      )}

      {dlg && dlg !== "delete" && (
        <Modal
          title={dlg === "new" ? "New watchlist" : "Rename watchlist"}
          onClose={() => setDlg(null)}
          footer={<><button className="btn" onClick={() => setDlg(null)}>Cancel</button><button className="btn primary" disabled={busy || !name.trim()} onClick={dialogOk}>{dlg === "new" ? "Create" : "Save"}</button></>}
        >
          <div className="field">
            <label htmlFor="wl-name">Name</label>
            <input id="wl-name" className="input" maxLength={40} autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && dialogOk()} />
          </div>
        </Modal>
      )}
      {dlg === "delete" && cur && (
        <Modal
          title={`Delete “${cur.name}”?`}
          onClose={() => setDlg(null)}
          footer={<><button className="btn" onClick={() => setDlg(null)}>Keep it</button><button className="btn danger solid" disabled={busy} onClick={dialogOk}>Delete list</button></>}
        >
          <p>The list and its {cur.items.length} instrument{cur.items.length === 1 ? "" : "s"} will be removed. Your positions and orders are not affected.</p>
        </Modal>
      )}
    </>
  );
}
