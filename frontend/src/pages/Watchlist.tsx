import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { openTicket, toast, useTitle } from "../lib/app";
import { compact, dash, int, pct, px, signed, tone } from "../lib/format";
import { lastMove, useQuote, useWant } from "../lib/quotes";
import { usePoll } from "../lib/store";
import type { Inst, WatchItem, Watchlist as WL } from "../lib/types";
import { openChart } from "../lib/chart";
import { IconChart, IconEdit, IconPlus, IconTrash } from "../components/Icons";
import { SearchBox } from "../components/SearchBox";
import { Empty, Modal } from "../components/ui";

const KIND: Record<string, string> = { EQ: "Stock", INDEX: "Index", CE: "Call", PE: "Put", FUT: "Future" };

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

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Watchlist</h1>
          <p>Prices stream live. Hover a row to buy, sell or remove.</p>
        </div>
        <SearchBox onPick={add} />
      </div>

      <section className="panel">
        <div className="tabs" role="tablist">
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
          <Empty title="Could not load your watchlists">{error}</Empty>
        ) : !cur ? (
          <Empty title="Loading…" />
        ) : cur.items.length === 0 ? (
          <Empty title="This watchlist is empty">Search above to add stocks, indices, futures or options.</Empty>
        ) : (
          <div className="tscroll">
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
        )}
      </section>

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
