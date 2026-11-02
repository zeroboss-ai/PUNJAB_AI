"""
The strategy, exactly as specified:

  * Range = high/low of the first 4 one-minute candles (09:15, 09:16, 09:17, 09:18)
    of the INDEX and of the ATM CALL and ATM PUT (ATM fixed from the index open).
  * SELL PUT  when  index > index range high   AND  ATM PUT  < ATM PUT  range low.
    SL = HIGH of the ATM PUT's 09:16 candle.
  * SELL CALL when  index < index range low    AND  ATM CALL < ATM CALL range low.
    SL = HIGH of the ATM CALL's 09:16 candle.
  * No profit target: hold until the SL is hit or 15:10, whichever comes first.

This class is pure logic (no broker, no threads): it receives ticks and returns
Signals. The engine places the orders and reports fills back.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Callable

from .candles import CandleStore
from .clock import at, parse_hm, round_tick
from .config import StrategyCfg
from .models import INDEX, Contract, Tick
from .risk import RiskGuard


class State(str, Enum):
    WAIT_LOCK = "WAIT_LOCK"            # before the 09:15 open
    BUILDING_RANGE = "BUILDING_RANGE"  # 09:15 - 09:19
    SCANNING = "SCANNING"              # watching for a breakout
    ENTRY_PENDING = "ENTRY_PENDING"
    IN_TRADE = "IN_TRADE"
    EXIT_PENDING = "EXIT_PENDING"
    HALTED = "HALTED"                  # kill switch / daily loss limit
    DONE = "DONE"                      # finished for the day (or no trade possible)


@dataclass
class Signal:
    kind: str                # "ENTER" | "EXIT"
    side: str                # option type being sold: "CE" | "PE"
    contract: Contract
    reason: str
    qty: int = 0
    limit_price: float = 0.0
    sl_price: float = 0.0
    ref_price: float = 0.0


@dataclass
class Position:
    contract: Contract
    side: str
    qty: int
    entry: float
    sl: float
    entry_ts: datetime


Say = Callable[[str, str], None]


class ORBStrategy:
    def __init__(self, cfg: StrategyCfg, risk: RiskGuard, store: CandleStore, say: Say) -> None:
        self.cfg, self.risk, self.store, self.say = cfg, risk, store, say
        self.state = State.WAIT_LOCK
        self.entries_paused = False
        self.trades_taken = 0
        self.trades: list[dict] = []
        self.pos: Position | None = None
        self.ranges: dict[str, tuple[float, float]] = {}   # INDEX/CE/PE -> (high, low)
        self.sl_ref: dict[str, float] = {}                 # CE/PE -> high of the 09:16 candle
        self.ce: Contract | None = None
        self.pe: Contract | None = None
        self._armed = {"CE": True, "PE": True}
        self._pending: Signal | None = None

    # ------------------------------------------------------------------ setup
    def lock(self, day: date, ce: Contract, pe: Contract) -> None:
        self.day, self.ce, self.pe = day, ce, pe
        self.t_start = at(day, parse_hm(self.cfg.range_start))
        self.t_end = self.t_start + timedelta(minutes=self.cfg.range_candles)
        self.t_sl_minute = at(day, parse_hm(self.cfg.sl_candle))
        self.t_cutoff = at(day, parse_hm(self.cfg.entry_cutoff))
        self.t_exit = at(day, parse_hm(self.cfg.exit_time))
        self.state = State.BUILDING_RANGE
        self.say("INFO", f"ATM locked: sell-side candidates {ce.symbol} / {pe.symbol} "
                         f"(strike {ce.strike:g}, lot {ce.lot_size})")

    def stop_day(self, reason: str, state: State = State.DONE) -> None:
        self.state = state
        self.say("WARN", reason)

    # ------------------------------------------------------------- market data
    def on_tick(self, tick: Tick) -> Signal | None:
        if self.state in (State.WAIT_LOCK, State.DONE, State.HALTED,
                          State.ENTRY_PENDING, State.EXIT_PENDING):
            return None
        ts = tick.ts
        if self.state == State.BUILDING_RANGE:
            if ts < self.t_end:
                return None
            self._finalise_range()
        if self.state == State.SCANNING:
            return self._scan(ts)
        if self.state == State.IN_TRADE:
            return self._manage(tick)
        return None

    def check_time(self, ts: datetime) -> Signal | None:
        """Called from a 1-second timer so the 15:10 exit never depends on a tick arriving."""
        if self.state == State.IN_TRADE and ts >= self.t_exit:
            return self._exit_signal("TIME", ts)
        if self.state == State.SCANNING and ts >= self.t_exit:
            self.stop_day("15:10 reached with no open trade - done for the day")
        return None

    # ------------------------------------------------------------------ range
    def _finalise_range(self) -> None:
        roles = {"INDEX": INDEX, "CE": self.ce.symbol, "PE": self.pe.symbol}
        missing, ranges = [], {}
        for role, sym in roles.items():
            hl = self.store.high_low(sym, self.t_start, self.t_end)
            first = self.store.candle(sym, self.t_start)
            if hl is None or first is None:
                missing.append(role)
            else:
                ranges[role] = hl
        sl_ref = {}
        for role, c in (("CE", self.ce), ("PE", self.pe)):
            cd = self.store.candle(c.symbol, self.t_sl_minute)
            if cd is None:
                missing.append(f"{role} {self.cfg.sl_candle} candle")
            else:
                sl_ref[role] = cd.h
        if missing:
            self.stop_day("No trade today - missing opening data for: " + ", ".join(missing))
            return
        self.ranges, self.sl_ref = ranges, sl_ref
        self.state = State.SCANNING
        r = ranges
        self.say("INFO",
                 f"Range locked  INDEX {r['INDEX'][1]:g}-{r['INDEX'][0]:g} | "
                 f"CE {r['CE'][1]:g}-{r['CE'][0]:g} | PE {r['PE'][1]:g}-{r['PE'][0]:g} | "
                 f"SL refs: CE {sl_ref['CE']:g}, PE {sl_ref['PE']:g}")

    # ------------------------------------------------------------------ entry
    def _scan(self, ts: datetime) -> Signal | None:
        if ts >= self.t_exit:
            self.stop_day("15:10 reached with no open trade - done for the day")
            return None
        if ts >= self.t_cutoff or self.entries_paused or self.trades_taken >= self.cfg.max_trades_per_day:
            return None
        syms = {"INDEX": INDEX, "CE": self.ce.symbol, "PE": self.pe.symbol}
        px: dict[str, float] = {}
        for role, sym in syms.items():
            p, t = self.store.last_price.get(sym), self.store.last_ts.get(sym)
            if p is None or t is None or (ts - t).total_seconds() > self.cfg.max_tick_age_s:
                return None                      # stale or missing price: do nothing
            px[role] = p
        idx_hi, idx_lo = self.ranges["INDEX"]
        conds = (
            ("PE", px["INDEX"] > idx_hi and px["PE"] < self.ranges["PE"][1]),
            ("CE", px["INDEX"] < idx_lo and px["CE"] < self.ranges["CE"][1]),
        )
        for side, cond in conds:
            if not cond:
                self._armed[side] = True         # condition cleared -> allowed to fire again
                continue
            if not self._armed[side]:
                continue
            self._armed[side] = False            # edge-triggered: one attempt per breakout
            sig = self._build_entry(side)
            if sig:
                self.state = State.ENTRY_PENDING
                self._pending = sig
                return sig
        return None

    def _build_entry(self, side: str) -> Signal | None:
        c = self.pe if side == "PE" else self.ce
        ltp = self.store.last_price[c.symbol]
        limit = round_tick(ltp - self.cfg.entry_limit_buffer)
        sl = round_tick(self.sl_ref[side])
        if sl <= limit:
            self.say("WARN", f"{side} breakout seen but SL {sl:g} is not above entry {limit:g} - skipped")
            return None
        lots = self.risk.lots_for(sl - limit, c.lot_size, self.cfg.lots)
        if lots < 1:
            self.say("WARN",
                     f"{side} breakout skipped: 1 lot risks {(sl - limit) * c.lot_size:,.0f} "
                     f"but only {self.risk.budget_left():,.0f} of the daily loss budget is left")
            return None
        reason = (f"INDEX {self.store.last_price[INDEX]:g} broke range, {side} {ltp:g} "
                  f"broke its range low {self.ranges[side][1]:g}")
        return Signal("ENTER", side, c, reason, qty=lots * c.lot_size,
                      limit_price=limit, sl_price=sl, ref_price=ltp)

    # ----------------------------------------------------------------- manage
    def _manage(self, tick: Tick) -> Signal | None:
        p = self.pos
        if tick.ts >= self.t_exit:
            return self._exit_signal("TIME", tick.ts)
        if tick.symbol == p.contract.symbol:
            if tick.price >= p.sl:
                return self._exit_signal("SL", tick.ts, tick.price)
            if self.risk.breached(self.open_pnl()):
                return self._exit_signal("DAILY_LOSS", tick.ts, tick.price)
        return None

    def _exit_signal(self, reason: str, ts: datetime, ref: float | None = None) -> Signal:
        p = self.pos
        self.state = State.EXIT_PENDING
        ref = ref if ref is not None else self.store.last_price.get(p.contract.symbol, p.entry)
        return Signal("EXIT", p.side, p.contract, reason, qty=p.qty, ref_price=ref)

    def request_exit(self, reason: str, ts: datetime) -> Signal | None:
        """Used by the kill switch and the feed watchdog."""
        if self.state == State.IN_TRADE:
            return self._exit_signal(reason, ts)
        return None

    # ------------------------------------------------- fills reported by engine
    def on_entry_filled(self, price: float, qty: int, ts: datetime) -> None:
        sig = self._pending
        self.pos = Position(sig.contract, sig.side, qty, price, sig.sl_price, ts)
        self.trades_taken += 1
        self.state = State.IN_TRADE
        self._pending = None
        self.say("TRADE", f"SOLD {qty} {sig.contract.symbol} @ {price:g} | SL {sig.sl_price:g} | {sig.reason}")

    def on_entry_failed(self, reason: str) -> None:
        self.state = State.SCANNING
        self._pending = None
        self.say("WARN", f"Entry not filled: {reason}")

    def on_exit_filled(self, price: float, ts: datetime, reason: str) -> None:
        p = self.pos
        pnl = RiskGuard.open_pnl(p.entry, price, p.qty)
        self.risk.realised += pnl
        self.trades.append({
            "symbol": p.contract.symbol, "side": p.side, "qty": p.qty,
            "entry": p.entry, "exit": price, "sl": p.sl, "pnl": round(pnl, 2),
            "entry_ts": p.entry_ts.isoformat(), "exit_ts": ts.isoformat(), "reason": reason,
        })
        self.say("TRADE", f"BOUGHT BACK {p.qty} {p.contract.symbol} @ {price:g} ({reason}) | "
                          f"P&L {pnl:+,.2f} | day {self.risk.realised:+,.2f}")
        self.pos = None
        if reason in ("KILL", "DAILY_LOSS") or self.risk.breached():
            self.state = State.HALTED
            self.say("WARN", "Trading halted for the day")
        elif self.trades_taken >= self.cfg.max_trades_per_day or ts >= self.t_exit:
            self.state = State.DONE
        else:
            self.state = State.SCANNING

    # ---------------------------------------------------------------- reporting
    def open_pnl(self) -> float:
        if not self.pos:
            return 0.0
        ltp = self.store.last_price.get(self.pos.contract.symbol, self.pos.entry)
        return RiskGuard.open_pnl(self.pos.entry, ltp, self.pos.qty)

    def snapshot(self) -> dict:
        p = self.pos
        return {
            "state": self.state.value,
            "entries_paused": self.entries_paused,
            "ranges": {k: {"high": v[0], "low": v[1]} for k, v in self.ranges.items()},
            "sl_ref": self.sl_ref,
            "position": None if not p else {
                "symbol": p.contract.symbol, "side": p.side, "qty": p.qty,
                "entry": p.entry, "sl": p.sl,
                "ltp": self.store.last_price.get(p.contract.symbol),
                "open_pnl": round(self.open_pnl(), 2),
            },
            "realised": round(self.risk.realised, 2),
            "trades": self.trades,
            "trades_taken": self.trades_taken,
            "max_trades": self.cfg.max_trades_per_day,
        }
