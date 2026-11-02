"""
Strike selection for the leg builder: given the option chain and live prices, which strike does a leg trade?

Everything here is a pure function of (strikes, spot, prices, time to expiry), so it is tested without any market.
Nothing is guessed: if the prices needed to decide are missing, a StrikeError says what is missing and the caller
tries again a moment later.

Selectors (the leg's `strike` object, key `by`):
  atm            steps (+ = out of the money, - = in the money)         ATM, OTM 3, ITM 2 ...
  premium_closest value                                                  the strike whose price is closest to the value
  premium_gte     value                                                  the cheapest strike that still costs at least the value
  premium_lte     value                                                  the dearest strike that costs at most the value
  premium_range   lo, hi                                                 a strike priced inside the range (closest to its middle)
  pct_atm         value (% , + = out of the money)                       ATM strike moved by that % of the index
  straddle        value (% of the ATM straddle price, + = out)           ATM strike moved by that part of the straddle price
  synthetic       steps                                                  like atm, but the centre is the synthetic future
  delta_closest   value (0.05 .. 0.95)                                   the strike whose delta is closest to the value
  delta_range     lo, hi                                                 a strike with delta inside the range

Delta is NOT sent by the broker's chain. It is worked out here: the implied volatility is solved from the option's last
price (Black-Scholes, no interest, no dividend), then delta is taken from it. It is an estimate, not the exchange's number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

BY = ("atm", "premium_closest", "premium_gte", "premium_lte", "premium_range", "pct_atm", "straddle", "synthetic",
      "delta_closest", "delta_range")
NEEDS_PRICES = {"premium_closest", "premium_gte", "premium_lte", "premium_range", "delta_closest", "delta_range", "straddle", "synthetic"}
WIDE = 25                                   # strikes each side of ATM that must be watched for price-based selectors


class StrikeError(Exception):
    """The strike cannot be chosen (yet). The message is shown to the person."""


@dataclass
class Pick:
    strike: float
    note: str


# --------------------------------------------------------------------------------------------- Black-Scholes
def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(kind: str, s: float, k: float, t: float, vol: float) -> float:
    if t <= 0 or vol <= 0:
        return max(s - k, 0.0) if kind == "CE" else max(k - s, 0.0)
    sq = vol * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * vol * vol * t) / sq
    d2 = d1 - sq
    call = s * _cdf(d1) - k * _cdf(d2)
    return call if kind == "CE" else call - s + k


def implied_vol(kind: str, price: float, s: float, k: float, t: float) -> float | None:
    """Volatility that makes Black-Scholes equal `price`; None if the price carries no time value (cannot be solved)."""
    if price <= 0 or s <= 0 or k <= 0 or t <= 0:
        return None
    intrinsic = max(s - k, 0.0) if kind == "CE" else max(k - s, 0.0)
    if price <= intrinsic + 0.01:
        return None
    lo, hi = 0.005, 6.0
    if bs_price(kind, s, k, t, hi) < price or bs_price(kind, s, k, t, lo) > price:
        return None
    for _ in range(70):
        mid = (lo + hi) / 2
        if bs_price(kind, s, k, t, mid) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def delta(kind: str, price: float, s: float, k: float, t: float) -> float | None:
    """Absolute delta (0..1) estimated from the option's price, or None if it cannot be estimated."""
    vol = implied_vol(kind, price, s, k, t)
    if vol is None:
        return None
    d1 = (math.log(s / k) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))
    return _cdf(d1) if kind == "CE" else 1.0 - _cdf(d1)


# ------------------------------------------------------------------------------------------------ selection
def step_of(strikes: list[float]) -> float:
    d = sorted({round(b - a, 4) for a, b in zip(strikes, strikes[1:]) if b > a})
    return d[0] if d else 1.0


def atm_index(strikes: list[float], spot: float) -> int:
    return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot), strikes[i]))


def _nearest(strikes: list[float], x: float) -> int:
    return min(range(len(strikes)), key=lambda i: (abs(strikes[i] - x), strikes[i]))


def _shift(strikes: list[float], i: int, steps: int, kind: str) -> int:
    j = i + steps if kind == "CE" else i - steps          # out of the money is up for a Call and down for a Put
    if not 0 <= j < len(strikes):
        raise StrikeError(f"{abs(steps)} strikes {'out of' if steps > 0 else 'in'} the money is outside the option chain.")
    return j


def pick_strike(sel: dict, kind: str, strikes: list[float], spot: float, price: Callable[[float, str], float | None],
                t_years: float) -> Pick:
    """Choose a strike. `price(strike, 'CE'|'PE')` returns the last price or None. Raises StrikeError if it cannot decide."""
    if not strikes:
        raise StrikeError("The option chain has no strikes.")
    by = sel.get("by", "atm")
    i = atm_index(strikes, spot)
    atm = strikes[i]

    if by == "atm":
        steps = int(sel.get("steps", 0))
        j = _shift(strikes, i, steps, kind)
        where = "ATM" if steps == 0 else f"{'OTM' if steps > 0 else 'ITM'} {abs(steps)}"
        return Pick(strikes[j], f"{where} (index {spot:g}, ATM {atm:g})")

    if by == "pct_atm":
        pct = float(sel.get("value", 0))
        x = atm * (1 + pct / 100.0) if kind == "CE" else atm * (1 - pct / 100.0)
        j = _nearest(strikes, x)
        if x < strikes[0] - step_of(strikes) or x > strikes[-1] + step_of(strikes):
            raise StrikeError(f"{pct:g}% from ATM is outside the option chain.")
        return Pick(strikes[j], f"{pct:g}% {'out of' if pct >= 0 else 'in'} the money from ATM {atm:g}")

    if by in ("straddle", "synthetic"):
        ce, pe = price(atm, "CE"), price(atm, "PE")
        if not ce or not pe:
            raise StrikeError(f"Waiting for the ATM {atm:g} Call and Put prices.")
        if by == "straddle":
            pct = float(sel.get("value", 0))
            dist = (ce + pe) * pct / 100.0
            x = atm + dist if kind == "CE" else atm - dist
            if x < strikes[0] - step_of(strikes) or x > strikes[-1] + step_of(strikes):
                raise StrikeError(f"{pct:g}% of the straddle price ({ce + pe:.1f}) is outside the option chain.")
            return Pick(strikes[_nearest(strikes, x)], f"ATM {atm:g} straddle costs {ce + pe:.1f}; {pct:g}% of it is {dist:.1f} points")
        centre = _nearest(strikes, atm + ce - pe)
        steps = int(sel.get("steps", 0))
        j = _shift(strikes, centre, steps, kind)
        return Pick(strikes[j], f"synthetic future {atm + ce - pe:.1f} (ATM {atm:g}), {steps:+d} steps")

    # ---- price based: premium or delta. Only strikes with an unbroken run of prices outward from ATM are trusted.
    run = [i]
    gap_lo = gap_hi = False
    for direction in (-1, 1):
        j = i + direction
        while 0 <= j < len(strikes):
            if not price(strikes[j], kind):
                if direction < 0:
                    gap_lo = True
                else:
                    gap_hi = True
                break
            run.append(j)
            j += direction
    if not price(atm, kind):
        raise StrikeError(f"Waiting for the ATM {atm:g} {kind} price.")
    rows: list[tuple[int, float]] = []
    for j in sorted(run):
        px = price(strikes[j], kind)
        if px is None:
            continue
        if by.startswith("premium"):
            rows.append((j, float(px)))
        else:
            d = delta(kind, float(px), spot, strikes[j], t_years)
            if d is not None:
                rows.append((j, d))
    if not rows:
        raise StrikeError("No usable prices yet.")
    lo_i, hi_i = min(j for j, _ in rows), max(j for j, _ in rows)

    def edge_gap(j: int) -> bool:
        """The chosen strike sits next to strikes we have no price for, so a better one may exist beyond it."""
        return (j == lo_i and gap_lo and lo_i == min(run)) or (j == hi_i and gap_hi and hi_i == max(run))

    unit = "premium" if by.startswith("premium") else "delta"
    fmt = (lambda v: f"{v:.2f}") if unit == "premium" else (lambda v: f"{v:.3f}")
    if by.endswith("_range"):
        lo, hi = float(sel.get("lo", 0)), float(sel.get("hi", 0))
        inside = [(j, v) for j, v in rows if lo <= v <= hi]
        if not inside:
            raise StrikeError(f"No strike has a {unit} between {lo:g} and {hi:g} right now.")
        mid = (lo + hi) / 2
        j, v = min(inside, key=lambda r: (abs(r[1] - mid), abs(strikes[r[0]] - spot)))
    else:
        target = float(sel.get("value", 0))
        if by == "premium_gte":
            cand = [(j, v) for j, v in rows if v >= target]
            if not cand:
                raise StrikeError(f"No strike costs {target:g} or more right now.")
            j, v = min(cand, key=lambda r: (r[1], abs(strikes[r[0]] - spot)))
        elif by == "premium_lte":
            cand = [(j, v) for j, v in rows if v <= target]
            if not cand:
                raise StrikeError(f"No strike costs {target:g} or less right now.")
            j, v = max(cand, key=lambda r: (r[1], -abs(strikes[r[0]] - spot)))
        else:
            j, v = min(rows, key=lambda r: (abs(r[1] - target), abs(strikes[r[0]] - spot)))
    if edge_gap(j):
        raise StrikeError("Waiting for prices of the strikes next to the best match, so a better strike is not missed.")
    return Pick(strikes[j], f"{unit} {fmt(v)} at strike {strikes[j]:g} (index {spot:g})")
