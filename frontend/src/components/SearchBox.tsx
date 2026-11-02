import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Inst } from "../lib/types";
import { IconSearch } from "./Icons";

const KIND: Record<string, string> = { EQ: "Stock", INDEX: "Index", CE: "Call", PE: "Put", FUT: "Future" };

export function SearchBox({ onPick, placeholder = "Search: RELIANCE, NIFTY 24500 CE, BANKNIFTY FUT" }: { onPick: (i: Inst) => void; placeholder?: string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Inst[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setHits([]);
      setErr(null);
      return;
    }
    const mine = ++seq.current;
    const id = window.setTimeout(async () => {
      try {
        const r = await api<{ loading: boolean; results: Inst[] }>("/search?q=" + encodeURIComponent(term) + "&limit=25");
        if (mine !== seq.current) return;
        setHits(r.results);
        setLoading(r.loading);
        setErr(null);
        setSel(0);
      } catch (e) {
        if (mine === seq.current) setErr((e as Error).message);
      }
    }, 180);
    return () => window.clearTimeout(id);
  }, [q]);

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);

  const pick = (i: Inst) => {
    onPick(i);
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
    <div className="search" ref={box}>
      <div style={{ position: "relative" }}>
        <span style={{ position: "absolute", left: 10, top: 8, color: "var(--faint)" }}><IconSearch /></span>
        <input
          className="input"
          style={{ paddingLeft: 32 }}
          role="combobox"
          aria-expanded={open}
          aria-controls="search-results"
          aria-label="Search instruments"
          placeholder={placeholder}
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
        />
      </div>
      {open && q.trim() && (
        <div className="search-pop" id="search-results" role="listbox">
          {err && <div className="empty err">{err}</div>}
          {!err && hits.length === 0 && (
            <div className="empty">{loading ? "Loading the instrument list… try again in a few seconds." : "Nothing matches. Try the stock name, or NIFTY 24500 CE for an option."}</div>
          )}
          {hits.map((h, i) => (
            <button key={h.key} className="hit" role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => pick(h)}>
              <span style={{ fontWeight: 580 }}>{h.label}</span>
              <span className="chip">{KIND[h.kind] ?? h.kind}</span>
              <span className="meta">{h.segment === "mcx_fo" ? `MCX${h.lot > 1 ? ` · lot ${h.lot}` : ""}` : h.desc || (h.lot > 1 ? `lot ${h.lot}` : h.segment.replace("_", " ").toUpperCase())}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
