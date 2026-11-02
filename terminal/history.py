"""
Candle history for the back-tester.

Kotak only lets you download history for contracts that are still listed, so expired weekly options cannot be fetched
afterwards. Instead this terminal RECORDS its own one-minute candles (index and index options) while it runs, and keeps
them in a separate small database (data/candles.db). The back-tester replays your strategy over those recorded days.

What this means, plainly:
  * history starts the day you first run the terminal in paper or live mode, and grows by one day per trading day;
  * a day is only as complete as the time the terminal was running and the strikes it was watching;
  * candles are built from the ticks this terminal received (like the live strategies); minutes with no tick have no row.

Sample mode records nothing. It can create INVENTED days (source "sample") so the screens can be tried at once.
"""
from __future__ import annotations

import logging
import math
import random
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .clock import IST
from .market.chain import INDEX_UNDERLYINGS

log = logging.getLogger("history")

OPEN_MIN, CLOSE_MIN = 9 * 60 + 15, 15 * 60 + 30          # minutes since midnight, IST
SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (src TEXT NOT NULL, day TEXT NOT NULL, key TEXT NOT NULL, minute INTEGER NOT NULL,
    o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (src, key, minute));
CREATE INDEX IF NOT EXISTS candles_day ON candles (src, day, key);
CREATE TABLE IF NOT EXISTS contracts (src TEXT NOT NULL, day TEXT NOT NULL, key TEXT NOT NULL, symbol TEXT NOT NULL, und TEXT NOT NULL,
    kind TEXT NOT NULL, expiry TEXT, strike REAL, lot INTEGER NOT NULL, spot_key TEXT, PRIMARY KEY (src, day, key));
"""
SPOT_KEYS = {u["spot_key"]: u["name"] for u in INDEX_UNDERLYINGS}


def minute_of_day(epoch_min: int) -> int:
    t = datetime.fromtimestamp(epoch_min * 60, IST)
    return t.hour * 60 + t.minute


def day_of(epoch_min: int) -> str:
    return datetime.fromtimestamp(epoch_min * 60, IST).date().isoformat()


class CandleDB:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def add_candles(self, src: str, rows: list[tuple]) -> None:
        """rows: (key, minute, o, h, l, c, n). A minute written twice (restart mid-minute) is merged, not replaced."""
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO candles (src, day, key, minute, o, h, l, c, n) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(src, key, minute) DO UPDATE SET h=max(h, excluded.h), l=min(l, excluded.l), c=excluded.c, n=n+excluded.n",
                [(src, day_of(r[1]), r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows])
            self._db.commit()

    def add_contracts(self, src: str, day: str, rows: list[dict]) -> None:
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO contracts (src, day, key, symbol, und, kind, expiry, strike, lot, spot_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(src, day, r["key"], r["symbol"], r["und"], r["kind"], r.get("expiry"), r.get("strike"), r["lot"], r.get("spot_key")) for r in rows])
            self._db.commit()

    def clear(self, src: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM candles WHERE src=?", (src,))
            self._db.execute("DELETE FROM contracts WHERE src=?", (src,))
            self._db.commit()

    def contracts(self, src: str, day: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM contracts WHERE src=? AND day=?", (src, day)).fetchall()]

    def day_candles(self, src: str, day: str) -> dict[str, list[tuple]]:
        with self._lock:
            rows = self._db.execute("SELECT key, minute, o, h, l, c FROM candles WHERE src=? AND day=? ORDER BY minute", (src, day)).fetchall()
        out: dict[str, list[tuple]] = {}
        for r in rows:
            out.setdefault(r["key"], []).append((r["minute"], r["o"], r["h"], r["l"], r["c"]))
        return out

    def days(self, src: str) -> list[dict]:
        """One row per recorded day with how complete it is, newest first."""
        with self._lock:
            rows = self._db.execute("SELECT day, key, COUNT(*) n, MIN(minute) a, MAX(minute) b FROM candles WHERE src=? GROUP BY day, key", (src,)).fetchall()
            ctr = self._db.execute("SELECT day, key, und, kind, strike FROM contracts WHERE src=?", (src,)).fetchall()
        meta = {(r["day"], r["key"]): r for r in ctr}
        days: dict[str, dict] = {}
        for r in rows:
            d = days.setdefault(r["day"], {"day": r["day"], "und": {}})
            m = meta.get((r["day"], r["key"]))
            und = m["und"] if m else SPOT_KEYS.get(r["key"])
            if not und:
                continue
            u = d["und"].setdefault(und, {"spot": None, "options": 0, "strikes": set()})
            if (m and m["kind"] == "INDEX") or (not m and r["key"] in SPOT_KEYS):
                u["spot"] = {"candles": r["n"], "first": minute_of_day(r["a"]), "last": minute_of_day(r["b"])}
            elif m and m["kind"] in ("CE", "PE"):
                u["options"] += 1
                u["strikes"].add(m["strike"])
        out = []
        for day, d in sorted(days.items(), reverse=True):
            unds = []
            for name, u in d["und"].items():
                sp = u["spot"]
                full = CLOSE_MIN - OPEN_MIN
                cover = round(min(100.0, sp["candles"] / full * 100), 0) if sp else 0
                unds.append({"und": name, "spot_candles": sp["candles"] if sp else 0, "from": sp["first"] if sp else None, "to": sp["last"] if sp else None,
                             "coverage_pct": cover, "contracts": u["options"], "strikes": len(u["strikes"]),
                             "usable": bool(sp and u["options"] >= 4 and sp["first"] <= OPEN_MIN + 1)})
            out.append({"day": day, "unds": sorted(unds, key=lambda x: x["und"])})
        return out


# --------------------------------------------------------------------------------------------- recorder
class Recorder:
    """Listens to the quote hub and saves one-minute candles for the index and its options. Never raises, never blocks."""

    def __init__(self, db: CandleDB, catalog, src: str = "market", clock: Callable[[], float] = time.time, flush_s: float = 30.0) -> None:
        self.db, self.cat, self.src, self._clock, self._flush_s = db, catalog, src, clock, flush_s
        self._cur: dict[str, list] = {}            # key -> [minute, o, h, l, c, n]
        self._done: list[tuple] = []
        self._accept: dict[str, bool] = {}
        self._meta: dict[str, dict] = {}
        self._retry: dict[str, float] = {}
        self._saved_meta: set[tuple] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.saved = 0
        self.last_flush: float | None = None
        self.last_error = ""

    def _decide(self, key: str) -> bool:
        ok = self._accept.get(key)
        if ok is not None:
            return ok
        if self._retry.get(key, 0) > self._clock():
            return False
        inst = self.cat.get(key)
        if inst is None:
            self._retry[key] = self._clock() + 30     # catalog not ready or key unknown: ask again in 30 seconds, not on every tick
            return False
        if inst.kind == "INDEX" and key in SPOT_KEYS:
            self._meta[key] = {"key": key, "symbol": inst.name, "und": SPOT_KEYS[key], "kind": "INDEX", "lot": 1, "spot_key": key}
            ok = True
        elif inst.kind in ("CE", "PE") and inst.name in {u["name"] for u in INDEX_UNDERLYINGS} and inst.expiry and inst.strike is not None:
            spot = next(u["spot_key"] for u in INDEX_UNDERLYINGS if u["name"] == inst.name)
            self._meta[key] = {"key": key, "symbol": inst.symbol, "und": inst.name, "kind": inst.kind, "expiry": inst.expiry.isoformat(),
                               "strike": inst.strike, "lot": inst.lot, "spot_key": spot}
            ok = True
        else:
            ok = False
        self._accept[key] = ok
        return ok

    def on_quote(self, q) -> None:
        try:
            now = self._clock()
            minute = int(now // 60)
            mod = minute_of_day(minute)
            if mod < OPEN_MIN or mod >= CLOSE_MIN or not self._decide(q.key):
                return
            p = q.ltp
            if p is None or not math.isfinite(p) or p <= 0:
                return
            with self._lock:
                c = self._cur.get(q.key)
                if c is None or c[0] != minute:
                    if c is not None:
                        self._done.append((q.key, c[0], c[1], c[2], c[3], c[4], c[5]))
                    self._cur[q.key] = [minute, p, p, p, p, 1]
                else:
                    c[2], c[3], c[4], c[5] = max(c[2], p), min(c[3], p), p, c[5] + 1
        except Exception:
            self.last_error = "recorder error (see the log)"
            log.exception("recorder")

    def flush(self, final: bool = False) -> int:
        """Write finished minutes (and, on shutdown, the minute in progress). Returns rows written."""
        try:
            now_min = int(self._clock() // 60)
            with self._lock:
                rows, self._done = self._done, []
                for k, c in list(self._cur.items()):
                    if final or c[0] < now_min:
                        rows.append((k, c[0], c[1], c[2], c[3], c[4], c[5]))
                        del self._cur[k]
            if rows:
                self.db.add_candles(self.src, rows)
                for key in {r[0] for r in rows}:
                    m = self._meta.get(key)
                    d = day_of(next(r[1] for r in rows if r[0] == key))
                    if m and (key, d) not in self._saved_meta:
                        self.db.add_contracts(self.src, d, [m])
                        self._saved_meta.add((key, d))
                self.saved += len(rows)
            self.last_flush = self._clock()
            self.last_error = ""
            return len(rows)
        except Exception as e:
            self.last_error = f"could not save candles ({type(e).__name__})"
            log.exception("recorder flush")
            return 0

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self._flush_s):
                self.flush()
        self._thread = threading.Thread(target=loop, name="recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.flush(final=True)

    def status(self) -> dict:
        return {"on": True, "saved": self.saved, "last_flush": self.last_flush, "error": self.last_error,
                "keys": sum(1 for v in self._accept.values() if v)}


# -------------------------------------------------------------------------------------- invented days
def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs(kind: str, s: float, k: float, t_years: float, vol: float) -> float:
    t_years = max(t_years, 1.0 / (365 * 24 * 60))
    sd = vol * math.sqrt(t_years)
    d1 = (math.log(s / k) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    px = s * _cdf(d1) - k * _cdf(d2) if kind == "CE" else k * _cdf(-d2) - s * _cdf(-d1)
    return max(px, 0.05)


def _tick(x: float) -> float:
    return max(0.05, round(round(x / 0.05) * 0.05, 2))


def weekdays_back(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def synth_day(db: CandleDB, day: date, und: str = "NIFTY", src: str = "sample") -> None:
    """One INVENTED trading day: an index random walk (trend, range or choppy) and Black-Scholes style option prices."""
    u = next(x for x in INDEX_UNDERLYINGS if x["name"] == und)
    rng = random.Random(f"{und}|{day.isoformat()}")
    regime = rng.choice(["up", "down", "up", "down", "range", "choppy"])
    vol = rng.uniform(0.11, 0.19)
    base = {"NIFTY": 24250.0, "BANKNIFTY": 51850.0}.get(und, 24000.0) * (1 + rng.uniform(-0.03, 0.03))
    gap = rng.uniform(-0.003, 0.003)
    drift = {"up": 0.014, "down": -0.014, "range": 0.0, "choppy": 0.0}[regime] / 375
    sig = {"range": 0.00030, "choppy": 0.00062}.get(regime, 0.00042)
    step = u["step"]
    expiry = day + timedelta(days=(1 - day.weekday()) % 7)                       # the coming Tuesday (today if Tuesday)
    exp_end = datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST)
    s = base * (1 + gap)
    open_px = s
    atm = round(open_px / step) * step
    strikes = [atm + i * step for i in range(-8, 9)]
    keys = {(k, kd): f"{u['fo']}|{und}{expiry:%y%m%d}{k:g}{kd}" for k in strikes for kd in ("CE", "PE")}
    t0 = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    cand: dict[str, list[tuple]] = {u["spot_key"]: []}
    for kk in keys.values():
        cand[kk] = []
    for m in range(CLOSE_MIN - OPEN_MIN):
        t = t0 + timedelta(minutes=m)
        emin = int(t.timestamp() // 60)
        path = [s]
        busy = 1.0 + 1.2 * math.exp(-m / 25.0)                                       # markets are livelier just after the open
        for _ in range(3):
            path.append(path[-1] * (1 + rng.gauss(drift / 3, sig * busy / 1.7)))
        s = path[-1]
        if regime == "range":
            s += (open_px - s) * 0.004
        cand[u["spot_key"]].append((emin, path[0], max(path), min(path), path[-1]))
        for (k, kd), key in keys.items():
            px = []
            for j, sp in enumerate(path):
                tt = (exp_end - (t + timedelta(seconds=15 * j))).total_seconds() / (365 * 86400)
                px.append(_tick(_bs(kd, sp, k, tt, vol * (1 + rng.uniform(-0.01, 0.01)))))
            cand[key].append((emin, px[0], max(px), min(px), px[-1]))
    rows = [(key, e, o, h, lo, c, 4) for key, cs in cand.items() for (e, o, h, lo, c) in cs]
    db.add_candles(src, [(r[0], r[1], round(r[2], 2), round(r[3], 2), round(r[4], 2), round(r[5], 2), r[6]) for r in rows])
    ctr = [{"key": u["spot_key"], "symbol": und, "und": und, "kind": "INDEX", "lot": 1, "spot_key": u["spot_key"]}]
    lot = {"NIFTY": 75, "BANKNIFTY": 30}.get(und, 50)
    for (k, kd), key in keys.items():
        ctr.append({"key": key, "symbol": f"{und}{expiry:%y%b}".upper() + f"{k:g}{kd}", "und": und, "kind": kd, "expiry": expiry.isoformat(),
                    "strike": float(k), "lot": lot, "spot_key": u["spot_key"]})
    db.add_contracts(src, day.isoformat(), ctr)
