/** Types and small helpers for the rule builder, the strategy manager and the trade journal. */

export type Src = "INDEX" | "CE" | "PE" | "SELF";

export interface Operand {
  kind: string;
  src?: Src;
  value?: number;
  field?: string;
  at?: string;
  start?: string;
  candles?: number;
  tf?: number;
  period?: number;
  offset?: number;
}
export interface Condition {
  left: Operand;
  op: string;
  right: Operand;
}
export interface Rule {
  side: "S" | "B";
  leg: "CE" | "PE";
  match: "all" | "any";
  conditions: Condition[];
}
export interface Spec {
  name: string;
  underlying: string;
  expiry: string;
  product: "MIS" | "NRML";
  lots: number;
  lock_time: string;
  offset_ce: number;
  offset_pe: number;
  entries: Rule[];
  exit: {
    stop: { mode: "none" | "points" | "percent" | "level"; value?: number; level?: Operand | null };
    target: { mode: "none" | "points" | "percent"; value?: number };
    time: string;
    match: "all" | "any";
    conditions: Condition[];
    exchange_sl: boolean;
  };
  limits: { max_entries: number; window_start: string; window_end: string; max_loss: number; slippage_pct: number };
  note?: string;
}

export interface Template {
  id: string;
  title: string;
  blurb: string;
  caution: string;
  engine?: "legs" | "rules";
  spec: Spec | import("./legs").LegSpec;
}

export interface CondState {
  text: string;
  l: number | null;
  r: number | null;
  ok: boolean;
  why: string[];
}
export interface RuleState {
  side: "S" | "B";
  leg: "CE" | "PE";
  match: string;
  ok: boolean;
  in_window: boolean;
  armed: boolean;
  conds: CondState[];
}
export interface PosState {
  leg: string;
  symbol: string;
  label: string;
  side: "S" | "B";
  qty: number;
  lots: number;
  entry_price: number;
  entry_time: string;
  sl: number | null;
  target: number | null;
  exchange_sl: boolean;
  status: "open" | "closed" | "attention";
  exit_price: number | null;
  exit_time: string;
  exit_reason: string;
  estimated: boolean;
  ltp: number | null;
  pnl: number;
}
export interface RunState {
  id: number;
  name: string;
  status: "starting" | "waiting" | "watching" | "done" | "halted" | "stopped";
  paused: boolean;
  stop_requested?: boolean;
  message: string;
  halt: string;
  expiry?: string;
  kind?: "legs";
  legs?: Record<string, { key: string; symbol: string; strike: number; ltp: number | null }> | import("./legs").LegView[];
  overall?: { floor: number | null; reason: string; monitoring: boolean };
  spot?: { key: string; ltp: number | null };
  rules?: RuleState[];
  exit_rule?: { ok: boolean; conds: CondState[] } | null;
  positions?: PosState[];
  pnl?: { realised: number; unrealised: number; total: number };
  entries_done?: number;
  max_entries?: number;
  log: { t: string; level: "info" | "warn" | "error"; msg: string }[];
}
export interface StrategyRow {
  id: number;
  name: string;
  spec: Spec | import("./legs").LegSpec;
  valid: boolean;
  errors: string[];
  warnings: string[];
  summary: string[];
  state: RunState | null;
  running: boolean;
  updated: number;
}

// ------------------------------------------------------------------------------------------ labels
export const KIND_LABEL: Record<string, string> = {
  ltp: "Last price",
  open_range_high: "Opening-range high",
  open_range_low: "Opening-range low",
  candle: "A fixed candle (by clock time)",
  prev_candle: "The previous candle",
  day_high: "Day high",
  day_low: "Day low",
  day_open: "Day open",
  prev_close: "Previous close",
  ema: "EMA (moving average)",
  sma: "SMA (simple average)",
  rsi: "RSI",
  number: "A number",
};
export const KINDS = Object.keys(KIND_LABEL);
export const SRC_LABEL: Record<string, string> = { INDEX: "the index", CE: "the Call", PE: "the Put", SELF: "this option" };
export const OP_LABEL: Record<string, string> = {
  ">": "is above",
  ">=": "is at or above",
  "<": "is below",
  "<=": "is at or below",
  crosses_above: "crosses above",
  crosses_below: "crosses below",
};
export const TFS = [1, 3, 5, 10, 15, 30, 60];

/** A sensible starting operand of each kind. */
export function operandOf(kind: string, keep?: Operand): Operand {
  const src = (keep?.src ?? "SELF") as Src;
  switch (kind) {
    case "number":
      return { kind, value: keep?.kind === "number" ? keep.value ?? 0 : 0 };
    case "open_range_high":
    case "open_range_low":
      return { kind, src, start: "09:15", candles: 4, offset: 0 };
    case "candle":
      return { kind, src, field: "high", at: "09:16", offset: 0 };
    case "prev_candle":
      return { kind, src, field: "high", tf: 5, offset: 0 };
    case "ema":
    case "sma":
      return { kind, src, tf: 5, period: 20, offset: 0 };
    case "rsi":
      return { kind, src, tf: 5, period: 14, offset: 0 };
    default:
      return { kind, src, offset: 0 };
  }
}

export const newCondition = (): Condition => ({ left: operandOf("ltp", { kind: "ltp", src: "INDEX" }), op: ">", right: operandOf("prev_candle", { kind: "x", src: "INDEX" }) });

export const STATUS_TEXT: Record<string, string> = {
  starting: "Starting",
  waiting: "Waiting to choose strikes",
  watching: "Watching for signals",
  done: "Finished for today",
  halted: "Halted. Action required",
  stopped: "Stopped",
};

// ------------------------------------------------------------------------------------------ journal
export interface Trip {
  id: string;
  key: string;
  label: string;
  name: string;
  product: string;
  dir: "Long" | "Short";
  qty: number;
  lots: number;
  entry: number;
  exit: number;
  entry_ts: number;
  exit_ts: number;
  day: string;
  pnl: number;
  hold_s: number;
  tag: string;
  note: string;
  tags: string[];
}
export interface Group {
  key: string;
  n: number;
  pnl: number;
  win_rate: number;
}
export interface JStats {
  n: number;
  net: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  best: number | null;
  worst: number | null;
  max_drawdown: number;
  days: number;
  avg_hold_min: number | null;
  max_losing_streak: number;
}
export interface JournalData {
  ok: boolean;
  reason?: string;
  mode: string;
  trips: Trip[];
  open: { key: string; label: string; product: string; side: string; qty: number; price: number; day: string; tag: string }[];
  tags: string[];
  stats: JStats;
  by_day: Group[];
  by_tag: Group[];
  by_name: Group[];
  by_dir: Group[];
  by_hour: Group[];
  equity: { ts: number; cum: number; pnl: number; day: string }[];
  day_notes: Record<string, { body: string; tags: string[] }>;
  demo: boolean;
  disclaimer: string;
}
