"""
TradingService: every manual action goes through here (UI buttons, square-off, panic).

It resolves the instrument and lot size from the catalog (never from the browser), validates the ticket,
applies the risk checks, and only then calls the gateway (live Kotak or the simulator).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable

from ..config import Config
from ..market.catalog import Catalog
from ..market.hub import QuoteHub
from ..market.models import Instrument
from ..models import OrderError, OrderUnknown
from .models import OrderTicket, PositionRow

log = logging.getLogger("trading")

CASH_PRODUCTS = {"MIS", "CNC"}
DERIV_PRODUCTS = {"MIS", "NRML"}
ORDER_TYPES = {"L", "MKT", "SL", "SL-M"}


@dataclass
class ManualLimits:
    max_lots_per_order: int = 20
    max_order_value: float = 2_500_000.0
    block_after_loss: bool = True
    squareoff_buffer_pct: float = 3.0       # square-off sends a limit this far through the last price


class TradingService:
    def __init__(self, cfg: Config, hub: QuoteHub, catalog: Catalog, gateway_provider: Callable[..., object | None],
                 limits: ManualLimits | None = None, on_panic: Callable[[], None] | None = None,
                 all_gateways_provider: Callable[[], dict[str, object]] | None = None,
                 account_labels_provider: Callable[[], dict[str, str]] | None = None) -> None:
        self.cfg, self.hub, self.cat = cfg, hub, catalog
        self._gw = gateway_provider
        self._all_gws = all_gateways_provider
        self._acct_labels = account_labels_provider
        self.limits = limits or ManualLimits()
        self.on_panic = on_panic
        self.on_order: Callable[..., None] | None = None   # journal hook: (order id, instrument, ticket, units, [account_id])

    # ------------------------------------------------------------------ helpers
    def gateway(self, account_id: str | None = None):
        try:
            gw = self._gw(account_id)
        except TypeError:
            gw = self._gw()
        if gw is None:
            raise OrderError("Not connected to the broker - log in first")
        return gw

    def all_gateways(self) -> dict[str, object]:
        if self._all_gws:
            try:
                gws = self._all_gws()
                if gws:
                    return gws
            except Exception:
                pass
        try:
            gw = self.gateway()
            return {"default": gw}
        except Exception:
            return {}

    def account_label(self, account_id: str | None) -> str:
        if not account_id:
            return ""
        if self._acct_labels:
            try:
                labels = self._acct_labels()
                if account_id in labels:
                    return labels[account_id]
            except Exception:
                pass
        return ""

    def _instrument(self, key: str) -> Instrument:
        inst = self.cat.get(key)
        if inst is None:
            raise OrderError("Unknown instrument - search for it again")
        if inst.kind == "INDEX":
            raise OrderError("Indices cannot be traded - pick a future, option or stock")
        return inst

    def _qty(self, inst: Instrument, t: OrderTicket) -> int:
        if inst.is_derivative:
            if t.lots < 1:
                raise OrderError("Enter at least 1 lot")
            if t.lots > self.limits.max_lots_per_order:
                raise OrderError(f"Max {self.limits.max_lots_per_order} lots per order (change manual.max_lots_per_order in config.yaml)")
            return t.lots * inst.lot
        if t.qty < 1:
            raise OrderError("Enter a quantity of at least 1")
        return t.qty

    def _follow_positions(self, open_keys: set[str]) -> None:
        """Keep live prices flowing for every open position, even when no browser tab is subscribed to it."""
        prev = getattr(self, "_pos_keys", set())
        new, gone = open_keys - prev, prev - open_keys
        if new:
            self.hub.want("positions", sorted(new))
        if gone:
            self.hub.release("positions", sorted(gone))
        self._pos_keys = set(open_keys)

    def day_pnl(self, account_id: str | None = None) -> float | None:
        rows = self.gateway(account_id).positions()
        return None if rows is None else sum(r.pnl for r in rows)

    def _reduces_exposure(self, inst: Instrument, side: str, qty: int, account_id: str | None = None) -> bool:
        rows = self.gateway(account_id).positions()
        if rows is None:
            return False
        net = sum(r.net_qty for r in rows if r.key == inst.key)
        return (side == "B" and net < 0 and qty <= -net) or (side == "S" and net > 0 and qty <= net)

    # ------------------------------------------------------------------- place
    def validate(self, t: OrderTicket) -> tuple[Instrument, int, float]:
        inst = self._instrument(t.key)
        if t.side not in ("B", "S"):
            raise OrderError("Side must be Buy or Sell")
        if t.order_type not in ORDER_TYPES:
            raise OrderError("Unknown order type")
        if t.validity not in ("DAY", "IOC"):
            raise OrderError("Validity must be DAY or IOC")
        if inst.is_commodity and t.validity != "DAY":
            raise OrderError("Commodity (MCX) orders accept Day validity only")
        allowed = DERIV_PRODUCTS if inst.is_derivative else CASH_PRODUCTS
        if t.product not in allowed:
            raise OrderError(f"Product must be one of {', '.join(sorted(allowed))} for {inst.kind}")
        for name, v in (("price", t.price), ("trigger", t.trigger)):
            if not math.isfinite(v) or v < 0:
                raise OrderError(f"Invalid {name}")
        qty = self._qty(inst, t)
        ref = t.price if t.order_type in ("L", "SL") and t.price > 0 else (self.hub.ltp(inst.key) or 0.0)
        value = qty * ref
        if value > self.limits.max_order_value:
            raise OrderError(f"Order value ₹{value:,.0f} is above the per-order limit ₹{self.limits.max_order_value:,.0f} "
                             f"(manual.max_order_value in config.yaml)")
        return inst, qty, value

    def place(self, t: OrderTicket, account_id: str | None = None) -> dict:
        inst, qty, value = self.validate(t)
        gw = self.gateway(account_id)
        if self.limits.block_after_loss and self.cfg.risk.max_daily_loss > 0 and not self._reduces_exposure(inst, t.side, qty, account_id=account_id):
            pnl = self.day_pnl(account_id=account_id)
            if pnl is None:
                raise OrderError("Cannot read your positions to check the daily loss limit, so new exposure is blocked. "
                                 "Closing orders still work.")
            if pnl <= -abs(self.cfg.risk.max_daily_loss):
                raise OrderError(f"Daily loss limit reached (day P&L ₹{pnl:,.0f}). New positions are blocked; "
                                 f"closing orders are still allowed.")
        try:
            oid = gw.place(inst, t, qty)
        except OrderUnknown:
            raise OrderUnknown("Kotak did not confirm the order. It may or may not have been placed - check the Orders page "
                               "before trying again.")
        lbl = self.account_label(account_id)
        log.info("order %s (%s, account %s): %s %s x%s %s", oid, t.tag, lbl or account_id or "default", t.side, inst.symbol, qty, t.order_type)
        self._note_order(oid, inst, t, qty, account_id=account_id)
        return {"order_id": oid, "qty": qty, "value": round(value, 2), "account_id": account_id or "", "account_label": lbl}

    def _note_order(self, oid: str, inst: Instrument, t: OrderTicket, qty: int, account_id: str | None = None) -> None:
        if self.on_order:
            try:
                try:
                    self.on_order(oid, inst, t, qty, account_id)
                except TypeError:
                    self.on_order(oid, inst, t, qty)
            except Exception:
                log.exception("order hook failed")

    def modify(self, order_id: str, *, order_type: str, lots: int, qty: int, price: float, trigger: float, validity: str) -> None:
        gws = self.all_gateways()
        target_gw = None
        for aid, gw in gws.items():
            row = next((o for o in (gw.orders() or []) if o.id == order_id), None)
            if row is not None:
                target_gw = gw
                break
        if target_gw is None:
            target_gw = self.gateway()
            row = next((o for o in (target_gw.orders() or []) if o.id == order_id), None)
        if row is None:
            raise OrderError("Order not found")
        if not row.is_open:
            raise OrderError("Only open orders can be modified")
        if order_type not in ORDER_TYPES or validity not in ("DAY", "IOC"):
            raise OrderError("Invalid order type or validity")
        if validity != "DAY" and getattr(row, "key", "").startswith("mcx_fo|"):
            raise OrderError("Commodity (MCX) orders accept Day validity only")
        units = lots * row.lot if row.lot > 1 else qty
        if units < 1:
            raise OrderError("Enter a valid quantity")
        try:
            target_gw.modify(order_id, order_type=order_type, qty=units, price=price, trigger=trigger, validity=validity)
        except OrderUnknown:
            raise OrderUnknown("Kotak did not confirm the change - check the Orders page")

    def cancel(self, order_id: str) -> None:
        gws = self.all_gateways()
        for aid, gw in gws.items():
            row = next((o for o in (gw.orders() or []) if o.id == order_id), None)
            if row is not None:
                try:
                    gw.cancel(order_id)
                    return
                except OrderUnknown:
                    raise OrderUnknown("Kotak did not confirm the cancel - check the Orders page")
        try:
            self.gateway().cancel(order_id)
        except OrderUnknown:
            raise OrderUnknown("Kotak did not confirm the cancel - check the Orders page")

    # --------------------------------------------------------------- square off
    def _closing_ticket(self, p: PositionRow) -> OrderTicket | None:
        if p.net_qty == 0:
            return None
        inst = self.cat.get(p.key)
        tick = inst.tick if inst else 0.05
        ltp = self.hub.ltp(p.key) or p.ltp
        if not ltp:
            return None
        side = "S" if p.net_qty > 0 else "B"
        buf = max(2 * tick, ltp * self.limits.squareoff_buffer_pct / 100.0)
        px = ltp - buf if side == "S" else ltp + buf
        px = max(tick, round(round(px / tick) * tick, 2))
        lots = abs(p.net_qty) // p.lot if p.lot > 1 else 0
        return OrderTicket(key=p.key, side=side, lots=lots, qty=abs(p.net_qty), product=p.product, order_type="L",
                           price=px, tag="squareoff")

    def squareoff_preview(self, keys: list[str] | None = None, account_id: str | None = None) -> list[dict]:
        gws = {account_id: self.gateway(account_id)} if account_id else self.all_gateways()
        out = []
        for aid, gw in gws.items():
            rows = gw.positions()
            if rows is None:
                continue
            for p in rows:
                if p.net_qty == 0 or (keys is not None and p.key not in keys):
                    continue
                t = self._closing_ticket(p)
                out.append({"key": p.key, "label": p.label, "side": t.side if t else None, "qty": abs(p.net_qty),
                            "price": t.price if t else None, "product": p.product,
                            "account_id": aid, "account_label": self.account_label(aid),
                            "error": None if t else "No live price - close it from the broker app"})
        return out

    def squareoff(self, keys: list[str] | None = None, account_id: str | None = None) -> list[dict]:
        """Closes positions with marketable limit orders. Returns one result per position."""
        gws = {account_id: self.gateway(account_id)} if account_id else self.all_gateways()
        results = []
        for aid, gw in gws.items():
            rows = gw.positions()
            if rows is None:
                continue
            for p in rows:
                if p.net_qty == 0 or (keys is not None and p.key not in keys):
                    continue
                t = self._closing_ticket(p)
                if t is None:
                    results.append({"key": p.key, "ok": False, "error": "No live price", "account_id": aid})
                    continue
                try:
                    inst = self.cat.get(p.key) or Instrument(p.key, p.segment, p.key.split("|")[-1], p.symbol, p.symbol, "EQ",
                                                             lot=p.lot)
                    oid = gw.place(inst, t, abs(p.net_qty))
                    self._note_order(oid, inst, t, abs(p.net_qty), account_id=aid)
                    results.append({"key": p.key, "ok": True, "order_id": oid, "account_id": aid})
                except Exception as e:
                    results.append({"key": p.key, "ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}", "account_id": aid})
        return results

    # ---------------------------------------------- helpers for strategies (exact quantities, no whole-position guesses)
    def limit_through(self, key: str, side: str, ltp: float, pct: float) -> float:
        """A limit price `pct` % through the last price (below for a sell, above for a buy), on the tick grid."""
        inst = self.cat.get(key)
        tick = inst.tick if inst else 0.05
        buf = max(2 * tick, ltp * pct / 100.0)
        px = ltp - buf if side == "S" else ltp + buf
        return max(tick, round(round(px / tick) * tick, 2))

    def net_qty(self, key: str, product: str | None = None, account_id: str | None = None) -> int | None:
        """Net units held in `key` (negative = short), or None when the position book cannot be read."""
        rows = self.gateway(account_id).positions()
        if rows is None:
            return None
        return sum(r.net_qty for r in rows if r.key == key and (product is None or r.product == product))

    def order_row(self, order_id: str):
        """The order-book row for an order id, None if the book has no such order. Raises OrderUnknown if it cannot be read."""
        gws = self.all_gateways()
        for gw in gws.values():
            rows = gw.orders()
            if rows is not None:
                hit = next((o for o in rows if o.id == order_id), None)
                if hit:
                    return hit
        return None

    def open_orders(self, key: str, tag: str | None = None, account_id: str | None = None) -> list:
        gws = {account_id: self.gateway(account_id)} if account_id else self.all_gateways()
        out = []
        for gw in gws.values():
            rows = gw.orders()
            if rows is not None:
                out.extend([o for o in rows if o.is_open and o.key == key and (tag is None or o.tag == tag)])
        return out

    def close_exact(self, key: str, side: str, qty: int, product: str, tag: str, buffer_pct: float, account_id: str | None = None) -> str:
        """Close EXACTLY `qty` units with a marketable limit order. Skips the new-exposure checks (it only reduces risk)."""
        ltp = self.hub.ltp(key)
        if not ltp:
            raise OrderError("No live price to close with")
        return self._place_exact(key, side, qty, product, tag, "L", self.limit_through(key, side, ltp, buffer_pct), 0.0, account_id=account_id)

    def stop_exact(self, key: str, side: str, qty: int, product: str, tag: str, trigger: float, buffer_pct: float, account_id: str | None = None) -> str:
        """A resting exchange stop-loss (SL, limit) for exactly `qty` units: the limit sits `buffer_pct` % beyond the trigger."""
        inst = self._instrument(key)
        tick = inst.tick
        trigger = max(tick, round(round(trigger / tick) * tick, 2))
        buf = max(2 * tick, trigger * buffer_pct / 100.0)
        px = trigger + buf if side == "B" else max(tick, trigger - buf)
        px = max(tick, round(round(px / tick) * tick, 2))
        return self._place_exact(key, side, qty, product, tag, "SL", px, trigger, account_id=account_id)

    def _place_exact(self, key: str, side: str, qty: int, product: str, tag: str, order_type: str, price: float, trigger: float, account_id: str | None = None) -> str:
        inst = self._instrument(key)
        if qty <= 0:
            raise OrderError("Quantity must be positive")
        t = OrderTicket(key=key, side=side, lots=(qty // inst.lot if inst.lot > 1 else 0), qty=qty, product=product,
                        order_type=order_type, price=price, trigger=trigger, tag=tag)
        oid = self.gateway(account_id).place(inst, t, qty)
        self._note_order(oid, inst, t, qty, account_id=account_id)
        return oid

    def panic(self) -> dict:
        """Cancel every open order, stop the algo, then close every open position."""
        gws = self.all_gateways()
        cancelled, errors = 0, []
        for aid, gw in gws.items():
            for o in gw.orders() or []:
                if o.is_open:
                    try:
                        gw.cancel(o.id)
                        cancelled += 1
                    except Exception as e:
                        errors.append(f"cancel {o.id}: {e}")
        if self.on_panic:
            try:
                self.on_panic()
            except Exception as e:
                errors.append(f"algo stop: {e}")
        return {"cancelled": cancelled, "squareoff": self.squareoff(), "errors": errors}

    # ---------------------------------------------------------------- read views
    def positions_view(self, account_id: str | None = None) -> dict:
        gws = {account_id: self.gateway(account_id)} if account_id else self.all_gateways()
        if not gws:
            return {"ok": False, "rows": [], "totals": None, "reason": "not_connected"}
        all_rows = []
        for aid, gw in gws.items():
            rows = gw.positions()
            if rows is None:
                continue
            lbl = self.account_label(aid)
            for r in rows:
                r.account_id = aid
                r.account_label = lbl
                all_rows.append(r)
        all_rows.sort(key=lambda r: (r.net_qty == 0, r.label))
        self._follow_positions({r.key for r in all_rows if r.net_qty})
        return {"ok": True, "rows": [r.to_dict() for r in all_rows],
                "totals": {"realised": round(sum(r.realised for r in all_rows), 2),
                           "unrealised": round(sum(r.unrealised for r in all_rows), 2),
                           "pnl": round(sum(r.pnl for r in all_rows), 2), "open": sum(1 for r in all_rows if r.net_qty)}}

    def orders_view(self, account_id: str | None = None) -> dict:
        gws = {account_id: self.gateway(account_id)} if account_id else self.all_gateways()
        if not gws:
            return {"ok": False, "rows": [], "reason": "not_connected"}
        all_rows = []
        for aid, gw in gws.items():
            rows = gw.orders()
            if rows is None:
                continue
            lbl = self.account_label(aid)
            for r in rows:
                r.account_id = aid
                r.account_label = lbl
                inst = self.cat.get(r.key) if r.key else None
                if inst:
                    r.label = inst.label()
                all_rows.append(r)
        all_rows.sort(key=lambda o: (o.time or "", o.id), reverse=True)
        return {"ok": True, "rows": [r.to_dict() for r in all_rows]}

    def trades_view(self, account_id: str | None = None) -> dict:
        gws = {account_id: self.gateway(account_id)} if account_id else self.all_gateways()
        if not gws:
            return {"ok": False, "rows": [], "reason": "not_connected"}
        all_rows = []
        for aid, gw in gws.items():
            rows = gw.trades()
            if rows is None:
                continue
            lbl = self.account_label(aid)
            for r in rows:
                r.account_id = aid
                r.account_label = lbl
                inst = self.cat.get(r.key) if r.key else None
                if inst:
                    r.label = inst.label()
                all_rows.append(r)
        all_rows.sort(key=lambda t: (t.time or "", t.order_id), reverse=True)
        return {"ok": True, "rows": [r.to_dict() for r in all_rows]}
