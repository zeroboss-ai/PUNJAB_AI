"""
Leg runner: executes a leg-based strategy (legspec.LegStrategy) against live prices.

It reuses the order machinery of the rule-block Runner (verified cancels, exact-quantity exits, exchange stop-loss, halts),
so the safety rules documented there apply here too. What is new:

  * at the entry time every leg's strike is chosen from the same index price; a leg with a momentum / range / condition
    trigger then waits, a time-triggered leg enters straight away (bought legs first);
  * each leg has its own stop-loss, target, trailing stop, and re-entries (same strike, same or opposite side);
  * strategy-wide: overall stop-loss / target / profit trailing, "complete" square-off, trail-to-break-even,
    no-re-entry-after and stop-monitoring-after times, the max-loss check;
  * a stop-loss that is also an exchange order is moved by cancelling it (confirmed from the order book) and placing a
    new one; if the new one cannot be placed the position is closed rather than left without a stop.

Honest limits (shown in the UI and the README):
  * a strategy started more than LATE_START_MIN minutes after its entry time does not trade that day;
  * two legs may not choose the same option (their positions would net against each other at the broker);
  * strategies do not survive a restart of the terminal. A position left open by a crash must be handled by hand.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from ..clock import IST, at, parse_hm
from ..market import commodity
from ..market.chain import ChainUnavailable
from ..models import OrderError, OrderUnknown
from ..trading.models import OrderTicket
from . import book as B
from .legspec import LegCfg, LegStrategy, strike_text
from .runner import Env, Pos, Runner
from .strikes import NEEDS_PRICES, WIDE, StrikeError, pick_strike, step_of

log = logging.getLogger("rules")

LATE_START_MIN = 5
SELECT_TIMEOUT_S = 90
GUARD_PCT = 0.2                      # a moved stop-loss must stay at least this % beyond the current price


def _hm_minutes(hm: str) -> int:
    t = parse_hm(hm)
    return t.hour * 60 + t.minute


class LegState:
    def __init__(self, cfg: LegCfg) -> None:
        self.cfg = cfg
        self.status = "waiting"              # waiting | selected | open | done | skipped
        self.key = ""
        self.symbol = ""
        self.label = ""
        self.strike: float | None = None
        self.expiry = ""
        self.lot = 1
        self.tick = 0.05
        self.pick_note = ""
        self.ref_price: float | None = None  # price when the strike was chosen (momentum)
        self.side = cfg.side                 # the side of the next entry (reverse re-entries flip it)
        self.pos: Pos | None = None
        self.re_sl = 0
        self.re_tgt = 0
        self.pending: dict | None = None     # a re-entry waiting for its trigger
        self.armed = False                   # condition trigger: it must be false before it may fire
        self.progress = ""                   # one line for the screen: what the leg is waiting for
        self.note = ""

    def title(self) -> str:
        c = self.cfg
        what = "future" if c.segment == "futures" else ("Call" if c.kind == "CE" else "Put")
        return f"Leg {c.id[1:]}: {'SELL' if c.side == 'S' else 'BUY'} {what}"


class LegRunner(Runner):
    def __init__(self, sid: int, spec: LegStrategy, env: Env) -> None:
        super().__init__(sid, spec, env)
        self.lst: list[LegState] = [LegState(c) for c in spec.legs]
        self.by_id = {ls.cfg.id: ls for ls in self.lst}
        self.selected = False
        self._next_select = 0.0
        self._next_watch = 0.0
        self.entry_dt: datetime | None = None
        self.complete_reason = ""            # set when a stopped leg must close everything
        self.be_request = False
        self.overall_reason = ""
        self.floor: float | None = None      # overall profit trailing: the locked-in profit
        self._cur: LegState | None = None
        self.premium_base = 0.0
        self.kind = "legs"
        self._warned: set[str] = set()

    # ------------------------------------------------------------------------------------ helpers
    def _once(self, tag: str, msg: str, level: str = "warn") -> None:
        if tag not in self._warned:
            self._warned.add(tag)
            self.say(msg, level)

    def _key(self, src: str, self_leg: str | None) -> str | None:       # operands inside a leg's condition trigger
        if src == "INDEX":
            return self.spot_key
        if src == "SELF" and self._cur is not None and self._cur.key:
            return self._cur.key
        return None

    def _at(self, hm: str) -> datetime:
        return at(self.day, parse_hm(hm))

    @property
    def monitoring(self) -> bool:
        sm = self.s.stop_monitoring_after
        return not (sm and self.now_dt >= self._at(sm))

    def _reentry_allowed(self) -> bool:
        nr = self.s.no_reentry_after
        return self.monitoring and not (nr and self.now_dt >= self._at(nr))

    def _live(self) -> list[Pos]:
        return [p for p in self.positions if p.status == "open"]

    def _expiry_iso(self, sel: str, und: str) -> str:
        exps = self.m.expiries(und)
        if not exps:
            raise ChainUnavailable(f"No expiries found for {und}.")
        if sel == "next":
            if len(exps) < 2:
                raise ChainUnavailable(f"Only one expiry is listed for {und}.")
            return exps[1]
        if sel == "monthly":
            month = exps[0][:7]
            return max(e for e in exps if e[:7] == month)
        return exps[0]

    # ------------------------------------------------------------------------------------ step
    def step(self) -> None:
        now = self.m.now()
        self.now_dt, self.now_ts, self.day = now, now.timestamp(), now.date()
        if self.status not in ("waiting", "watching"):
            return
        if not self.selected:
            if self.stop_requested:
                self.status = "stopped"
                self.say("Stopped before any strike was chosen.")
                return self._publish()
            self._select_phase()
        if self.selected and self.status in ("waiting", "watching"):
            self._process_actions()
            if self.status in ("waiting", "watching"):
                self._manage()
            if self.status in ("waiting", "watching"):
                self._entries()
            if self.status in ("waiting", "watching"):
                self._maybe_finish()
        self._publish()

    # ------------------------------------------------------------------------- strike selection
    def _prewatch(self, spot: float) -> None:
        keys: list[str] = []
        for sel in {(ls.cfg.expiry, ls.cfg.segment) for ls in self.lst}:
            try:
                if sel[1] == "futures":
                    f = self.m.future(self.s.underlying, sel[0])
                    if f:
                        keys.append(f.key)
                    continue
                info = self.m.chain(self.s.underlying, self._expiry_iso(sel[0], self.s.underlying))
            except ChainUnavailable as e:
                self._once(f"chain-{sel}", f"Could not read the option chain yet: {e}")
                continue
            strikes = sorted(info.rows)
            if commodity.is_commodity(self.s.underlying) and info.spot_key:      # an option on a future: watch that future too
                keys.append(info.spot_key)
                spot = self.m.ltp(info.spot_key) or spot
            i = min(range(len(strikes)), key=lambda j: abs(strikes[j] - spot))
            wide = any(ls.cfg.segment == "options" and ls.cfg.expiry == sel[0] and ls.cfg.strike.by in NEEDS_PRICES for ls in self.lst)
            span = WIDE if wide else 4 + max([abs(ls.cfg.strike.steps) for ls in self.lst if ls.cfg.segment == "options"] + [0])
            for j in range(max(0, i - span), min(len(strikes), i + span + 1)):
                for kind in ("CE", "PE"):
                    if kind in info.rows[strikes[j]]:
                        keys.append(info.rows[strikes[j]][kind][0])
        for k in keys:
            self.book.watch(k, self.now_ts)
        self.m.watch(self.owner, [self.spot_key] + keys)

    def _select_phase(self) -> None:
        s, now = self.s, self.now_dt
        self.entry_dt = self._at(s.entry_time)
        exit_dt = self._at(s.exit.time)
        if now >= exit_dt:
            self.status = "done"
            self.say("Exit time passed before the strikes were chosen. Nothing was traded.")
            return
        spot = self.m.ltp(self.spot_key)
        if now < self.entry_dt:
            self.message = f"Waiting for {s.entry_time} to choose the strikes."
            if spot and self.now_ts >= self._next_watch:                 # start recording nearby prices early so ranges are complete
                self._next_watch = self.now_ts + 60
                self._prewatch(spot)
            return
        if now > self.entry_dt + timedelta(minutes=LATE_START_MIN):
            self.status = "done"
            self.message = f"Started after {s.entry_time}. Nothing was traded today."
            self.say(f"The strategy was started more than {LATE_START_MIN} minutes after its entry time ({s.entry_time}), "
                     f"so it will not trade today. Start it before {s.entry_time}.", "warn", notable=True)
            return
        if self.now_ts < self._next_select:
            return
        self._next_select = self.now_ts + 2
        if not spot:
            self.message = "Waiting for the index price."
            return
        chosen: dict[str, dict] = {}
        problems: list[str] = []
        infos: dict[str, object] = {}
        watch_extra: set[str] = set()
        for ls in self.lst:
            c = ls.cfg
            try:
                if c.segment == "futures":
                    f = self.m.future(s.underlying, c.expiry)
                    if f is None:
                        raise StrikeError("No index future is available (not in the instrument list, or not recorded).")
                    px = self.m.ltp(f.key)
                    if not px:
                        raise StrikeError("Waiting for the future's price.")
                    chosen[c.id] = {"key": f.key, "symbol": f.symbol, "strike": None, "expiry": f.expiry, "lot": f.lot, "tick": f.tick,
                                    "note": f"future expiring {f.expiry}", "ref": px}
                    continue
                iso = self._expiry_iso(c.expiry, s.underlying)
                info = infos.get(iso) or self.m.chain(s.underlying, iso)
                infos[iso] = info
                strikes = sorted(info.rows)
                ref_spot = spot
                if commodity.is_commodity(s.underlying) and info.spot_key:           # ATM = nearest the future this option is written on
                    ref_spot = self.m.ltp(info.spot_key) or spot
                    watch_extra.add(info.spot_key)
                t_years = max((self._at_date(iso) - now).total_seconds(), 60.0) / (365 * 86400)
                pick = pick_strike(self._sel_dict(c), c.kind, strikes, ref_spot,
                                   lambda k, kind, info=info: (self.m.ltp(info.rows[k][kind][0]) if kind in info.rows.get(k, {}) else None), t_years)
                if c.kind not in info.rows[pick.strike]:
                    raise StrikeError(f"There is no {c.kind} at strike {pick.strike:g}.")
                key, sym = info.rows[pick.strike][c.kind]
                ti = self.trading.cat.get(key)
                px = self.m.ltp(key)
                if c.trigger.type == "momentum" and not px:
                    raise StrikeError("Waiting for the option's price (needed as the starting point for momentum).")
                chosen[c.id] = {"key": key, "symbol": sym, "strike": pick.strike, "expiry": info.expiry, "lot": info.lot, "tick": ti.tick if ti else 0.05,
                                "note": pick.note, "ref": px}
            except (StrikeError, ChainUnavailable) as e:
                problems.append(f"{ls.title()}: {e}")
        if not problems:
            keys = [v["key"] for v in chosen.values()]
            dup = {k for k in keys if keys.count(k) > 1}
            if dup:
                names = ", ".join(ls.title() for ls in self.lst if chosen[ls.cfg.id]["key"] in dup)
                return self.halt(f"Two legs chose the same option ({names}). Their positions would cancel each other at the broker. "
                                 f"Give them different strikes or expiries. Nothing was traded.")
        if problems:
            self.message = "Choosing strikes: " + problems[0]
            if now > self.entry_dt + timedelta(seconds=SELECT_TIMEOUT_S):
                return self.halt("Could not choose the strikes in time. Nothing was traded. " + " | ".join(problems)[:300])
            return
        for ls in self.lst:
            v = chosen[ls.cfg.id]
            ls.key, ls.symbol, ls.strike, ls.expiry, ls.lot, ls.tick = v["key"], v["symbol"], v["strike"], v["expiry"], v["lot"], v["tick"]
            ls.pick_note, ls.ref_price, ls.status = v["note"], v["ref"], "selected"
            inst = self.trading.cat.get(ls.key)
            ls.label = inst.label() if inst else ls.symbol
            self.book.watch(ls.key, self.now_ts)
        self.m.watch(self.owner, [self.spot_key] + [ls.key for ls in self.lst] + sorted(watch_extra))
        self.selected = True
        self.status = "watching"
        self.message = ""
        self.expiry = self.lst[0].expiry
        self.lot = self.lst[0].lot
        self.say(f"Strikes chosen at index {spot:g}: " + "; ".join(f"{ls.title()} -> {ls.label} ({ls.pick_note})" for ls in self.lst), notable=True)

    def _at_date(self, iso: str) -> datetime:
        y, m, d = (int(x) for x in iso.split("-"))
        hm = commodity.session_hm(self.s.underlying, date(y, m, d))[1] if commodity.is_commodity(self.s.underlying) else "15:30"
        return datetime(y, m, d, int(hm[:2]), int(hm[3:]), tzinfo=IST)

    @staticmethod
    def _sel_dict(c: LegCfg) -> dict:
        s = c.strike
        return {"by": s.by, "steps": s.steps, "value": s.value, "lo": s.lo, "hi": s.hi}

    # ------------------------------------------------------------------------------------ actions
    def _process_actions(self) -> None:
        if self.complete_reason and not self.overall_reason:
            self.overall_reason = self.complete_reason
            self.say(f"{self.complete_reason}. Closing everything and ending the strategy for today.", "warn", notable=True)
        if self.overall_reason:
            for p in self._live():
                self._close(p, self.overall_reason)
                if self.status not in ("waiting", "watching"):
                    return
            for ls in self.lst:
                if ls.status in ("waiting", "selected") or ls.pending:
                    ls.status, ls.pending = "skipped", None
                    ls.note = ls.note or self.overall_reason
        if self.be_request:
            self.be_request = False
            for p in self._live():
                ls = self.by_id.get(p.leg)
                if ls is None or p.sl is None:
                    continue
                if self.s.trail_be_scope == "sl_legs" and ls.cfg.sl.mode == "none":
                    continue
                self._tighten(p, p.entry_price, "break-even (entry price)")

    # ------------------------------------------------------------------------------ managing positions
    def _premium_base(self) -> float:
        return sum(p.entry_price * p.qty for p in self.positions)

    def _manage(self) -> None:
        live = self._live()
        if not live:
            return
        s, o = self.s, self.s.overall
        mtm = self.pnl()["total"]
        reason = ""
        if s.limits.max_loss > 0 and not self.loss_stopped and mtm <= -s.limits.max_loss:
            self.loss_stopped = True
            self.say(f"Max loss reached (₹{mtm:,.0f}). Closing positions and stopping.", "warn")
        base = self._premium_base()
        if o.sl.mode == "rupees" and mtm <= -o.sl.value:
            reason = f"Overall stop-loss (₹{mtm:,.0f})"
        elif o.sl.mode == "percent" and base > 0 and mtm <= -base * o.sl.value / 100:
            reason = f"Overall stop-loss ({o.sl.value:g}% of premium)"
        if not reason and self.monitoring:
            if o.target.mode == "rupees" and mtm >= o.target.value:
                reason = f"Overall target (₹{mtm:,.0f})"
            elif o.target.mode == "percent" and base > 0 and mtm >= base * o.target.value / 100:
                reason = f"Overall target ({o.target.value:g}% of premium)"
            elif o.trail.on:
                t = o.trail
                if mtm >= t.reach:
                    floor = t.lock + (int((mtm - t.reach) // t.every) * t.by if t.every > 0 else 0)
                    if self.floor is None or floor > self.floor:
                        if self.floor is None:
                            self.say(f"Profit reached ₹{t.reach:,.0f}: locking in ₹{floor:,.0f}.", notable=True)
                        self.floor = floor
                if self.floor is not None and mtm <= self.floor:
                    reason = f"Overall profit lock (₹{self.floor:,.0f})"
        exit_dt = self._at(s.exit.time)
        if reason:
            self.overall_reason = reason
            self.say(f"{reason}. Closing everything and ending the strategy for today.", "warn", notable=True)
        for p in live:
            r = None
            if self.stop_requested:
                r = "Stopped by you"
            elif self.overall_reason:
                r = self.overall_reason
            elif self.loss_stopped:
                r = "Max loss reached"
            elif self.now_dt >= exit_dt:
                r = "Exit time"
            else:
                if p.sl_oid and self.now_ts >= p.next_poll:
                    p.next_poll = self.now_ts + self.env.sl_poll_s
                    self._poll_sl(p)
                    if p.status != "open":
                        continue
                ltp = self.m.ltp(p.key)
                if ltp:
                    if p.sl is not None and not p.sl_oid and ((p.side == "S" and ltp >= p.sl) or (p.side == "B" and ltp <= p.sl)):
                        r = f"Stop-loss ({p.sl:g})"
                    elif p.sl is not None and p.sl_oid and self._ran_past(p, ltp):
                        r = "Stop-loss (price ran past the exchange order)"
                    elif self.monitoring:
                        if p.target is not None and ((p.side == "S" and ltp <= p.target) or (p.side == "B" and ltp >= p.target)):
                            r = f"Target ({p.target:g})"
                        else:
                            self._trail(p, ltp)
                            if p.status != "open":
                                continue
            if r:
                self._close(p, r)
                if self.status not in ("waiting", "watching"):
                    return
        if self.complete_reason and self.status in ("waiting", "watching"):
            self._process_actions()

    def _ran_past(self, p: Pos, ltp: float) -> bool:
        """The exchange stop-loss is a stop-LIMIT: in a fast move the price can jump past its limit and the order rests unfilled.
        If the price is that far beyond the stop, stop waiting for the order and close the position ourselves."""
        k = 2 * max(self.s.limits.slippage_pct, 0.5) / 100
        return ltp >= p.sl * (1 + k) if p.side == "S" else ltp <= p.sl * (1 - k)

    def _pts(self, rule_mode: str, value: float, ref: float) -> float:
        return value if rule_mode == "points" else ref * value / 100.0

    def _trail(self, p: Pos, ltp: float) -> None:
        ls = self.by_id.get(p.leg)
        if ls is None or not ls.cfg.trail.on or p.sl_init is None or p.sl is None:
            return
        c = ls.cfg
        every, by = self._pts(c.sl.mode, c.trail.every, p.entry_price), self._pts(c.sl.mode, c.trail.by, p.entry_price)
        fav = (p.entry_price - ltp) if p.side == "S" else (ltp - p.entry_price)
        if every <= 0 or fav < every:
            return
        n = int(fav // every)
        if n <= p.trail_steps:
            return
        new = p.sl_init - n * by if p.side == "S" else p.sl_init + n * by
        if self._tighten(p, new, f"trailing ({n} step(s))"):
            p.trail_steps = n

    def _tighten(self, p: Pos, new_sl: float, why: str) -> bool:
        """Move the stop-loss to `new_sl` only if that is tighter than now and still safely beyond the current price."""
        ls = self.by_id.get(p.leg)
        tk = ls.tick if ls else 0.05
        new_sl = round(round(new_sl / tk) * tk, 2)
        if p.sl is not None and ((p.side == "S" and new_sl >= p.sl) or (p.side == "B" and new_sl <= p.sl)):
            return False
        ltp = self.m.ltp(p.key)
        if not ltp or new_sl <= 0:
            return False
        g = ltp * GUARD_PCT / 100
        if (p.side == "S" and new_sl <= ltp + g) or (p.side == "B" and new_sl >= ltp - g):
            self._once(f"guard-{p.leg}-{round(new_sl, 2)}", f"{ls.title() if ls else p.leg}: the stop-loss would move to {new_sl:g} ({why}) but the price "
                       f"{ltp:g} is too close, so it is left where it is.", "info")
            return False
        old = p.sl
        if p.sl_oid:
            st, _row = self._cancel_verified(p.sl_oid)
            if st == "unknown":
                p.status = "attention"
                self.halt(f"Could not confirm whether the exchange stop-loss for {p.symbol} is cancelled (order {p.sl_oid}) while moving it. "
                          f"Check the Orders page; the position may still be open.")
                return False
            if st == "complete":
                self._poll_sl(p)                                  # it was triggered just now: book the exit
                return False
            p.sl_oid = None
        p.sl = new_sl
        self.say(f"{ls.title() if ls else p.leg}: stop-loss moved {old:g} -> {new_sl:g} ({why}).")
        if self.s.exit.exchange_sl:
            self._place_exchange_sl(p)
        return True

    # ------------------------------------------------------------------------------- exits -> follow-ups
    def _finish(self, p: Pos, price: float, reason: str, estimated: bool = False) -> None:
        super()._finish(p, price, reason, estimated)
        ls = self.by_id.get(p.leg)
        if ls is None:
            return
        ls.pos = None
        ls.status = "done"
        kind = "sl" if reason.startswith("Stop-loss (") else "tgt" if reason.startswith("Target (") else ""
        if not kind:
            return
        if self.s.square_off == "complete":
            self.complete_reason = f"{ls.title()} hit its {'stop-loss' if kind == 'sl' else 'target'}: complete square-off"
            return
        rcfg = ls.cfg.reentry_sl if kind == "sl" else ls.cfg.reentry_tgt
        used = ls.re_sl if kind == "sl" else ls.re_tgt
        if rcfg.mode != "none" and used < rcfg.count:
            rs = ("B" if p.side == "S" else "S") if rcfg.reverse else p.side
            ls.pending = {"kind": kind, "ref": p.exit_price, "entry": p.entry_price, "side": rs, "cfg": rcfg}
            ls.status = "selected"
            self.say(f"{ls.title()}: re-entry {'(opposite side) ' if rcfg.reverse else ''}armed ({rcfg.mode}), {used + 1} of {rcfg.count}.")
        if kind == "sl" and self.s.trail_be:
            self.be_request = True

    # ------------------------------------------------------------------------------------ entries
    def _stale(self) -> bool:
        age = self.m.feed_age()
        return age is None or age > self.env.stale_s

    def _trigger_ok(self, ls: LegState) -> tuple[bool, str]:
        """Has the leg's first-entry trigger happened? Returns (fire, text for the screen)."""
        c, t = ls.cfg, ls.cfg.trigger
        px = self.m.ltp(ls.key)
        if t.type == "time":
            return True, "entering now"
        if t.type == "momentum":
            if not px or not ls.ref_price:
                return False, "waiting for a price"
            moved = px - ls.ref_price
            moved_u = moved if t.unit == "points" else moved / ls.ref_price * 100
            need = t.value if t.dir == "up" else -t.value
            ok = moved_u >= need if t.dir == "up" else moved_u <= need
            u = "%" if t.unit == "percent" else " pts"
            return ok, f"moved {moved_u:+.2f}{u} of {'+' if t.dir == 'up' else '-'}{t.value:g}{u} needed"
        if t.type == "range":
            n = _hm_minutes(t.until) - _hm_minutes(self.s.entry_time)
            key = self.spot_key if t.of == "underlying" else ls.key
            r = self.book.open_range(key, B.minute_at(self.day, self.s.entry_time), n, self.now_ts)
            if r is None:
                if self.now_dt >= self._at(t.until) + timedelta(seconds=10):
                    self._once(f"range-{c.id}", f"{ls.title()}: the {self.s.entry_time}-{t.until} range is not available (the terminal did not see every "
                               f"minute of it). This leg will not enter today.")
                    ls.status, ls.note = "skipped", "Range not available"
                    return False, "range not available"
                return False, f"building the range until {t.until}"
            hi, lo = r
            cur = self.m.ltp(key)
            if not cur:
                return False, "waiting for a price"
            ok = cur > hi if t.side == "high" else cur < lo
            return ok, f"range {lo:g} - {hi:g}; price {cur:g}; waiting to break the {t.side}"
        self._cur = ls
        res = self._group(f"leg-{c.id}", t.conditions, t.match, c.kind)
        self._cur = None
        txt = (" AND " if t.match == "all" else " OR ").join(x["text"] for x in res["conds"])
        if not res["ok"]:
            ls.armed = True
            why = next((w for x in res["conds"] for w in x["why"]), "")
            return False, (f"waiting: {why}" if why else f"waiting for: {txt}")
        return ls.armed, ("signal: " + txt) if ls.armed else "already true when it became possible; waiting for it to go false and true again"

    def _reentry_ok(self, ls: LegState) -> tuple[bool, str]:
        pd = ls.pending
        cfg = pd["cfg"]
        px = self.m.ltp(ls.key)
        if cfg.mode == "asap":
            return True, "re-entering now"
        if not px:
            return False, "waiting for a price"
        ref, entry, rs = pd["ref"], pd["entry"], pd["side"]
        if cfg.mode == "cost":
            ok = px <= entry if ref > entry else px >= entry
            return ok, f"waiting for the price to return to {entry:g} (now {px:g})"
        moved = (px - ref) if cfg.unit == "points" else (px - ref) / ref * 100
        ok = moved <= -cfg.value if rs == "S" else moved >= cfg.value
        return ok, f"waiting for a {'fall' if rs == 'S' else 'rise'} of {cfg.value:g}{'%' if cfg.unit == 'percent' else ' pts'} from {ref:g} (moved {moved:+.2f})"

    def _entries(self) -> None:
        s = self.s
        if self.overall_reason or self.loss_stopped or self.stop_requested:
            return
        last = self._at(s.last_entry_time)
        exit_dt = self._at(s.exit.time)
        stale = self._stale()
        order = sorted(self.lst, key=lambda ls: 0 if ls.cfg.side == "B" else 1)       # hedges (bought legs) before sold legs
        waiting: list[str] = []
        for ls in order:
            if ls.status != "selected" or self.status not in ("waiting", "watching"):
                continue
            first = ls.pending is None
            if self.now_dt >= exit_dt or (first and self.now_dt >= last):
                ls.status, ls.pending, ls.note = "skipped", None, ("Exit time reached" if self.now_dt >= exit_dt else "Its trigger did not happen before the last entry time")
                self.say(f"{ls.title()}: {ls.note}.", "info")
                continue
            if not first and not self._reentry_allowed():
                ls.status, ls.pending, ls.note = "done", None, "Re-entry time is over"
                self.say(f"{ls.title()}: no re-entry (time limit).", "info")
                continue
            ok, txt = self._trigger_ok(ls) if first else self._reentry_ok(ls)
            ls.progress = txt
            if ls.status == "skipped":
                continue
            if not ok:
                waiting.append(f"{ls.title()}: {txt}")
                continue
            if self.paused or stale:
                if stale:
                    self.message = "Holding: the price feed has no recent update."
                continue
            if self.failed_entries >= 3:
                continue
            side = ls.cfg.side if first else ls.pending["side"]
            if first and ls.cfg.trigger.type == "condition":
                ls.armed = False                                       # that signal is spent
            pkind = None if first else ls.pending["kind"]
            fired = self._enter_leg(ls, side, "first entry" if first else f"re-entry after {pkind}")
            if fired and not first:
                if pkind == "sl":
                    ls.re_sl += 1
                else:
                    ls.re_tgt += 1
                ls.pending = None
            if not fired and ls.cfg.side == "B" and first:
                break                                                  # do not sell while a hedge is missing
            if self.status not in ("waiting", "watching"):
                return
        if not self.message.startswith("Holding"):
            self.message = (waiting[0] if waiting else "Watching.") if not self.paused else "Paused: no new entries."

    def _enter_leg(self, ls: LegState, side: str, why: str) -> bool:
        s, c = self.s, ls.cfg
        key = ls.key
        ltp = self.m.ltp(key)
        if not ltp:
            self._once(f"noprice-{c.id}", f"No price for {ls.symbol} yet - not entering.")
            return False
        try:
            net0 = self.trading.net_qty(key, s.product)
        except OrderError:
            net0 = None
        if net0 is None:
            self.say("Cannot read your positions, so the entry is skipped (it could not be checked).", "warn")
            return False
        price = self.trading.limit_through(key, side, ltp, s.limits.slippage_pct)
        t = OrderTicket(key=key, side=side, lots=c.lots, product=s.product, order_type="L", price=price, tag=self.tag)
        self.say(f"{ls.title()} ({why}): {'SELL' if side == 'S' else 'BUY'} {ls.label}", notable=True)
        try:
            res = self.trading.place(t)
        except OrderUnknown as e:
            self.say(f"Entry reply unclear: {e}. Checking positions.", "warn")
            return self._settle(ls, side, None, net0, ltp)
        except OrderError as e:
            self.failed_entries += 1
            self.say(f"Entry order refused: {e}", "warn")
            if self.failed_entries >= 3:
                self.halt("Three entry orders in a row were refused. Check the reason above, then restart the strategy.")
            return False
        self.say(f"Entry order {res['order_id']}: {'SELL' if side == 'S' else 'BUY'} {res['qty']} {ls.symbol} limit {price:g}")
        return self._settle(ls, side, res["order_id"], net0, ltp)

    def _settle(self, ls: LegState, side: str, oid: str | None, net0: int, ltp: float) -> bool:
        key, c = ls.key, ls.cfg
        row = None
        if oid:
            row = self._wait_done(oid, self.env.fill_wait_s)
            if row is not None and row.is_open:
                st, row = self._cancel_verified(oid)
                if st == "unknown":
                    self.halt(f"Could not confirm whether entry order {oid} ({ls.symbol}) is cancelled. Check the Orders and Positions pages before restarting.")
                    return False
        elif self._sweep_open_orders(key) == "unknown":
            return False
        net1 = self.trading.net_qty(key, self.s.product)
        if net1 is None:
            self.halt(f"Cannot read positions after the entry order for {ls.symbol}. Check the Orders and Positions pages.")
            return False
        held = (net0 - net1) if side == "S" else (net1 - net0)
        if held <= 0:
            why = f" ({row.status}{': ' + row.reason if row and row.reason else ''})" if row else ""
            self.failed_entries += 1
            self.say(f"The entry order did not fill{why}. No position was taken.", "warn")
            if self.failed_entries >= 3:
                self.halt("Three entry attempts in a row did not fill. Check liquidity and the reason above, then restart.")
            return False
        px = row.avg_price if row is not None and row.filled > 0 and row.avg_price else ltp
        inst = self.trading.cat.get(key)
        pos = Pos(leg=c.id, key=key, symbol=ls.symbol, label=ls.label, side=side, qty=held, lot=inst.lot if inst else ls.lot,
                  entry_price=round(px, 2), entry_time=self.now_dt.strftime("%H:%M:%S"), entry_oid=oid or "")
        pos.estimated = row is None or not row.avg_price
        self.positions.append(pos)
        self.entries_done += 1
        self.failed_entries = 0
        ls.pos, ls.status, ls.pending = pos, "open", None
        tk = inst.tick if inst else ls.tick
        if c.sl.mode != "none":
            d = self._pts(c.sl.mode, c.sl.value, px)
            sl = px + d if side == "S" else px - d
            pos.sl = pos.sl_init = round(round(max(sl, tk) / tk) * tk, 2)
        if c.target.mode != "none":
            d = self._pts(c.target.mode, c.target.value, px)
            pos.target = round(round(max(px - d if side == "S" else px + d, tk) / tk) * tk, 2)
        self.say(f"In position: {'SOLD' if side == 'S' else 'BOUGHT'} {held} {pos.symbol} at {pos.entry_price:g}"
                 f"{' (price estimated)' if pos.estimated else ''}. Stop-loss {pos.sl if pos.sl is not None else 'none'}, "
                 f"target {pos.target if pos.target is not None else 'none'}.", notable=True)
        if held < c.lots * pos.lot:
            self.say(f"Only {held} of {c.lots * pos.lot} units filled; managing the {held} we hold.", "warn")
        if self.s.exit.exchange_sl and pos.sl is not None:
            self._place_exchange_sl(pos)
        return True

    # ------------------------------------------------------------------------------------ finishing
    def _maybe_finish(self) -> None:
        if any(p.status in ("open", "attention") for p in self.positions):
            return
        exit_dt = self._at(self.s.exit.time)
        pending = [ls for ls in self.lst if ls.status == "selected"]
        if self.stop_requested:
            self.status = "stopped"
            self.say("Stopped.", notable=True)
        elif self.overall_reason:
            self.status, self.message = "done", f"Finished for today ({self.overall_reason})."
            self.say(f"Done for today ({self.overall_reason}).", notable=True)
        elif self.loss_stopped:
            self.status, self.message = "done", "Stopped after reaching the max loss."
            self.say("Done for today (max loss).", notable=True)
        elif self.now_dt >= exit_dt:
            self.status, self.message = "done", "Finished for today."
            self.say("Done for today (exit time reached).", notable=True)
        elif not pending:
            self.status, self.message = "done", "Finished for today."
            self.say("Done for today (every leg has finished and nothing is waiting).", notable=True)

    def kill(self, why: str = "Stopped by Stop-all") -> None:
        for ls in self.lst:
            if ls.status in ("selected", "waiting"):
                ls.status, ls.pending = "skipped", None
        super().kill(why)

    # ------------------------------------------------------------------------------------ snapshot
    def _leg_view(self, ls: LegState) -> dict:
        c = ls.cfg
        return {"id": c.id, "title": ls.title(), "status": ls.status, "side": c.side, "segment": c.segment, "kind": c.kind, "key": ls.key,
                "symbol": ls.symbol, "label": ls.label, "strike": ls.strike, "expiry": ls.expiry, "ltp": self.m.ltp(ls.key) if ls.key else None,
                "pick": ls.pick_note, "trigger": c.trigger.type, "progress": ls.progress, "note": ls.note,
                "re_sl": [ls.re_sl, c.reentry_sl.count if c.reentry_sl.mode != "none" else 0],
                "re_tgt": [ls.re_tgt, c.reentry_tgt.count if c.reentry_tgt.mode != "none" else 0],
                "pending": None if not ls.pending else {"kind": ls.pending["kind"], "side": ls.pending["side"], "mode": ls.pending["cfg"].mode},
                "strike_rule": strike_text(c.strike, c.kind) if c.segment == "options" else "the index future"}

    def _publish(self) -> None:
        self._snap = {
            "id": self.sid, "name": self.s.name, "kind": "legs", "status": self.status, "paused": self.paused, "stop_requested": self.stop_requested,
            "message": self.message, "halt": self.halt_reason, "expiry": self.expiry, "lot": self.lot, "legs": [self._leg_view(ls) for ls in self.lst],
            "spot": {"key": self.spot_key, "ltp": self.m.ltp(self.spot_key) if self.spot_key else None},
            "rules": [], "exit_rule": None,
            "positions": [p.to_dict(self.m.ltp(p.key)) for p in self.positions], "pnl": self.pnl(),
            "overall": {"floor": self.floor, "reason": self.overall_reason, "monitoring": self.monitoring if self.now_dt else True},
            "entries_done": self.entries_done, "max_entries": len(self.lst),
            "log": list(self._logs)[-80:],
        }
