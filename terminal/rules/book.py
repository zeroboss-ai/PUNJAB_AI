"""
CandleBook: 1-minute candles built from live ticks, aggregated on demand, plus the indicators the rule blocks use.

Honest limits (shown in the UI):
  * candles are built from the moment the terminal started watching an instrument today - nothing is back-filled;
    a value that needs candles from before that moment is "not available", never guessed.
  * a minute with no tick is a flat candle at the last price; minutes before the first tick do not exist.
  * candles are bucketed by the time the tick ARRIVED here, not the exchange timestamp.
  * indicators use COMPLETED candles only, aligned to 09:15 like most charting tools.
"""
from __future__ import annotations

import threading
from datetime import date, datetime

from ..clock import at, parse_hm

LATE_OK_S = 5.0              # watching may have started this many seconds after a candle began and still count as complete


def minute_at(day: date, hm: str) -> int:
    """Epoch minute of a clock time (IST) on `day`."""
    return int(at(day, parse_hm(hm)).timestamp() // 60)


def epoch_min(ts: float) -> int:
    return int(ts // 60)


class CandleBook:
    def __init__(self) -> None:
        self._c: dict[str, dict[int, list[float]]] = {}      # key -> epoch minute -> [o, h, l, c]
        self._since: dict[str, float] = {}                   # key -> epoch seconds when we started watching it
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------------------------- writing
    def watch(self, key: str, ts: float) -> None:
        with self._lock:
            self._since.setdefault(key, ts)
            self._c.setdefault(key, {})

    def watching(self, key: str) -> bool:
        return key in self._since

    def keys(self) -> set[str]:
        with self._lock:
            return set(self._since)

    def on_tick(self, key: str, price: float, ts: float) -> None:
        with self._lock:
            d = self._c.get(key)
            if d is None:
                return
            m = epoch_min(ts)
            c = d.get(m)
            if c is None:
                d[m] = [price, price, price, price]
            else:
                if price > c[1]:
                    c[1] = price
                if price < c[2]:
                    c[2] = price
                c[3] = price
            self._last[key] = price

    def reset(self) -> None:
        with self._lock:
            self._c.clear()
            self._since.clear()
            self._last.clear()

    # ----------------------------------------------------------------------------- reading
    def since(self, key: str) -> float | None:
        return self._since.get(key)

    def _complete_from(self, key: str, start_min: int) -> bool:
        s = self._since.get(key)
        return s is not None and s <= start_min * 60 + LATE_OK_S

    def minute(self, key: str, m: int) -> list[float] | None:
        """The 1-minute candle for epoch minute m, filled forward flat if that minute had no tick. None if unknowable."""
        with self._lock:
            d = self._c.get(key)
            if not d:
                return None
            c = d.get(m)
            if c is not None:
                return list(c)
            first = min(d)
            if m < first:
                return None
            k = m - 1
            while k >= first and k not in d:
                k -= 1
            if k < first:
                return None
            px = d[k][3]
            return [px, px, px, px]

    def _span(self, key: str, a: int, b: int) -> list[list[float]] | None:
        """Candles for minutes a <= m < b, or None if any is unknowable."""
        out = []
        for m in range(a, b):
            c = self.minute(key, m)
            if c is None:
                return None
            out.append(c)
        return out

    def open_range(self, key: str, start_min: int, n: int, now: float) -> tuple[float, float] | None:
        """(high, low) of n one-minute candles from start_min, once they are all complete and fully observed."""
        if now < (start_min + n) * 60 or not self._complete_from(key, start_min):
            return None
        cs = self._span(key, start_min, start_min + n)
        return None if cs is None else (max(c[1] for c in cs), min(c[2] for c in cs))

    def fixed_candle(self, key: str, m: int, now: float) -> list[float] | None:
        if now < (m + 1) * 60 or not self._complete_from(key, m):
            return None
        return self.minute(key, m)

    def _buckets(self, key: str, tf: int, day: date, now: float, hours: tuple[str, str] = ("09:15", "15:30")) -> list[list[float]]:
        """Completed tf-minute candles from the session open (09:15 on NSE/BSE, 09:00 on MCX), oldest first. Buckets we only saw part of are left out."""
        open_m = minute_at(day, hours[0])
        close_m = minute_at(day, hours[1])
        out = []
        end_all = min(epoch_min(now), close_m)
        b = open_m
        while b + tf <= end_all:
            if self._complete_from(key, b):
                cs = self._span(key, b, b + tf)
                if cs is not None:
                    out.append([cs[0][0], max(c[1] for c in cs), min(c[2] for c in cs), cs[-1][3]])
            b += tf
        return out

    def closes(self, key: str, tf: int, day: date, now: float, hours: tuple[str, str] = ("09:15", "15:30")) -> list[float]:
        return [c[3] for c in self._buckets(key, tf, day, now, hours)]

    def prev_candle(self, key: str, tf: int, day: date, now: float, hours: tuple[str, str] = ("09:15", "15:30")) -> list[float] | None:
        """The most recent COMPLETED tf-minute candle - only if it is directly adjacent to the current one."""
        b = self._buckets(key, tf, day, now, hours)
        if not b:
            return None
        open_m = minute_at(day, hours[0])
        cur_start = open_m + ((epoch_min(now) - open_m) // tf) * tf
        want_start = cur_start - tf
        if want_start < open_m or not self._complete_from(key, want_start):
            return None
        cs = self._span(key, want_start, want_start + tf)
        return None if cs is None else [cs[0][0], max(c[1] for c in cs), min(c[2] for c in cs), cs[-1][3]]


# ------------------------------------------------------------------------------------------ indicators
def sma(v: list[float], n: int) -> float | None:
    return None if len(v) < n or n < 1 else sum(v[-n:]) / n


def ema(v: list[float], n: int) -> float | None:
    if len(v) < n or n < 1:
        return None
    k = 2.0 / (n + 1)
    e = sum(v[:n]) / n
    for x in v[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(v: list[float], n: int) -> float | None:
    """Wilder's RSI."""
    if len(v) < n + 1 or n < 2:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = v[i] - v[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(v)):
        d = v[i] - v[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)
