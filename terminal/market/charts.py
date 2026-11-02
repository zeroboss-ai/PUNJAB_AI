"""
Chart candles, built from the live prices the terminal receives.

  * 1-minute candles per instrument, aggregated in memory and saved to the local database every few seconds
  * only instruments you have opened in the chart are recorded (the last MAX_KEYS of them), while the terminal is running
  * 5m / 15m / 1h / 1D are made from the 1-minute candles, aligned to the market open (09:15 shares, 09:00 MCX)
  * there is NO history before the terminal first watched an instrument: Kotak's API offers no history feed
  * drawings (horizontal and trend lines) are saved per instrument

Times sent to the browser are epoch seconds shifted by +05:30, so the chart axis reads in Indian time.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime

from ..clock import IST
from .hub import QuoteHub
from .models import Quote

log = logging.getLogger("charts")

MAX_KEYS = 30
KEEP_DAYS = 45
TIMEFRAMES = {"1": 1, "5": 5, "15": 15, "60": 60, "D": 1440}
MAX_DRAWINGS = 60
IST_SHIFT = 19800
RECENT_KEY = "chart.recent"


def session_open_min(key: str) -> int:
    """Minute of the Indian day the market opens: 09:00 for commodities, 09:15 otherwise."""
    return 9 * 60 if key.startswith("mcx_fo|") else 9 * 60 + 15


def bucket_start(m: int, tf: int, key: str) -> int:
    """Start (epoch minute) of the tf-minute candle that epoch minute m belongs to."""
    if tf >= 1440:
        return (m + 330) // 1440 * 1440 - 330                      # the Indian calendar day
    day0 = (m + 330) // 1440 * 1440 - 330
    o = day0 + session_open_min(key)
    if m < o:
        return o - ((o - m + tf - 1) // tf) * tf
    return o + (m - o) // tf * tf


def aggregate(rows: list[tuple], tf: int, key: str) -> list[list[float]]:
    """rows: (epoch minute, o, h, l, c, v) oldest first -> [[bucket start minute, o, h, l, c, v], ...]"""
    out: list[list[float]] = []
    for m, o, h, l, c, v in rows:
        b = bucket_start(m, tf, key) if tf > 1 else m
        if out and out[-1][0] == b:
            cur = out[-1]
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], l), c, cur[5] + v
        else:
            out.append([b, o, h, l, c, v])
    return out


class ChartService:
    OWNER = "chart"

    def __init__(self, hub: QuoteHub, store, sample: bool = False) -> None:
        self.hub, self.store, self.sample = hub, store, sample
        self._cur: dict[str, list] = {}              # key -> [minute, o, h, l, c, vol, last cumulative volume]
        self._dirty: dict[tuple[str, int], tuple] = {}
        self._rec: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backfilled: set[str] = set()
        hub.add_listener(self.on_quote)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Resume recording the instruments you charted before. Safe to call again."""
        try:
            self.store.prune_candles(time.time() // 60 - KEEP_DAYS * 1440)
        except Exception:
            log.exception("could not prune old candles")
        keys = [k for k in (self.store.get(RECENT_KEY, []) or []) if isinstance(k, str)][:MAX_KEYS]
        self._rec = set(keys)
        if keys:
            self.hub.want(self.OWNER, keys)
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="chart-flush", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.flush()

    def _loop(self) -> None:
        while not self._stop.wait(8.0):
            try:
                self.flush()
            except Exception:
                log.exception("chart flush failed")

    # ------------------------------------------------------------------ sample / real
    def _sk(self, key: str) -> str:
        """Where a key's candles are stored: sample (invented) prices never share rows with real ones."""
        return "sample:" + key if self.sample else key

    def set_sample(self, flag: bool) -> None:
        """Called when the whole terminal changes between the sample world and real prices."""
        self.flush()
        with self._lock:
            self._cur.clear()
            self._dirty.clear()
            self._backfilled.clear()
            self.sample = flag

    def forget_recent(self) -> None:
        """Stop recording the instruments of the old world (their keys mean nothing in the new one)."""
        self.store.set(RECENT_KEY, [])
        with self._lock:
            self._rec = set()
        self.hub.release_owner(self.OWNER)

    # ------------------------------------------------------------------ recording
    def watch(self, key: str) -> None:
        """Called when a chart is opened: record this instrument from now on (the 30 most recent are kept)."""
        recent = [k for k in (self.store.get(RECENT_KEY, []) or []) if isinstance(k, str) and k != key]
        recent = [key] + recent
        dropped = recent[MAX_KEYS:]
        recent = recent[:MAX_KEYS]
        self.store.set(RECENT_KEY, recent)
        with self._lock:
            self._rec = set(recent)
        self.hub.want(self.OWNER, [key])
        if dropped:
            self.hub.release(self.OWNER, dropped)

    def recent(self) -> list[str]:
        return list(self.store.get(RECENT_KEY, []) or [])

    def on_quote(self, q: Quote) -> None:
        if q.key not in self._rec or not q.ltp or q.ltp <= 0:
            return
        m = int((q.ts or time.time()) // 60)
        px = q.ltp
        cum = q.volume or 0
        with self._lock:
            c = self._cur.get(q.key)
            if c is None or c[0] != m:
                if c is not None and c[0] < m:
                    self._dirty[(q.key, c[0])] = (q.key, c[0], c[1], c[2], c[3], c[4], c[5])
                last_cum = c[6] if c is not None else cum
                d = cum - last_cum if cum >= last_cum else 0
                self._cur[q.key] = [m, px, px, px, px, d, cum]
            else:
                if px > c[2]:
                    c[2] = px
                if px < c[3]:
                    c[3] = px
                c[4] = px
                if cum >= c[6]:
                    c[5] += cum - c[6]
                c[6] = cum

    def flush(self) -> None:
        with self._lock:
            rows = list(self._dirty.values())
            self._dirty.clear()
            for k, c in self._cur.items():
                rows.append((k, c[0], c[1], c[2], c[3], c[4], c[5]))
        if rows:
            self.store.put_candles([(self._sk(r[0]),) + tuple(r[1:]) for r in rows])

    # ------------------------------------------------------------------ reading
    def minutes(self, key: str, since_min: int = 0) -> list[tuple]:
        self.flush()
        return self.store.candles(self._sk(key), since_min)

    def candles(self, key: str, tf: str, limit: int = 600) -> dict:
        n = TIMEFRAMES.get(tf)
        if n is None:
            raise ValueError("Unknown timeframe")
        if self.sample:
            self._sample_backfill(key)
        rows = self.minutes(key)
        agg = aggregate(rows, n, key)
        first = rows[0][0] * 60 if rows else None
        agg = agg[-max(10, min(limit, 3000)):]
        return {"tf": tf, "since": None if first is None else first + IST_SHIFT, "minutes_recorded": len(rows),
                "candles": [[int(b * 60) + IST_SHIFT, o, h, l, c, int(v)] for b, o, h, l, c, v in agg]}

    def _sample_backfill(self, key: str) -> None:
        """SAMPLE MODE ONLY: lay down an invented random walk behind the first live price so the chart has something to show."""
        if key in self._backfilled:
            return
        self._backfilled.add(key)
        end = self.hub.ltp(key)
        if end is None:
            for _ in range(20):                       # the feed publishes a first price within a moment of being asked
                time.sleep(0.1)
                end = self.hub.ltp(key)
                if end is not None:
                    break
        if end is None:
            self._backfilled.discard(key)
            return
        sk = self._sk(key)
        have = self.store.candles(sk, 0)
        stop_at = have[0][0] if have else int(time.time() // 60)
        if len(have) > 60:
            return                                   # real recording already exists: leave it alone
        rng = random.Random(key)
        px, rows = float(end), []
        lo_m, hi_m = session_open_min(key), (23 * 60 + 25 if key.startswith("mcx_fo|") else 15 * 60 + 25)
        for m in range(stop_at - 1, stop_at - 1 - 6 * 1440, -1):
            ist = m + 330
            minute_of_day, weekday = ist % 1440, (ist // 1440 + 3) % 7        # epoch day 0 was a Thursday; Monday = 0
            if weekday >= 5 or not (lo_m <= minute_of_day <= hi_m):
                continue
            step = px * 0.0006
            o = px + rng.gauss(0, step)
            hi, lo = max(o, px) + abs(rng.gauss(0, step)), min(o, px) - abs(rng.gauss(0, step))
            rows.append((sk, m, round(o, 2), round(hi, 2), round(max(lo, 0.05), 2), round(px, 2), rng.randint(200, 4000)))
            px = o
            if len(rows) >= 700:
                break
        if rows:
            self.store.put_candles(rows)

    # ------------------------------------------------------------------ drawings
    @staticmethod
    def clean_drawings(items) -> list[dict]:
        if not isinstance(items, list) or len(items) > MAX_DRAWINGS:
            raise ValueError(f"At most {MAX_DRAWINGS} drawings per instrument")
        out = []
        for d in items:
            if not isinstance(d, dict):
                raise ValueError("Bad drawing")
            typ, did = d.get("type"), str(d.get("id", ""))[:24]

            def num(x):
                if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or abs(x) > 1e12:
                    raise ValueError("Bad number in a drawing")
                return float(x)
            if typ == "h":
                out.append({"id": did, "type": "h", "price": num(d.get("price"))})
            elif typ == "t":
                out.append({"id": did, "type": "t", "t1": num(d.get("t1")), "p1": num(d.get("p1")), "t2": num(d.get("t2")), "p2": num(d.get("p2"))})
            else:
                raise ValueError("Unknown drawing type")
        return out

    def drawings(self, key: str) -> list[dict]:
        return self.store.get(f"chart.draw.{key}", []) or []

    def set_drawings(self, key: str, items) -> list[dict]:
        clean = self.clean_drawings(items)
        self.store.set(f"chart.draw.{key}", clean)
        return clean


def ist_text(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, IST).strftime("%d %b %H:%M")
