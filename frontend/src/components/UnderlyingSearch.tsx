import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { openUnderlying, type Underlying } from "../lib/chain";
import { IconSearch } from "./Icons";

/** Find any index or F&O stock that has options and open its chain. */
export function UnderlyingSearch() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Underlying[]>([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setHits([]);
      setMsg(null);
      return;
    }
    const mine = ++seq.current;
    setBusy(true);
    const id = window.setTimeout(async () => {
      try {
        const r = await api<{ ok: boolean; results?: Underlying[]; error?: string; reason?: string }>("/chain/search?q=" + encodeURIComponent(term));
        if (mine !== seq.current) return;
        setHits(r.results ?? []);
        setMsg(r.ok ? null : r.error ?? "The instrument list is not ready yet. Try again in a few seconds.");
        setSel(0);
      } catch (e) {
        if (mine === seq.current) setMsg((e as Error).message);
      } finally {
        if (mine === seq.current) setBusy(false);
      }
    }, 200);
    return () => window.clearTimeout(id);
  }, [q]);

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  const pick = (u: Underlying) => {
    openUnderlying(u);
    setQ("");
    setHits([]);
    setOpen(false);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter" && hits[sel]) {
      e.preventDefault();
      pick(hits[sel]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="search ul-search" ref={box}>
      <div style={{ position: "relative" }}>
        <span style={{ position: "absolute", left: 10, top: 8, color: "var(--faint)" }}><IconSearch /></span>
        <input className="input" style={{ paddingLeft: 32 }} role="combobox" aria-expanded={open} aria-controls="ul-results" aria-label="Find a stock, index or commodity with options"
          placeholder="Stock or commodity: TCS, GOLD…" autoComplete="off" spellCheck={false} value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} onKeyDown={onKey} />
      </div>
      {open && q.trim() && (
        <div className="search-pop" id="ul-results" role="listbox">
          {msg && <div className="empty err">{msg}</div>}
          {!msg && hits.length === 0 && <div className="empty">{busy ? "Searching…" : "Nothing matches. Try the exact short name, like RELIANCE or M&M, or a commodity like GOLD, SILVER, CRUDE."}</div>}
          {hits.map((h, i) => (
            <button key={h.name} className="hit" role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => pick(h)}>
              <span style={{ fontWeight: 580 }}>{h.name}</span>
              <span className="chip">{h.kind === "index" ? "Index" : h.kind === "commodity" ? (h.size ? `MCX · ${h.size}` : "MCX") : "Stock"}</span>
              <span className="meta">{h.label !== h.name ? h.label : ""}{h.lot ? `${h.label !== h.name ? " · " : ""}lot ${h.lot}` : ""}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
