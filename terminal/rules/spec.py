"""
Strategy spec: what the visual builder produces and the rule engine runs.

A strategy is JSON. `parse()` checks it strictly (nothing is guessed or silently fixed) and returns a normalised
Strategy, plus warnings for legal-but-risky choices. `describe()` turns it back into plain English so the person
can read exactly what will run before starting it.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..clock import parse_hm

TFS = (1, 3, 5, 10, 15, 30, 60)
SRCS = ("INDEX", "CE", "PE", "SELF")
OPS = {">": "is above", ">=": "is at or above", "<": "is below", "<=": "is at or below",
       "crosses_above": "crosses above", "crosses_below": "crosses below"}
FIELDS = ("open", "high", "low", "close")
UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY")
KINDS = {
    "number": "a number", "ltp": "last price", "open_range_high": "opening-range high", "open_range_low": "opening-range low",
    "candle": "a fixed candle", "prev_candle": "the previous candle", "day_high": "day high", "day_low": "day low",
    "day_open": "day open", "prev_close": "previous close", "ema": "EMA", "sma": "SMA", "rsi": "RSI",
}
NEEDS_SRC = set(KINDS) - {"number"}
HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RULES, MAX_CONDS = 4, 6


class SpecError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Operand:
    kind: str = "ltp"
    src: str = "SELF"
    value: float = 0.0            # kind == number
    field: str = "close"          # candle / prev_candle
    at: str = "09:16"             # candle: the 1-minute candle that starts at this clock time
    start: str = "09:15"          # open range: first candle
    candles: int = 4              # open range: how many 1-minute candles
    tf: int = 1                   # minutes per candle (prev_candle, indicators)
    period: int = 14              # indicators
    offset: float = 0.0           # added to the value (e.g. high + 2 points)


@dataclass
class Condition:
    left: Operand
    op: str
    right: Operand


@dataclass
class EntryRule:
    id: str
    side: str                     # "S" sell | "B" buy
    leg: str                      # "CE" | "PE"
    match: str = "all"            # all | any
    conditions: list[Condition] = field(default_factory=list)


@dataclass
class Stop:
    mode: str = "none"            # none | points | percent | level
    value: float = 0.0
    level: Operand | None = None  # mode == level: price level taken at entry time (SELF = the traded option)


@dataclass
class Target:
    mode: str = "none"            # none | points | percent
    value: float = 0.0


@dataclass
class Exit:
    stop: Stop = field(default_factory=Stop)
    target: Target = field(default_factory=Target)
    time: str = "15:10"
    match: str = "any"
    conditions: list[Condition] = field(default_factory=list)   # extra exit rule; SELF = the open option
    exchange_sl: bool = False


@dataclass
class Limits:
    max_entries: int = 0          # 0 = no limit (your own rule)
    window_start: str = "09:15"
    window_end: str = "15:00"
    max_loss: float = 0.0         # rupees, whole strategy, software check; 0 = none
    slippage_pct: float = 1.0     # limit price placed this % through the last price so it fills


@dataclass
class Strategy:
    name: str
    underlying: str = "NIFTY"
    expiry: str = "nearest"       # nearest | next | YYYY-MM-DD
    product: str = "MIS"
    lots: int = 1
    lock_time: str = "09:15"
    offset_ce: int = 0            # strikes away from ATM (+ = higher strike)
    offset_pe: int = 0
    entries: list[EntryRule] = field(default_factory=list)
    exit: Exit = field(default_factory=Exit)
    limits: Limits = field(default_factory=Limits)
    note: str = ""

    def to_json(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------------------------------- parsing
class _P:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}" if where else msg)

    def num(self, d: dict, k: str, where: str, lo: float, hi: float, default: float | None = None, integer: bool = False) -> float:
        v = d.get(k, default)
        if isinstance(v, bool) or v is None or v == "":
            if default is None:
                self.err(where, f"'{k}' needs a number")
                return 0.0
            v = default
        try:
            x = float(v)
        except (TypeError, ValueError):
            self.err(where, f"'{k}' must be a number")
            return 0.0
        if not math.isfinite(x) or x < lo or x > hi:
            self.err(where, f"'{k}' must be between {lo:g} and {hi:g}")
            return 0.0
        if integer and x != int(x):
            self.err(where, f"'{k}' must be a whole number")
            return 0.0
        return int(x) if integer else x

    def choice(self, d: dict, k: str, where: str, allowed, default=None):
        v = d.get(k, default)
        if v not in allowed:
            self.err(where, f"'{k}' must be one of {', '.join(map(str, allowed))}")
            return default if default in allowed else allowed[0]
        return v

    def hm(self, d: dict, k: str, where: str, default: str) -> str:
        v = str(d.get(k, default) or default).strip()
        if not HM.match(v):
            self.err(where, f"'{k}' must be a time like 09:20")
            return default
        return v


def _minutes(hm: str) -> int:
    t = parse_hm(hm)
    return t.hour * 60 + t.minute


def _operand(p: _P, d: Any, where: str) -> Operand:
    if not isinstance(d, dict):
        p.err(where, "is missing")
        return Operand()
    o = Operand()
    o.kind = p.choice(d, "kind", where, tuple(KINDS), "ltp")
    if o.kind == "number":
        o.value = p.num(d, "value", where, -1e9, 1e9)
        o.src = "SELF"
    else:
        o.src = p.choice(d, "src", where, SRCS, "SELF")
    if o.kind in ("candle", "prev_candle"):
        o.field = p.choice(d, "field", where, FIELDS, "close")
    if o.kind == "candle":
        o.at = p.hm(d, "at", where, "09:16")
    if o.kind in ("open_range_high", "open_range_low"):
        o.start = p.hm(d, "start", where, "09:15")
        o.candles = p.num(d, "candles", where, 1, 60, 4, integer=True)
    if o.kind in ("prev_candle", "ema", "sma", "rsi"):
        o.tf = int(p.num(d, "tf", where, 1, 60, 1, integer=True))
        if o.tf not in TFS:
            p.err(where, f"candle size must be one of {', '.join(map(str, TFS))} minutes")
    if o.kind in ("ema", "sma"):
        o.period = p.num(d, "period", where, 1, 200, 20, integer=True)
    if o.kind == "rsi":
        o.period = p.num(d, "period", where, 2, 100, 14, integer=True)
    if o.kind != "number":
        o.offset = p.num(d, "offset", where, -1e6, 1e6, 0.0)
    return o


def _conditions(p: _P, lst: Any, where: str) -> list[Condition]:
    if lst is None:
        return []
    if not isinstance(lst, list):
        p.err(where, "conditions must be a list")
        return []
    if len(lst) > MAX_CONDS:
        p.err(where, f"at most {MAX_CONDS} conditions")
        lst = lst[:MAX_CONDS]
    out = []
    for i, c in enumerate(lst, 1):
        w = f"{where}, condition {i}"
        if not isinstance(c, dict):
            p.err(w, "is not valid")
            continue
        op = p.choice(c, "op", w, tuple(OPS), ">")
        out.append(Condition(_operand(p, c.get("left"), f"{w} (left side)"), op, _operand(p, c.get("right"), f"{w} (right side)")))
    return out


def parse(raw: Any) -> tuple[Strategy | None, list[str], list[str]]:
    """Returns (strategy, errors, warnings). strategy is None when there are errors."""
    p = _P()
    if not isinstance(raw, dict):
        return None, ["The strategy must be an object"], []
    name = str(raw.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        p.err("", "Give the strategy a name (up to 60 characters)")
    s = Strategy(name=name or "Untitled")
    s.underlying = p.choice(raw, "underlying", "Underlying", UNDERLYINGS, "NIFTY")
    exp = str(raw.get("expiry", "nearest"))
    if exp not in ("nearest", "next") and not re.match(r"^\d{4}-\d{2}-\d{2}$", exp):
        p.err("Expiry", "must be nearest, next, or a date like 2026-10-29")
        exp = "nearest"
    s.expiry = exp
    s.product = p.choice(raw, "product", "Product", ("MIS", "NRML"), "MIS")
    s.lots = p.num(raw, "lots", "Lots", 1, 50, 1, integer=True)
    s.lock_time = p.hm(raw, "lock_time", "Strike lock time", "09:15")
    s.offset_ce = int(p.num(raw, "offset_ce", "Call strike offset", -20, 20, 0, integer=True))
    s.offset_pe = int(p.num(raw, "offset_pe", "Put strike offset", -20, 20, 0, integer=True))
    s.note = str(raw.get("note", ""))[:400]

    ents = raw.get("entries")
    if not isinstance(ents, list) or not ents:
        p.err("", "Add at least one entry rule")
        ents = []
    if len(ents) > MAX_RULES:
        p.err("", f"At most {MAX_RULES} entry rules")
        ents = ents[:MAX_RULES]
    for i, e in enumerate(ents, 1):
        w = f"Entry {i}"
        if not isinstance(e, dict):
            p.err(w, "is not valid")
            continue
        r = EntryRule(id=f"e{i}", side=p.choice(e, "side", w, ("S", "B"), "S"), leg=p.choice(e, "leg", w, ("CE", "PE"), "CE"),
                      match=p.choice(e, "match", w, ("all", "any"), "all"))
        r.conditions = _conditions(p, e.get("conditions"), w)
        s.entries.append(r)
    if len({(r.leg) for r in s.entries}) < len(s.entries):
        p.err("", "Each option (Call / Put) can have only one entry rule")

    x = raw.get("exit") or {}
    ex = Exit()
    st = x.get("stop") or {}
    ex.stop.mode = p.choice(st, "mode", "Stop-loss", ("none", "points", "percent", "level"), "none")
    if ex.stop.mode in ("points", "percent"):
        ex.stop.value = p.num(st, "value", "Stop-loss", 0.05, 1e6 if ex.stop.mode == "points" else 1000, None)
    if ex.stop.mode == "level":
        ex.stop.level = _operand(p, st.get("level"), "Stop-loss level")
        if ex.stop.level.kind == "number":
            p.err("Stop-loss level", "use points or percent for a fixed number; a level should come from the chart (a candle high, a range...)")
    tg = x.get("target") or {}
    ex.target.mode = p.choice(tg, "mode", "Target", ("none", "points", "percent"), "none")
    if ex.target.mode != "none":
        ex.target.value = p.num(tg, "value", "Target", 0.05, 1e6 if ex.target.mode == "points" else 1000, None)
    ex.time = p.hm(x, "time", "Exit time", "15:10")
    ex.match = p.choice(x, "match", "Exit rule", ("all", "any"), "any")
    ex.conditions = _conditions(p, x.get("conditions"), "Exit rule")
    ex.exchange_sl = bool(x.get("exchange_sl", False))
    s.exit = ex

    lm = raw.get("limits") or {}
    s.limits = Limits(
        max_entries=p.num(lm, "max_entries", "Limits", 0, 100, 0, integer=True),
        window_start=p.hm(lm, "window_start", "Entry window", "09:15"), window_end=p.hm(lm, "window_end", "Entry window", "15:00"),
        max_loss=p.num(lm, "max_loss", "Max loss", 0, 10_000_000, 0.0),
        slippage_pct=p.num(lm, "slippage_pct", "Slippage", 0.1, 10, 1.0))

    warnings: list[str] = []
    if not p.errors:
        _cross_checks(s, p, warnings)
    if p.errors:
        return None, p.errors, warnings
    return s, [], warnings


def _cross_checks(s: Strategy, p: _P, warnings: list[str]) -> None:
    lo, hi = _minutes(s.limits.window_start), _minutes(s.limits.window_end)
    if lo >= hi:
        p.err("Entry window", "must end after it starts")
    if hi > _minutes(s.exit.time):
        p.err("Entry window", "cannot end after the exit time")
    if _minutes(s.exit.time) > 15 * 60 + 15:
        p.err("Exit time", "must be 15:15 or earlier so intraday positions are closed before the exchange's auto square-off")
    if _minutes(s.lock_time) < 9 * 60 + 15 or _minutes(s.lock_time) >= _minutes(s.exit.time):
        p.err("Strike lock time", "must be between 09:15 and the exit time")
    if lo < _minutes(s.lock_time):
        warnings.append("The entry window opens before the strikes are locked; entries can only start after the lock time.")
    for r in s.entries:
        for c in r.conditions:
            for o in (c.left, c.right):
                if o.kind in ("candle", "open_range_high", "open_range_low"):
                    a = _minutes(o.at if o.kind == "candle" else o.start)
                    if a < 9 * 60 + 15 or a > 15 * 60 + 29:
                        p.err(f"Entry ({r.leg})", "candle times must be during market hours (09:15 to 15:29)")
    sells = [r for r in s.entries if r.side == "S"]
    if sells and s.exit.stop.mode == "none":
        warnings.append("Selling options with no stop-loss" + (" and no max loss" if s.limits.max_loss <= 0 else "") +
                        ": the loss on one option can be many times what you collect. " +
                        ("Only the max-loss check (software) protects you." if s.limits.max_loss > 0 else "Nothing in this strategy limits the loss; only you can."))
    if s.exit.exchange_sl and s.exit.stop.mode == "none":
        p.err("Exit", "An exchange stop-loss order needs a stop-loss rule")
    if s.limits.max_loss <= 0:
        warnings.append("No max loss is set for this strategy. Losses are limited only by the stop-loss and exit rules you set.")
    if s.limits.max_entries <= 0:
        warnings.append("No limit on entries per day: after an exit, a signal that turns false and true again can enter again.")
    lv = s.exit.stop.level
    if lv is not None and lv.src != "SELF":
        p.err("Stop-loss level", "must come from the traded option itself (pick 'this option'); the index price is not an option price")


# ---------------------------------------------------------------------------------------- plain English
def _src_text(src: str, s: Strategy, self_leg: str | None) -> str:
    if src == "INDEX":
        return f"the {s.underlying} index"
    if src == "SELF":
        src = self_leg or "SELF"
    return {"CE": "the Call", "PE": "the Put", "SELF": "this option"}.get(src, src)


def operand_text(o: Operand, s: Strategy, self_leg: str | None = None) -> str:
    if o.kind == "number":
        return f"{o.value:g}"
    who = _src_text(o.src, s, self_leg)
    tf = "" if o.tf == 1 else f"{o.tf}-minute "
    base = {
        "ltp": f"{who} last price",
        "open_range_high": f"{who} opening-range high ({o.start}, first {o.candles} one-minute candles)",
        "open_range_low": f"{who} opening-range low ({o.start}, first {o.candles} one-minute candles)",
        "candle": f"{o.field} of {who}'s {o.at} one-minute candle",
        "prev_candle": f"{o.field} of {who}'s previous {tf}candle",
        "day_high": f"{who} day high", "day_low": f"{who} day low", "day_open": f"{who} day open",
        "prev_close": f"{who} previous close",
        "ema": f"{o.period}-period EMA of {who} ({o.tf}-min candles)", "sma": f"{o.period}-period SMA of {who} ({o.tf}-min candles)",
        "rsi": f"{o.period}-period RSI of {who} ({o.tf}-min candles)",
    }[o.kind]
    if o.offset:
        base += f" {'+' if o.offset > 0 else '−'} {abs(o.offset):g}"
    return base


def condition_text(c: Condition, s: Strategy, self_leg: str | None = None) -> str:
    return f"{operand_text(c.left, s, self_leg)} {OPS[c.op]} {operand_text(c.right, s, self_leg)}"


def describe(s: Strategy) -> list[str]:
    """Numbered plain-English lines: exactly what this strategy will do."""
    kind = {"CE": "Call", "PE": "Put"}
    out = [f"Trades {s.underlying} options, {'nearest expiry' if s.expiry == 'nearest' else 'next expiry' if s.expiry == 'next' else 'expiry ' + s.expiry}, "
           f"{s.lots} lot(s), product {s.product}. Strikes are chosen at {s.lock_time} from the index price: "
           f"Call {_off(s.offset_ce)}, Put {_off(s.offset_pe)}."]
    for r in s.entries:
        verb = "SELL" if r.side == "S" else "BUY"
        if r.conditions:
            joiner = " AND " if r.match == "all" else " OR "
            when = joiner.join(condition_text(c, s, r.leg) for c in r.conditions)
            out.append(f"{verb} the {kind[r.leg]} when {when}.")
        else:
            out.append(f"{verb} the {kind[r.leg]} as soon as the entry window opens.")
    per_day = f"at most {s.limits.max_entries} entry(ies) a day. " if s.limits.max_entries > 0 else "no limit on entries per day. "
    out.append(f"Entries only between {s.limits.window_start} and {s.limits.window_end}; {per_day}"
               f"Each signal fires once and must go false before it can fire again.")
    st, tg = s.exit.stop, s.exit.target
    if st.mode == "none":
        out.append("No stop-loss rule.")
    elif st.mode == "points":
        out.append(f"Stop-loss: {st.value:g} points against the entry price{' (also placed as an exchange order)' if s.exit.exchange_sl else ''}.")
    elif st.mode == "percent":
        out.append(f"Stop-loss: {st.value:g}% against the entry price{' (also placed as an exchange order)' if s.exit.exchange_sl else ''}.")
    else:
        out.append(f"Stop-loss level: {operand_text(st.level, s, 'the traded option')}, fixed when the trade is entered"
                   f"{' (also placed as an exchange order)' if s.exit.exchange_sl else ''}.")
    out.append("No target." if tg.mode == "none" else f"Target: {tg.value:g} {'points' if tg.mode == 'points' else '%'} in your favour.")
    if s.exit.conditions:
        joiner = " AND " if s.exit.match == "all" else " OR "
        out.append("Also exit when " + joiner.join(condition_text(c, s) for c in s.exit.conditions) + ".")
    out.append(f"Everything is closed at {s.exit.time}. " + (f"If total loss reaches ₹{s.limits.max_loss:,.0f} the strategy closes its positions and stops."
                                                           if s.limits.max_loss > 0 else "No strategy-level max loss."))
    return out


def _off(n: int) -> str:
    return "at the money" if n == 0 else f"{abs(n)} strike(s) {'above' if n > 0 else 'below'} the money"


# --------------------------------------------------------------------------------------------- templates
def _lt(kind: str, src: str, **kw) -> dict:
    return {"kind": kind, "src": src, **kw}


def templates() -> list[dict]:
    rng_hi = lambda src: _lt("open_range_high", src, start="09:15", candles=4)
    rng_lo = lambda src: _lt("open_range_low", src, start="09:15", candles=4)
    return [
        {
            "id": "orb", "title": "Opening-range breakout (your strategy)",
            "blurb": "The index and the at-the-money option both break their first-four-minute range. Sell the Put when the index "
                     "breaks up and the Put breaks down; sell the Call when the index breaks down and the Call breaks down. "
                     "Stop-loss = high of that option's 09:16 candle. No target. Exit 15:10.",
            "caution": "This is the same idea as the built-in Algo page, rebuilt from the rule blocks. The built-in version stays the more "
                       "carefully tested one; the two are not guaranteed to trade identically.",
            "spec": {
                "name": "ORB sell ATM", "underlying": "NIFTY", "expiry": "nearest", "product": "MIS", "lots": 1, "lock_time": "09:15",
                "entries": [
                    {"side": "S", "leg": "PE", "match": "all", "conditions": [
                        {"left": _lt("ltp", "INDEX"), "op": ">", "right": rng_hi("INDEX")},
                        {"left": _lt("ltp", "PE"), "op": "<", "right": rng_lo("PE")}]},
                    {"side": "S", "leg": "CE", "match": "all", "conditions": [
                        {"left": _lt("ltp", "INDEX"), "op": "<", "right": rng_lo("INDEX")},
                        {"left": _lt("ltp", "CE"), "op": "<", "right": rng_lo("CE")}]},
                ],
                "exit": {"stop": {"mode": "level", "level": _lt("candle", "SELF", field="high", at="09:16")},
                         "target": {"mode": "none"}, "time": "15:10", "match": "any", "conditions": [], "exchange_sl": True},
                "limits": {"max_entries": 0, "window_start": "09:19", "window_end": "15:00", "max_loss": 0, "slippage_pct": 1.0},
            },
        },
        {
            "id": "straddle", "title": "Short straddle at a set time (example)",
            "blurb": "Sells the at-the-money Call and Put at 09:20 with a 30% stop-loss on each and exit at 15:10. "
                     "A teaching example for time-based entries and two positions at once, not a recommendation.",
            "caution": "Selling both sides needs a lot of margin and can lose heavily on a big move. Paper-trade it first. No max loss is set; add your own below.",
            "spec": {
                "name": "Short straddle 09:20", "underlying": "NIFTY", "expiry": "nearest", "product": "MIS", "lots": 1, "lock_time": "09:20",
                "entries": [{"side": "S", "leg": "CE", "match": "all", "conditions": []},
                            {"side": "S", "leg": "PE", "match": "all", "conditions": []}],
                "exit": {"stop": {"mode": "percent", "value": 30}, "target": {"mode": "none"}, "time": "15:10",
                         "match": "any", "conditions": [], "exchange_sl": True},
                "limits": {"max_entries": 0, "window_start": "09:20", "window_end": "10:00", "max_loss": 0, "slippage_pct": 1.0},
            },
        },
        {
            "id": "blank", "title": "Start from scratch",
            "blurb": "One empty entry rule. Pick what to trade, what must be true, and how to get out.",
            "caution": "",
            "spec": {
                "name": "My strategy", "underlying": "NIFTY", "expiry": "nearest", "product": "MIS", "lots": 1, "lock_time": "09:15",
                "entries": [{"side": "B", "leg": "CE", "match": "all", "conditions": [
                    {"left": _lt("ltp", "INDEX"), "op": ">", "right": _lt("prev_candle", "INDEX", field="high", tf=5)}]}],
                "exit": {"stop": {"mode": "percent", "value": 25}, "target": {"mode": "percent", "value": 50}, "time": "15:10",
                         "match": "any", "conditions": [], "exchange_sl": False},
                "limits": {"max_entries": 0, "window_start": "09:20", "window_end": "14:30", "max_loss": 0, "slippage_pct": 1.0},
            },
        },
    ]
