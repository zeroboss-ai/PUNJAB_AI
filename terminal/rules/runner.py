"""
Rule engine: runs validated strategies against live prices and sends orders through TradingService.

Safety rules (each is covered by tests):
  * a signal fires ONCE, then its conditions must turn false before it can fire again; a signal that is already
    true when you start (or resume) is ignored - you wait for a fresh one;
  * a value that is not available (candles from before we were watching, indicator without enough candles, no price)
    makes its condition FALSE and says why; nothing is guessed;
  * we never trust an order reply that is unclear: positions are re-read, open orders are swept, and if the
    truth still cannot be established the strategy HALTS with "ACTION REQUIRED" instead of acting again;
  * exits close the exact quantity this strategy holds (never the whole position of a key), cancel the exchange
    stop-loss first and confirm it is gone, retry with a wider price, and halt loudly if the position stays open;
  * if an exchange stop-loss is used, the software stop-loss stands down (no double exit); if that order cannot be
    placed, the position is closed straight away;
  * strategies do not survive a restart of the terminal. A position left open by a crash must be handled by hand.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from ..clock import at, parse_hm
from ..market.chain import INDEX_UNDERLYINGS, ChainUnavailable
from ..market.models import Quote
from ..models import OrderError, OrderUnknown
from ..trading.models import OrderTicket
from . import book as B
from .spec import FIELDS, Condition, EntryRule, Operand, Strategy, condition_text, operand_text, parse

log = logging.getLogger("rules")

FILL_WAIT_S = 8.0
EXIT_ATTEMPTS = 3
SL_POLL_S = 2.0
STALE_S = 10.0


# ------------------------------------------------------------------------------------- market access
@dataclass
class ChainInfo:
    expiry: str
    lot: int
    spot_key: str | None
    rows: dict[float, dict[str, tuple[str, str]]]          # strike -> {"CE": (key, symbol), "PE": (key, symbol)}


@dataclass
class FutInfo:
    key: str
    symbol: str
    lot: int
    expiry: str
    tick: float = 0.05


class Market(Protocol):
    def now(self) -> datetime: ...
    def ltp(self, key: str) -> float | None: ...
    def quote(self, key: str) -> Quote | None: ...
    def feed_age(self) -> float | None: ...
    def chain(self, und: str, expiry_sel: str) -> ChainInfo: ...
    def spot_key(self, und: str) -> str | None: ...
    def expiries(self, und: str) -> list[str]: ...
    def future(self, und: str, expiry_sel: str) -> "FutInfo | None": ...
    def watch(self, owner: str, keys: list[str]) -> None: ...
    def unwatch(self, owner: str) -> None: ...


class HubMarket:
    """The production Market: the shared quote hub + the option chain service."""

    def __init__(self, hub, chain, catalog=None) -> None:
        self.hub, self._chain, self._cat = hub, chain, catalog

    def now(self) -> datetime:
        from ..clock import now_ist
        return now_ist()

    def ltp(self, key):
        return self.hub.ltp(key)

    def quote(self, key):
        return self.hub.get(key)

    def feed_age(self):
        return self.hub.age_s()

    def spot_key(self, und):
        u = next((u for u in INDEX_UNDERLYINGS if u["name"] == und), None)
        if u:
            return u["spot_key"]
        if self._cat is not None:                                  # a commodity: its front-month future is the reference price
            try:
                f = self._cat.mcx_future(und)
                return f.key if f else None
            except Exception:
                return None
        return None

    def expiries(self, und):
        return self._chain.expiries(und)

    def future(self, und, expiry_sel):
        """The index future for 'nearest' / 'next' (monthly = nearest). None if the instrument list has none."""
        if self._cat is None:
            return None
        try:
            hits = [i for i in self._cat.search(f"{und} FUT", 40) if i.kind == "FUT" and i.name == und and i.expiry]
        except Exception:
            return None
        today = self.now().date()
        hits = sorted((i for i in hits if i.expiry >= today), key=lambda i: i.expiry)
        if not hits:
            return None
        i = hits[1] if expiry_sel == "next" and len(hits) > 1 else hits[0]
        return FutInfo(i.key, i.symbol, i.lot, i.expiry.isoformat(), i.tick)

    def chain(self, und, expiry_sel):
        raw = self._chain.raw_for(und, expiry_sel)
        rows = {k: {kind: (leg.key, leg.symbol) for kind, leg in legs.items()} for k, legs in raw.rows.items()}
        return ChainInfo(raw.expiry, raw.lot, raw.spot_key, rows)

    def watch(self, owner, keys):
        self.hub.want(owner, keys)

    def unwatch(self, owner):
        self.hub.release_owner(owner)


@dataclass
class Env:
    market: Market
    book: B.CandleBook
    trading: object                                  # TradingService (or a test double with the same methods)
    sleep: Callable[[float], None] = time.sleep
    audit: Callable[[dict], None] | None = None
    notify: Callable[[str, str], None] | None = None       # (kind, text) -> alerts; must never raise or block
    fill_wait_s: float = FILL_WAIT_S
    exit_attempts: int = EXIT_ATTEMPTS
    sl_poll_s: float = SL_POLL_S
    stale_s: float = STALE_S


# ------------------------------------------------------------------------------------------ position
@dataclass
class Pos:
    leg: str
    key: str
    symbol: str
    label: str
    side: str                       # S | B
    qty: int
    lot: int
    entry_price: float
    entry_time: str
    sl: float | None = None
    target: float | None = None
    sl_oid: str | None = None
    status: str = "open"            # open | closed | attention
    exit_price: float | None = None
    exit_time: str = ""
    exit_reason: str = ""
    estimated: bool = False         # exit price estimated (could not be read from the order book)
    entry_oid: str = ""
    next_poll: float = 0.0
    sl_init: float | None = None    # leg builder: the stop-loss when the position was opened (trailing counts from it)
    trail_steps: int = 0

    @property
    def sign(self) -> int:
        return 1 if self.side == "B" else -1

    def pnl(self, ltp: float | None) -> float:
        px = self.exit_price if self.status == "closed" else ltp
        return 0.0 if px is None else round((px - self.entry_price) * self.qty * self.sign, 2)

    def to_dict(self, ltp: float | None) -> dict:
        return {"leg": self.leg, "key": self.key, "symbol": self.symbol, "label": self.label, "side": self.side, "qty": self.qty,
                "lots": self.qty // self.lot if self.lot > 1 else self.qty, "entry_price": self.entry_price,
                "entry_time": self.entry_time, "sl": self.sl, "target": self.target, "exchange_sl": bool(self.sl_oid),
                "status": self.status, "exit_price": self.exit_price, "exit_time": self.exit_time, "exit_reason": self.exit_reason,
                "estimated": self.estimated, "ltp": ltp, "pnl": self.pnl(ltp)}


def _tag(name: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return (t or "strategy")[:20]


def _cmp(op: str, l: float, r: float, prev: tuple[float, float] | None) -> bool:
    if op == ">":
        return l > r
    if op == ">=":
        return l >= r
    if op == "<":
        return l < r
    if op == "<=":
        return l <= r
    if prev is None:
        return False
    pl, pr = prev
    if op == "crosses_above":
        return pl <= pr and l > r
    return pl >= pr and l < r                       # crosses_below


# ------------------------------------------------------------------------------------------- runner
class Runner:
    def __init__(self, sid: int, spec: Strategy, env: Env) -> None:
        self.sid, self.s, self.env = sid, spec, env
        self.m, self.book, self.trading = env.market, env.book, env.trading
        self.owner = f"rules:{sid}"
        self.tag = _tag(spec.name)
        self.status = "starting"                    # starting | waiting | watching | done | halted | stopped
        self.paused = False
        self.stop_requested = False
        self.killed = False
        self.loss_stopped = False
        self.halt_reason = ""
        self.message = ""
        self.spot_key = self.m.spot_key(spec.underlying)
        self.legs: dict[str, dict] = {}
        self.positions: list[Pos] = []
        self.entries_done = 0
        self.failed_entries = 0
        self.armed = {r.id: False for r in getattr(spec, "entries", [])}
        self._prev: dict[tuple, tuple[float, float]] = {}
        self.evals: dict[str, dict] = {}
        self.exit_eval: dict | None = None
        self._logs: deque = deque(maxlen=300)
        self._snap: dict = {}
        self._thread: threading.Thread | None = None
        self._next_warm = 0.0
        self._next_lock_try = 0.0
        self.now_dt: datetime = self.m.now()
        self.now_ts = self.now_dt.timestamp()
        self.day = self.now_dt.date()
        self.expiry = ""
        self.lot = 1

    # ----------------------------------------------------------------------------- lifecycle
    def start(self, threaded: bool = True) -> None:
        now = self.m.now()
        if now >= at(now.date(), parse_hm(self.s.exit.time)):
            raise RuntimeError(f"It is already past this strategy's exit time ({self.s.exit.time}).")
        if not self.spot_key:
            raise RuntimeError(f"No price source known for {self.s.underlying} (commodity contract list not loaded yet?).")
        self.now_dt, self.now_ts, self.day = now, now.timestamp(), now.date()
        o_hm, c_hm = self._hours()
        if self.s.exit.time > c_hm and o_hm == "09:00":
            raise RuntimeError(f"The exit time {self.s.exit.time} is after today's commodity market close ({c_hm}). Edit the strategy's exit time first.")
        self.book.watch(self.spot_key, self.now_ts)
        self.m.watch(self.owner, [self.spot_key])
        self.status = "waiting"
        self.say("Started. Signals that are already true now are ignored until they turn false and true again.", notable=True)
        self._publish()
        if threaded:
            self._thread = threading.Thread(target=self._loop, name=f"rules-{self.sid}", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        try:
            while not self.killed and self.status in ("waiting", "watching"):
                try:
                    self.step()
                except Exception as e:                                   # never keep trading after something unexpected
                    log.exception("strategy %s crashed", self.sid)
                    self.halt(f"Unexpected error ({type(e).__name__}: {str(e)[:120]}). Check the Orders and Positions pages.")
                    break
                self.env.sleep(0.25)
        finally:
            self.m.unwatch(self.owner)
            self._publish()

    def _entries_full(self) -> bool:
        mx = self.s.limits.max_entries
        return mx > 0 and self.entries_done >= mx

    def pause(self) -> None:
        self.paused = True
        self.say("Paused: no new entries. Open positions keep their stop-loss and exit rules.", notable=True)

    def resume(self) -> None:
        self.paused = False
        self.say("Resumed. Only a fresh signal (false, then true) will enter.", notable=True)

    def stop(self) -> None:
        """No more entries; close what is open at the first step; then stop."""
        self.stop_requested = True
        self.say("Stop requested: closing open positions.", notable=True)

    def kill(self, why: str = "Stopped by Stop-all") -> None:
        """Immediate halt without trading (Stop-all closes positions itself). Also used when shutting down."""
        self.killed = True
        if self.status not in ("done", "halted", "stopped"):
            for p in self.positions:
                if p.status == "open":
                    p.status = "attention"
            self.status, self.halt_reason = "halted", why
            self.say(why, "warn")
        self._publish()

    def halt(self, msg: str) -> None:
        self.status, self.halt_reason = "halted", msg
        self.say(f"ACTION REQUIRED: {msg}", "error")
        self._publish()

    @property
    def active(self) -> bool:
        return self.status in ("starting", "waiting", "watching")

    # ------------------------------------------------------------------------------------ logging
    def say(self, msg: str, level: str = "info", notable: bool = False) -> None:
        t = self.m.now().strftime("%H:%M:%S")
        if self.env.notify and (notable or level != "info"):
            try:
                self.env.notify("error" if level == "error" else "warning" if level == "warn" else "strategy", f"{self.s.name}: {msg}")
            except Exception:
                pass
        self._logs.append({"t": t, "level": level, "msg": msg})
        getattr(log, "error" if level == "error" else "info")("[%s] %s", self.s.name, msg)
        if self.env.audit:
            try:
                self.env.audit({"ts": self.m.now().isoformat(timespec="seconds"), "sid": self.sid, "name": self.s.name, "level": level, "msg": msg})
            except Exception:
                pass

    # ------------------------------------------------------------------------------ evaluation
    def _hours(self) -> tuple[str, str]:
        """Session open/close the candles are aligned to: commodities start 09:00, everything else 09:15 - 15:30."""
        from ..market import commodity
        und = getattr(self.s, "underlying", "")
        return commodity.session_hm(und, self.day) if commodity.is_commodity(und) else ("09:15", "15:30")

    def _key(self, src: str, self_leg: str | None) -> str | None:
        if src == "INDEX":
            return self.spot_key
        if src == "SELF":
            src = self_leg or ""
        leg = self.legs.get(src)
        return leg["key"] if leg else None

    def _val(self, o: Operand, self_leg: str | None, why: list[str]) -> float | None:
        if o.kind == "number":
            return o.value
        key = self._key(o.src, self_leg)
        if key is None:
            why.append("strikes are not chosen yet")
            return None
        v: float | None = None
        k = o.kind
        if k == "ltp":
            v = self.m.ltp(key)
        elif k in ("open_range_high", "open_range_low"):
            r = self.book.open_range(key, B.minute_at(self.day, o.start), o.candles, self.now_ts)
            v = None if r is None else (r[0] if k == "open_range_high" else r[1])
        elif k == "candle":
            c = self.book.fixed_candle(key, B.minute_at(self.day, o.at), self.now_ts)
            v = None if c is None else c[FIELDS.index(o.field)]
        elif k == "prev_candle":
            c = self.book.prev_candle(key, o.tf, self.day, self.now_ts, self._hours())
            v = None if c is None else c[FIELDS.index(o.field)]
        elif k in ("day_high", "day_low", "day_open", "prev_close"):
            q = self.m.quote(key)
            v = None if q is None else {"day_high": q.high, "day_low": q.low, "day_open": q.open, "prev_close": q.prev_close}[k]
        elif k in ("ema", "sma", "rsi"):
            closes = self.book.closes(key, o.tf, self.day, self.now_ts, self._hours())
            fn = {"ema": B.ema, "sma": B.sma, "rsi": B.rsi}[k]
            v = fn(closes, o.period)
        if v is None:
            why.append(f"{operand_text(o, self.s, self_leg)}: not available yet")
            return None
        return v + o.offset

    def _cond(self, rid: str, i: int, c: Condition, self_leg: str | None) -> dict:
        why: list[str] = []
        l, r = self._val(c.left, self_leg, why), self._val(c.right, self_leg, why)
        ok = False
        if l is not None and r is not None:
            ok = _cmp(c.op, l, r, self._prev.get((rid, i)))
            self._prev[(rid, i)] = (l, r)
        return {"text": condition_text(c, self.s, self_leg), "l": l, "r": r, "ok": ok, "why": why}

    def _group(self, rid: str, conds: list[Condition], match: str, self_leg: str | None) -> dict:
        res = [self._cond(rid, i, c, self_leg) for i, c in enumerate(conds)]
        ok = True if not res else (all(x["ok"] for x in res) if match == "all" else any(x["ok"] for x in res))
        return {"ok": ok, "match": match, "conds": res}

    # ------------------------------------------------------------------------------------ step
    def step(self) -> None:
        now = self.m.now()
        self.now_dt, self.now_ts, self.day = now, now.timestamp(), now.date()
        if self.status not in ("waiting", "watching"):
            return
        if self.now_dt < at(self.day, parse_hm(self.s.limits.window_start)):
            for rid in self.armed:                                       # before the entry window every signal is "false", so it is armed
                self.armed[rid] = True
        if not self.legs:
            if self.stop_requested:
                self.status = "stopped"
                self.say("Stopped before any strike was chosen.")
                return self._publish()
            self._pre_lock()
        if self.legs:
            self._manage_positions()
            if self.status in ("waiting", "watching"):
                self._entries()
            self._maybe_finish()
        self._publish()

    # ---- strikes
    def _pre_lock(self) -> None:
        s, now = self.s, self.now_dt
        lock_dt = at(self.day, parse_hm(s.lock_time))
        exit_dt = at(self.day, parse_hm(s.exit.time))
        if now >= exit_dt:
            self.status = "done"
            self.say("Exit time passed before the strikes were chosen. Nothing was traded.")
            return
        spot = self.m.ltp(self.spot_key)
        if now < lock_dt:
            self.message = f"Waiting for {s.lock_time} to choose the strikes."
            if spot and self.now_ts >= self._next_warm:                   # start watching nearby strikes early so their candles start on time
                self._next_warm = self.now_ts + 60
                try:
                    self._watch_near(spot)
                except ChainUnavailable as e:
                    self.say(f"Could not read the option chain yet: {e}", "warn")
            return
        if self.now_ts < self._next_lock_try:
            return
        if not spot:
            self.message = "Waiting for the index price."
            return
        self._next_lock_try = self.now_ts + 5
        try:
            info = self.m.chain(s.underlying, s.expiry)
        except ChainUnavailable as e:
            self.message = f"Cannot read the option chain: {e}"
            self.say(self.message, "warn")
            return
        strikes = sorted(info.rows)
        atm = min(strikes, key=lambda k: abs(k - spot))
        i = strikes.index(atm)
        chosen: dict[str, dict] = {}
        for leg, off in (("CE", s.offset_ce), ("PE", s.offset_pe)):
            j = i + off
            if not 0 <= j < len(strikes) or leg not in info.rows[strikes[j]]:
                return self.halt(f"The {leg} strike {off:+d} steps from ATM is not in the option chain.")
            key, sym = info.rows[strikes[j]][leg]
            chosen[leg] = {"key": key, "symbol": sym, "strike": strikes[j]}
        self.legs, self.expiry, self.lot = chosen, info.expiry, info.lot
        keys = [v["key"] for v in chosen.values()]
        for k in keys:
            self.book.watch(k, self.now_ts)
        self.m.watch(self.owner, [self.spot_key] + keys)
        self.status = "watching"
        self.message = ""
        self.say(f"Strikes locked at index {spot:g} (ATM {atm:g}), expiry {info.expiry}: Call {chosen['CE']['strike']:g} "
                 f"({chosen['CE']['symbol']}), Put {chosen['PE']['strike']:g} ({chosen['PE']['symbol']}).", notable=True)
        self._data_notes()

    def _watch_near(self, spot: float) -> None:
        info = self.m.chain(self.s.underlying, self.s.expiry)
        strikes = sorted(info.rows)
        atm = min(strikes, key=lambda k: abs(k - spot))
        i = strikes.index(atm)
        keys = []
        for j in range(max(0, i - 4), min(len(strikes), i + 5)):
            for kind in ("CE", "PE"):
                if kind in info.rows[strikes[j]]:
                    keys.append(info.rows[strikes[j]][kind][0])
        for k in keys:
            self.book.watch(k, self.now_ts)
        self.m.watch(self.owner, [self.spot_key] + keys)

    def _operands(self):
        for r in self.s.entries:
            for c in r.conditions:
                yield c.left, r.leg
                yield c.right, r.leg
        for c in self.s.exit.conditions:
            yield c.left, None
            yield c.right, None
        if self.s.exit.stop.level:
            yield self.s.exit.stop.level, None

    def _data_notes(self) -> None:
        seen = set()
        for o, leg in self._operands():
            if o.kind not in ("open_range_high", "open_range_low", "candle"):
                continue
            hm = o.at if o.kind == "candle" else o.start
            for l in ([leg] if leg and o.src == "SELF" else [o.src] if o.src != "SELF" else ["CE", "PE"]):
                key = self._key(l, leg)
                since = self.book.since(key) if key else None
                if since is not None and since > B.minute_at(self.day, hm) * 60 + B.LATE_OK_S and (key, hm) not in seen:
                    seen.add((key, hm))
                    self.say(f"Not enough data: a value needs the {hm} candle, but this terminal only started watching "
                             f"{l if l != 'INDEX' else 'the index'} at {datetime.fromtimestamp(since, self.now_dt.tzinfo):%H:%M:%S}. "
                             f"Conditions using it will stay false today. Start the strategy before {hm}.", "warn")

    # ---- entries
    def _entries(self) -> None:
        s, lm = self.s, self.s.limits
        w0, w1 = at(self.day, parse_hm(lm.window_start)), at(self.day, parse_hm(lm.window_end))
        in_window = w0 <= self.now_dt < w1
        age = self.m.feed_age()
        stale = age is None or age > self.env.stale_s
        waiting = []
        held_stale = False
        for r in s.entries:
            res = self._group(r.id, r.conditions, r.match, r.leg)
            ok = bool(res["ok"] and in_window)
            self.evals[r.leg] = {"side": r.side, "leg": r.leg, "match": r.match, "ok": ok, "in_window": in_window,
                                 "armed": self.armed[r.id], "conds": res["conds"]}
            for c in res["conds"]:
                waiting += c["why"][:1]
            if not ok:
                self.armed[r.id] = True
                continue
            if not self.armed[r.id]:
                continue
            blocked = (self.paused or self.stop_requested or self.loss_stopped or self._entries_full()
                       or any(p.leg == r.leg and p.status != "closed" for p in self.positions) or self.failed_entries >= 3)
            if blocked:
                self.armed[r.id] = False                                  # that signal is spent
                continue
            if stale:
                held_stale = True
                continue
            self.armed[r.id] = False
            verb = "SELL" if r.side == "S" else "BUY"
            if res["conds"]:
                self.say(f"Signal: {verb} {r.leg}: " + (" AND " if r.match == "all" else " OR ").join(x["text"] for x in res["conds"]), notable=True)
            else:
                self.say(f"Signal: {verb} {r.leg} (entry window open)", notable=True)
            self._enter(r)
            if self.status not in ("waiting", "watching"):
                return
        if held_stale:
            self.message = "Holding: the price feed has no recent update."
        elif not in_window and self.now_dt < w0:
            self.message = f"Entry window opens at {lm.window_start}."
        elif self._entries_full():
            self.message = "Entry limit for today reached."
        elif self.paused:
            self.message = "Paused: no new entries."
        else:
            self.message = ("Waiting for data: " + waiting[0]) if waiting else "Watching for a signal."

    def _enter(self, r: EntryRule) -> bool:
        s, lm = self.s, self.s.limits
        leg = self.legs[r.leg]
        key = leg["key"]
        ltp = self.m.ltp(key)
        if not ltp:
            self.say(f"No price for {leg['symbol']} yet - not entering.", "warn")
            return False
        sl = None
        if s.exit.stop.mode == "level":
            why: list[str] = []
            sl = self._val(s.exit.stop.level, r.leg, why)
            if sl is None:
                self.say(f"Cannot set the stop-loss level ({why[0] if why else 'no data'}) - not entering.", "warn")
                return False
            if (r.side == "S" and sl <= ltp) or (r.side == "B" and sl >= ltp):
                self.say(f"The stop-loss level {sl:g} is already beyond the price {ltp:g} - not entering.", "warn")
                return False
        try:
            net0 = self.trading.net_qty(key, s.product)
        except OrderError as e:
            net0 = None
        if net0 is None:
            self.say("Cannot read your positions, so the entry is skipped (it could not be checked).", "warn")
            return False
        price = self.trading.limit_through(key, r.side, ltp, lm.slippage_pct)
        t = OrderTicket(key=key, side=r.side, lots=s.lots, product=s.product, order_type="L", price=price, tag=self.tag)
        try:
            res = self.trading.place(t)
        except OrderUnknown as e:
            self.say(f"Entry reply unclear: {e}. Checking positions.", "warn")
            return self._settle_entry(r, key, None, net0, sl, ltp)
        except OrderError as e:
            self.failed_entries += 1
            self.say(f"Entry order refused: {e}", "warn")
            if self.failed_entries >= 3:
                self.halt("Three entry orders in a row were refused. Check the reason above, then restart the strategy.")
            return False
        self.say(f"Entry order {res['order_id']}: {'SELL' if r.side == 'S' else 'BUY'} {res['qty']} {leg['symbol']} limit {price:g}")
        return self._settle_entry(r, key, res["order_id"], net0, sl, ltp)

    def _held(self, key: str, side: str) -> int | None:
        """Units held in `key` in the direction of `side` (S = short units, B = long units), None if unreadable."""
        net = self.trading.net_qty(key, self.s.product)
        if net is None:
            return None
        return -net if side == "S" else net

    def _settle_entry(self, r: EntryRule, key: str, oid: str | None, net0: int, sl: float | None, ltp: float) -> bool:
        leg = self.legs[r.leg]
        row = None
        if oid:
            row = self._wait_done(oid, self.env.fill_wait_s)
            if row is not None and row.is_open:
                st, row = self._cancel_verified(oid)
                if st == "unknown":
                    self.halt(f"Could not confirm whether entry order {oid} ({leg['symbol']}) is cancelled. "
                              f"Check the Orders and Positions pages before restarting.")
                    return False
        else:
            if self._sweep_open_orders(key) == "unknown":
                return False
        net1 = self.trading.net_qty(key, self.s.product)
        if net1 is None:
            self.halt(f"Cannot read positions after the entry order for {leg['symbol']}. Check the Orders and Positions pages.")
            return False
        held = (net0 - net1) if r.side == "S" else (net1 - net0)
        if held <= 0:
            why = f" ({row.status}{': ' + row.reason if row and row.reason else ''})" if row else ""
            self.failed_entries += 1
            self.say(f"The entry order did not fill{why}. No position was taken.", "warn")
            if self.failed_entries >= 3:
                self.halt("Three entry attempts in a row did not fill. Check liquidity and the reason above, then restart.")
            return False
        px = row.avg_price if row is not None and row.filled > 0 and row.avg_price else ltp
        inst = self.trading.cat.get(key)
        pos = Pos(leg=r.leg, key=key, symbol=leg["symbol"], label=inst.label() if inst else leg["symbol"], side=r.side, qty=held,
                  lot=inst.lot if inst else self.lot, entry_price=round(px, 2), entry_time=self.now_dt.strftime("%H:%M:%S"),
                  entry_oid=oid or "")
        pos.estimated = row is None or not row.avg_price
        self.positions.append(pos)
        self.entries_done += 1
        self.failed_entries = 0
        st = self.s.exit.stop
        if st.mode == "points":
            sl = px + st.value if r.side == "S" else px - st.value
        elif st.mode == "percent":
            sl = px * (1 + st.value / 100) if r.side == "S" else px * (1 - st.value / 100)
        tk = inst.tick if inst else 0.05
        pos.sl = None if sl is None else round(round(sl / tk) * tk, 2)
        tg = self.s.exit.target
        if tg.mode != "none":
            d = tg.value if tg.mode == "points" else px * tg.value / 100
            pos.target = round(round((px - d if r.side == "S" else px + d) / tk) * tk, 2)
        self.say(f"In position: {'SOLD' if r.side == 'S' else 'BOUGHT'} {held} {pos.symbol} at {pos.entry_price:g}"
                 f"{' (price estimated)' if pos.estimated else ''}. Stop-loss {pos.sl if pos.sl is not None else 'none'}, "
                 f"target {pos.target if pos.target is not None else 'none'}.", notable=True)
        if held < self.s.lots * pos.lot:
            self.say(f"Only {held} of {self.s.lots * pos.lot} units filled; managing the {held} we hold.", "warn")
        if self.s.exit.exchange_sl and pos.sl is not None:
            self._place_exchange_sl(pos)
        return True

    def _place_exchange_sl(self, pos: Pos) -> None:
        opp = "B" if pos.side == "S" else "S"
        try:
            pos.sl_oid = self.trading.stop_exact(pos.key, opp, pos.qty, self.s.product, self.tag, pos.sl, self.s.limits.slippage_pct)
            self.say(f"Exchange stop-loss placed: trigger {pos.sl:g} (order {pos.sl_oid}). The software stop-loss stands down while it is active.", notable=True)
            pos.next_poll = self.now_ts + self.env.sl_poll_s
        except (OrderError, OrderUnknown) as e:
            self.say(f"The exchange stop-loss could not be placed ({e}). Closing the position now rather than running without a stop.", "error")
            self._close(pos, "Stop-loss order could not be placed")

    # ---- managing open positions
    def pnl(self) -> dict:
        real = sum(p.pnl(None) for p in self.positions if p.status == "closed")
        unreal = sum(p.pnl(self.m.ltp(p.key)) for p in self.positions if p.status != "closed")
        return {"realised": round(real, 2), "unrealised": round(unreal, 2), "total": round(real + unreal, 2)}

    def _manage_positions(self) -> None:
        live = [p for p in self.positions if p.status == "open"]
        if not live:
            return
        ml = self.s.limits.max_loss
        if ml > 0 and not self.loss_stopped and self.pnl()["total"] <= -ml:
            self.loss_stopped = True
            self.say(f"Max loss reached (₹{self.pnl()['total']:,.0f}). Closing positions and stopping.", "warn")
        exit_dt = at(self.day, parse_hm(self.s.exit.time))
        if self.s.exit.conditions:
            self.exit_eval = self._group("exit", self.s.exit.conditions, self.s.exit.match, live[0].leg)
        for p in live:
            reason = None
            if self.stop_requested:
                reason = "Stopped by you"
            elif self.loss_stopped:
                reason = "Max loss reached"
            elif self.now_dt >= exit_dt:
                reason = "Exit time"
            else:
                if p.sl_oid and self.now_ts >= p.next_poll:
                    p.next_poll = self.now_ts + self.env.sl_poll_s
                    self._poll_sl(p)
                    if p.status != "open":
                        continue
                ltp = self.m.ltp(p.key)
                if ltp:
                    if p.sl is not None and not p.sl_oid and ((p.side == "S" and ltp >= p.sl) or (p.side == "B" and ltp <= p.sl)):
                        reason = f"Stop-loss ({p.sl:g})"
                    elif p.target is not None and ((p.side == "S" and ltp <= p.target) or (p.side == "B" and ltp >= p.target)):
                        reason = f"Target ({p.target:g})"
                if reason is None and self.s.exit.conditions:
                    ev = self._group(f"exit-{p.leg}", self.s.exit.conditions, self.s.exit.match, p.leg)
                    if ev["ok"]:
                        reason = "Exit rule"
            if reason:
                self._close(p, reason)
                if self.status not in ("waiting", "watching"):
                    return

    def _poll_sl(self, p: Pos) -> None:
        try:
            row = self.trading.order_row(p.sl_oid)
        except OrderUnknown:
            self.say("Could not read the order book to check the exchange stop-loss.", "warn")
            return
        if row is None:
            self.say("The exchange stop-loss order is not in the order book. The software stop-loss takes over.", "warn")
            p.sl_oid = None
        elif row.status == "COMPLETE":
            held = self._held(p.key, p.side)
            if held is not None and held <= 0:
                self._finish(p, row.avg_price or p.sl or p.entry_price, "Stop-loss (exchange order)", estimated=not row.avg_price)
            else:
                self.say("The exchange stop-loss shows complete but a position remains - verifying.", "warn")
                p.sl_oid = None
        elif row.status in ("REJECTED", "CANCELLED"):
            self.say(f"The exchange stop-loss order is {row.status}{': ' + row.reason if row.reason else ''}. The software stop-loss takes over.", "warn")
            p.sl_oid = None

    def _close(self, p: Pos, reason: str) -> None:
        self.say(f"Closing {p.symbol}: {reason}")
        sl_row = None
        if p.sl_oid:
            st, sl_row = self._cancel_verified(p.sl_oid)
            if st == "unknown":
                p.status = "attention"
                return self.halt(f"Could not confirm whether the exchange stop-loss for {p.symbol} is cancelled (order {p.sl_oid}). "
                                 f"Check the Orders page; the position may still be open.")
            p.sl_oid = None
        if self._sweep_open_orders(p.key) == "unknown":
            p.status = "attention"
            return
        opp = "B" if p.side == "S" else "S"
        oids: list[str] = []
        start = self._held(p.key, p.side)
        if start is None:
            p.status = "attention"
            return self.halt(f"Cannot read positions while closing {p.symbol}. Check the Orders and Positions pages.")
        floor = start - min(p.qty, max(start, 0))             # what must still be held when OUR quantity is closed (e.g. a manual lot)
        for attempt in range(1, self.env.exit_attempts + 1):
            if self.killed:
                return
            held = self._held(p.key, p.side)
            if held is None:
                p.status = "attention"
                return self.halt(f"Cannot read positions while closing {p.symbol}. Check the Orders and Positions pages.")
            if held <= floor:
                break
            qty = held - floor
            pct = max(self.s.limits.slippage_pct, 1.0) * attempt
            try:
                oid = self.trading.close_exact(p.key, opp, qty, self.s.product, self.tag, pct)
            except OrderUnknown as e:
                self.say(f"Exit reply unclear: {e}. Re-checking.", "warn")
                if self._sweep_open_orders(p.key) == "unknown":
                    p.status = "attention"
                    return
                continue
            except OrderError as e:
                self.say(f"Exit order refused (attempt {attempt}): {e}", "warn")
                self.env.sleep(0.5)
                continue
            oids.append(oid)
            row = self._wait_done(oid, self.env.fill_wait_s)
            if row is not None and row.is_open:
                st, _ = self._cancel_verified(oid)
                if st == "unknown":
                    p.status = "attention"
                    return self.halt(f"Could not confirm whether closing order {oid} for {p.symbol} is cancelled. "
                                     f"Check the Orders and Positions pages.")
        held = self._held(p.key, p.side)
        if held is None:
            p.status = "attention"
            return self.halt(f"Cannot read positions after closing {p.symbol}. Check the Orders and Positions pages.")
        if held > floor:
            p.status = "attention"
            if p.sl is not None and self.s.exit.exchange_sl:
                try:
                    p.sl_oid = self.trading.stop_exact(p.key, opp, held - floor, self.s.product, self.tag, p.sl, self.s.limits.slippage_pct)
                    p.status = "open"
                except (OrderError, OrderUnknown):
                    pass
            return self.halt(f"{p.symbol} is still open ({held - floor} units) after {self.env.exit_attempts} attempts to close it. "
                             f"Close it by hand in the Kotak app{' (its exchange stop-loss was put back)' if p.status == 'open' else ''}.")
        price, est = self._avg_fill(oids, sl_row)
        self._finish(p, price if price is not None else (self.m.ltp(p.key) or p.entry_price), reason, estimated=price is None)

    def _avg_fill(self, oids: list[str], sl_row) -> tuple[float | None, bool]:
        qty = val = 0.0
        rows = []
        for oid in oids:
            try:
                rows.append(self.trading.order_row(oid))
            except OrderUnknown:
                return None, True
        if sl_row is not None:
            rows.append(sl_row)
        for r in rows:
            if r is not None and r.filled and r.avg_price:
                qty += r.filled
                val += r.filled * r.avg_price
        return (round(val / qty, 2), False) if qty else (None, True)

    def _finish(self, p: Pos, price: float, reason: str, estimated: bool = False) -> None:
        p.status, p.exit_price, p.exit_reason = "closed", round(price, 2), reason
        p.exit_time, p.estimated = self.now_dt.strftime("%H:%M:%S"), p.estimated or estimated
        self.say(f"Closed {p.symbol} at {p.exit_price:g}{' (estimated)' if estimated else ''} - {reason}. P&L ₹{p.pnl(None):,.0f}", notable=True)

    def _maybe_finish(self) -> None:
        if any(p.status in ("open", "attention") for p in self.positions):
            return
        exit_dt = at(self.day, parse_hm(self.s.exit.time))
        if self.stop_requested:
            self.status = "stopped"
            self.say("Stopped.", notable=True)
        elif self.loss_stopped:
            self.status = "done"
            self.message = "Stopped after reaching the max loss."
            self.say("Done for today (max loss).", notable=True)
        elif self.now_dt >= exit_dt:
            self.status = "done"
            self.message = "Finished for today."
            self.say("Done for today (exit time reached).", notable=True)
        elif self._entries_full():
            self.status = "done"
            self.message = "Finished for today."
            self.say("Done for today (entry limit reached and flat).", notable=True)

    # ---- order-book helpers (all tolerate an unreadable broker)
    def _wait_done(self, oid: str, secs: float):
        """Poll an order until it is no longer open. Returns its last row (None if the book was unreadable)."""
        row = None
        for i in range(int(secs / 0.5) + 1):
            try:
                row = self.trading.order_row(oid)
            except OrderUnknown:
                row = None
            if (row is not None and not row.is_open) or self.killed or i >= int(secs / 0.5):
                return row
            self.env.sleep(0.5)
        return row

    def _cancel_verified(self, oid: str):
        """Cancel and CONFIRM from the order book. Returns ('cancelled'|'complete'|'unknown', last_row)."""
        try:
            self.trading.cancel(oid)
        except (OrderUnknown, OrderError):
            pass                                                            # the re-read below decides
        row = None
        for _ in range(4):
            try:
                row = self.trading.order_row(oid)
            except OrderUnknown:
                row = None
            if row is not None:
                if row.status == "COMPLETE":
                    return "complete", row
                if not row.is_open:
                    return "cancelled", row
            self.env.sleep(0.5)
        return "unknown", row

    def _sweep_open_orders(self, key: str) -> str:
        """Cancel every open order on this instrument and confirm none are left (a stray order could double-close)."""
        for _ in range(3):
            try:
                rows = self.trading.open_orders(key)
            except OrderUnknown:
                rows = None
            if rows is not None and not rows:
                return "ok"
            for o in rows or []:
                try:
                    self.trading.cancel(o.id)
                except (OrderUnknown, OrderError):
                    pass
            self.env.sleep(0.5)
        self.halt(f"Could not confirm that no orders are left open on {key}. Check the Orders page.")
        return "unknown"

    # -------------------------------------------------------------------------------- snapshot
    def _publish(self) -> None:
        legs = {}
        for leg, v in self.legs.items():
            legs[leg] = {**v, "ltp": self.m.ltp(v["key"])}
        self._snap = {
            "id": self.sid, "name": self.s.name, "status": self.status, "paused": self.paused, "stop_requested": self.stop_requested,
            "message": self.message, "halt": self.halt_reason, "expiry": self.expiry, "lot": self.lot, "legs": legs,
            "spot": {"key": self.spot_key, "ltp": self.m.ltp(self.spot_key) if self.spot_key else None},
            "rules": list(self.evals.values()), "exit_rule": self.exit_eval,
            "positions": [p.to_dict(self.m.ltp(p.key)) for p in self.positions], "pnl": self.pnl(),
            "entries_done": self.entries_done, "max_entries": self.s.limits.max_entries,
            "log": list(self._logs)[-80:],
        }

    def snapshot(self) -> dict:
        return self._snap or {"id": self.sid, "name": self.s.name, "status": self.status, "log": list(self._logs)[-80:]}


# ----------------------------------------------------------------------------------------- manager
class RuleEngine:
    """Owns the shared candle book and one Runner per started strategy."""

    def __init__(self, store, market: Market, trading, data_dir: str | None = None, sleep: Callable[[float], None] = time.sleep,
                 notify: Callable[[str, str], None] | None = None) -> None:
        self.store, self.market, self.trading = store, market, trading
        self.book = B.CandleBook()
        self.runners: dict[int, Runner] = {}
        self._dir = Path(data_dir) if data_dir else None
        self._lock = threading.Lock()
        self.env = Env(market, self.book, trading, sleep=sleep, audit=self._audit, notify=notify)

    def attach(self, hub) -> None:
        hub.add_listener(self._on_quote)

    def _on_quote(self, q: Quote) -> None:
        if self.book.watching(q.key):
            self.book.on_tick(q.key, q.ltp, self.market.now().timestamp())

    def feed(self, key: str, price: float) -> None:
        """Test hook: same as a live tick."""
        self.book.on_tick(key, price, self.market.now().timestamp())

    def _audit(self, rec: dict) -> None:
        if not self._dir:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / f"rules-{rec['ts'][:10].replace('-', '')}.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("could not write the strategy log")

    # ---- control
    def start(self, sid: int, threaded: bool = True) -> Runner:
        row = self.store.get_strategy(sid)
        if row is None:
            raise KeyError("No such strategy")
        from .strategies import make_runner, parse as parse_any
        spec, errs, _ = parse_any(row["spec"])
        if spec is None:
            raise ValueError("The strategy is not valid: " + "; ".join(errs))
        with self._lock:
            old = self.runners.get(sid)
            if old and old.active:
                raise RuntimeError("This strategy is already running")
            self.trading.gateway()                                           # raises if not connected
            r = make_runner(sid, spec, self.env)
            r.start(threaded)
            self.runners[sid] = r
            return r

    def _get(self, sid: int) -> Runner:
        r = self.runners.get(sid)
        if r is None or not r.active:
            raise RuntimeError("This strategy is not running")
        return r

    def pause(self, sid: int) -> None:
        self._get(sid).pause()

    def resume(self, sid: int) -> None:
        self._get(sid).resume()

    def stop(self, sid: int) -> None:
        self._get(sid).stop()

    def kill_all(self, why: str = "Stopped by Stop-all") -> None:
        for r in list(self.runners.values()):
            r.kill(why)

    def running(self) -> list[int]:
        return [i for i, r in self.runners.items() if r.active]

    def status(self, sid: int) -> dict | None:
        r = self.runners.get(sid)
        return r.snapshot() if r else None
