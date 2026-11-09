import { useEffect, useRef, useState } from "react";
import { createStore, useStore } from "./store";

export interface ChainLeg {
  key: string;
  symbol: string;
  ltp: number | null;
  pc: number | null;
  chg_pct: number | null;
  oi: number | null;
  oi_prev: number | null;
  oi_chg: number | null;
  oi_chg_pct: number | null;
  vol: number | null;
  buildup: string | null;
}
export interface ChainRow {
  strike: number;
  ce: ChainLeg | null;
  pe: ChainLeg | null;
}
export interface ChainSummary {
  pcr: number | null;
  pcr_prev: number | null;
  ce_oi: number;
  pe_oi: number;
  ce_oi_chg: number;
  pe_oi_chg: number;
  max_pain: number | null;
  ce_top: { strike: number; oi: number }[];
  pe_top: { strike: number; oi: number }[];
  straddle: number | null;
  straddle_pct: number | null;
  atm_ce?: number | null;
  atm_pe?: number | null;
}
export interface ChainData {
  ok: boolean;
  reason?: string;
  error?: string;
  underlying: string;
  expiry: string;
  expiries: string[];
  lot: number;
  spot: number | null;
  spot_key: string | null;
  atm: number | null;
  rows: ChainRow[];
  strikes_total: number;
  summary: ChainSummary;
  source: "sample" | "kotak";
  snapshot_age_s: number;
  stale: boolean;
}
export interface Underlying {
  name: string;
  label: string;
  lot?: number | null;
  kind?: "index" | "stock" | "commodity";
  size?: "big" | "mini" | "micro" | "";
}
export interface OiPoint {
  ts: number;
  spot: number | null;
  pcr: number | null;
  ce_oi: number;
  pe_oi: number;
  max_pain: number | null;
  straddle?: number | null;
  atm_ce?: number | null;
  atm_pe?: number | null;
}

interface Sel {
  underlying: string;
  expiry: string; // "" = nearest
  window: number; // strikes each side of ATM; 0 = all
}

function load(): Sel {
  try {
    const s = JSON.parse(localStorage.getItem("nt-chain") || "{}");
    return { underlying: typeof s.underlying === "string" ? s.underlying : "NIFTY", expiry: "", window: [6, 10, 15, 0].includes(s.window) ? s.window : 10 };
  } catch {
    return { underlying: "NIFTY", expiry: "", window: 10 };
  }
}
export const chainSel = createStore<Sel>(load());
export function setChainSel(p: Partial<Sel>) {
  chainSel.set((s) => {
    const n = { ...s, ...p };
    if (p.underlying && p.underlying !== s.underlying && p.expiry === undefined) n.expiry = "";
    try {
      localStorage.setItem("nt-chain", JSON.stringify({ underlying: n.underlying, window: n.window }));
    } catch {
      /* storage may be unavailable */
    }
    return n;
  });
}
export const useChainSel = () => useStore(chainSel);

/** F&O stocks you opened recently, newest first, so the next visit is one tap. */
function loadRecent(): Underlying[] {
  try {
    const r = JSON.parse(localStorage.getItem("nt-chain-recent") || "[]");
    return Array.isArray(r) ? r.filter((x) => x && typeof x.name === "string").slice(0, 6) : [];
  } catch {
    return [];
  }
}
export const recentUnderlyings = createStore<Underlying[]>(loadRecent());
export function openUnderlying(u: Underlying) {
  if (u.kind !== "index") {
    recentUnderlyings.set((l) => {
      const n = [{ name: u.name, label: u.label, lot: u.lot, kind: u.kind === "commodity" ? ("commodity" as const) : ("stock" as const), size: u.size }, ...l.filter((x) => x.name !== u.name)].slice(0, 6);
      try {
        localStorage.setItem("nt-chain-recent", JSON.stringify(n));
      } catch {
        /* storage may be unavailable */
      }
      return n;
    });
  }
  setChainSel({ underlying: u.name });
}
export const useRecentUnderlyings = () => useStore(recentUnderlyings);

export function expiryLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", weekday: "short" });
}

export function daysTo(iso: string): number {
  const d = new Date(iso + "T15:30:00+05:30").getTime();
  return Math.max(0, Math.ceil((d - Date.now()) / 86400000));
}

/** Width of an element in CSS pixels, following resizes. */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}
