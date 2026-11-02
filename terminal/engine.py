"""
Engine: wires feed -> candles -> strategy -> orders, and owns the order lifecycle.

Threading model: the market feed (any thread) only pushes events onto a queue. ONE engine
thread consumes the queue, so strategy state is never touched from two places at once.
Dashboard buttons (pause / kill) set flags that the engine thread acts on.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .brokers.base import Feed, Instruments, Orders, Subscription
from .candles import CandleStore
from .clock import at, now_ist, parse_hm, round_tick
from .config import Config
from .models import INDEX, TERMINAL_STATES, OrderError, OrderRequest, OrderStatus, OrderUnknown, Tick
from .risk import RiskGuard
from .strategy import ORBStrategy, Signal, State

log = logging.getLogger("engine")


class Engine:
    def __init__(self, cfg: Config, instruments: Instruments, feed: Feed, orders: Orders,
                 clock: Callable[[], datetime] = now_ist) -> None:
        self.cfg, self.instruments, self.feed, self.orders, self.clock = cfg, instruments, feed, orders, clock
        self.replay = cfg.mode == "replay"
        self.q: queue.Queue = queue.Queue()
        self.store = CandleStore()
        self.risk = RiskGuard(cfg.risk.max_daily_loss, cfg.risk.max_lots)
        self.events: deque = deque(maxlen=400)
        self.strategy = ORBStrategy(cfg.strategy, self.risk, self.store, self.say)
        self.phase = "INIT"              # INIT WAIT_REF WAIT_OPEN RUNNING NO_TRADE BLOCKED
        self.chain: dict = {}
        self.atm: float | None = None
        self.tick_count = 0
        self._last_event_ts: datetime | None = None
        self._sl_oid: str | None = None
        self._last_sl_poll: datetime | None = None
        self._sl_unreadable = 0
        self._stop = threading.Event()
        self._kill = threading.Event()
        self._kill_handled = False
        self._thread: threading.Thread | None = None
        self.finished = threading.Event()
        self.manual_action_required = False
        self.notify: Callable[[str, str], None] | None = None       # alerts hook: (kind, text); set by Services

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.q.put(("wake",))

    def kill(self) -> None:
        """Square off now and halt for the day."""
        self._kill.set()
        self.q.put(("wake",))

    def pause_entries(self, paused: bool) -> None:
        self.strategy.entries_paused = paused
        self.say("WARN" if paused else "INFO", "New entries PAUSED" if paused else "New entries resumed")

    def say(self, level: str, msg: str) -> None:
        ts = self._now()
        self.events.append({"ts": ts.strftime("%H:%M:%S"), "level": level, "msg": msg})
        if self.notify and not self.replay:
            kind = "error" if level in ("ERROR", "CRITICAL") else "warning" if level == "WARN" else \
                   "strategy" if msg.startswith(("ENTRY", "EXIT", "Session starting", "Market closed - session", "New entries")) else None
            if kind:
                try:
                    self.notify(kind, f"Built-in algo: {msg}")
                except Exception:
                    pass
        getattr(log, "error" if level in ("ERROR", "CRITICAL") else "warning" if level == "WARN" else "info")(msg)

    def _now(self) -> datetime:
        if self.replay and self._last_event_ts:
            return self._last_event_ts
        return self.clock()

    # --------------------------------------------------------------------- run
    def run(self) -> None:
        try:
            if not self._startup():
                return
            while not self._stop.is_set():
                try:
                    ev = self.q.get(timeout=1.0)
                except queue.Empty:
                    ev = ("timer", self.clock()) if not self.replay else None
                if ev is not None:
                    kind = ev[0]
                    if kind == "tick":
                        self._on_tick(ev[1])
                    elif kind == "timer":
                        self._on_timer(ev[1])
                    elif kind == "eof":
                        self._on_eof()
                        break
                if self._kill.is_set() and not self._kill_handled:
                    self._do_kill()
                if not self.replay and self.clock() >= at(self.clock().date(), parse_hm("15:35")):
                    self.say("INFO", "Market closed - session finished")
                    break
        except Exception as e:                                  # never die silently with a position open
            self.say("CRITICAL", f"Engine crashed: {e!r} - CHECK POSITIONS MANUALLY")
            log.exception("engine crash")
            self.manual_action_required = True
        finally:
            try:
                self.feed.stop()
            except Exception:
                pass
            self.finished.set()

    def _startup(self) -> bool:
        self.say("INFO", f"Session starting: mode={self.cfg.mode} instrument={self.cfg.instrument} "
                         f"max daily loss={'no limit' if self.cfg.risk.max_daily_loss <= 0 else format(self.cfg.risk.max_daily_loss, ',.0f')}")
        if self.cfg.mode == "live":
            pos = self._read_positions()
            if pos is None:
                self.phase = "BLOCKED"
                self.manual_action_required = True
                self.say("CRITICAL", "Could not read your Kotak positions, so the terminal cannot tell whether a trade "
                                     "is already open. It will not trade blind. Check the Kotak app / your login and "
                                     "start again.")
                return False
            u = self.cfg.inst.underlying.upper()
            open_ = {s: q for s, q in pos.items() if q != 0 and s.upper().startswith(u)}
            if open_:
                self.phase = "BLOCKED"
                self.say("CRITICAL", f"Existing open position(s) found {open_}. Square them off "
                                     f"manually - terminal will not trade on top of them.")
                return False
        if hasattr(self.feed, "on_eof"):
            self.feed.on_eof = lambda: self.q.put(("eof",))
        self.feed.start(lambda t: self.q.put(("tick", t)))
        self.feed.subscribe([self.instruments.index_subscription(self.cfg.instrument)])
        self.phase = "WAIT_REF"
        return True

    # -------------------------------------------------------------- tick events
    def _on_tick(self, t: Tick) -> None:
        if t.price <= 0:
            return
        self._last_event_ts = t.ts
        self.tick_count += 1
        self.store.on_tick(t.symbol, t.price, t.ts)
        if hasattr(self.orders, "on_tick"):
            self.orders.on_tick(t)

        if self.phase == "WAIT_REF" and t.symbol == INDEX:
            self._build_chain(t)
        if self.phase == "WAIT_OPEN" and t.symbol == INDEX and t.ts >= self._open_dt(t.ts):
            self._lock(t)
        if self.phase == "RUNNING":
            self._poll_sl(t.ts)
            sig = self.strategy.on_tick(t)
            if sig:
                self._handle(sig, t.ts)

    def _open_dt(self, ts: datetime) -> datetime:
        return at(ts.date(), parse_hm(self.cfg.strategy.range_start))

    def _build_chain(self, t: Tick) -> None:
        s = self.cfg.strategy
        if t.ts >= self._open_dt(t.ts) - timedelta(seconds=s.min_lead_s):
            self.phase = "NO_TRADE"
            self.say("WARN", "Started too late: option feeds must be subscribed before 09:15 to capture the "
                             "whole opening range. No trade today - start the terminal before 09:10.")
            self.strategy.stop_day("late start")
            return
        try:
            self.chain = self.instruments.candidates(self.cfg.instrument, t.price, s.strike_window, s.expiry_offset)
            subs = [Subscription(c.segment, c.token, c.symbol) for ce, pe in self.chain.values() for c in (ce, pe)]
            self.feed.subscribe(subs)
        except Exception as e:
            self.phase = "NO_TRADE"
            self.say("ERROR", f"Could not load option contracts: {e}")
            self.strategy.stop_day("contract lookup failed")
            return
        self.phase = "WAIT_OPEN"
        self.say("INFO", f"Index reference {t.price:g}; subscribed {len(self.chain) * 2} option contracts "
                         f"(expiry {next(iter(self.chain.values()))[0].expiry})")

    def _lock(self, t: Tick) -> None:
        step = self.cfg.inst.strike_step
        atm = float(round(t.price / step) * step)
        pair = self.chain.get(atm)
        if not pair:
            self.phase = "NO_TRADE"
            self.say("WARN", f"Index opened at {t.price:g} -> ATM {atm:g} is outside the subscribed strikes. "
                             f"No trade today (increase strategy.strike_window).")
            self.strategy.stop_day("ATM outside subscribed window")
            return
        self.atm = atm
        self.say("INFO", f"Index open {t.price:g} -> ATM strike {atm:g}")
        self.strategy.lock(t.ts.date(), pair[0], pair[1])
        self.phase = "RUNNING"

    # ------------------------------------------------------------------- timer
    def _on_timer(self, now: datetime) -> None:
        if self.phase != "RUNNING":
            return
        self._poll_sl(now)
        sig = self.strategy.check_time(now)
        if not sig and self.strategy.state == State.IN_TRADE:
            last = self.store.last_ts.get(self.strategy.pos.contract.symbol)
            idx_last = self.store.last_ts.get(INDEX)
            newest = max([x for x in (last, idx_last) if x], default=None)
            if newest and (now - newest).total_seconds() > self.cfg.risk.feed_stale_s:
                self.say("CRITICAL", f"No market data for {self.cfg.risk.feed_stale_s:g}s while in a trade - squaring off")
                sig = self.strategy.request_exit("FEED_STALE", now)
        if sig:
            self._handle(sig, now)

    def _on_eof(self) -> None:
        if self.strategy.state == State.IN_TRADE:
            sig = self.strategy.request_exit("EOF", self._now())
            if sig:
                self._handle(sig, self._now())
        self.say("INFO", "Replay finished")

    def _do_kill(self) -> None:
        self._kill_handled = True
        self.say("CRITICAL", "KILL SWITCH pressed")
        now = self._now()
        sig = self.strategy.request_exit("KILL", now)
        if sig:
            self._handle(sig, now)
        else:
            self.strategy.stop_day("Kill switch - halted for the day", State.HALTED)

    # ----------------------------------------------------------------- orders
    def _handle(self, sig: Signal, ts: datetime) -> None:
        if sig.kind == "ENTER":
            self._do_entry(sig, ts)
        else:
            self._do_exit(sig, ts)

    # ---- helpers that never trust a single unclear answer from the broker
    def _safe_status(self, oid: str) -> OrderStatus:
        try:
            return self.orders.status(oid)
        except Exception as e:
            return OrderStatus(oid, "UNKNOWN", 0, 0.0, repr(e))

    def _read_positions(self) -> dict[str, int] | None:
        r = self.cfg.risk
        for i in range(max(1, r.unclear_retries)):
            try:
                pos = self.orders.positions()
            except Exception:
                pos = None
            if pos is not None:
                return pos
            if i + 1 < r.unclear_retries:
                time.sleep(r.unclear_wait_s)
        return None

    def _net_stable(self, symbol: str) -> int | None:
        """Net position at the broker in units (+long / -short), accepted only when two reads in a row agree.
        None = could not be established."""
        r = self.cfg.risk
        prev = None
        for _ in range(max(2, r.unclear_retries)):
            try:
                pos = self.orders.positions()
            except Exception:
                pos = None
            cur = None if pos is None else int(pos.get(symbol, 0))
            if cur is not None and cur == prev:
                return cur
            prev = cur
            time.sleep(r.unclear_wait_s)
        return None

    def _halt_manual(self, msg: str) -> None:
        self.manual_action_required = True
        self.say("CRITICAL", msg)
        self.strategy.stop_day("Halted for the day - manual action required", State.HALTED)

    def _wait(self, oid: str, timeout: float) -> OrderStatus:
        deadline = time.monotonic() + timeout
        st = self._safe_status(oid)
        while st.state not in TERMINAL_STATES and time.monotonic() < deadline:
            time.sleep(self.cfg.poll_interval_s)
            st = self._safe_status(oid)
        return st

    def _settle(self, oid: str, timeout: float) -> OrderStatus:
        """Wait for the order to finish; if it has not, cancel it and make sure it is really final.
        The result is only trustworthy when its state is in TERMINAL_STATES."""
        st = self._wait(oid, timeout)
        if st.state in TERMINAL_STATES:
            return st
        self._safe_cancel(oid)
        r = self.cfg.risk
        for i in range(max(1, r.unclear_retries)):
            st = self._safe_status(oid)
            if st.state in TERMINAL_STATES:
                return st
            if i + 1 < r.unclear_retries:
                time.sleep(r.unclear_wait_s)
        return st

    @staticmethod
    def _clear(st: OrderStatus) -> bool:
        """Final AND self-consistent: a COMPLETE order that reports zero fill is not believable."""
        return st.state in TERMINAL_STATES and not (st.state == "COMPLETE" and st.filled_qty <= 0)

    # ---- entry
    def _do_entry(self, sig: Signal, ts: datetime) -> None:
        r = self.cfg.risk
        self.say("INFO", f"ENTRY signal: sell {sig.qty} {sig.contract.symbol} limit {sig.limit_price:g} "
                         f"(SL {sig.sl_price:g}) - {sig.reason}")
        try:
            oid = self.orders.place(OrderRequest(sig.contract, "S", sig.qty, "L", sig.limit_price, tag="orb-entry"))
        except OrderUnknown as e:
            self.say("CRITICAL", f"Entry order reply unclear ({e}) - checking your positions")
            self._entry_by_position(sig, ts, None, 0.0)
            return
        except OrderError as e:
            self.strategy.on_entry_failed(f"order error: {e}")
            return
        except Exception as e:
            self.say("CRITICAL", f"Entry order call failed unexpectedly ({e!r}) - checking your positions")
            self._entry_by_position(sig, ts, None, 0.0)
            return
        st = self._settle(oid, r.entry_timeout_s)
        if not self._clear(st):
            self.say("CRITICAL", f"Entry order {oid} state unclear ({st.state} {st.reason}) - checking your positions")
            self._entry_by_position(sig, ts, oid, st.avg_price)
            return
        if st.filled_qty <= 0:
            self.strategy.on_entry_failed(f"state={st.state} {st.reason}")
            return
        self._entry_filled(sig, st.filled_qty, st.avg_price or sig.limit_price, ts, estimated=not st.avg_price)

    def _entry_by_position(self, sig: Signal, ts: datetime, oid: str | None, avg: float) -> None:
        sym = sig.contract.symbol
        net = self._net_stable(sym)
        if net is None:
            self._halt_manual(f"Cannot tell whether the entry sell of {sym} executed and cannot read positions. "
                              f"OPEN THE KOTAK APP NOW: you may be short {sym} with NO stop-loss.")
        elif net < 0:
            self.say("WARN", f"Broker shows you short {-net} {sym}; treating the entry as filled "
                             f"(price estimated). Cancel any part of the sell order still open in the Kotak app.")
            self._entry_filled(sig, -net, avg or sig.limit_price, ts, estimated=True)
        elif net > 0:
            self._halt_manual(f"Unexpected LONG {net} {sym} at the broker. Check the Kotak app.")
        else:
            self._halt_manual(f"Entry order {oid or '(id unknown)'} for {sym} could not be confirmed and no position "
                              f"shows yet. It may still be resting at Kotak - CHECK THE APP and cancel it if open.")

    def _entry_filled(self, sig: Signal, qty: int, price: float, ts: datetime, estimated: bool) -> None:
        if qty < sig.qty:
            self.say("WARN", f"Partial fill {qty}/{sig.qty} - managing the filled part")
        self.strategy.on_entry_filled(price, qty, ts)
        self._log_trade({"event": "entry", "symbol": sig.contract.symbol, "qty": qty, "price": price,
                         "price_estimated": estimated, "sl": sig.sl_price, "ts": ts.isoformat(), "why": sig.reason})
        self._place_exchange_sl(sig, qty, ts)

    def _place_exchange_sl(self, sig: Signal, qty: int, ts: datetime) -> None:
        if not self.cfg.risk.exchange_sl:
            return
        try:
            self._sl_oid = self.orders.place(OrderRequest(
                sig.contract, "B", qty, "SL", price=round_tick(sig.sl_price + self.cfg.risk.sl_limit_buffer),
                trigger=sig.sl_price, tag="orb-sl"))
            self.say("INFO", f"Exchange SL placed: buy-stop trigger {sig.sl_price:g}")
        except OrderUnknown as e:
            # the stop may or may not be resting. Do NOT add a second one; the software stop still works,
            # and _do_exit() checks positions before buying back.
            self._sl_oid = None
            self.say("ERROR", f"Exchange SL reply unclear ({e}). Check the Kotak app for a resting buy-stop; "
                              f"the software stop-loss is active.")
        except Exception as e:
            self.say("CRITICAL", f"Could not place the exchange stop-loss ({e}). Closing the trade rather "
                                 f"than leaving a naked short.")
            s = self.strategy.request_exit("SL_ORDER_FAILED", ts)
            if s:
                self._do_exit(s, ts)

    def _poll_sl(self, ts: datetime) -> None:
        if self.strategy.state != State.IN_TRADE or not self._sl_oid:
            return
        if self._last_sl_poll and (ts - self._last_sl_poll).total_seconds() < self.cfg.risk.sl_poll_s:
            return
        self._last_sl_poll = ts
        st = self._safe_status(self._sl_oid)
        if st.state == "UNKNOWN":
            self._sl_unreadable += 1
            if self._sl_unreadable == 1 or self._sl_unreadable % 20 == 0:
                self.say("WARN", f"Could not read SL order status ({self._sl_unreadable}x): {st.reason}")
        elif st.state == "COMPLETE":
            self._sl_unreadable = 0
            pos = self.strategy.pos
            if not st.avg_price:
                self.say("WARN", "Exchange SL filled but Kotak gave no average price - using the trigger price")
            self._finish_exit(st.avg_price or pos.sl, ts, "SL(exchange)")
        elif st.state in ("REJECTED", "CANCELLED"):
            self.say("ERROR", f"Exchange SL order is {st.state} ({st.reason}) - relying on the software stop-loss")
            self._sl_oid = None

    # ---- exit
    def _do_exit(self, sig: Signal, ts: datetime) -> None:
        pos = self.strategy.pos
        sym = pos.contract.symbol
        reason, remaining = sig.reason, pos.qty
        fills: list[tuple[int, float]] = []
        r = self.cfg.risk
        self.say("INFO", f"EXIT ({reason}): buying back {pos.qty} {sym}")

        def sync_with_broker(est_price: float) -> bool:
            """Unclear what happened: make the broker's position the truth. False = halted."""
            nonlocal remaining
            net = self._net_stable(sym)
            if net is None:
                self._halt_manual(f"Cannot confirm the state of the buy-back of {sym} and cannot read positions. "
                                  f"OPEN THE KOTAK APP NOW and square off manually.")
                return False
            if net > 0:
                self._halt_manual(f"Now LONG {net} {sym} at the broker (a buy-back probably executed twice). "
                                  f"Check the Kotak app and close it.")
                return False
            short = -net
            got = remaining - short
            if got > 0:
                fills.append((got, est_price))
                self.say("WARN", f"Broker shows {got} of {sym} already bought back (price estimated at {est_price:g})")
            remaining = short
            return True

        # 1) deal with the resting exchange stop first so we never double-buy
        if self._sl_oid:
            oid, self._sl_oid = self._sl_oid, None
            st = self._safe_status(oid)
            if st.state != "COMPLETE":
                self._safe_cancel(oid)
                st = self._settle(oid, 0.0)
            if self._clear(st):
                if st.filled_qty > 0:
                    fills.append((st.filled_qty, st.avg_price or pos.sl))
                    remaining -= st.filled_qty
                    if st.state == "COMPLETE":
                        reason = "SL(exchange)"
            else:
                self.say("ERROR", f"Exchange SL order {oid} state unclear ({st.state} {st.reason}) - checking positions")
                if not sync_with_broker(pos.sl):
                    return

        # 2) buy back what is still short, chasing the price a little more each attempt
        attempt = 0
        while remaining > 0 and attempt <= r.exit_retries:
            attempt += 1
            ltp = self.store.last_price.get(sym, sig.ref_price)
            last_try = attempt > r.exit_retries
            req = (OrderRequest(pos.contract, "B", remaining, "MKT", tag="orb-exit") if last_try else
                   OrderRequest(pos.contract, "B", remaining, "L",
                                round_tick(ltp + r.exit_buffer * attempt), tag="orb-exit"))
            try:
                oid = self.orders.place(req)
            except OrderUnknown as e:
                self.say("ERROR", f"Exit attempt {attempt}: reply unclear ({e}) - checking positions")
                if not sync_with_broker(ltp):
                    return
                continue
            except OrderError as e:
                self.say("ERROR", f"Exit attempt {attempt} refused: {e}")
                continue
            except Exception as e:
                self.say("ERROR", f"Exit attempt {attempt} failed unexpectedly: {e!r} - checking positions")
                if not sync_with_broker(ltp):
                    return
                continue
            st = self._settle(oid, r.exit_timeout_s)
            if self._clear(st):
                if st.filled_qty > 0:
                    fills.append((st.filled_qty, st.avg_price or ltp))
                    remaining -= st.filled_qty
            else:
                self.say("ERROR", f"Exit order {oid} state unclear ({st.state} {st.reason}) - checking positions")
                if not sync_with_broker(st.avg_price or ltp):
                    return

        if remaining > 0:
            self.manual_action_required = True
            self.say("CRITICAL", f"COULD NOT BUY BACK {remaining} of {sym}. "
                                 f"SQUARE OFF MANUALLY IN THE KOTAK NEO APP NOW.")
            self.strategy.state = State.HALTED
            return
        qty = sum(q for q, _ in fills)
        avg = round(sum(q * p for q, p in fills) / qty, 2) if qty else round(sig.ref_price, 2)
        self._finish_exit(avg, ts, reason)

    def _finish_exit(self, price: float, ts: datetime, reason: str) -> None:
        pos = self.strategy.pos
        self._sl_oid = None
        self.strategy.on_exit_filled(price, ts, reason)
        self._log_trade({"event": "exit", "symbol": pos.contract.symbol, "qty": pos.qty, "price": price,
                         "reason": reason, "ts": ts.isoformat(), "day_pnl": round(self.risk.realised, 2)})

    def _safe_cancel(self, oid: str) -> None:
        try:
            self.orders.cancel(oid)
        except Exception as e:
            self.say("WARN", f"Cancel {oid} failed: {e}")

    # --------------------------------------------------------------- reporting
    def _log_trade(self, rec: dict) -> None:
        try:
            d = Path(self.cfg.data_dir)
            d.mkdir(parents=True, exist_ok=True)
            day = (self._now()).strftime("%Y%m%d")
            with open(d / f"trades-{self.cfg.mode}-{day}.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            log.exception("could not write trade log")

    def snapshot(self) -> dict:
        s, st = self.cfg.strategy, self.store
        sym = {"CE": self.strategy.ce.symbol if self.strategy.ce else None,
               "PE": self.strategy.pe.symbol if self.strategy.pe else None}
        open_pnl = self.strategy.open_pnl()
        return {
            "mode": self.cfg.mode, "instrument": self.cfg.instrument, "phase": self.phase,
            "time": self._now().strftime("%H:%M:%S"), "atm": self.atm,
            "ticks": self.tick_count, "finished": self.finished.is_set(),
            "manual_action_required": self.manual_action_required,
            "prices": {"INDEX": st.last_price.get(INDEX),
                       "CE": st.last_price.get(sym["CE"]) if sym["CE"] else None,
                       "PE": st.last_price.get(sym["PE"]) if sym["PE"] else None},
            "symbols": sym,
            "strategy": self.strategy.snapshot(),
            "risk": {"max_daily_loss": self.risk.max_daily_loss, "realised": round(self.risk.realised, 2),
                     "open_pnl": round(open_pnl, 2), "total": round(self.risk.total(open_pnl), 2)},
            "rules": {"range": f"{s.range_start} + {s.range_candles} candles", "sl_candle": s.sl_candle,
                      "exit_time": s.exit_time, "entry_cutoff": s.entry_cutoff},
            "events": list(self.events)[-80:],
        }
