"""
Synthetic trading days for offline testing / demos. Prices are piece-wise linear so every
outcome is predictable. ATM premium is 100 at index 25000; each option moves 0.5 per index point.

Scenarios
  put_sell   index breaks UP, ATM put breaks down  -> SELL PUT, held to 15:10 (profit)
  put_sl     same entry, then index falls back     -> SELL PUT, stop-loss hit (loss)
  call_sell  index breaks DOWN, ATM call breaks down -> SELL CALL, held to 15:10
  call_sl    same entry, then index rallies back   -> SELL CALL, stop-loss hit
  inside     index never leaves the opening range  -> no trade
  unconfirmed index breaks up but the put does NOT break its low -> no trade (AND rule)
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .clock import IST, round_tick
from .models import INDEX, Tick

BASE = 25000.0
STEP = 50.0
PREM = 100.0

# (minutes since 09:00, index level)
_RANGE = [(15, 25000), (15.5, 25010), (16, 25000), (16.5, 24990), (17, 25000),
          (17.5, 25010), (18, 25000), (18.5, 24990), (19, 25000)]
_PRE = [(0, 25000), (15, 25000)]

SCENARIOS: dict[str, list[tuple[float, float]]] = {
    "put_sell": _PRE[:1] + _RANGE + [(25, 25030), (60, 25060), (120, 25100), (390, 25100)],
    "put_sl": _PRE[:1] + _RANGE + [(25, 25030), (40, 25030), (60, 24975), (390, 24975)],
    "call_sell": _PRE[:1] + _RANGE + [(25, 24970), (60, 24940), (120, 24900), (390, 24900)],
    "call_sl": _PRE[:1] + _RANGE + [(25, 24970), (40, 24970), (60, 25025), (390, 25025)],
    "inside": _PRE[:1] + _RANGE + [(30, 25005), (60, 24995), (120, 25000), (390, 25000)],
    "unconfirmed": _PRE[:1] + _RANGE + [(25, 25030), (60, 25060), (390, 25060)],
}


def _interp(knots, m: float) -> float:
    if m <= knots[0][0]:
        return knots[0][1]
    for (m0, v0), (m1, v1) in zip(knots, knots[1:]):
        if m <= m1:
            return v0 + (v1 - v0) * (m - m0) / (m1 - m0)
    return knots[-1][1]


def option_symbol(strike: float, ot: str, underlying: str = "NIFTY") -> str:
    return f"{underlying}{int(strike)}{ot}"


def build_day(day: date, scenario: str = "put_sell", tick_s: int = 2, strikes_each_side: int = 3,
              underlying: str = "NIFTY") -> list[Tick]:
    knots = SCENARIOS[scenario]
    start = datetime.combine(day, time(9, 0), tzinfo=IST)
    n = int(390 * 60 / tick_s) + 1
    freeze_pe = None
    ticks: list[Tick] = []
    for i in range(n):
        ts = start + timedelta(seconds=i * tick_s)
        m = i * tick_s / 60.0
        idx = _interp(knots, m)
        ticks.append(Tick(INDEX, round_tick(idx), ts))
        for k in range(-strikes_each_side, strikes_each_side + 1):
            strike = BASE + k * STEP
            ce = PREM + 0.5 * (idx - BASE) - 0.5 * (strike - BASE)
            pe = PREM - 0.5 * (idx - BASE) + 0.5 * (strike - BASE)
            if scenario == "unconfirmed" and k == 0:
                if m >= 19 and freeze_pe is None:
                    freeze_pe = pe
                if freeze_pe is not None:
                    pe = freeze_pe          # put refuses to confirm the breakout
            ticks.append(Tick(option_symbol(strike, "CE", underlying), round_tick(max(3.0, ce)), ts))
            ticks.append(Tick(option_symbol(strike, "PE", underlying), round_tick(max(3.0, pe)), ts))
    return ticks
