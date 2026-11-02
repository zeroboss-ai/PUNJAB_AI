"""
Trade journal: keeps every fill, pairs buys with sells into round trips, and works out the statistics.

  * fills are copied from the broker's trade book (every ~20 s and whenever the journal is opened) into SQLite, so
    history survives restarts and Kotak's one-day trade book;
  * round trips pair opening and closing fills first-in-first-out per instrument and product, across days, long or short;
  * every figure is BEFORE brokerage, STT, exchange charges, GST and stamp duty - those are not modelled;
  * tags come from the order tag recorded when the terminal placed the order ("manual", a strategy name, "squareoff").
    Orders placed outside this terminal (e.g. in the Kotak app) show up as "other".
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta

from .clock import IST, now_ist

log = logging.getLogger("journal")

TIME_FORMATS = ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S %p", "%H:%M:%S")


def parse_time(s: str, fallback: float | None = None) -> float:
    s = (s or "").strip()
    for f in TIME_FORMATS:
        try:
            d = datetime.strptime(s, f)
        except ValueError:
            continue
        if f == "%H:%M:%S":
            d = datetime.combine(now_ist().date(), d.time())
        return d.replace(tzinfo=IST).timestamp()
    return fallback if fallback is not None else time.time()


def _name_of(label: str) -> str:
    return (label or "").split(" ")[0] or "?"


class Journal:
    def __init__(self, store, trading, mode: str) -> None:
        self.store, self.trading, self.mode = store, trading, mode
        self.ns: str | None = None            # which Kotak client the fills belong to (None = the plain mode name, as before accounts existed)
        self._last_sync = 0.0

    @property
    def space(self) -> str:
        return self.ns or self.mode

    # ------------------------------------------------------------------------------ capture
    def note_order(self, oid: str, inst, ticket, qty: int) -> None:
        self.store.add_order_meta(oid, self.space, now_ist().date().isoformat(), inst.key, inst.symbol, inst.label(), inst.lot,
                                  (ticket.tag or "manual")[:30])

    def sync(self) -> int:
        """Copy new fills from the broker's trade book. Returns how many were new. Never raises."""
        try:
            gw = self.trading.gateway()
            rows = gw.trades()
        except Exception as e:
            log.debug("journal sync skipped: %s", e)
            return 0
        if rows is None:
            return 0
        today = now_ist().date().isoformat()
        meta = self.store.order_meta(self.space, today)
        out = []
        for r in sorted(rows, key=lambda r: (parse_time(r.time), len(r.order_id), r.order_id, r.trade_id)):   # same-second fills keep order
            m = meta.get(r.order_id)
            key = r.key or (m["key"] if m else "")
            ts = parse_time(r.time)
            day = datetime.fromtimestamp(ts, IST).date().isoformat()
            uid = hashlib.sha1(f"{self.space}|{day}|{r.order_id}|{r.trade_id}|{r.qty}|{r.price}|{r.time}".encode()).hexdigest()[:16]
            label = (m["label"] if m else r.label) or r.symbol
            out.append({"uid": uid, "mode": self.space, "day": day, "ts": ts, "order_id": r.order_id, "key": key or r.symbol,
                        "symbol": r.symbol, "label": label, "side": r.side, "qty": int(r.qty), "price": float(r.price),
                        "product": r.product, "lot": int(m["lot"]) if m else 1, "tag": m["tag"] if m else "other",
                        "name": _name_of(label)})
        self._last_sync = time.time()
        return self.store.add_fills(out) if out else 0

    # ------------------------------------------------------------------------------ analysis
    def trips(self) -> tuple[list[dict], list[dict]]:
        """(closed round trips oldest-first, open lots still held)."""
        books: dict[tuple, deque] = defaultdict(deque)             # (key, product) -> deque of [fill, remaining]
        agg: dict[tuple, dict] = {}
        for f in self.store.fills(self.space):
            q = f["qty"]
            if q <= 0 or f["side"] not in ("B", "S"):
                continue
            book = books[(f["key"], f["product"])]
            while q > 0 and book and book[0][0]["side"] != f["side"]:
                op, rem = book[0]
                m = min(q, rem)
                k = (op["order_id"], f["order_id"], f["key"], f["product"])
                a = agg.get(k)
                if a is None:
                    a = agg[k] = {"open": op, "close": f, "qty": 0, "ev": 0.0, "xv": 0.0}
                a["qty"] += m
                a["ev"] += m * op["price"]
                a["xv"] += m * f["price"]
                a["close"] = f
                book[0][1] -= m
                q -= m
                if book[0][1] == 0:
                    book.popleft()
            if q > 0:
                book.append([f, q])
        trips = []
        for a in agg.values():
            op, cl, qty = a["open"], a["close"], a["qty"]
            long = op["side"] == "B"
            e, x = a["ev"] / qty, a["xv"] / qty
            pnl = (x - e) * qty * (1 if long else -1)
            tid = hashlib.sha1(f"{op['uid']}>{cl['uid']}".encode()).hexdigest()[:12]
            trips.append({"id": tid, "key": op["key"], "label": op["label"], "name": op["name"], "product": op["product"],
                          "dir": "Long" if long else "Short", "qty": qty, "lots": qty // op["lot"] if op["lot"] > 1 else qty,
                          "entry": round(e, 2), "exit": round(x, 2), "entry_ts": op["ts"], "exit_ts": cl["ts"], "day": cl["day"],
                          "pnl": round(pnl, 2), "hold_s": int(max(0, cl["ts"] - op["ts"])), "tag": op["tag"]})
        trips.sort(key=lambda t: (t["exit_ts"], t["id"]))
        held = []
        for (key, product), book in books.items():
            for f, rem in book:
                held.append({"key": key, "label": f["label"], "product": product, "side": "Long" if f["side"] == "B" else "Short",
                             "qty": rem, "price": f["price"], "day": f["day"], "tag": f["tag"]})
        return trips, held

    def report(self, frm: str | None = None, to: str | None = None, tag: str | None = None) -> dict:
        self.sync()
        trips, held = self.trips()
        notes = self.store.notes()
        for t in trips:
            n = notes.get(f"trip:{t['id']}")
            t["note"], t["tags"] = (n["body"], n["tags"]) if n else ("", [])
        all_tags = sorted({t["tag"] for t in trips})
        sel = [t for t in trips if (not frm or t["day"] >= frm) and (not to or t["day"] <= to) and (not tag or t["tag"] == tag)]
        day_notes = {k[4:]: v for k, v in notes.items() if k.startswith("day:")}
        return {"mode": self.mode, "trips": list(reversed(sel)), "open": held, "tags": all_tags, "stats": stats(sel),
                "by_day": group(sel, lambda t: t["day"]), "by_tag": group(sel, lambda t: t["tag"]),
                "by_name": group(sel, lambda t: t["name"]), "by_dir": group(sel, lambda t: t["dir"]),
                "by_hour": group(sel, lambda t: datetime.fromtimestamp(t["entry_ts"], IST).strftime("%H:00")),
                "equity": equity(sel), "day_notes": day_notes,
                "demo": any(f["order_id"].startswith("DEMO") for f in self.store.fills(self.space)),
                "disclaimer": "Before brokerage, taxes and charges (not modelled). Past results do not predict future results."}

    # ------------------------------------------------------------------------------ demo data
    def seed_demo(self, days: int = 30) -> int:
        """Invented trades so the journal can be explored in sample mode. Clearly marked DEMO; removable."""
        if self.mode != "sample":
            raise ValueError("Demo trades are only for sample mode")
        rng = random.Random(2610)
        rows = []
        d0 = now_ist().date()
        n = 0
        for back in range(days, 0, -1):
            d = d0 - timedelta(days=back)
            if d.weekday() >= 5:
                continue
            for j in range(rng.choice([1, 1, 2])):
                n += 1
                strat = rng.choice(["ORB_sell_ATM", "ORB_sell_ATM", "manual", "Short_straddle"])
                side = "S" if strat != "manual" else rng.choice("BS")
                strike = 24000 + 50 * rng.randint(0, 10)
                kind = rng.choice(["CE", "PE"])
                entry = round(rng.uniform(60, 140) / 0.05) * 0.05
                win = rng.random() < 0.46
                move = rng.uniform(0.10, 0.45) * entry
                exit_ = entry - move if (side == "S") == win else entry + move * (0.8 if side == "S" else 1.0)
                exit_ = max(0.05, round(exit_ / 0.05) * 0.05)
                t0 = datetime(d.year, d.month, d.day, 9, 19 + rng.randint(0, 40), rng.randint(0, 59), tzinfo=IST).timestamp()
                t1 = t0 + rng.randint(5, 300) * 60
                key = f"nse_fo|demo{strike}{kind}"
                label = f"NIFTY {(d + timedelta(days=3)):%d %b} {strike} {kind}"
                for k, (sd, px, ts) in enumerate([(side, entry, t0), ("B" if side == "S" else "S", exit_, t1)]):
                    oid = f"DEMO{n:04d}{k}"
                    rows.append({"uid": hashlib.sha1(oid.encode()).hexdigest()[:16], "mode": "sample", "day": d.isoformat(), "ts": ts,
                                 "order_id": oid, "key": key, "symbol": label.replace(" ", ""), "label": label, "side": sd, "qty": 75,
                                 "price": float(round(px, 2)), "product": "MIS", "lot": 75, "tag": strat, "name": "NIFTY"})
        return self.store.add_fills(rows)

    def clear_demo(self) -> None:
        self.store.delete_fills(self.mode, "DEMO")


# ------------------------------------------------------------------------------------------- stats
def stats(trips: list[dict]) -> dict:
    n = len(trips)
    if not n:
        return {"n": 0, "net": 0.0, "wins": 0, "losses": 0, "win_rate": None, "avg_win": None, "avg_loss": None, "profit_factor": None,
                "expectancy": None, "best": None, "worst": None, "max_drawdown": 0.0, "days": 0, "avg_hold_min": None, "max_losing_streak": 0}
    wins = [t["pnl"] for t in trips if t["pnl"] > 0]
    losses = [t["pnl"] for t in trips if t["pnl"] < 0]
    gp, gl = sum(wins), -sum(losses)
    peak = cum = dd = 0.0
    streak = best_streak = 0
    for t in trips:
        cum += t["pnl"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if t["pnl"] < 0 else 0
        best_streak = max(best_streak, streak)
    return {"n": n, "net": round(sum(t["pnl"] for t in trips), 2), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / n * 100, 1), "avg_win": round(gp / len(wins), 2) if wins else None,
            "avg_loss": round(-gl / len(losses), 2) if losses else None,
            "profit_factor": round(gp / gl, 2) if gl > 0 else None, "expectancy": round(sum(t["pnl"] for t in trips) / n, 2),
            "best": max(t["pnl"] for t in trips), "worst": min(t["pnl"] for t in trips), "max_drawdown": round(dd, 2),
            "days": len({t["day"] for t in trips}), "avg_hold_min": round(sum(t["hold_s"] for t in trips) / n / 60, 1),
            "max_losing_streak": best_streak}


def group(trips: list[dict], keyfn) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for t in trips:
        g[keyfn(t)].append(t)
    out = []
    for k, v in g.items():
        w = sum(1 for t in v if t["pnl"] > 0)
        out.append({"key": k, "n": len(v), "pnl": round(sum(t["pnl"] for t in v), 2), "win_rate": round(w / len(v) * 100, 1)})
    return sorted(out, key=lambda r: r["key"])


def equity(trips: list[dict]) -> list[dict]:
    cum, out = 0.0, []
    for t in trips:
        cum += t["pnl"]
        out.append({"ts": t["exit_ts"], "cum": round(cum, 2), "pnl": t["pnl"], "day": t["day"]})
    return out
