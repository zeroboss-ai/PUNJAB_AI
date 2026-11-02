/** Types and defaults for the leg builder (strategy spec version 2) and the live view of a running leg strategy. */
import type { Condition, PosState } from "./strategy";

export type Side = "S" | "B";
export interface StrikeSel { by: string; steps: number; value: number; lo: number; hi: number }
export interface RiskRule { mode: "none" | "points" | "percent"; value: number }
export interface Trail { on: boolean; every: number; by: number }
export interface Reentry { mode: "none" | "asap" | "cost" | "momentum"; reverse: boolean; count: number; unit: "points" | "percent"; value: number }
export interface Trigger {
  type: "time" | "momentum" | "range" | "condition";
  unit: "points" | "percent";
  dir: "up" | "down";
  value: number;
  until: string;
  side: "high" | "low";
  of: "option" | "underlying";
  match: "all" | "any";
  conditions: Condition[];
}
export interface LegCfg {
  segment: "options" | "futures";
  lots: number;
  side: Side;
  kind: "CE" | "PE";
  expiry: "nearest" | "next" | "monthly";
  strike: StrikeSel;
  sl: RiskRule;
  target: RiskRule;
  trail: Trail;
  reentry_sl: Reentry;
  reentry_tgt: Reentry;
  trigger: Trigger;
}
export interface OverallRule { mode: "none" | "rupees" | "percent"; value: number }
export interface LegSpec {
  version: 2;
  name: string;
  underlying: string;
  product: "MIS";
  entry_time: string;
  last_entry_time: string;
  exit_time?: string;
  square_off: "partial" | "complete";
  trail_be: boolean;
  trail_be_scope: "all" | "sl_legs";
  no_reentry_after: string;
  stop_monitoring_after: string;
  legs: LegCfg[];
  overall: { sl: OverallRule; target: OverallRule; trail: { on: boolean; reach: number; lock: number; every: number; by: number } };
  limits: { max_loss: number; slippage_pct: number };
  exit: { time: string; exchange_sl: boolean };
  note?: string;
}

export const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"];
export const UND_LABEL: Record<string, string> = { NIFTY: "Nifty", BANKNIFTY: "Bank Nifty", FINNIFTY: "Fin Nifty", SENSEX: "Sensex", MIDCPNIFTY: "Midcap Nifty" };
export const COMMODITY_UNDERLYINGS = [
  "GOLD", "GOLDM", "GOLDPETAL", "GOLDGUINEA", "SILVER", "SILVERM", "SILVERMIC", "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI",
  "COPPER", "COPPERM", "ZINC", "ZINCMINI", "LEAD", "LEADMINI", "ALUMINIUM", "ALUMINI", "NICKEL", "NICKELM", "MENTHAOIL", "CPO", "COTTON",
];
export const COMMODITY_LABEL: Record<string, string> = {
  GOLD: "Gold", GOLDM: "Gold Mini", GOLDPETAL: "Gold Petal", GOLDGUINEA: "Gold Guinea", SILVER: "Silver", SILVERM: "Silver Mini", SILVERMIC: "Silver Micro",
  CRUDEOIL: "Crude Oil", CRUDEOILM: "Crude Oil Mini", NATURALGAS: "Natural Gas", NATGASMINI: "Natural Gas Mini", COPPER: "Copper", COPPERM: "Copper Mini",
  ZINC: "Zinc", ZINCMINI: "Zinc Mini", LEAD: "Lead", LEADMINI: "Lead Mini", ALUMINIUM: "Aluminium", ALUMINI: "Aluminium Mini", NICKEL: "Nickel",
  NICKELM: "Nickel Mini", MENTHAOIL: "Mentha Oil", CPO: "Palm Oil (CPO)", COTTON: "Cotton",
};
export const isCommodity = (u: string) => COMMODITY_UNDERLYINGS.includes(u);
/** Typical times for each market; 23:15 is safe in both the 23:30 (US summer) and 23:55 (winter) MCX seasons. */
export const MARKET_TIMES = {
  index: { entry_time: "09:35", last_entry_time: "15:00", exit: "15:14" },
  commodity: { entry_time: "09:05", last_entry_time: "22:45", exit: "23:15" },
};
export const MAX_LEGS = 8;

export const STRIKE_BY: { id: string; label: string; hint: string }[] = [
  { id: "atm", label: "ATM / OTM / ITM", hint: "A fixed number of strikes from the at-the-money strike." },
  { id: "premium_closest", label: "Closest premium", hint: "The strike whose price is nearest to the amount." },
  { id: "premium_gte", label: "Premium ≥", hint: "The cheapest strike that still costs at least the amount." },
  { id: "premium_lte", label: "Premium ≤", hint: "The dearest strike that costs at most the amount." },
  { id: "premium_range", label: "Premium range", hint: "A strike priced inside the range (nearest its middle)." },
  { id: "delta_closest", label: "Closest delta", hint: "Delta is estimated by this terminal from the option price; Kotak does not send it." },
  { id: "delta_range", label: "Delta range", hint: "A strike whose estimated delta is inside the range." },
  { id: "pct_atm", label: "% of ATM", hint: "The ATM strike moved by this percent. Positive = out of the money, negative = in the money." },
  { id: "straddle", label: "Straddle width", hint: "The ATM strike moved by this percent of the ATM straddle price." },
  { id: "synthetic", label: "Synthetic future", hint: "Like ATM, but counted from the synthetic future (ATM + Call - Put)." },
];

export const strikeOf = (by: string, keep?: Partial<StrikeSel>): StrikeSel => {
  const base: StrikeSel = { by, steps: 0, value: 0, lo: 0, hi: 0 };
  switch (by) {
    case "premium_closest": case "premium_gte": case "premium_lte": return { ...base, value: keep?.value && keep.by?.startsWith("premium") ? keep.value : 40 };
    case "premium_range": return { ...base, lo: 30, hi: 50 };
    case "delta_closest": return { ...base, value: 0.2 };
    case "delta_range": return { ...base, lo: 0.15, hi: 0.25 };
    case "pct_atm": return { ...base, value: 2 };
    case "straddle": return { ...base, value: 50 };
    default: return { ...base, steps: keep?.steps ?? 0 };
  }
};

export const noRule = (): RiskRule => ({ mode: "none", value: 0 });
export const noReentry = (): Reentry => ({ mode: "none", reverse: false, count: 1, unit: "points", value: 0 });
export const timeTrigger = (): Trigger => ({ type: "time", unit: "points", dir: "up", value: 0, until: "09:45", side: "high", of: "option", match: "all", conditions: [] });

export function newLeg(over: Partial<LegCfg> = {}): LegCfg {
  return {
    segment: "options", lots: 1, side: "S", kind: "CE", expiry: "nearest", strike: strikeOf("atm"),
    sl: { mode: "percent", value: 25 }, target: noRule(), trail: { on: false, every: 0, by: 0 },
    reentry_sl: noReentry(), reentry_tgt: noReentry(), trigger: timeTrigger(), ...over,
  };
}

export const REENTRY_LABEL: Record<string, string> = {
  none: "No re-entry",
  asap: "Re ASAP: enter again at once",
  cost: "Re Cost: enter again at the entry price",
  momentum: "Re Momentum: enter after a move",
};
export const TRIGGER_LABEL: Record<string, string> = {
  time: "At the entry time",
  momentum: "Simple momentum (price moves first)",
  range: "Range breakout",
  condition: "When conditions are true",
};

// ----------------------------------------------------------------------------------------- live view
export interface LegView {
  id: string;
  title: string;
  status: string;
  side: Side;
  segment: string;
  kind: string;
  key: string | null;
  symbol: string;
  label: string;
  strike: number | null;
  expiry: string;
  ltp: number | null;
  pick: string;
  trigger: string;
  progress: string;
  note: string;
  re_sl: [number, number];
  re_tgt: [number, number];
  pending: { kind: string; side: Side; mode: string } | null;
  strike_rule: string;
}
export interface LegsLive {
  legs: LegView[];
  overall: { floor: number | null; reason: string; monitoring: boolean };
  positions: PosState[];
}
