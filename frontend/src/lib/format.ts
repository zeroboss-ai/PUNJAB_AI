const nf2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const dash = "–";

export function px(n: number | null | undefined, dp = 2): string {
  if (n == null || !isFinite(n)) return dash;
  return dp === 2 ? nf2.format(n) : new Intl.NumberFormat("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp }).format(n);
}

export function int(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return dash;
  return nf0.format(n);
}

/** +1,234.50 / -1,234.50 / 0.00 */
export function signed(n: number | null | undefined, dp = 2): string {
  if (n == null || !isFinite(n)) return dash;
  const s = px(Math.abs(n), dp);
  if (n > 0) return "+" + s;
  if (n < 0 && Math.abs(n) >= 0.005) return "−" + s;
  return s;
}

export function pct(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return dash;
  const s = Math.abs(n).toFixed(2) + "%";
  return n > 0 ? "+" + s : n < 0 && Math.abs(n) >= 0.005 ? "−" + s : s;
}

export function rupees(n: number | null | undefined, dp = 0): string {
  if (n == null || !isFinite(n)) return dash;
  return "₹" + (dp === 0 ? nf0.format(Math.abs(n)) : px(Math.abs(n), dp));
}

export function signedRupees(n: number | null | undefined, dp = 0): string {
  if (n == null || !isFinite(n)) return dash;
  const s = rupees(n, dp);
  if (n > 0) return "+" + s;
  if (n < 0 && Math.abs(n) >= (dp === 0 ? 0.5 : 0.005)) return "−" + s;
  return s;
}

export function compact(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return dash;
  const a = Math.abs(n);
  if (a >= 1e7) return (n / 1e7).toFixed(2) + " Cr";
  if (a >= 1e5) return (n / 1e5).toFixed(2) + " L";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + " K";
  return String(Math.round(n));
}

/** CSS class for the sign of a number. */
export function tone(n: number | null | undefined): "up" | "down" | "flat" {
  if (n == null || !isFinite(n) || Math.abs(n) < 0.005) return "flat";
  return n > 0 ? "up" : "down";
}

export function snapToTick(v: number, tick: number): number {
  const t = tick > 0 ? tick : 0.05;
  return Math.round(Math.round(v / t) * t * 100) / 100;
}

export function lotsLabel(qty: number, lot: number): string {
  if (lot > 1) {
    const l = Math.abs(qty) / lot;
    return `${int(qty)} (${Number.isInteger(l) ? l : l.toFixed(2)} lot${l === 1 ? "" : "s"})`;
  }
  return int(qty);
}

export function isDerivativeKey(key: string): boolean {
  return /_fo\|/.test(key);
}

export function clock(d = new Date()): string {
  return d.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
}

export function shortTime(s: string): string {
  // Broker times arrive in assorted shapes ("26-Oct-2026 09:31:02", "09:31:02"); show just the time when we can.
  const m = /(\d{1,2}:\d{2}:\d{2})/.exec(s || "");
  return m ? m[1] : s || dash;
}

export function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
