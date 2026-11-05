"""
Leg-based strategy spec (version 2): what the leg builder produces and the leg runner executes.

Think of it as AlgoTest's builder: a list of legs (each a Call, Put or the index future, bought or sold), each with its own
strike rule, entry trigger, stop-loss, target, trailing and re-entries, plus settings for the whole strategy (entry and exit
time, overall stop/target/trailing, what happens when one leg stops out).

Same promise as the older rule-block spec: parse() checks everything strictly and never guesses; describe() writes in plain
English exactly what will run.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..market import commodity
from .spec import UNDERLYINGS as NSE_UNDERLYINGS, Condition, _conditions, _minutes, _P

UNDERLYINGS = tuple(NSE_UNDERLYINGS) + tuple(c.name for c in commodity.COMMODITIES)
from .strikes import BY

VERSION = 2
MAX_LEGS = 8
EXPIRIES = ("nearest", "next", "monthly")
EXPIRY_TEXT = {"nearest": "the nearest expiry", "next": "the next expiry", "monthly": "the monthly expiry"}
TRIGGERS = ("time", "momentum", "range", "condition")
REENTRY = ("none", "asap", "cost", "momentum")
LATEST_EXIT = 15 * 60 + 15


@dataclass
class StrikeSel:
    by: str = "atm"
    steps: int = 0                  # atm, synthetic: + out of the money, - in the money
    value: float = 0.0              # premium_*, delta_closest, pct_atm, straddle
    lo: float = 0.0                 # *_range
    hi: float = 0.0


@dataclass
class RiskRule:
    mode: str = "none"              # none | points | percent
    value: float = 0.0


@dataclass
class Trail:
    on: bool = False
    every: float = 0.0              # for every this much in your favour ...
    by: float = 0.0                 # ... move the stop-loss this much (same unit as the stop-loss)


@dataclass
class Reentry:
    mode: str = "none"              # none | asap | cost | momentum
    reverse: bool = False           # take the opposite side (buy <-> sell) when re-entering
    count: int = 1
    unit: str = "points"            # momentum: points | percent
    value: float = 0.0


@dataclass
class Trigger:
    type: str = "time"              # time | momentum | range | condition
    unit: str = "points"            # momentum
    dir: str = "up"                 # momentum: up | down (from the price when the strike was chosen)
    value: float = 0.0
    until: str = "09:45"            # range: the range is built from the entry time until this time
    side: str = "high"              # range: enter when price breaks the range high or the range low
    of: str = "option"              # range: option | underlying
    match: str = "all"              # condition
    conditions: list[Condition] = field(default_factory=list)


@dataclass
class LegCfg:
    id: str
    segment: str = "options"        # options | futures
    lots: int = 1
    side: str = "S"
    kind: str = "CE"                # options only
    expiry: str = "nearest"
    strike: StrikeSel = field(default_factory=StrikeSel)
    sl: RiskRule = field(default_factory=RiskRule)
    target: RiskRule = field(default_factory=RiskRule)
    trail: Trail = field(default_factory=Trail)
    reentry_sl: Reentry = field(default_factory=Reentry)
    reentry_tgt: Reentry = field(default_factory=Reentry)
    trigger: Trigger = field(default_factory=Trigger)


@dataclass
class OverallRule:
    mode: str = "none"              # none | rupees | percent (of the premium of all positions taken)
    value: float = 0.0


@dataclass
class OverallTrail:
    on: bool = False
    reach: float = 0.0              # when total profit reaches this (rupees) ...
    lock: float = 0.0               # ... lock in this much (leave if profit falls back to it) ...
    every: float = 0.0              # ... and for every this much more profit ...
    by: float = 0.0                 # ... raise the locked amount by this much


@dataclass
class Overall:
    sl: OverallRule = field(default_factory=OverallRule)
    target: OverallRule = field(default_factory=OverallRule)
    trail: OverallTrail = field(default_factory=OverallTrail)


@dataclass
class LegLimits:
    max_loss: float = 0.0           # rupees, whole strategy, software check; 0 = none
    slippage_pct: float = 1.0


@dataclass
class ExitCfg:
    time: str = "15:14"
    exchange_sl: bool = True


@dataclass
class LegStrategy:
    name: str
    underlying: str = "NIFTY"
    product: str = "MIS"
    entry_time: str = "09:35"
    last_entry_time: str = "15:00"
    square_off: str = "partial"     # partial: only the leg that hit its stop closes | complete: everything closes
    trail_be: bool = False          # after a leg stops out, move the stop of the others to their entry price
    trail_be_scope: str = "all"     # all | sl_legs (only legs that have a stop-loss)
    no_reentry_after: str = ""
    stop_monitoring_after: str = ""
    legs: list[LegCfg] = field(default_factory=list)
    overall: Overall = field(default_factory=Overall)
    limits: LegLimits = field(default_factory=LegLimits)
    exit: ExitCfg = field(default_factory=ExitCfg)
    note: str = ""
    account_id: str = ""
    version = 2                     # not a dataclass field on purpose

    def to_json(self) -> dict:
        d = asdict(self)
        d["version"] = VERSION
        return d


def is_leg_spec(raw: Any) -> bool:
    return isinstance(raw, dict) and (raw.get("version") == VERSION or "legs" in raw)


# ------------------------------------------------------------------------------------------- parsing
def _rule(p: _P, d: Any, where: str, modes: tuple, hi_points: float = 1e6, hi_pct: float = 1000.0) -> RiskRule:
    d = d if isinstance(d, dict) else {}
    r = RiskRule(mode=p.choice(d, "mode", where, modes, "none"))
    if r.mode != "none":
        r.value = p.num(d, "value", where, 0.05, hi_points if r.mode in ("points", "rupees") else hi_pct, None)
    return r


def _reentry(p: _P, d: Any, where: str) -> Reentry:
    d = d if isinstance(d, dict) else {}
    r = Reentry(mode=p.choice(d, "mode", where, REENTRY, "none"))
    if r.mode != "none":
        r.reverse = bool(d.get("reverse", False))
        r.count = p.num(d, "count", where, 1, 10, 1, integer=True)
    if r.mode == "momentum":
        r.unit = p.choice(d, "unit", where, ("points", "percent"), "points")
        r.value = p.num(d, "value", where, 0.05, 1e6 if r.unit == "points" else 1000, None)
    return r


def _strike(p: _P, d: Any, where: str) -> StrikeSel:
    d = d if isinstance(d, dict) else {}
    s = StrikeSel(by=p.choice(d, "by", where, BY, "atm"))
    if s.by in ("atm", "synthetic"):
        s.steps = int(p.num(d, "steps", where, -30, 30, 0, integer=True))
    elif s.by in ("premium_closest", "premium_gte", "premium_lte"):
        s.value = p.num(d, "value", where, 0.05, 100000, None)
    elif s.by == "premium_range":
        s.lo, s.hi = p.num(d, "lo", where, 0.05, 100000, None), p.num(d, "hi", where, 0.05, 100000, None)
        if s.lo >= s.hi:
            p.err(where, "the lower premium must be below the upper premium")
    elif s.by in ("pct_atm", "straddle"):
        s.value = p.num(d, "value", where, -100, 100, 0.0)
    elif s.by == "delta_closest":
        s.value = p.num(d, "value", where, 0.02, 0.98, None)
    elif s.by == "delta_range":
        s.lo, s.hi = p.num(d, "lo", where, 0.02, 0.98, None), p.num(d, "hi", where, 0.02, 0.98, None)
        if s.lo >= s.hi:
            p.err(where, "the lower delta must be below the upper delta")
    return s


def _trigger(p: _P, d: Any, where: str, s: LegStrategy) -> Trigger:
    d = d if isinstance(d, dict) else {}
    t = Trigger(type=p.choice(d, "type", where, TRIGGERS, "time"))
    if t.type == "momentum":
        t.unit = p.choice(d, "unit", where, ("points", "percent"), "points")
        t.dir = p.choice(d, "dir", where, ("up", "down"), "up")
        t.value = p.num(d, "value", where, 0.05, 1e6 if t.unit == "points" else 1000, None)
    elif t.type == "range":
        t.until = p.hm(d, "until", where, "09:45")
        t.side = p.choice(d, "side", where, ("high", "low"), "high")
        t.of = p.choice(d, "of", where, ("option", "underlying"), "option")
    elif t.type == "condition":
        t.match = p.choice(d, "match", where, ("all", "any"), "all")
        t.conditions = _conditions(p, d.get("conditions"), where)
        if not t.conditions:
            p.err(where, "add at least one condition, or choose another entry trigger")
        for c in t.conditions:
            for o in (c.left, c.right):
                if o.kind != "number" and o.src not in ("INDEX", "SELF"):
                    p.err(where, "conditions can look at the index or at this leg's own option; pick one of those")
    return t


def parse(raw: Any) -> tuple[LegStrategy | None, list[str], list[str]]:
    """Returns (strategy, errors, warnings). strategy is None when there are errors."""
    p = _P()
    if not isinstance(raw, dict):
        return None, ["The strategy must be an object"], []
    name = str(raw.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        p.err("", "Give the strategy a name (up to 60 characters)")
    s = LegStrategy(name=name or "Untitled")
    s.underlying = p.choice(raw, "underlying", "Underlying", UNDERLYINGS, "NIFTY")
    s.product = p.choice(raw, "product", "Product", ("MIS",), "MIS")
    s.entry_time = p.hm(raw, "entry_time", "Entry time", "09:35")
    ex = raw.get("exit") if isinstance(raw.get("exit"), dict) else {}
    mcx = commodity.is_commodity(s.underlying)
    dflt_exit = "23:15" if mcx else "15:14"                  # 23:15 is safe in both the 23:30 and the 23:55 season
    s.exit = ExitCfg(time=p.hm({"t": ex.get("time", raw.get("exit_time", dflt_exit))}, "t", "Exit time", dflt_exit),
                     exchange_sl=bool(ex.get("exchange_sl", raw.get("exchange_sl", True))))
    if mcx:
        dflt_last = f"{max(_minutes(s.exit.time) - 30, 0) // 60:02d}:{max(_minutes(s.exit.time) - 30, 0) % 60:02d}"
    else:
        dflt_last = "15:00" if s.exit.time >= "15:05" else s.exit.time
    s.last_entry_time = p.hm(raw, "last_entry_time", "Last entry time", dflt_last)
    s.square_off = p.choice(raw, "square_off", "Square off", ("partial", "complete"), "partial")
    tb = raw.get("trail_be")
    if isinstance(tb, dict):
        s.trail_be, scope_src = bool(tb.get("on", False)), tb
    else:
        s.trail_be, scope_src = bool(tb), {"scope": raw.get("trail_be_scope", "all")}
    s.trail_be_scope = p.choice(scope_src, "scope", "Trail SL to break-even", ("all", "sl_legs"), "all")
    for k, label in (("no_reentry_after", "No re-entry after"), ("stop_monitoring_after", "Stop monitoring after")):
        v = str(raw.get(k, "") or "").strip()
        if v:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
                p.err(label, "must be a time like 14:30, or empty")
                v = ""
        setattr(s, k, v)
    s.note = str(raw.get("note", ""))[:400]
    s.account_id = str(raw.get("account_id", "") or "").strip()

    legs = raw.get("legs")
    if not isinstance(legs, list) or not legs:
        p.err("", "Add at least one leg")
        legs = []
    if len(legs) > MAX_LEGS:
        p.err("", f"At most {MAX_LEGS} legs")
        legs = legs[:MAX_LEGS]
    for i, d in enumerate(legs, 1):
        w = f"Leg {i}"
        if not isinstance(d, dict):
            p.err(w, "is not valid")
            continue
        bad = [k for k in ("strike", "sl", "target", "trail", "reentry_sl", "reentry_tgt", "trigger") if k in d and not isinstance(d[k], dict)]
        if bad:
            p.err(w, f"'{bad[0]}' is not valid")
            continue
        lg = LegCfg(id=f"L{i}")
        lg.segment = p.choice(d, "segment", w, ("options", "futures"), "options")
        lg.lots = p.num(d, "lots", w, 1, 50, 1, integer=True)
        lg.side = p.choice(d, "side", w, ("S", "B"), "S")
        lg.expiry = p.choice(d, "expiry", w, EXPIRIES, "nearest")
        if lg.segment == "options":
            lg.kind = p.choice(d, "kind", w, ("CE", "PE"), "CE")
            lg.strike = _strike(p, d.get("strike"), f"{w} strike")
        else:
            lg.kind = "FUT"
        lg.sl = _rule(p, d.get("sl"), f"{w} stop-loss", ("none", "points", "percent"))
        lg.target = _rule(p, d.get("target"), f"{w} target", ("none", "points", "percent"))
        tr = d.get("trail") if isinstance(d.get("trail"), dict) else {}
        lg.trail.on = bool(tr.get("on", False))
        if lg.trail.on:
            lg.trail.every = p.num(tr, "every", f"{w} trailing", 0.05, 1e6, None)
            lg.trail.by = p.num(tr, "by", f"{w} trailing", 0.05, 1e6, None)
            if lg.sl.mode == "none":
                p.err(f"{w} trailing", "needs a stop-loss to move")
        lg.reentry_sl = _reentry(p, d.get("reentry_sl"), f"{w} re-entry after stop-loss")
        lg.reentry_tgt = _reentry(p, d.get("reentry_tgt"), f"{w} re-entry after target")
        if lg.reentry_sl.mode != "none" and lg.sl.mode == "none":
            p.err(f"{w} re-entry", "after stop-loss needs a stop-loss on the leg")
        if lg.reentry_tgt.mode != "none" and lg.target.mode == "none":
            p.err(f"{w} re-entry", "after target needs a target on the leg")
        lg.trigger = _trigger(p, d.get("trigger"), f"{w} entry trigger", s)
        s.legs.append(lg)

    ov = raw.get("overall") if isinstance(raw.get("overall"), dict) else {}
    s.overall.sl = _rule(p, ov.get("sl"), "Overall stop-loss", ("none", "rupees", "percent"), 1e7, 1000)
    s.overall.target = _rule(p, ov.get("target"), "Overall target", ("none", "rupees", "percent"), 1e7, 1000)
    ot = ov.get("trail") if isinstance(ov.get("trail"), dict) else {}
    s.overall.trail.on = bool(ot.get("on", False))
    if s.overall.trail.on:
        w = "Overall trailing"
        s.overall.trail.reach = p.num(ot, "reach", w, 1, 1e7, None)
        s.overall.trail.lock = p.num(ot, "lock", w, 0, 1e7, 0.0)
        s.overall.trail.every = p.num(ot, "every", w, 0, 1e7, 0.0)
        s.overall.trail.by = p.num(ot, "by", w, 0, 1e7, 0.0)
        if s.overall.trail.lock >= s.overall.trail.reach:
            p.err(w, "the locked profit must be less than the profit that triggers it")
        if (s.overall.trail.every > 0) != (s.overall.trail.by > 0):
            p.err(w, "fill in both 'for every' and 'raise the lock by', or leave both at 0")

    lm = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    s.limits = LegLimits(max_loss=p.num(lm, "max_loss", "Max loss", 0, 10_000_000, 0.0),
                         slippage_pct=p.num(lm, "slippage_pct", "Slippage", 0.1, 10, 1.0))
    warnings: list[str] = []
    if not p.errors:
        _cross_checks(s, p, warnings)
    if p.errors:
        return None, p.errors, warnings
    return s, [], warnings


def _und(s: LegStrategy) -> str:
    """Name used in the plain-English text: 'Gold Mini' for GOLDM, 'NIFTY' for NIFTY."""
    return commodity.label_of(s.underlying) if commodity.is_commodity(s.underlying) else s.underlying


def session_of(s: LegStrategy, day=None) -> tuple[str, str]:
    """(open, close) of the market this strategy trades: MCX for commodities (09:00 - 23:30 / 23:55), NSE/BSE otherwise."""
    from datetime import timedelta
    from ..clock import now_ist
    if day is None:
        day = now_ist().date()
        while day.weekday() >= 5:                       # saved over the weekend: judge it by the next trading day's hours
            day += timedelta(days=1)
    return commodity.session_hm(s.underlying, day) if commodity.is_commodity(s.underlying) else ("09:15", "15:30")


def _cross_checks(s: LegStrategy, p: _P, warnings: list[str]) -> None:
    e, x, last = _minutes(s.entry_time), _minutes(s.exit.time), _minutes(s.last_entry_time)
    o_hm, c_hm = session_of(s)
    mcx = commodity.is_commodity(s.underlying)
    o_min, c_min = _minutes(o_hm), _minutes(c_hm)
    if e < o_min:
        p.err("Entry time", f"must be {o_hm} or later")
    if mcx:
        if x > c_min:
            p.err("Exit time", f"must be {c_hm} or earlier: the commodity market closes then (today's MCX close; it changes with US daylight saving time)")
        elif x > c_min - commodity.BROKER_SQUAREOFF_MIN:
            warnings.append(f"The exit time {s.exit.time} is within {commodity.BROKER_SQUAREOFF_MIN} minutes of the {c_hm} close. Brokers usually square off "
                            f"intraday positions about {commodity.BROKER_SQUAREOFF_MIN} minutes before the close (Kotak's own time may differ), so the broker may close them first. "
                            f"Choosing the exit time is up to you; an earlier one is safer.")
    elif x > LATEST_EXIT:
        p.err("Exit time", "must be 15:15 or earlier so intraday positions are closed before the exchange's auto square-off")
    if x <= e:
        p.err("Exit time", "must be after the entry time")
    if not e <= last <= x:
        p.err("Last entry time", "must be between the entry time and the exit time")
    if s.no_reentry_after and not e < _minutes(s.no_reentry_after) <= x:
        p.err("No re-entry after", "must be after the entry time and no later than the exit time")
    if s.stop_monitoring_after and not e < _minutes(s.stop_monitoring_after) <= x:
        p.err("Stop monitoring after", "must be after the entry time and no later than the exit time")
    for lg in s.legs:
        w = f"Leg {lg.id[1:]}"
        if lg.trigger.type == "range":
            u = _minutes(lg.trigger.until)
            if not e < u <= last:
                p.err(f"{w} range breakout", "the range must end after the entry time and before the last entry time")
        if lg.trigger.type == "condition":
            for c in lg.trigger.conditions:
                for o in (c.left, c.right):
                    if o.kind in ("candle", "open_range_high", "open_range_low"):
                        a = _minutes(o.at if o.kind == "candle" else o.start)
                        if a < o_min or a > c_min - 1:
                            p.err(f"{w} condition", f"candle times must be during market hours ({o_hm} to {(c_min - 1) // 60:02d}:{(c_min - 1) % 60:02d})")
        if lg.trail.on and lg.trail.every <= 0:
            p.err(f"{w} trailing", "needs a size")
    sells = [lg for lg in s.legs if lg.side == "S"]
    naked = [lg for lg in sells if lg.sl.mode == "none"]
    protected = s.overall.sl.mode != "none" or s.limits.max_loss > 0
    if naked:
        warnings.append(f"{len(naked)} sold leg(s) have no stop-loss. A sold option can lose many times what it earns. " +
                        ("Only the overall stop-loss and the max-loss check (software) limit the loss." if protected else
                         "There is no overall stop-loss or max loss either, so nothing in this strategy limits the loss; only you can."))
    if s.limits.max_loss <= 0:
        warnings.append("No max loss is set for this strategy. Losses are limited only by the stops and exit rules you set.")
    if any(lg.segment == "futures" for lg in s.legs):
        warnings.append("Futures legs need much more margin than options and are not recorded for back-testing yet.")
    if any(lg.segment == "options" and lg.strike.by.startswith("delta") for lg in s.legs):
        warnings.append("Delta is estimated by the terminal from the option price (Black-Scholes), not sent by Kotak. "
                        "It can differ from the number on other platforms.")
    buys = [lg for lg in s.legs if lg.side == "B" and lg.trigger.type == "time"]
    if buys and sells:
        warnings.append("Bought legs are placed before sold legs so the hedge is in place first.")
    if s.square_off == "complete" and any(lg.reentry_sl.mode != "none" or lg.reentry_tgt.mode != "none" for lg in s.legs):
        warnings.append("With 'complete' square-off, a stopped leg closes everything and ends the strategy for the day, so re-entries will not happen.")
    if s.trail_be and s.square_off == "complete":
        warnings.append("Trail to break-even does nothing with 'complete' square-off, because the other legs are already closed.")


# ---------------------------------------------------------------------------------------- plain English
def _unit(rule_mode: str) -> str:
    return "%" if rule_mode == "percent" else " points"


def strike_text(sel: StrikeSel, kind: str) -> str:
    b = sel.by
    if b == "atm":
        return "the at-the-money strike" if sel.steps == 0 else f"{abs(sel.steps)} strike(s) {'out of' if sel.steps > 0 else 'in'} the money from ATM"
    if b == "premium_closest":
        return f"the strike with the price closest to ₹{sel.value:g}"
    if b == "premium_gte":
        return f"the cheapest strike priced at ₹{sel.value:g} or more"
    if b == "premium_lte":
        return f"the dearest strike priced at ₹{sel.value:g} or less"
    if b == "premium_range":
        return f"a strike priced between ₹{sel.lo:g} and ₹{sel.hi:g}"
    if b == "pct_atm":
        return f"the strike {abs(sel.value):g}% {'out of' if sel.value >= 0 else 'in'} the money from ATM"
    if b == "straddle":
        return f"the strike {abs(sel.value):g}% of the ATM straddle price {'out of' if sel.value >= 0 else 'in'} the money"
    if b == "synthetic":
        return (f"the synthetic-future strike" if sel.steps == 0 else
                f"{abs(sel.steps)} strike(s) {'out of' if sel.steps > 0 else 'in'} the money from the synthetic-future strike")
    if b == "delta_closest":
        return f"the strike with delta closest to {sel.value:g}"
    return f"a strike with delta between {sel.lo:g} and {sel.hi:g}"


def _rule_text(r: RiskRule, lg: LegCfg, what: str) -> str:
    if r.mode == "none":
        return f"no {what}"
    against = "against" if what == "stop-loss" else "in your favour"
    return f"{what} {r.value:g}{_unit(r.mode)} {against}"


def _reentry_text(r: Reentry, what: str) -> str:
    if r.mode == "none":
        return ""
    how = {"asap": "immediately", "cost": "when the price returns to the original entry price",
           "momentum": f"after the price moves {r.value:g}{'%' if r.unit == 'percent' else ' points'} from the exit price"}[r.mode]
    return (f"After a {what}, {'take the opposite side' if r.reverse else 're-enter'} {how}, up to {r.count} time(s).")


def _trigger_text(t: Trigger, s: LegStrategy) -> str:
    if t.type == "time":
        return f"Enters at {s.entry_time}."
    if t.type == "momentum":
        return (f"The strike is chosen at {s.entry_time}; enters when its price has moved "
                f"{'up' if t.dir == 'up' else 'down'} by {t.value:g}{'%' if t.unit == 'percent' else ' points'} from that price.")
    if t.type == "range":
        what = "the option's own" if t.of == "option" else f"the {_und(s)} {'future' if commodity.is_commodity(s.underlying) else 'index'}"
        return (f"The strike is chosen at {s.entry_time}; {what} price range from {s.entry_time} to {t.until} is recorded, "
                f"and the leg enters when the price breaks the range {t.side}.")
    from .spec import condition_text
    joiner = " AND " if t.match == "all" else " OR "
    return (f"The strike is chosen at {s.entry_time}; enters when " + joiner.join(condition_text(c, s, "SELF") for c in t.conditions) +
            " (each signal fires once and must go false before it can fire again).")


def describe(s: LegStrategy) -> list[str]:
    out = [f"Trades {_und(s)}, intraday (product {s.product}). Everything starts at {s.entry_time}, new entries stop at {s.last_entry_time}, "
           f"and all positions are closed at {s.exit.time}."]
    for lg in s.legs:
        verb = "SELL" if lg.side == "S" else "BUY"
        if lg.segment == "futures":
            what = f"the {_und(s)} future ({EXPIRY_TEXT[lg.expiry]})"
        else:
            what = f"the {_und(s)} {'Call' if lg.kind == 'CE' else 'Put'} ({EXPIRY_TEXT[lg.expiry]}), {strike_text(lg.strike, lg.kind)}"
        bits = [f"Leg {lg.id[1:]}: {verb} {lg.lots} lot(s) of {what}. {_trigger_text(lg.trigger, s)}"]
        bits.append(f"{_rule_text(lg.sl, lg, 'stop-loss').capitalize()}"
                    f"{' (also placed as an exchange order)' if lg.sl.mode != 'none' and s.exit.exchange_sl else ''}; "
                    f"{_rule_text(lg.target, lg, 'target')}.")
        if lg.trail.on:
            bits.append(f"Trailing: for every {lg.trail.every:g}{_unit(lg.sl.mode)} in your favour, the stop-loss moves {lg.trail.by:g}{_unit(lg.sl.mode)}.")
        for r, w in ((lg.reentry_sl, "stop-loss"), (lg.reentry_tgt, "target")):
            t = _reentry_text(r, w)
            if t:
                bits.append(t)
        out.append(" ".join(bits))
    o = s.overall
    if o.sl.mode != "none":
        out.append(f"Overall stop-loss: close everything if the total loss reaches "
                   f"{'₹' + format(o.sl.value, ',.0f') if o.sl.mode == 'rupees' else format(o.sl.value, 'g') + '% of the premium taken'}.")
    if o.target.mode != "none":
        out.append(f"Overall target: close everything if the total profit reaches "
                   f"{'₹' + format(o.target.value, ',.0f') if o.target.mode == 'rupees' else format(o.target.value, 'g') + '% of the premium taken'}.")
    if o.trail.on:
        t = o.trail
        extra = f", then for every ₹{t.every:,.0f} more profit raise it by ₹{t.by:,.0f}" if t.every > 0 else ""
        out.append(f"Overall profit trailing: once profit reaches ₹{t.reach:,.0f}, lock in ₹{t.lock:,.0f}{extra}. If profit falls back to the locked amount, everything closes.")
    out.append("When one leg hits its stop-loss or target: " + ("everything else closes and the strategy ends for the day."
                                                              if s.square_off == "complete" else "only that leg closes; the others carry on."))
    if s.trail_be:
        out.append(f"After a leg stops out, the stop-loss of the {'other legs' if s.trail_be_scope == 'all' else 'other legs that have one'} moves to their entry price.")
    if s.no_reentry_after:
        out.append(f"No re-entries after {s.no_reentry_after}.")
    if s.stop_monitoring_after:
        out.append(f"From {s.stop_monitoring_after} the terminal stops watching targets, trailing, re-entries and the overall profit rules; "
                   f"stop-loss orders stay, and open legs close at {s.exit.time}.")
    out.append(f"Whatever happens, if the total loss reaches ₹{s.limits.max_loss:,.0f} everything is closed and the strategy stops."
               if s.limits.max_loss > 0 else "No strategy-level max loss.")
    return out


# --------------------------------------------------------------------------------------------- templates
def _leg(side: str, kind: str, by: dict, **kw) -> dict:
    return {"segment": "options", "lots": 1, "side": side, "kind": kind, "expiry": "nearest", "strike": by, **kw}


def templates() -> list[dict]:
    atm = {"by": "atm", "steps": 0}
    sl = lambda pct: {"mode": "percent", "value": pct}
    return [
        {
            "id": "leg_straddle", "title": "Short straddle at a set time",
            "blurb": "Sells the at-the-money Call and Put at 09:35, 25% stop-loss on each, everything closed at 15:14. "
                     "The classic time-based straddle: the stop of one leg does not close the other.",
            "caution": "Selling both sides needs a lot of margin and can lose heavily on a big move. Paper-trade it first.",
            "spec": {"version": 2, "name": "Straddle 09:35", "underlying": "NIFTY", "entry_time": "09:35", "exit_time": "15:14",
                     "square_off": "partial", "legs": [_leg("S", "CE", atm, sl=sl(25)), _leg("S", "PE", atm, sl=sl(25))],
                     "limits": {"max_loss": 0, "slippage_pct": 1.0}},
        },
        {
            "id": "leg_strangle_delta", "title": "Short strangle by delta",
            "blurb": "Sells the Call and the Put whose delta is closest to 0.20 at 09:20, 30% stop-loss on each, and closes everything if "
                     "the stop-loss of one leg is hit (complete square-off).",
            "caution": "Delta is estimated by the terminal from option prices, so it can differ from other platforms.",
            "spec": {"version": 2, "name": "Strangle delta 0.20", "underlying": "NIFTY", "entry_time": "09:20", "exit_time": "15:14",
                     "square_off": "complete",
                     "legs": [_leg("S", "CE", {"by": "delta_closest", "value": 0.2}, sl=sl(30)),
                              _leg("S", "PE", {"by": "delta_closest", "value": 0.2}, sl=sl(30))],
                     "limits": {"max_loss": 0, "slippage_pct": 1.0}},
        },
        {
            "id": "leg_strangle_premium", "title": "Strangle by premium (about ₹40)",
            "blurb": "Sells the Call and Put priced closest to ₹40 at 09:30, 40% stop-loss on each with trailing: for every 10% the price falls, the "
                     "stop moves down 10%. Overall target ₹3,000.",
            "caution": "A premium of ₹40 means a different distance from the index on different days.",
            "spec": {"version": 2, "name": "Strangle premium 40", "underlying": "NIFTY", "entry_time": "09:30", "exit_time": "15:14",
                     "square_off": "partial",
                     "legs": [_leg("S", "CE", {"by": "premium_closest", "value": 40}, sl=sl(40), trail={"on": True, "every": 10, "by": 10}),
                              _leg("S", "PE", {"by": "premium_closest", "value": 40}, sl=sl(40), trail={"on": True, "every": 10, "by": 10})],
                     "overall": {"target": {"mode": "rupees", "value": 3000}},
                     "limits": {"max_loss": 0, "slippage_pct": 1.0}},
        },
        {
            "id": "leg_condor", "title": "Iron condor with bought hedges",
            "blurb": "Buys a Call and a Put six strikes out (the hedges, bought first), then sells a Call and a Put three strikes out at 09:30. "
                     "The bought legs cap the loss; the sold legs have a 30% stop-loss.",
            "caution": "Check the margin your broker asks for. Four legs means four orders and four sets of charges.",
            "spec": {"version": 2, "name": "Iron condor", "underlying": "NIFTY", "entry_time": "09:30", "exit_time": "15:14",
                     "square_off": "partial",
                     "legs": [_leg("B", "CE", {"by": "atm", "steps": 6}), _leg("B", "PE", {"by": "atm", "steps": 6}),
                              _leg("S", "CE", {"by": "atm", "steps": 3}, sl=sl(30)), _leg("S", "PE", {"by": "atm", "steps": 3}, sl=sl(30))],
                     "limits": {"max_loss": 0, "slippage_pct": 1.0}},
        },
        {
            "id": "leg_momentum", "title": "Sell Call after it rises (momentum entry, re-entry)",
            "blurb": "At 09:30 the at-the-money Call is chosen; it is sold only if its price rises 10% from there (momentum). 25% stop-loss, "
                     "and after a stop-loss it re-enters once when the price returns to the entry price.",
            "caution": "A momentum entry may never trigger. Re-entries add trades and charges.",
            "spec": {"version": 2, "name": "Call momentum", "underlying": "NIFTY", "entry_time": "09:30", "exit_time": "15:14",
                     "square_off": "partial",
                     "legs": [_leg("S", "CE", atm, sl=sl(25), trigger={"type": "momentum", "unit": "percent", "dir": "up", "value": 10},
                                   reentry_sl={"mode": "cost", "count": 1})],
                     "limits": {"max_loss": 0, "slippage_pct": 1.0}},
        },
        {
            "id": "leg_blank", "title": "Start from scratch (legs)",
            "blurb": "One sold at-the-money Call with a 25% stop-loss. Add legs, change strikes, triggers, stops and re-entries.",
            "caution": "",
            "spec": {"version": 2, "name": "My leg strategy", "underlying": "NIFTY", "entry_time": "09:35", "exit_time": "15:14",
                     "square_off": "partial", "legs": [_leg("S", "CE", atm, sl=sl(25))],
                     "limits": {"max_loss": 0, "slippage_pct": 1.0}},
        },
    ]
