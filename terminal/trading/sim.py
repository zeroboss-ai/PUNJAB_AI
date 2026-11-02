"""
SimBroker: a paper-trading broker for the manual terminal (sample mode and paper mode).

Fills are deliberately a little pessimistic and follow exchange-like rules:
  * MKT fills at the last price.   * a limit order that is marketable when placed fills at the last price,
  * a resting limit order fills at its OWN price once the market trades through it,
  * SL / SL-M are buy-stops (trigger at or above) or sell-stops (trigger at or below),
  * IOC orders that cannot fill immediately are cancelled.
No brokerage, taxes, margin or queue priority are modelled - so real results will be slightly worse.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
from pathlib import Path

from ..clock import now_ist
from ..market.catalog import Catalog
from ..market.hub import QuoteHub
from ..market.models import Instrument, Quote
from ..models import OrderError
from .models import Funds, OrderRow, OrderTicket, PositionRow, TradeRow

log = logging.getLogger("simbroker")


class SimBroker:
    simulated = True

    def __init__(self, hub: QuoteHub, catalog: Catalog, capital: float = 1_000_000.0, data_dir: str | None = None) -> None:
        self.hub, self.cat, self.capital = hub, catalog, capital
        self._ids = itertools.count(1)
        self._orders: dict[str, dict] = {}
        self._trades: list[TradeRow] = []
        self._pos: dict[tuple[str, str], dict] = {}
        self._lock = threading.RLock()
        self._dir = Path(data_dir) if data_dir else None
        hub.add_listener(self._on_quote)

    # ---------------------------------------------------------------- Gateway API
    def place(self, inst: Instrument, t: OrderTicket, qty: int) -> str:
        if qty <= 0:
            raise OrderError("Quantity must be positive")
        if t.order_type in ("L", "SL") and t.price <= 0:
            raise OrderError("A limit price is required")
        if t.order_type in ("SL", "SL-M") and t.trigger <= 0:
            raise OrderError("A trigger price is required for stop orders")
        for label, v in (("price", t.price), ("trigger", t.trigger)):
            if v and abs(round(v / inst.tick) * inst.tick - v) > 1e-6:
                raise OrderError(f"The {label} must be a multiple of the tick size {inst.tick:g}")
        ltp = self.hub.ltp(inst.key)
        if ltp is None and t.order_type != "L":
            raise OrderError("No live price for this instrument yet - cannot simulate a fill")
        with self._lock:
            oid = f"SIM{now_ist():%y%m%d}{next(self._ids):05d}"
            o = {"id": oid, "inst": inst, "t": t, "qty": qty, "filled": 0, "avg": 0.0, "status": "OPEN", "reason": "",
                 "time": now_ist().strftime("%d-%b-%Y %H:%M:%S"), "active": t.order_type not in ("SL", "SL-M")}
            if not o["active"]:
                o["status"] = "TRIGGER_PENDING"
            self._orders[oid] = o
            if ltp is not None:
                self._evaluate(o, ltp, on_place=True)
            if o["status"] == "OPEN" and t.validity == "IOC":
                o["status"], o["reason"] = "CANCELLED", "IOC: not immediately executable"
        self.hub.want("sim", [inst.key])
        return oid

    def modify(self, order_id: str, *, order_type: str, qty: int, price: float, trigger: float, validity: str) -> None:
        with self._lock:
            o = self._orders.get(order_id)
            if not o or o["status"] not in ("OPEN", "TRIGGER_PENDING"):
                raise OrderError("Only open orders can be modified")
            if qty < o["filled"] or qty <= 0:
                raise OrderError("Quantity cannot be below the quantity already filled")
            t = o["t"]
            t.order_type, t.price, t.trigger, t.validity = order_type, price, trigger, validity
            o["qty"] = qty
            o["active"] = order_type not in ("SL", "SL-M")
            o["status"] = "OPEN" if o["active"] else "TRIGGER_PENDING"
            ltp = self.hub.ltp(o["inst"].key)
            if ltp is not None:
                self._evaluate(o, ltp, on_place=True)

    def cancel(self, order_id: str) -> None:
        with self._lock:
            o = self._orders.get(order_id)
            if not o:
                raise OrderError("No such order")
            if o["status"] in ("OPEN", "TRIGGER_PENDING"):
                o["status"] = "CANCELLED"
            else:
                raise OrderError(f"Order is already {o['status'].lower()}")

    def orders(self) -> list[OrderRow]:
        with self._lock:
            rows = [self._row(o) for o in self._orders.values()]
        return sorted(rows, key=lambda r: r.id, reverse=True)

    def trades(self) -> list[TradeRow]:
        with self._lock:
            return list(reversed(self._trades))

    def positions(self) -> list[PositionRow]:
        with self._lock:
            out = []
            for (key, product), p in self._pos.items():
                inst = p["inst"]
                ltp = self.hub.ltp(key)
                unreal = 0.0
                if p["net"] != 0 and ltp is not None:
                    unreal = (ltp - p["avg"]) * p["net"]
                out.append(PositionRow(
                    key=key, symbol=inst.symbol, label=inst.label(), segment=inst.segment, product=product, lot=inst.lot,
                    buy_qty=p["bq"], sell_qty=p["sq"], net_qty=p["net"],
                    buy_avg=round(p["ba"] / p["bq"], 2) if p["bq"] else 0.0, sell_avg=round(p["sa"] / p["sq"], 2) if p["sq"] else 0.0,
                    avg_price=round(p["avg"], 2) if p["net"] else 0.0, ltp=ltp, realised=round(p["real"], 2),
                    unrealised=round(unreal, 2), expiry=inst.expiry.isoformat() if inst.expiry else None))
            return out

    def funds(self) -> Funds:
        rows = self.positions()
        real, unreal = sum(r.realised for r in rows), sum(r.unrealised for r in rows)
        return Funds(available=round(self.capital + real + unreal, 2), used=None, realised=round(real, 2),
                     unrealised=round(unreal, 2), simulated=True,
                     note="Simulated account. Margin is not modelled - real orders need far more margin for short options.")

    # ----------------------------------------------------------------------------- internals
    def _row(self, o: dict) -> OrderRow:
        i, t = o["inst"], o["t"]
        return OrderRow(id=o["id"], key=i.key, symbol=i.symbol, label=i.label(), side=t.side, qty=o["qty"], filled=o["filled"],
                        lot=i.lot, product=t.product, order_type=t.order_type, price=t.price, trigger=t.trigger,
                        avg_price=o["avg"], status=o["status"], reason=o["reason"], time=o["time"], validity=t.validity, tag=t.tag)

    def _on_quote(self, q: Quote) -> None:
        with self._lock:
            for o in self._orders.values():
                if o["status"] in ("OPEN", "TRIGGER_PENDING") and o["inst"].key == q.key:
                    self._evaluate(o, q.ltp, on_place=False)

    def _evaluate(self, o: dict, ltp: float, on_place: bool) -> None:
        t, buy = o["t"], o["t"].side == "B"
        if not o["active"]:                                           # stop order waiting for its trigger
            if (buy and ltp >= t.trigger) or (not buy and ltp <= t.trigger):
                o["active"], o["status"] = True, "OPEN"
                if t.order_type == "SL-M":
                    return self._fill(o, ltp)
            else:
                return
        if t.order_type in ("MKT", "SL-M"):
            return self._fill(o, ltp)
        if buy and ltp <= t.price:
            self._fill(o, ltp if on_place else t.price)
        elif not buy and ltp >= t.price:
            self._fill(o, ltp if on_place else t.price)

    def _fill(self, o: dict, price: float) -> None:
        qty, t, inst = o["qty"] - o["filled"], o["t"], o["inst"]
        if qty <= 0:
            return
        price = round(price, 2)
        o.update(filled=o["qty"], avg=price, status="COMPLETE")
        p = self._pos.setdefault((inst.key, t.product), {"inst": inst, "net": 0, "avg": 0.0, "real": 0.0,
                                                          "bq": 0, "sq": 0, "ba": 0.0, "sa": 0.0})
        signed = qty if t.side == "B" else -qty
        if t.side == "B":
            p["bq"] += qty
            p["ba"] += qty * price
        else:
            p["sq"] += qty
            p["sa"] += qty * price
        net = p["net"]
        if net == 0 or (net > 0) == (signed > 0):                      # opening / adding
            p["avg"] = (p["avg"] * abs(net) + price * qty) / (abs(net) + qty)
            p["net"] = net + signed
        else:                                                         # reducing / flipping
            closing = min(qty, abs(net))
            p["real"] += (price - p["avg"]) * closing * (1 if net > 0 else -1)
            p["net"] = net + signed
            if p["net"] != 0 and (p["net"] > 0) == (signed > 0):       # flipped through zero
                p["avg"] = price
        self._trades.append(TradeRow(order_id=o["id"], key=inst.key, symbol=inst.symbol, label=inst.label(), side=t.side,
                                     qty=qty, price=price, product=t.product, time=now_ist().strftime("%d-%b-%Y %H:%M:%S"),
                                     trade_id=f"T{len(self._trades) + 1:05d}"))
        self._log(self._trades[-1])

    def _log(self, tr: TradeRow) -> None:
        if not self._dir:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._dir / f"manual-trades-sim-{now_ist():%Y%m%d}.jsonl", "a") as f:
                f.write(json.dumps(tr.to_dict()) + "\n")
        except Exception:
            log.exception("could not write the simulated trade log")
