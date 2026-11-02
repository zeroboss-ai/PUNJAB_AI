"""Builds 1-minute candles from raw ticks (bucketed by tick arrival time, IST)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .clock import minute_floor


@dataclass
class Candle:
    o: float
    h: float
    l: float
    c: float


class CandleStore:
    def __init__(self) -> None:
        self._c: dict[str, dict[datetime, Candle]] = defaultdict(dict)
        self.last_price: dict[str, float] = {}
        self.last_ts: dict[str, datetime] = {}

    def on_tick(self, symbol: str, price: float, ts: datetime) -> None:
        m = minute_floor(ts)
        d = self._c[symbol]
        c = d.get(m)
        if c is None:
            d[m] = Candle(price, price, price, price)
        else:
            if price > c.h:
                c.h = price
            if price < c.l:
                c.l = price
            c.c = price
        self.last_price[symbol] = price
        self.last_ts[symbol] = ts

    def candle(self, symbol: str, minute: datetime) -> Candle | None:
        return self._c.get(symbol, {}).get(minute)

    def high_low(self, symbol: str, start: datetime, end: datetime) -> tuple[float, float] | None:
        """High and low across all 1-minute candles with start <= minute < end."""
        cs = [c for m, c in self._c.get(symbol, {}).items() if start <= m < end]
        if not cs:
            return None
        return max(c.h for c in cs), min(c.l for c in cs)
