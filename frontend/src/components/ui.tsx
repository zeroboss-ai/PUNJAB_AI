import { useEffect, useRef, type ReactNode } from "react";
import { closeTicket } from "../lib/app";
import { dash, pct, px, signed, tone } from "../lib/format";
import { lastMove, useHistory, useQuote } from "../lib/quotes";
import { IconX } from "./Icons";

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <b>{title}</b>
      {children}
    </div>
  );
}

/** Small price trail drawn from this browser session's ticks. */
export function Spark({ k, up }: { k: string; up: boolean }) {
  const h = useHistory(k);
  if (h.length < 3) return null;
  const lo = Math.min(...h);
  const hi = Math.max(...h);
  const span = hi - lo || 1;
  const pts = h.map((v, i) => `${((i / (h.length - 1)) * 100).toFixed(1)},${(36 - ((v - lo) / span) * 32 - 2).toFixed(1)}`).join(" ");
  return (
    <svg className="spark" viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={up ? "var(--up)" : "var(--down)"} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/** Where today's price sits between the day's low and high. */
export function DayRange({ low, high, ltp }: { low: number | null; high: number | null; ltp: number }) {
  if (low == null || high == null || high <= low) return <div className="range-lbl"><span>Day range</span><span>{dash}</span></div>;
  const at = Math.min(100, Math.max(0, ((ltp - low) / (high - low)) * 100));
  return (
    <div className="range" title="Today's low to high">
      <div className="range-bar">
        <i style={{ left: `${at}%` }} />
      </div>
      <div className="range-lbl">
        <span>{px(low)}</span>
        <span>{px(high)}</span>
      </div>
    </div>
  );
}

/** A price cell that flashes briefly when the price moves. */
export function LtpCell({ k, fallback }: { k: string; fallback?: number | null }) {
  const q = useQuote(k);
  const v = q?.l ?? fallback ?? null;
  const dir = q ? lastMove(k) : 0;
  return (
    <td className={`num ${dir > 0 ? "flash-up" : dir < 0 ? "flash-down" : ""}`} key={q?.l ?? "x"} style={{ fontWeight: 560 }}>
      {v == null ? dash : px(v)}
    </td>
  );
}

export function Change({ k }: { k: string }) {
  const q = useQuote(k);
  const t = tone(q?.c);
  return (
    <>
      <td className={`num ${t}`}>{signed(q?.c)}</td>
      <td className={`num ${t}`}>{pct(q?.p)}</td>
    </>
  );
}

export function Pnl({ v, dp = 2 }: { v: number | null | undefined; dp?: number }) {
  if (v == null) return <span className="muted">{dash}</span>;
  return <span className={tone(v)}>{signed(v, dp)}</span>;
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("keydown", esc);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref} tabIndex={-1}>
        <h2>{title}</h2>
        <div className="modal-b">{children}</div>
        {footer && <div className="modal-f">{footer}</div>}
      </div>
    </>
  );
}

export function Drawer({ side, children, onClose, label }: { side: "B" | "S"; children: ReactNode; onClose: () => void; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("keydown", esc);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={closeTicket} />
      <aside className={`drawer ${side === "S" ? "s" : ""}`} role="dialog" aria-modal="true" aria-label={label} ref={ref}>
        {children}
      </aside>
    </>
  );
}

export function CloseBtn({ onClick }: { onClick: () => void }) {
  return (
    <button className="btn ghost icon sm" onClick={onClick} aria-label="Close">
      <IconX />
    </button>
  );
}
