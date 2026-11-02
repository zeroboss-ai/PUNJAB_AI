"""
Back-tester: replays a saved strategy over recorded one-minute candles, using the SAME rule engine that trades live.

How a day is replayed
  * a private world is built for the day: its own price hub, paper broker, trading service and rule engine, with a
    simulated clock. Nothing here can touch your live strategies, positions or broker;
  * each recorded minute is turned into four ticks (open, then the low and high in the order that fits the candle,
    then close). The rule engine sees them exactly as it would see live ticks, so the same "signal fires once",
    "not enough candles yet" and stop-loss rules apply;
  * orders go to the paper broker: limit orders fill at the last price when marketable, a resting order fills at its own
    price once the market trades through it. No brokerage, taxes, margin or queue priority, unless you enter a cost.

What this cannot tell you (the result screen says so too)
  * which of the high and low came first inside a minute is a convention, not a fact: stops and entries that sit
    close to a candle's extremes can differ in real life;
  * a few weeks of days is a tiny sample; trying many variations until one looks good fits the past, not the future.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from datetime import date, datetime, timedelta

from .clock import IST, at, parse_hm
from .config import Config
from .history import CLOSE_MIN, OPEN_MIN, CandleDB
from .journal import equity, group, stats
from .market.catalog import Catalog
from .market.chain import INDEX_UNDERLYINGS, ChainUnavailable
from .market.hub import QuoteHub
from .market.models import Instrument, Quote
from .rules.runner import ChainInfo, FutInfo, HubMarket, RuleEngine
from .rules.strategies import parse
from .store import Store
from .trading.service import ManualLimits, TradingService
from .trading.sim import SimBroker

log = logging.getLogger("backtest")

MAX_DAYS = 60
STEPS = 4                                          # ticks between two corner points of a candle (so a level is crossed, not jumped over)
TICK_SECONDS = [min(58, 1 + round(i * 57 / (3 * STEPS))) for i in range(3 * STEPS + 1)]
ASSUMPTIONS = [
    "Each recorded minute is replayed as a path of thirteen ticks: open, low, high, close for a rising candle and open, high, low, close for a falling one, with straight lines between. The real order inside the minute is unknown.",
    "Orders use the paper broker: marketable limits fill at the last price, resting orders fill at their own price once the market trades through them. No queue, no partial fills, no rejections.",
    "Brokerage, taxes and charges are not included unless you enter a cost per trade.",
    "Candles come from the ticks this terminal received; a minute with no tick has no candle.",
    "A few weeks is a small sample. Results from past days do not predict future days, and adjusting a strategy until the past looks good tends to fit noise.",
]


def phase_prices(o: float, h: float, lo: float, c: float) -> list[float]:
    """The minute as a path: open, then the low and high in the order that fits the candle (low first for a rising candle),
    then close, with straight-line steps in between so that a price level is crossed rather than jumped over."""
    corners = [o, lo, h, c] if c >= o else [o, h, lo, c]
    out = [corners[0]]
    for a, b in zip(corners, corners[1:]):
        out.extend(a + (b - a) * i / STEPS for i in range(1, STEPS + 1))
    return [round(x, 2) for x in out]


class _Clock:
    def __init__(self, day: date) -> None:
        self.day = day
        self.t = datetime.combine(day, datetime.min.time(), tzinfo=IST)

    def set(self, minute_of_day: int, second: int = 0) -> None:
        self.t = datetime.combine(self.day, datetime.min.time(), tzinfo=IST) + timedelta(minutes=minute_of_day, seconds=second)


class ReplayMarket(HubMarket):
    """The Market the rule engine sees during a replay: the day's own hub, the simulated clock and the recorded contracts."""

    def __init__(self, hub, clock: _Clock, contracts: list[dict]) -> None:
        super().__init__(hub, None)
        self.clock, self.contracts = clock, contracts

    def now(self):
        return self.clock.t

    def feed_age(self):
        return 0.0

    def spot_key(self, und):
        return next((c["spot_key"] for c in self.contracts if c["und"] == und and c["kind"] == "INDEX"), None) or super().spot_key(und)

    def chain(self, und, expiry_sel):
        opts = [c for c in self.contracts if c["und"] == und and c["kind"] in ("CE", "PE") and c.get("expiry")]
        today = self.clock.day.isoformat()
        exps = sorted({c["expiry"] for c in opts if c["expiry"] >= today})
        if not exps:
            raise ChainUnavailable(f"No recorded {und} options for this day")
        if expiry_sel == "next":
            exp = exps[1] if len(exps) > 1 else exps[0]
        elif expiry_sel in exps:
            exp = expiry_sel
        else:
            exp = exps[0]
        rows: dict[float, dict[str, tuple[str, str]]] = {}
        lot = 1
        for c in opts:
            if c["expiry"] == exp:
                rows.setdefault(c["strike"], {})[c["kind"]] = (c["key"], c["symbol"])
                lot = c["lot"]
        rows = {k: v for k, v in rows.items() if "CE" in v and "PE" in v}
        if not rows:
            raise ChainUnavailable(f"No recorded {und} strikes with both a Call and a Put")
        return ChainInfo(exp, lot, self.spot_key(und), rows)

    def expiries(self, und):
        today = self.clock.day.isoformat()
        return sorted({c["expiry"] for c in self.contracts if c["und"] == und and c["kind"] in ("CE", "PE") and c.get("expiry") and c["expiry"] >= today})

    def future(self, und, expiry_sel):
        today = self.clock.day.isoformat()
        futs = sorted((c for c in self.contracts if c["und"] == und and c["kind"] == "FUT" and c.get("expiry") and c["expiry"] >= today), key=lambda c: c["expiry"])
        if not futs:
            return None
        c = futs[1] if expiry_sel == "next" and len(futs) > 1 else futs[0]
        return FutInfo(c["key"], c["symbol"], c["lot"], c["expiry"], 0.05)

    def watch(self, owner, keys):
        pass

    def unwatch(self, owner):
        pass


def _leg_name(spec, p) -> str:
    """'CE' / 'PE' for rule-block strategies; 'L2 Sell Put' style for the leg builder, so the by-leg table is readable."""
    legs = getattr(spec, "legs", None)
    if not legs:
        return p.leg
    cfg = next((l for l in legs if l.id == p.leg), None)
    if cfg is None:
        return p.leg
    what = "Future" if cfg.segment == "futures" else ("Call" if cfg.kind == "CE" else "Put")
    return f"{p.leg} {'Sell' if cfg.side == 'S' else 'Buy'} {what}"


def _secs(day: str, hms: str) -> float:
    try:
        return datetime.fromisoformat(f"{day}T{hms}").replace(tzinfo=IST).timestamp()
    except ValueError:
        return 0.0


def run_day(cfg: Config, db: CandleDB, src: str, day: date, spec_raw: dict, name: str, start_time: str = "09:10",
            cost_per_trade: float = 0.0, should_stop=lambda: False) -> dict:
    """Replay one recorded day. Always returns a result dict; problems are reported in it, never raised."""
    res = {"day": day.isoformat(), "status": "ok", "note": "", "trades": [], "pnl": 0.0, "log": [], "outcome": ""}
    try:
        spec, errs, _ = parse(spec_raw)
        if spec is None:
            return {**res, "status": "error", "note": "The strategy is not valid: " + "; ".join(errs)}
        contracts = db.contracts(src, day.isoformat())
        candles = db.day_candles(src, day.isoformat())
        spot_key = next((c["spot_key"] for c in contracts if c["und"] == spec.underlying and c["kind"] == "INDEX"), None)
        if not spot_key or spot_key not in candles:
            return {**res, "status": "no_data", "note": f"No recorded {spec.underlying} prices for this day."}
        contracts = [c for c in contracts if c["und"] == spec.underlying and (c["kind"] == "INDEX" or c["key"] in candles)]
        if not any(c["kind"] in ("CE", "PE") for c in contracts):
            return {**res, "status": "no_data", "note": f"No recorded {spec.underlying} option prices for this day."}
        if any(getattr(lg, "segment", "") == "futures" for lg in getattr(spec, "legs", [])) and not any(c["kind"] == "FUT" for c in contracts):
            return {**res, "status": "no_data", "note": "This strategy trades futures, and no futures prices were recorded for this day. "
                                                          "Only the index and its options are recorded, so futures legs cannot be back-tested yet."}

        hub, clock = QuoteHub(), _Clock(day)
        cat = Catalog()
        for c in contracts:
            seg = c["key"].split("|", 1)[0]
            cat.register(Instrument(c["key"], seg, c["key"].split("|", 1)[1], c["symbol"], c["und"], c["kind"], c["lot"], 0.05,
                                    date.fromisoformat(c["expiry"]) if c.get("expiry") else None, c.get("strike")))
        broker = SimBroker(hub, cat, cfg.manual.paper_capital, None)
        m = cfg.manual
        trading = TradingService(dataclasses.replace(cfg, mode="sample"), hub, cat, lambda: broker,
                                 ManualLimits(m.max_lots_per_order, m.max_order_value, m.block_after_loss, m.squareoff_buffer_pct))
        store = Store(":memory:")
        sid = store.save_strategy(None, name, spec_raw)
        eng = RuleEngine(store, ReplayMarket(hub, clock, contracts), trading, None, sleep=lambda s: None)
        eng.env.fill_wait_s, eng.env.sl_poll_s, eng.env.stale_s = 0, 1, 1e12
        eng.attach(hub)

        series = {k: v for k, v in candles.items() if k in {c["key"] for c in contracts}}
        first = {k: v[0] for k, v in series.items()}
        by_min: dict[int, list[tuple]] = {}
        for k, rows in series.items():
            for (em, o, h, lo, c) in rows:
                by_min.setdefault(em, []).append((k, o, h, lo, c))
        if not by_min:
            return {**res, "status": "no_data", "note": "No recorded prices for this day."}
        day0 = int(datetime.combine(day, datetime.min.time(), tzinfo=IST).timestamp() // 60)
        st = parse_hm(start_time)
        start_mod = st.hour * 60 + st.minute

        def push(k: str, px: float) -> None:
            hub.update(Quote(k, px, prev_close=px, ts=clock.t.timestamp()))

        # before the open: the index and the strikes that were already being recorded show their first price; then the
        # strategy is started like a person would start it, a few minutes before the open
        clock.set(min(start_mod, OPEN_MIN - 1), 0)
        for k, (em, o, h, lo, c) in first.items():
            if k == spot_key or em - day0 <= OPEN_MIN + 5:
                push(k, o)
        runner = eng.start(sid, threaded=False)
        if start_mod < OPEN_MIN:
            for mod, sec in ((start_mod, 5), (max(start_mod, OPEN_MIN - 3), 0), (OPEN_MIN - 1, 30)):
                clock.set(mod, sec)
                runner.step()
        last_mod = OPEN_MIN
        for em in sorted(by_min):
            if should_stop():
                return {**res, "status": "cancelled", "note": "Stopped."}
            mod = em - day0
            if mod < OPEN_MIN or mod >= CLOSE_MIN:
                continue
            paths = [(k, phase_prices(o, h, lo, c)) for (k, o, h, lo, c) in by_min[em]]
            for j, sec in enumerate(TICK_SECONDS):
                clock.set(mod, sec)
                for k, p in paths:
                    push(k, p[j])
                runner.step()
            last_mod = mod
        clock.set(last_mod, 58)
        if any(p.status == "open" for p in runner.positions):
            note = f"The recorded data ended at {last_mod // 60:02d}:{last_mod % 60:02d} while a position was open; it was closed at the last recorded price."
            runner.stop()
            for sec in (59, 59, 59):
                runner.step()
            res["note"] = note

        trades = []
        for p in runner.positions:
            if p.status != "closed":
                continue
            gross = p.pnl(None)
            trades.append({"day": day.isoformat(), "name": name, "leg": _leg_name(spec, p), "label": p.label, "dir": "Short" if p.side == "S" else "Long",
                           "qty": p.qty, "lots": p.qty // p.lot if p.lot else p.qty, "entry": p.entry_price, "exit": p.exit_price,
                           "entry_time": p.entry_time, "exit_time": p.exit_time, "exit_reason": p.exit_reason,
                           "entry_ts": _secs(day.isoformat(), p.entry_time), "exit_ts": _secs(day.isoformat(), p.exit_time),
                           "gross": gross, "pnl": round(gross - cost_per_trade, 2), "forced": res["note"].startswith("The recorded data")})
            trades[-1]["hold_s"] = max(0, int(trades[-1]["exit_ts"] - trades[-1]["entry_ts"]))
        bad = [p for p in runner.positions if p.status == "attention"]
        if bad or runner.status == "halted":
            res["status"] = "attention"
            res["note"] = (res["note"] + " " if res["note"] else "") + f"The strategy halted: {(runner.halt_reason or 'a position could not be closed').rstrip('. ')}."
        res.update(trades=trades, pnl=round(sum(t["pnl"] for t in trades), 2), outcome=runner.status,
                   log=[l for l in list(runner._logs) if l["level"] != "info" or "Signal" in l["msg"] or "In position" in l["msg"] or "Closed" in l["msg"]][-30:])
        if not trades and res["status"] == "ok" and not res["note"]:
            res["note"] = "No entry signal."
        return res
    except Exception as e:                                                         # a broken day must not stop the whole run
        log.exception("back-test day %s", day)
        return {**res, "status": "error", "note": f"Could not replay this day ({type(e).__name__}: {str(e)[:120]})."}


def summarise(results: list[dict]) -> dict:
    trades = sorted((t for r in results for t in r["trades"]), key=lambda t: t["exit_ts"])
    ok_days = [r for r in results if r["status"] in ("ok", "attention")]
    return {"stats": stats(trades), "equity": equity(trades), "by_day": [{"key": r["day"], "pnl": r["pnl"], "n": len(r["trades"])} for r in ok_days],
            "by_dir": group(trades, lambda t: t["dir"]), "by_reason": group(trades, lambda t: t["exit_reason"] or "unknown"),
            "by_leg": group(trades, lambda t: t["leg"]), "days_run": len(ok_days), "days_skipped": len(results) - len(ok_days),
            "days_traded": len({t["day"] for t in trades})}


class BacktestManager:
    """One back-test at a time, on a background thread, with progress and a Stop button."""

    def __init__(self, cfg: Config, db: CandleDB) -> None:
        self.cfg, self.db = cfg, db
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.job: dict | None = None

    def start(self, strategy_name: str, spec_raw: dict, src: str, days: list[str], start_time: str = "09:10", cost: float = 0.0) -> dict:
        spec, errs, _ = parse(spec_raw)
        if spec is None:
            raise ValueError("The strategy is not valid: " + "; ".join(errs))
        from .market import commodity
        if commodity.is_commodity(spec.underlying):
            raise ValueError("Back-testing commodity strategies is not available yet: the terminal records only NSE/BSE index prices. "
                             "Test a commodity strategy in paper mode with live prices instead.")
        if not days:
            raise ValueError("Pick at least one recorded day.")
        if len(days) > MAX_DAYS:
            raise ValueError(f"Choose at most {MAX_DAYS} days at a time.")
        parse_hm(start_time)
        if cost < 0 or cost > 100000:
            raise ValueError("The cost per trade must be between 0 and 100000.")
        ds = sorted({date.fromisoformat(d) for d in days})
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise ValueError("A back-test is already running. Wait for it or stop it first.")
            self._stop.clear()
            self.job = {"id": int(time.time() * 1000), "state": "running", "strategy": strategy_name, "src": src, "total": len(ds), "done": 0,
                        "started": time.time(), "results": [], "summary": None, "assumptions": ASSUMPTIONS, "cost_per_trade": cost,
                        "start_time": start_time, "invented": src == "sample", "underlying": spec.underlying}
            job = self.job
            self._thread = threading.Thread(target=self._run, args=(job, spec_raw, strategy_name, src, ds, start_time, cost), name="backtest", daemon=True)
            self._thread.start()
        return {"id": job["id"]}

    def _run(self, job, spec_raw, name, src, days, start_time, cost) -> None:
        try:
            for d in days:
                if self._stop.is_set():
                    job["state"] = "stopped"
                    break
                job["results"].append(run_day(self.cfg, self.db, src, d, spec_raw, name, start_time, cost, self._stop.is_set))
                job["done"] += 1
                job["summary"] = summarise(job["results"])
            else:
                job["state"] = "done"
        except Exception as e:
            log.exception("back-test")
            job["state"], job["error"] = "error", f"{type(e).__name__}: {str(e)[:150]}"
        job["finished"] = time.time()
        job["summary"] = summarise(job["results"])

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict | None:
        j = self.job
        if j is None:
            return None
        return {**j, "results": list(j["results"])}

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())
