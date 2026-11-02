"""
Paper order router: simulated fills against the live (or replayed) tick stream.

Fills are deliberately pessimistic for a seller:
  * a SELL limit fills at its limit price (not at a better price)
  * a BUY limit fills at its limit price
  * a stop-limit BUY triggers when LTP >= trigger and then fills at the tick price,
    but only if that price is still <= its limit (so a gap through the limit is NOT filled)
No brokerage, taxes or exchange charges are modelled.
"""
from __future__ import annotations

import itertools

from ..models import TERMINAL_STATES, OrderError, OrderRequest, OrderStatus, Tick


class PaperOrders:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._orders: dict[str, dict] = {}
        self._last: dict[str, float] = {}
        self._net: dict[str, int] = {}

    # fed by the engine on every tick
    def on_tick(self, t: Tick) -> None:
        self._last[t.symbol] = t.price
        for o in self._orders.values():
            if o["state"] in TERMINAL_STATES or o["req"].contract.symbol != t.symbol:
                continue
            self._try_fill(o, t.price)

    def place(self, req: OrderRequest) -> str:
        if req.qty <= 0:
            raise OrderError("quantity must be positive")
        oid = f"P{next(self._ids):06d}"
        o = {"req": req, "state": "OPEN", "filled": 0, "avg": 0.0, "active": req.order_type != "SL"}
        if req.order_type == "SL":
            o["state"] = "TRIGGER_PENDING"
        self._orders[oid] = o
        last = self._last.get(req.contract.symbol)
        if req.order_type == "MKT":
            if last is None:
                o["state"] = "REJECTED"
            else:
                self._fill(o, last)
        elif last is not None:
            self._try_fill(o, last)
        return oid

    def cancel(self, order_id: str) -> None:
        o = self._orders[order_id]
        if o["state"] not in TERMINAL_STATES:
            o["state"] = "CANCELLED"

    def status(self, order_id: str) -> OrderStatus:
        o = self._orders[order_id]
        return OrderStatus(order_id, o["state"], o["filled"], o["avg"])

    def positions(self) -> dict[str, int] | None:
        return dict(self._net)

    # ---------------------------------------------------------------- internals
    def _try_fill(self, o: dict, price: float) -> None:
        req = o["req"]
        if req.order_type == "SL" and not o["active"]:
            if price >= req.trigger:                     # buy-stop triggers
                o["active"], o["state"] = True, "OPEN"
            else:
                return
        if req.order_type == "MKT":
            self._fill(o, price)
        elif req.side == "S" and price >= req.price:
            self._fill(o, req.price)
        elif req.side == "B" and price <= req.price:
            self._fill(o, price if req.order_type == "SL" else req.price)

    def _fill(self, o: dict, price: float) -> None:
        req = o["req"]
        o.update(state="COMPLETE", filled=req.qty, avg=round(price, 2))
        delta = -req.qty if req.side == "S" else req.qty
        self._net[req.contract.symbol] = self._net.get(req.contract.symbol, 0) + delta
