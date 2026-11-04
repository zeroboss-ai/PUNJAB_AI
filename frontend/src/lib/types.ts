import type { StrategyRow } from "./strategy";

export type Kind = "EQ" | "INDEX" | "CE" | "PE" | "FUT";

export interface Inst {
  key: string;
  segment: string;
  token: string;
  symbol: string;
  name: string;
  kind: Kind;
  lot: number;
  tick: number;
  expiry: string | null;
  strike: number | null;
  desc: string;
  label: string;
}

/** Quote as sent on the wire (short keys keep the stream small). */
export interface Quote {
  k: string;
  l: number;
  pc: number | null;
  c: number | null;
  p: number | null;
  o: number | null;
  h: number | null;
  lo: number | null;
  v: number | null;
  oi: number | null;
  t: number;
}

export interface SessionInfo {
  mode: "sample" | "paper" | "live";
  needs_connect: boolean;
  connected: boolean;
  my_ip: string | null;
  ip_alert?: string | null;
  activity?: { strategies: number; algo: boolean };
  market: { state: "open" | "closed" | "preopen" | "weekend"; label: string };
  market_mcx?: { state: "open" | "closed" | "weekend"; label: string; open: string; close: string };
  commodities?: { label: string; key: string; name: string; contract: string; expiry: string | null }[];
  feed: { connected: boolean; age_s: number | null; ticks: number; subscribed: number };
  catalog_loading: boolean;
  indices: { label: string; key: string }[];
  account?: { id: string; label: string; ucc: string } | null;
  accounts?: { id: string; label: string; ucc: string }[];
  active_account_id?: string;
  limits: {
    max_lots_per_order: number;
    max_order_value: number;
    block_after_loss: boolean;
    max_daily_loss: number;
    squareoff_buffer_pct: number;
  };
}

export interface WatchItem extends Partial<Inst> {
  key: string;
}
export interface Watchlist {
  id: number;
  name: string;
  items: WatchItem[];
}

export interface OrderRow {
  id: string;
  key: string;
  symbol: string;
  label: string;
  side: "B" | "S";
  qty: number;
  filled: number;
  lot: number;
  product: string;
  order_type: string;
  price: number;
  trigger: number;
  avg_price: number;
  status: string;
  reason: string;
  time: string;
  validity: string;
  tag: string;
  open: boolean;
  account_id?: string;
  account_label?: string;
}

export interface TradeRow {
  order_id: string;
  key: string;
  symbol: string;
  label: string;
  side: "B" | "S";
  qty: number;
  price: number;
  product: string;
  time: string;
  trade_id: string;
  account_id?: string;
  account_label?: string;
}

export interface PositionRow {
  key: string;
  symbol: string;
  label: string;
  segment: string;
  product: string;
  lot: number;
  buy_qty: number;
  sell_qty: number;
  net_qty: number;
  buy_avg: number;
  sell_avg: number;
  avg_price: number;
  ltp: number | null;
  realised: number;
  unrealised: number;
  pnl: number;
  open: boolean;
  expiry: string | null;
  account_id?: string;
  account_label?: string;
}

export interface Funds {
  available: number | null;
  used: number | null;
  collateral: number | null;
  realised: number | null;
  unrealised: number | null;
  simulated: boolean;
  note: string;
}

export interface Listing<T> {
  ok: boolean;
  rows: T[];
  reason?: string;
  totals?: { realised: number; unrealised: number; pnl: number; open: number } | null;
}

export interface AlgoEvent {
  ts: string;
  level: string;
  msg: string;
}

export interface AlgoSnap {
  mode: string;
  instrument: string;
  needs_login: boolean;
  logged_in: boolean;
  max_daily_loss: number;
  phase: string;
  time?: string;
  atm?: number | null;
  ticks?: number;
  finished?: boolean;
  manual_action_required?: boolean;
  prices?: { INDEX: number | null; CE: number | null; PE: number | null };
  symbols?: { CE: string | null; PE: string | null };
  strategy: null | {
    state: string;
    entries_paused: boolean;
    ranges: Record<string, { high: number; low: number }>;
    sl_ref: Record<string, number> | number | null;
    position: null | { symbol: string; side: string; qty: number; entry: number; sl: number; ltp: number | null; open_pnl: number };
    realised: number;
    trades: { side: string; symbol?: string; entry?: number; exit?: number; pnl?: number; reason?: string; [k: string]: unknown }[];
    trades_taken: number;
    max_trades: number;
  };
  risk?: { max_daily_loss: number; realised: number; open_pnl: number; total: number };
  rules?: { range: string; sl_candle: string; exit_time: string; entry_cutoff: string };
  events: AlgoEvent[];
  running?: StrategyRow[];
  available?: StrategyRow[];
  running_count?: number;
  total_pnl?: number;
  realised_pnl?: number;
  unrealised_pnl?: number;
}
